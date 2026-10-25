"""`nhj say`: speak exactly the text you give it (#16, phase 2).

The text comes from the command line (or from stdin only when you pass `-`); nothing else is ever
spoken: not command output, arguments or the environment. It is bounded, sanitised, and sent as a
*literal* event, so the character chooses the voice and delivery but never the words (no dynamic LLM
rewrite, phrase templates, garbling, chime downgrade or digest). `--rewrite` opts back in to the
character's own rendering. The words are never written to NHJ's logs.

Statuses: 0 delivered (queued; a muted/headless/voice-off call still prints the text to stderr),
2 usage or bounds error, 10 nothing could be spoken with --strict (or the event was rejected),
124 --wait timed out (like timeout(1)).
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_CHARS = 240
HARD_MAX_CHARS = 1000
EXIT_OK, EXIT_USAGE, EXIT_UNDELIVERED, EXIT_TIMEOUT = 0, 2, 10, 124

#: Friendly severities map onto the one event vocabulary (ok|err|warn|attn|celebrate|step).
SEVERITIES = {
    "success": "ok", "ok": "ok",
    "info": "step", "progress": "step", "step": "step",
    "warning": "warn", "warn": "warn",
    "error": "err", "err": "err",
    "attention": "attn", "attn": "attn",
    "celebrate": "celebrate",
}


class SayError(ValueError):
    """A usage or bounds problem: reported on stderr, exit status 2."""


def intent_for(severity: str) -> str:
    try:
        return SEVERITIES[(severity or "info").strip().lower()]
    except KeyError:
        raise SayError(f"unknown severity {severity!r}; use one of: {', '.join(sorted(SEVERITIES))}") from None


def clean(text: str) -> str:
    """No control characters (newlines and tabs become spaces), whitespace collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x08\x0b-\x1f\x7f]+", " ", text or "")).strip()


def read_text(args: list[str], stdin, max_chars: int) -> str:
    """The text to speak: the arguments joined by spaces, or stdin when the only argument is `-`.

    stdin is read in a bounded way (never more than the hard cap plus one character), so a runaway
    pipe cannot make `say` buffer megabytes. Over-long text is refused, never silently truncated.
    """
    if not 1 <= max_chars <= HARD_MAX_CHARS:
        raise SayError(f"--max-chars must be 1-{HARD_MAX_CHARS}")
    if args == ["-"]:
        raw = stdin.read(HARD_MAX_CHARS + 1)
    else:
        raw = " ".join(args)
    text = clean(raw)
    if not text:
        raise SayError("nothing to say")
    if len(text) > max_chars:
        raise SayError(f"text is {len(text)} characters; the limit is {max_chars} "
                       f"(raise it with --max-chars, at most {HARD_MAX_CHARS})")
    return text


@dataclass(frozen=True)
class Speakable:
    ok: bool
    reason: str = ""


def can_speak(source: str) -> Speakable:
    """Whether this call can produce speech at all (the text is printed to stderr when it cannot)."""
    try:
        from nhj.state import get_flag
        if get_flag("mute", False):
            return Speakable(False, "NHJ is muted (`nhj mute`)")
        from nhj import gate
        if not gate.voice_allowed(source):
            return Speakable(False, "voice is off for this project (`nhj voice on`)")
    except Exception:
        pass
    if os.getenv("TTS_ENGINE", "qwen").strip().lower() == "none":
        return Speakable(False, "no speech engine is configured (TTS_ENGINE=none)")
    return Speakable(True)


def wait_processed(queue_dir: Path, name: str, timeout: float, poll: float = 0.1) -> str:
    """Wait until the worker has finished with queue item *name*.

    Returns "done", "rejected" (the worker quarantined it) or "timeout". "Done" means dispatched
    to every channel; the voice is handed to the mixer, so the last words may still be playing.
    """
    stem = name[:-len(".vibe")] if name.endswith(".vibe") else name
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if any(queue_dir.glob(f"{stem}.poison*")):
            return "rejected"
        if not (queue_dir / f"{stem}.vibe").exists() and not any(queue_dir.glob(f"{stem}.proc.*")):
            return "done"
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(poll)
