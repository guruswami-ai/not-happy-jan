"""Multi-agent policy: who gets to speak while other sessions are still working.

The hold-music busy-set is ref-counted across sessions: the music plays while ANY session is
mid-turn. That is the right signal for "something is still working", but it means that with
an orchestrator and several workers, every worker that finishes would break in with a spoken
result (and a "you still there?" cue) while the orchestrator -- the call you are actually on
hold for -- is still going. This module decides, per event, whether to SPEAK or only CHIME.

    mode quiet-while-busy (default)
        routine completions (ok / celebrate / step) that land while OTHER sessions are still
        busy become a soft chime (plus the silent channels: board, displays, haptics). They are
        counted, and the next spoken completion adds "N other tasks finished while you waited".
        warn / err / attn ALWAYS speak: something needs a human.
    mode always-speak
        upstream behaviour: every event speaks.

Set with ``orchestration.mode`` in config or ``NHJ_ORCHESTRATION``.
The silent channels are never affected; only the voice channel is gated here.
"""
from __future__ import annotations

import os
import random
from pathlib import Path

from nhj import config, resources

MODES = ("quiet-while-busy", "always-speak")
COMPLETION = ("ok", "celebrate", "step")


def mode() -> str:
    m = (os.getenv("NHJ_ORCHESTRATION") or str(config.section("orchestration").get("mode", MODES[0]))).strip().lower()
    return m if m in MODES else MODES[0]


def voice_mode(intent: str) -> str:
    """``"speak"`` or ``"chime"`` for an event that is about to reach the voice channel."""
    if mode() == "always-speak" or intent not in COMPLETION:
        return "speak"
    from nhj import inference_muzak
    return "chime" if inference_muzak._real_busy() else "speak"


def note_chime(source: str = "") -> None:
    """Remember a completion that was chimed instead of spoken (for the digest)."""
    from nhj import inference_muzak

    def _m(d: dict) -> None:
        d.setdefault("quiet_done", []).append(source or "?")
        d["quiet_done"] = d["quiet_done"][-50:]

    inference_muzak._update(_m)


def peek_digest() -> int:
    """How many chimed completions are waiting to be mentioned (does not clear them)."""
    from nhj import inference_muzak
    return len(inference_muzak._read().get("quiet_done") or [])


def clear_digest(n: int) -> None:
    """Drop the oldest *n* chimed completions once they have actually been spoken, so any
    that arrived meanwhile stay pending."""
    from nhj import inference_muzak

    def _m(d: dict) -> None:
        rest = (d.get("quiet_done") or [])[max(0, n):]
        if rest:
            d["quiet_done"] = rest
        else:
            d.pop("quiet_done", None)

    inference_muzak._update(_m)


def take_digest() -> int:
    """How many completions were chimed since the last spoken one; resets the count."""
    from nhj import inference_muzak
    taken: list = []

    def _m(d: dict) -> None:
        taken.extend(d.pop("quiet_done", []) or [])

    inference_muzak._update(_m)
    return len(taken)


def digest_sentence(n: int) -> str:
    return f"{n} other {'task' if n == 1 else 'tasks'} finished while you waited." if n else ""


def chime_path() -> str | None:
    """A short bundled earcon: ``orchestration.chime`` (a file or dir), else sfx/buttons."""
    custom = str(config.section("orchestration").get("chime", "") or "").strip()
    cands: list[Path] = []
    for root in ([Path(custom).expanduser()] if custom else []) + [resources.audio_dir() / "sfx" / "buttons"]:
        if root.is_file():
            return str(root)
        if root.is_dir():
            cands += [p for p in root.iterdir() if p.suffix.lower() in (".wav", ".mp3", ".m4a", ".ogg")]
        if cands:
            break
    return str(random.choice(sorted(cands))) if cands else None


def play_chime() -> bool:
    """Queue the earcon on the fx bus (concurrent with the music bed, no voice-queue wait)."""
    from nhj import inference_muzak
    p = chime_path()
    return bool(p) and inference_muzak.submit_audio(p, bus="fx", gain=0.7)
