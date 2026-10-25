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
  (a claim), parses it, and returns a token. Unparseable items, and items
  ``event.validate`` rejects, are quarantined to ``*.poison`` so a bad file
  can never block the queue or burn the retry budget.
- The worker calls ``ack(token)`` (deletes the ``.proc`` file) only **after**
  dispatch completes. A ``.proc`` file left by a dead worker is re-queued on the
  next claim, with a bounded retry count before it is quarantined.
- Worker election is atomic under an ``flock``: the spawner records the child
  PID *before* releasing the lock, so a concurrent caller can't start a second
  worker. A worker only ever removes its **own** PID record.
"""
from __future__ import annotations

import base64
import contextlib
import datetime
import fcntl
import itertools
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from nhj import resources
from nhj.event import VibeValidationError, validate

_B64_FIELDS = ("intent", "message", "pretext", "source")
_MAX_RETRIES = 3          # re-queue a crashed-worker item this many times, then quarantine
# A dead worker is recovered at once via PID liveness. A recycled PID looks
# alive, so this lease stays short and reclaims that claim once its mtime goes
# stale. A live dispatch can outlast the lease (server start, the LLM, TTS);
# holding_claim refreshes the claim mtime for that stretch, and only up to
# _DISPATCH_BUDGET_SECONDS, so a normal line is not spoken twice. Past the
# budget the beat stops and the lease can reclaim a hung dispatch.
_CLAIM_LEASE_SECONDS = 30
_HEARTBEAT_SECONDS = 10   # well under the lease, so one late beat still counts
_DISPATCH_BUDGET_SECONDS = 180  # past a cold start, the LLM, and one TTS call
_CLAIM_ERROR_BACKOFF_SECONDS = 5
_LAZY_ATTRS = ("_LOG",)

# Compatibility declaration only; module __getattr__ resolves it on access.
_LOG: str


def _log_path() -> str:
    override = globals().get("_LOG")
    return override if override is not None else resources.log_file("worker.log")


def __getattr__(name: str) -> object:
    if name == "_LOG":
        return _log_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))

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
        resources.append_log(_log_path(), f"[{datetime.datetime.now().isoformat()}] {msg}")
    except OSError:
        # A log that cannot be written must not break the queue.
        return


def _touch_claim(token: Path) -> bool:
    """Refresh a claim's mtime. False when the file has already gone."""
    try:
        os.utime(token, None)
    except OSError:
        return False
    return True


@contextlib.contextmanager
def _holding_claim(token: Path, interval: float, budget: float) -> Iterator[None]:
    stop = threading.Event()
    started = time.monotonic()

    def _beat() -> None:
        while True:
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0 or stop.wait(min(interval, remaining)):
                return
            if time.monotonic() - started >= budget:
                return
            if not _touch_claim(token):
                return

    _touch_claim(token)
    thread = threading.Thread(target=_beat, name="nhj-claim-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=max(interval, 0.1) + 1.0)


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
            character: str = "", source: str = "") -> str:
        """Queue one event.

        Raises :class:`VibeValidationError` on a bad field and writes nothing.
        Stored fields are the canonical values from ``event.validate``.
        """
        ev = validate(
            intent, message=message, vibe_level=vibe_level, pretext=pretext,
            verbosity=verbosity, emotion=emotion, speed=speed,
            voice_variant=voice_variant, character=character, source=source,
        )
        ts = int(time.time() * 1000)
        name = f"{ts:013d}-{os.getpid()}-{next(_seq):06d}-{uuid.uuid4().hex[:8]}.vibe"
        data = {
            "intent":        base64.b64encode(ev.intent.encode("utf-8")).decode("ascii"),
            "message":       base64.b64encode(ev.message.encode("utf-8")).decode("ascii"),
            "vibe_level":    str(ev.vibe_level),
            "pretext":       base64.b64encode(ev.pretext.encode("utf-8")).decode("ascii"),
            "verbosity":     ev.verbosity,
            "emotion":       ev.emotion,
            "speed":         str(ev.speed),
            "voice_variant": ev.voice_variant,
            "nfe_step":      str(nfe_step),
            "character":     ev.character,
            "source":        base64.b64encode(ev.source.encode("utf-8")).decode("ascii"),
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
            try:
                data = self._checked(data)
            except VibeValidationError as e:  # bad fields — quarantine, do not retry
                self._quarantine(token, str(e))
                continue
            return token, data
        return None

    def holding_claim(self, token: Path) -> Iterator[None]:
        """Refresh *token*'s lease until dispatch finishes, or the budget runs out.

        Touch the claim mtime immediately, then every ``_HEARTBEAT_SECONDS``,
        until the context exits or ``_DISPATCH_BUDGET_SECONDS`` elapses. A
        dispatch that blocks inside one adapter (TTS can take longer than the
        lease) stays owned by this worker for that budget. After the budget
        the beat stops, so a hung adapter is reclaimed once the lease lapses.
        """
        return _holding_claim(token, _HEARTBEAT_SECONDS, _DISPATCH_BUDGET_SECONDS)

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

    def _checked(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Canonical copy of *data*. Raises VibeValidationError when a field is bad.

        ``vibe_level`` is written back as a plain integer string so the worker's
        ``int()`` accepts values validate allows, such as ``5.0``.
        """
        ev = validate(
            data.get("intent", ""),
            message=data.get("message", ""),
            vibe_level=data.get("vibe_level", 5),
            pretext=data.get("pretext", ""),
            verbosity=data.get("verbosity", "medium"),
            emotion=data.get("emotion", "neutral"),
            speed=data.get("speed", 1.0),
            voice_variant=data.get("voice_variant", ""),
            character=data.get("character", ""),
            source=data.get("source", ""),
        )
        checked = dict(data)
        checked.update({
            "intent": ev.intent,
            "message": ev.message,
            "vibe_level": str(ev.vibe_level),
            "pretext": ev.pretext,
            "verbosity": ev.verbosity,
            "emotion": ev.emotion,
            "speed": str(ev.speed),
            "voice_variant": ev.voice_variant,
            "character": ev.character,
            "source": ev.source,
        })
        return checked

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
        """Re-queue a claim whose worker has died, or whose lease has lapsed.

        A dead PID is reclaimed immediately. A live PID with a fresh lease is
        left alone. A recycled PID looks alive and is kept only until the
        lease expires, which is why that window stays short.
        """
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
            lf = open(_log_path(), "ab")
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
