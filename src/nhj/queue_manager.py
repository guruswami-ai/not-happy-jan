"""File-based vibe queue + worker lifecycle management.

Non-blocking by design: a producer (MCP server / hook / CLI) writes a `.vibe`
file and returns immediately. A background worker claims items, dispatches
them, and acknowledges only after processing — so a crash can't silently drop
or duplicate a notification.

Durability model
----------------
- Producers write ``<ts>-<pid>-<seq>-<rand>.vibe`` **atomically** (temp + rename),
  so a half-written file is never visible and same-millisecond writes never
  collide.
- ``claim_next()`` atomically renames the oldest ``*.vibe`` → ``*.proc.<pid>``
  (a claim), parses it, and returns a token. Unparseable items are quarantined
  to ``*.poison`` so a poison file can never block the queue.
- The worker calls ``ack(token)`` (deletes the ``.proc`` file) only **after**
  dispatch completes. A ``.proc`` file left by a dead worker is re-queued on the
  next claim, with a bounded retry count before it is quarantined.
- Worker election is atomic under an ``flock``: the spawner records the child
  PID *before* releasing the lock, so a concurrent caller can't start a second
  worker. A worker only ever removes its **own** PID record.
"""
from __future__ import annotations

import base64
import datetime
import fcntl
import itertools
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from nhj import resources

_B64_FIELDS = ("intent", "message", "pretext")
_MAX_RETRIES = 3          # re-queue a crashed-worker item this many times, then quarantine
# Short lease: a crashed worker is recovered immediately via PID-liveness; this is only
# the fallback for a hung-but-alive worker / PID reuse, so keep the stale window to
# seconds (any single dispatch is a few seconds at most) rather than minutes.
_CLAIM_LEASE_SECONDS = 30
_CLAIM_ERROR_BACKOFF_SECONDS = 5
_LOG = resources.log_file("worker.log")

_seq = itertools.count()  # process-local, thread-safe monotonic counter for unique names


def _is_zombie(pid: int) -> bool:
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip().startswith("Z")
    except (OSError, subprocess.SubprocessError):
        return False


def _pid_alive(pid: int) -> bool:
    """True iff *pid* is a live, non-zombie process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True            # exists but owned by another user
    except OSError:
        return False
    return not _is_zombie(pid)


def _log(msg: str) -> None:
    try:
        Path(_LOG).parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a") as lf:
            lf.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")
    except OSError:
        pass


class QueueManager:
    def __init__(self, queue_dir: Optional[str] = None):
        user = os.environ.get("USER", "default")
        self.queue_dir       = Path(queue_dir or f"/tmp/nhj-vibe-queue-{user}")
        self.lock_file       = self.queue_dir / "queue.lock"
        self.worker_pid_file = self.queue_dir / "worker.pid"
        self._claim_error_until: dict[Path, float] = {}
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.queue_dir.chmod(0o700)        # stay private even if it pre-existed
        except OSError:
            pass

    # ---- producer ---------------------------------------------------------
    def add(self, intent: str, message: str = "", vibe_level: int = 5,
            pretext: str = "", verbosity: str = "medium", emotion: str = "neutral",
            speed: float = 1.0, voice_variant: str = "", nfe_step: int = 0,
            character: str = "") -> str:
        ts = int(time.time() * 1000)
        name = f"{ts:013d}-{os.getpid()}-{next(_seq):06d}-{uuid.uuid4().hex[:8]}.vibe"
        data = {
            "intent":        base64.b64encode(intent.encode("utf-8")).decode("ascii"),
            "message":       base64.b64encode(message.encode("utf-8")).decode("ascii"),
            "vibe_level":    str(vibe_level),
            "pretext":       base64.b64encode(pretext.encode("utf-8")).decode("ascii"),
            "verbosity":     verbosity,
            "emotion":       emotion,
            "speed":         str(speed),
            "voice_variant": voice_variant,
            "nfe_step":      str(nfe_step),
            "character":     character,
        }
        body = "\n".join(f"{k}={v}" for k, v in data.items())
        self._atomic_write(self.queue_dir / name, body)
        return name

    def _atomic_write(self, path: Path, body: str) -> None:
        """Write *body* to *path* atomically (create temp + fsync + rename)."""
        tmp = self.queue_dir / f"{path.name}.tmp-{os.getpid()}-{next(_seq)}"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)              # atomic on the same filesystem
        except Exception:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    # ---- consumer (claim / ack) ------------------------------------------
    def claim_next(self) -> Optional[Tuple[Path, Dict[str, Any]]]:
        """Atomically claim the oldest pending item.

        Returns ``(token, data)`` or ``None``. Recovers items abandoned by dead
        workers, quarantines unparseable items, and never returns a poison file.
        """
        self._recover_stale()
        for vibe in sorted(self.queue_dir.glob("*.vibe")):
            retry_after = self._claim_error_until.get(vibe)
            if retry_after is not None and time.monotonic() < retry_after:
                continue
            token = self.queue_dir / f"{vibe.name[:-len('.vibe')]}.proc.{os.getpid()}"
            try:
                os.utime(vibe, None)            # lease starts when the item is claimed
                os.replace(vibe, token)        # claim — atomic; a racing worker hits FileNotFoundError
            except FileNotFoundError:
                continue                       # another worker claimed it first
            except OSError as e:
                _log(f"skip unclaimable {vibe.name}: {e}")
                self._claim_error_until[vibe] = time.monotonic() + _CLAIM_ERROR_BACKOFF_SECONDS
                continue                       # a filesystem error on one item must not block the rest
            self._claim_error_until.pop(vibe, None)
            data = self._parse(token)
            if data is None:                   # poison — quarantine and keep scanning
                self._quarantine(token, "unparseable")
                continue
            return token, data
        return None

    def ack(self, token: Path) -> None:
        """Acknowledge a claimed item — delete it now that processing is done."""
        try:
            token.unlink()
        except FileNotFoundError:
            pass

    def nack(self, token: Path, reason: str) -> None:
        """Return a failed claim to the queue, bounded by the shared retry limit."""
        self._requeue_or_quarantine(token, reason)

    # Back-compat: read-and-forget. Prefer claim_next()/ack() for durability.
    def get_next(self) -> Optional[Dict[str, Any]]:
        claimed = self.claim_next()
        if claimed is None:
            return None
        token, data = claimed
        self.ack(token)
        return data

    def _parse(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            data: Dict[str, Any] = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k] = base64.b64decode(v).decode("utf-8") if k in _B64_FIELDS else v
            return data if "intent" in data else None      # missing intent ⇒ structurally invalid
        except Exception:
            return None

    def _recover_stale(self) -> None:
        """Re-queue items whose claiming worker has died; quarantine past MAX_RETRIES."""
        for proc in self.queue_dir.glob("*.proc.*"):
            try:
                worker_pid = int(proc.name.rsplit(".proc.", 1)[1])
            except (ValueError, IndexError):
                continue
            try:
                lease_expired = time.time() - proc.stat().st_mtime >= _CLAIM_LEASE_SECONDS
            except FileNotFoundError:
                continue
            if _pid_alive(worker_pid) and not lease_expired:
                continue                                    # a live worker still owns a fresh claim
            owner = f"expired claim for pid {worker_pid}" if lease_expired else f"dead worker {worker_pid}"
            self._requeue_or_quarantine(proc, owner)

    def _requeue_or_quarantine(self, path: Path, reason: str) -> None:
        base = path.name.rsplit(".proc.", 1)[0]
        stem, retries = self._strip_retry(base)
        if retries >= _MAX_RETRIES:
            self._quarantine(path, f"{reason}; exceeded {_MAX_RETRIES} retries")
            return
        target = self.queue_dir / f"{stem}.r{retries + 1}.vibe"
        try:
            os.replace(path, target)
            _log(f"requeued {path.name}: {reason} (retry {retries + 1})")
        except FileNotFoundError:
            pass

    @staticmethod
    def _strip_retry(base: str) -> Tuple[str, int]:
        """Split a trailing ``.rN`` retry segment off a filename base."""
        head, _, tail = base.rpartition(".")
        if head and tail.startswith("r") and tail[1:].isdigit():
            return head, int(tail[1:])
        return base, 0

    def _quarantine(self, path: Path, reason: str) -> None:
        stem = path.name.split(".proc.")[0].split(".vibe")[0]
        try:
            os.replace(path, self.queue_dir / f"{stem}.poison")
        except OSError:
            try:
                path.unlink()
            except OSError:
                pass
        _log(f"quarantined {path.name}: {reason}")

    # ---- worker lifecycle -------------------------------------------------
    def start_worker_if_needed(self) -> None:
        """Spawn the worker iff none is live. Atomic: at most one worker wins."""
        self.lock_file.touch()
        with open(self.lock_file, "r+") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return                          # another spawner holds the lock — trust it
            try:
                if self.worker_pid_file.exists():
                    try:
                        if _pid_alive(int(self.worker_pid_file.read_text().strip())):
                            return
                    except (ValueError, OSError):
                        pass
                # Record the child PID BEFORE releasing the lock, so a concurrent
                # caller observes a live worker instead of spawning a duplicate.
                self._atomic_write(self.worker_pid_file, str(self._spawn_worker()))
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _spawn_worker(self) -> int:
        """Spawn the detached worker process and return its PID. (Seam for tests.)"""
        lf = None
        try:
            lf = open(_LOG, "ab")
            lf.write(f"\n=== spawn {datetime.datetime.now().isoformat()} ===\n".encode())
            stdout = stderr = lf
        except OSError:
            if lf is not None:
                lf.close()
                lf = None
            stdout = stderr = subprocess.DEVNULL
        try:
            # Module execution — works from a source checkout AND a wheel install
            # (no reliance on a src/nhj/worker.py path).
            proc = subprocess.Popen(
                [sys.executable, "-m", "nhj.worker"],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                start_new_session=True,
            )
            return proc.pid
        finally:
            if lf is not None:
                lf.close()

    def release_worker_slot(self) -> None:
        """On exit, remove our PID record — but never another worker's."""
        try:
            self.lock_file.touch()
            with open(self.lock_file, "r+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    if (self.worker_pid_file.exists()
                            and self.worker_pid_file.read_text().strip() == str(os.getpid())):
                        self.worker_pid_file.unlink()
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
