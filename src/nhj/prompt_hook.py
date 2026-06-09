#!/usr/bin/env python3
"""Lifecycle hook for hold music — dispatches by hook_event_name from stdin.

    UserPromptSubmit → agent is now busy → resume/start the muzak (if `hold` on)
    StopFailure      → turn ended with an API error → mark session idle immediately
    SessionEnd       → stop the muzak (always, cleanup)

Registered for UserPromptSubmit, StopFailure, and SessionEnd. Pausing on normal Stop lives
in hook.py (next to the vibe logic). Never blocks or errors the session — best-effort only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _scold_for_secret(prompt: str) -> None:
    """If the prompt is carrying a secret-shaped string, have Karren scold you.

    Detection only: `secret_scan.scan` returns a CATEGORY ("an API key") or None —
    the secret value is never read out, logged, or sent anywhere. The scold message
    contains only the category.
    """
    from nhj.secret_scan import scan
    category = scan(prompt)
    if not category:
        return
    from nhj.queue_manager import QueueManager
    qm = QueueManager()
    qm.add(intent="attn", message=f"you just pasted {category} into the chat", emotion="alert")
    qm.start_worker_if_needed()


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) or {}
    except Exception:
        payload = {}
    event = payload.get("hook_event_name", "")
    sid = payload.get("session_id", "")
    try:
        from nhj import inference_muzak
        from nhj.state import get_flag
        if event == "SessionEnd":
            inference_muzak.mark_idle(sid)          # this session gone → drop from busy set
            return 0
        if event == "StopFailure":
            # Turn ended due to an API error (rate limit, auth, billing, server error, or
            # max-output-token failure). Mark the session idle immediately so hold music
            # unwinds — no marker scan because there is no reliable final assistant message.
            inference_muzak.mark_idle(sid)
            if get_flag("stop_failure_vibe", False):
                try:
                    from nhj.queue_manager import QueueManager
                    qm = QueueManager()
                    qm.add(intent="warn", message="Claude stopped due to an API error")
                    qm.start_worker_if_needed()
                except Exception:
                    pass
            return 0
        if event == "UserPromptSubmit":
            # Seed the Stop-hook baseline NOW, while the previous turn's final assistant
            # message is definitely flushed. The Stop hook waits for the last-assistant text
            # to move off this baseline before scanning for [vibes:] markers — which is what
            # makes the marker scan immune to the transcript flush race.
            try:
                import hashlib
                from nhj.hook import _read_last_assistant_text, _seen_set
                prev = _read_last_assistant_text(Path(payload.get("transcript_path", "")))
                _seen_set(sid, hashlib.sha1(prev.encode()).hexdigest() if prev else "")
            except Exception:
                pass
            if get_flag("muzak", True):             # on by default — the ambient "still thinking" cue
                inference_muzak.mark_busy(sid)      # this session is burning tokens → play
            if get_flag("secret_guard", False):    # opt-in; enable with: nhj set secret_guard on
                _scold_for_secret(payload.get("prompt", ""))
    except Exception as e:
        from nhj import resources
        resources.debug(f"prompt_hook {event!r} failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
