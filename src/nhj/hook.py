#!/usr/bin/env python3
"""Claude Code Stop-hook — catches [vibes:INTENT] markers and fires NHJ.

Marker syntax:
    [vibes:ok]
    [vibes:err|Build broke]
    [vibes:warn|emotion=alert|Heads up]

Multiple markers fire in order. Install by running: nhj install-hook
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from nhj import resources

_LOG = resources.log_file("hook.log")
_SEEN = resources.state_file("hook-seen.json")   # per-session fire-once ledger
VALID       = {"ok", "err", "warn", "attn", "celebrate", "step"}
# Two marker forms (case-insensitive):
#   [vibes:ok]                 — intent only, auto-routes to a character by intent
#   [Jan:ok] [Bazza:err] …     — explicit character override (the "who" before the colon)
# The intent is constrained to the valid set so plain text like "[note:foo]" never matches.
MARKER_RE   = re.compile(
    r"\[([a-z]+):(ok|err|warn|attn|celebrate|step)(?:\|([^\]]*))?\]", re.IGNORECASE)


def _known_whos() -> set[str]:
    """vibes + every shipped character (voices/<name>/character.yaml). Anything else in the
    'who' slot is treated as prose and ignored."""
    whos = {"vibes"}
    try:
        for p in resources.character_defs_dir().iterdir():
            if (p / "character.yaml").is_file():
                whos.add(p.name.lower())
    except Exception:
        whos |= {"jan", "bazza", "karren"}
    return whos
_DEFAULTS   = {"ok": "Done.", "err": "Something failed.", "warn": "Heads up.",
               "attn": "Your attention is needed.", "celebrate": "Milestone reached.", "step": ""}


def _log(msg: str) -> None:
    try:
        import datetime
        Path(_LOG).parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a") as f:
            f.write(f"[{datetime.datetime.now().isoformat(timespec='milliseconds')}] {msg}\n")
    except OSError:
        pass


_SEEN_TTL = 86400.0   # drop ledger entries from sessions older than a day


def _seen_get(sid: str) -> str:
    try:
        e = json.loads(_SEEN.read_text()).get(sid)
    except Exception:
        return ""
    if isinstance(e, dict):
        return e.get("sig", "")
    return e or ""                            # legacy: bare sig string


def _seen_set(sid: str, sig: str) -> None:
    try:
        _SEEN.parent.mkdir(parents=True, exist_ok=True)
        try:
            d = json.loads(_SEEN.read_text())
        except Exception:
            d = {}
        now = time.time()
        # TTL: keep only fresh, timestamped entries (drops stale + legacy bare-sig)
        d = {k: v for k, v in d.items()
             if isinstance(v, dict) and now - float(v.get("ts", 0)) < _SEEN_TTL}
        d[sid] = {"sig": sig, "ts": now}
        if len(d) > 50:                       # hard cap as a backstop
            d = dict(list(d.items())[-50:])
        _SEEN.write_text(json.dumps(d))
    except Exception:
        pass


def _read_last_assistant_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        last = ""
        with path.open() as f:
            for line in f:
                try:
                    e = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                msg = e.get("message") or e
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content", "")
                text = "\n".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                ) if isinstance(content, list) else str(content)
                if text:
                    last = text
        return last
    except OSError:
        return ""


def _parse_inline(payload: str) -> tuple[dict, str]:
    kwargs, message = {}, ""
    for part in (payload or "").split("|"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            kwargs[k.strip()] = v.strip()
        elif not message:
            message = part
    return kwargs, message


def _fire_local(ev) -> bool:
    try:
        from nhj.queue_manager import QueueManager
    except ImportError:
        return False
    qm = QueueManager()
    qm.add(**ev.to_kwargs())
    qm.start_worker_if_needed()
    return True


def _fire_remote(url: str, ev) -> bool:
    try:
        import asyncio
        from fastmcp import Client

        async def _call():
            async with Client(url) as c:
                # Full option set (incl. character) — identical to the local path.
                await c.call_tool("nhj_vibe", ev.to_kwargs())
        asyncio.run(_call())
        return True
    except Exception as e:
        print(f"[nhj] remote dispatch failed ({url}): {e}", file=sys.stderr)
        return False


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    sid = payload.get("session_id", "")
    tpath = Path(payload.get("transcript_path", ""))
    _log(f"hook invoked sid={sid[:8]}")

    # This session's turn ended → it stops burning tokens. The muzak controller pauses
    # (and beeps "off hold") once ALL sessions are idle — ref-counted across concurrent
    # sessions. This is immediate and independent of the marker scan below.
    try:
        from nhj.state import get_flag
        if get_flag("muzak"):
            from nhj import inference_muzak
            inference_muzak.mark_idle(sid)
    except Exception:
        pass

    # The Stop hook can fire a beat BEFORE Claude Code flushes the final assistant
    # message — the one carrying the [vibes:] marker. UserPromptSubmit recorded a baseline
    # (the previous turn's final text) into the ledger; here we poll until the last-assistant
    # text has both CHANGED from that baseline (this turn's message has landed) AND gone
    # STABLE for two reads (it finished flushing — handles turns with several text blocks
    # where an earlier block lands before the final one). Change-keyed so a markerless turn
    # still returns promptly once its (markerless) final message settles.
    baseline = _seen_get(sid)
    wait = float(os.getenv("NHJ_HOOK_WAIT", "3.0"))
    min_wait = float(os.getenv("NHJ_HOOK_MIN", "0.4"))
    poll = float(os.getenv("NHJ_HOOK_POLL", "0.12"))
    started = time.monotonic()
    text = _read_last_assistant_text(tpath)
    sig = hashlib.sha1(text.encode()).hexdigest() if text else ""
    prev_read = None
    while True:
        elapsed = time.monotonic() - started
        changed = bool(sig) and sig != baseline
        stable = sig == prev_read
        if changed and stable and elapsed >= min_wait:
            break
        if elapsed >= wait:
            break
        prev_read = sig
        time.sleep(poll)
        text = _read_last_assistant_text(tpath)
        sig = hashlib.sha1(text.encode()).hexdigest() if text else ""

    waited = time.monotonic() - started
    changed = bool(sig) and sig != baseline
    _log(f"scan waited={waited:.2f}s changed={changed} len={len(text)} tail={text[-50:]!r}")
    if not changed:
        _log("hook complete fired=0 (final message not flushed in time)")
        return 0
    _seen_set(sid, sig)   # fire-once: never act on this same final message twice

    remote_url = os.environ.get("NHJ_REMOTE_URL", "").strip()
    known = _known_whos()
    fired = 0
    for match in MARKER_RE.finditer(text):
        who = match.group(1).lower()
        intent = match.group(2).lower()
        if who not in known or intent not in VALID:
            continue                            # a "[word:ok]" that isn't ours → prose
        # Skip backticked mentions — when I *discuss* `[Jan:ok]` in prose it's wrapped in
        # backticks; a real status marker stands on its own. Don't fire on the discussion.
        if (match.start() > 0 and text[match.start() - 1] == "`") or \
           (match.end() < len(text) and text[match.end()] == "`"):
            continue
        who_char = "" if who == "vibes" else who   # explicit character override
        options, message = _parse_inline(match.group(3) or "")
        if not message and os.environ.get("NHJ_ECHO_CONTEXT", "").lower() in ("1","true","yes"):
            preceding = text[:match.start()].rstrip()
            sentences = re.split(r"(?<=[.!?])\s+", preceding)
            message = (sentences[-1].strip() if sentences else "")[:200]

        # Validate at the boundary — same model the MCP tool uses. Bad options never
        # crash the session: log and skip this marker.
        try:
            from nhj import event
            ev, unknown = event.from_inline(intent, options, message, who_char)
        except Exception as e:
            _log(f"skip [{who}:{intent}] — invalid options: {e}")
            continue
        if unknown:
            _log(f"[{who}:{intent}] ignoring unknown options {unknown}")

        sent = _fire_remote(remote_url, ev) if remote_url else _fire_local(ev)
        if sent:
            fired += 1
            _log(f"fired who={who} intent={intent} msg={message[:60]!r}")
        else:
            target = "remote" if remote_url else "local"
            _log(f"failed target={target} who={who} intent={intent} msg={message[:60]!r}")

    _log(f"hook complete fired={fired}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
