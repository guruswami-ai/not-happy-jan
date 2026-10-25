#!/usr/bin/env python3
"""Claude Code Notification-hook — turns "needs permission / waiting / question" events into vibes.

The Stop hook only sees a *finished* turn, so a session blocked on a permission prompt was
silent. This catches the Notification event and fires an NHJ vibe through the same path
(local queue, or ``NHJ_REMOTE_URL`` for fleet nodes) with the session's ``source`` label.

    permission_prompt   -> attn  (Karren)   "needs your permission"
    elicitation_dialog  -> attn  (Karren)   "has a question"
    idle_prompt         -> warn  (Bazza)    "is waiting for you"
    anything else       -> attn  (the notification's own text)
"""
from __future__ import annotations

import json
import os
import sys

from nhj import resources

if __name__ == "__main__":
    # Hook entry point: load .env before importing nhj.hook (which resolves
    # log/state paths). Library import stays side-effect free; main() repeats
    # the idempotent bootstrap for direct callers.
    resources.load_env()

from nhj import event, hook

_MAP = {
    "permission_prompt":  ("attn", "needs your permission"),
    "elicitation_dialog": ("attn", "has a question"),
    "idle_prompt":        ("warn", "is waiting for you"),
}


def main() -> int:
    resources.load_env()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    kind = payload.get("notification_type", "")
    intent, phrase = _MAP.get(kind, ("attn", ""))
    remote_url = os.environ.get("NHJ_REMOTE_URL", "").strip()
    src = hook.source_label(payload.get("cwd", ""), remote=bool(remote_url))
    message = phrase or str(payload.get("message", ""))[:120]
    try:
        ev = event.validate(intent, message=message, source=src)
    except event.VibeValidationError as e:
        hook._log(f"notify: invalid event: {e}")
        return 0
    sent = hook._fire_remote(remote_url, ev) if remote_url else hook._fire_local(ev)
    hook._log(f"notify kind={kind} intent={intent} sent={sent} src={src!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
