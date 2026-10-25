"""`nhj run`: wrap a command and announce how it ended (#16, phase 1).

NHJ must never change a script's result. The wrapped command keeps stdin, stdout and stderr, runs
without a shell, and its exit status passes through (a signal death becomes 128+N, as in a shell);
anything NHJ itself does afterwards is best-effort and can only fail silently.

The spoken message is built here from facts NHJ already has, never from the command's arguments or
output (those can hold secrets): "<label> finished in 12 seconds", "<label> failed with status 3".
It is deliberately short and factual, which is what the local ocker-bogan-nano model was trained on
(`Status update: ...` -> one short in-character line), so with dynamic lines on it is rephrased in
the character's voice and otherwise spoken as written.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass

DEFAULT_MIN_SECONDS = 10.0
_LABEL_MAX = 40
_INTERRUPT_STATUSES = (130, 143)          # 128+SIGINT / 128+SIGTERM: scripts that exit this way were interrupted


@dataclass(frozen=True)
class Outcome:
    intent: str                           # ok | warn | err
    message: str


def default_label(argv: list[str]) -> str:
    """The command's basename only. Arguments are never part of a label (they can carry secrets)."""
    return os.path.basename(argv[0]) if argv else "command"


def clean_label(text: str) -> str:
    """A label safe to speak and log: no control characters, collapsed whitespace, capped length."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_LABEL_MAX].strip() or "command"


def spoken_duration(seconds: float) -> str:
    s = max(0, int(round(seconds)))
    if s < 60:
        return f"{s} second" + ("" if s == 1 else "s")
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} minute" + ("" if m == 1 else "s")
    h, m = divmod(m, 60)
    out = f"{h} hour" + ("" if h == 1 else "s")
    return out + (f" {m} minute" + ("" if m == 1 else "s") if m else "")


def exit_status(returncode: int) -> int:
    """The status `nhj run` itself exits with: the command's, or 128+N for death by signal N."""
    return returncode if returncode >= 0 else 128 - returncode


def decide(returncode: int, seconds: float, label: str, min_seconds: float = DEFAULT_MIN_SECONDS,
           only_failures: bool = False) -> Outcome | None:
    """What (if anything) to announce. Failures always speak; successes and interruptions only
    when the command took at least *min_seconds* (a quick command you were watching stays silent)."""
    status = exit_status(returncode)
    long_enough = seconds >= min_seconds
    if status == 0:
        if only_failures or not long_enough:
            return None
        return Outcome("ok", f"{label} finished in {spoken_duration(seconds)}")
    if returncode < 0 or status in _INTERRUPT_STATUSES:
        return Outcome("warn", f"{label} was interrupted") if long_enough else None
    return Outcome("err", f"{label} failed with status {status}")


def run_command(argv: list[str]) -> tuple[int, float]:
    """Run *argv* with inherited stdio, no shell. Returns (returncode, seconds).

    A command that cannot start reports 127 (not found) or 126 (not executable) like a shell. Ctrl-C
    reaches the whole foreground process group, so the child handles it itself; we keep waiting for
    it rather than dying first, and forward SIGTERM/SIGHUP so a service manager stopping us stops it.
    """
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv)
    except (FileNotFoundError, NotADirectoryError):
        print(f"nhj run: command not found: {argv[0]}", file=sys.stderr)
        return 127, time.monotonic() - started
    except (OSError, ValueError) as e:
        # Anything else that stops the exec (permission denied, exec format error, argument list
        # too long, an embedded NUL, ...): a shell reports these as "cannot execute", status 126.
        # Only the program name and the OS reason are printed, never the arguments.
        reason = e.strerror if isinstance(e, OSError) and e.strerror else type(e).__name__
        print(f"nhj run: cannot execute {argv[0]}: {reason}", file=sys.stderr)
        return 126, time.monotonic() - started

    previous = {}

    def _forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except OSError:
            pass

    for sig in (signal.SIGTERM, signal.SIGHUP):
        try:
            previous[sig] = signal.signal(sig, _forward)
        except (ValueError, OSError):      # not the main thread / unsupported: skip forwarding
            pass
    try:
        while True:
            try:
                rc = proc.wait()
                break
            except KeyboardInterrupt:
                continue
    finally:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
    return rc, time.monotonic() - started


def announce(outcome: Outcome, source: str, character: str = "") -> None:
    """Queue the event exactly as `nhj vibe` does. Best effort: it must never change the exit status."""
    try:
        from nhj import event
        from nhj.queue_manager import QueueManager
        ev = event.validate(outcome.intent, message=outcome.message, character=character, source=source)
        qm = QueueManager()
        qm.add(**ev.to_kwargs())
        qm.start_worker_if_needed()
    except Exception:
        try:
            from nhj import resources
            resources.debug("nhj run: announce failed")
        except Exception:
            pass
