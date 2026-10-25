#!/usr/bin/env python3
"""Claude Code Stop-hook — catches [vibes:INTENT] markers and fires NHJ.

Marker syntax:
    [vibes:ok]
    [vibes:err|Build broke]
    [vibes:warn|emotion=alert|Heads up]

Multiple markers fire in order. Install by running: nhj install-hook
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from nhj import resources

if __name__ == "__main__":
    # Hook entry point: agents do not start hooks from a shell, so load .env
    # (NHJ_SHARED_SECRET / NHJ_REMOTE_URL) before resolving log/state paths.
    # Importing nhj.hook as a library stays side-effect free; main() repeats
    # the idempotent bootstrap for direct callers.
    resources.load_env()

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


def source_label(cwd: str, remote: bool = False) -> str:
    """Who is speaking: the project dir name, prefixed by this host when dispatching to a
    remote NHJ daemon (so a fleet's displays can tell sessions on different machines apart)."""
    proj = os.path.basename((cwd or "").rstrip("/")) or ""
    if remote:
        import socket
        return f"{socket.gethostname().split('.')[0]}:{proj}".strip(":")
    return proj


def _log(msg: str) -> None:
    try:
        import datetime
        resources.append_log(
            _LOG, f"[{datetime.datetime.now().isoformat(timespec='milliseconds')}] {msg}")
    except OSError:
        pass


def _log_fire(prefix: str, who: str, intent: str, message: str) -> None:
    """Intent and length by default. The message text is kept only under NHJ_DEBUG."""
    if os.environ.get("NHJ_DEBUG"):
        _log(f"{prefix} who={who} intent={intent} msg={message!r}")
    else:
        _log(f"{prefix} who={who} intent={intent} chars={len(message)}")


_SEEN_TTL = 86400.0   # drop ledger entries from sessions older than a day
_SEEN_CAP = 50


def _seen_get(sid: str) -> str:
    try:
        e = json.loads(_SEEN.read_text()).get(sid)
    except Exception:
        return ""
    if isinstance(e, dict):
        return e.get("sig", "")
    return e or ""                            # legacy: bare sig string


def _seen_update(mutate) -> None:
    """Locked read-modify-write of the fire-once ledger.

    Same shape as ``state.update``: exclusive flock, unique temp file, atomic rename.
    Two Stop hooks (or a Stop racing the prompt hook) otherwise clobber the file.
    """
    _SEEN.parent.mkdir(parents=True, exist_ok=True)
    lock = _SEEN.with_suffix(".json.lock")
    lock.touch()
    with open(lock, "r+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            try:
                loaded = json.loads(_SEEN.read_text())
            except Exception:
                loaded = {}
            if not isinstance(loaded, dict):
                loaded = {}
            mutate(loaded)
            token = f".{os.getpid()}.{os.urandom(4).hex()}.json.tmp"
            tmp = _SEEN.with_suffix(token)
            tmp.write_text(json.dumps(loaded))
            tmp.replace(_SEEN)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _seen_set(sid: str, sig: str) -> None:
    """Record this session's baseline. The cap keeps the newest timestamps, not the
    first-seen keys: updating a long-lived session must not push it out of the file."""
    def _m(d: dict) -> None:
        now = time.time()
        fresh = {
            k: v for k, v in d.items()
            if isinstance(v, dict) and now - float(v.get("ts", 0)) < _SEEN_TTL
        }
        fresh.pop(sid, None)
        fresh[sid] = {"sig": sig, "ts": now}
        if len(fresh) > _SEEN_CAP:
            ranked = sorted(fresh.items(), key=lambda kv: float(kv[1].get("ts", 0)))
            fresh = dict(ranked[-_SEEN_CAP:])
        d.clear()
        d.update(fresh)

    try:
        _seen_update(_m)
    except Exception:
        pass


_TAIL_BYTES = 256 * 1024   # first transcript read window; grows only if no answer in it


def _last_assistant_in(chunk: bytes) -> str:
    last = ""
    for raw in chunk.splitlines():
        try:
            e = json.loads(raw.strip())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(e, dict):
            continue
        msg = e.get("message") or e
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        text = "\n".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ) if isinstance(content, list) else str(content)
        if text:
            last = text
    return last


# Per-path scan cache for the poll loop: path -> (inode, size seen, offset after
# the last complete line, last assistant text). Transcripts are append-only, so
# an unchanged size means nothing new to parse, and growth only needs the bytes
# from the last line boundary (a still-flushing final line is re-read whole).
_TAIL_CACHE: dict[str, tuple[int, int, int, str]] = {}


def _after_last_newline(start: int, chunk: bytes) -> int:
    nl = chunk.rfind(b"\n")
    return start + nl + 1 if nl >= 0 else start


def _read_last_assistant_text(path: Path) -> str:
    """Last non-empty assistant text, read from the tail of the transcript (#45).

    The Stop hook polls this every NHJ_HOOK_POLL while waiting for the final
    message to flush, so cost must not grow with transcript size *or* with poll
    count: the first call reads the last _TAIL_BYTES (widening x4 only when no
    assistant text is in the window — e.g. a final message larger than the
    window, which may mean one whole-file read); later calls answer from the
    scan cache when the file is unchanged and parse only appended bytes when
    it grew. A truncated or replaced file (size shrink / inode change) rescans."""
    try:
        st = path.stat()
    except OSError:
        return ""
    size, ino = st.st_size, st.st_ino
    key = str(path)
    cached = _TAIL_CACHE.get(key)
    if size == 0 or (cached and cached[0] == ino and size < cached[2]):
        # Truncated (possibly about to be rewritten on the same inode): the cached
        # offset no longer describes this file, so the next poll must rescan (#60).
        _TAIL_CACHE.pop(key, None)
        if size == 0:
            return ""
        cached = None
    if cached and cached[0] == ino and cached[1] == size:
        return cached[3]                       # unchanged since last poll: no re-read
    try:
        with path.open("rb") as f:
            if cached and cached[0] == ino and cached[1] < size and cached[2] <= size:
                start = cached[2]              # append-only growth: new bytes only
                f.seek(start)
                chunk = f.read()
                last = _last_assistant_in(chunk) or cached[3]
                line_off = _after_last_newline(start, chunk)
            else:
                window = max(1024, int(_TAIL_BYTES))
                while True:
                    offset = max(0, size - window)
                    f.seek(offset)
                    raw = f.read()
                    chunk = raw
                    if offset > 0:             # first line is partial; drop it
                        nl = chunk.find(b"\n")
                        chunk = chunk[nl + 1:] if nl >= 0 else b""
                    last = _last_assistant_in(chunk)
                    if last or offset == 0:
                        line_off = _after_last_newline(offset, raw)
                        break
                    window *= 4
    except OSError:
        return ""
    if len(_TAIL_CACHE) > 8:                  # one transcript per hook process in practice
        _TAIL_CACHE.clear()
    _TAIL_CACHE[key] = (ino, size, line_off, last)
    return last


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


_REMOTE_TIMEOUT = 5.0   # s; override with NHJ_REMOTE_TIMEOUT


def _remote_timeout() -> float:
    try:
        v = float(os.getenv("NHJ_REMOTE_TIMEOUT", str(_REMOTE_TIMEOUT)))
    except ValueError:
        return _REMOTE_TIMEOUT
    return v if v > 0 else _REMOTE_TIMEOUT


def _tool_text(result) -> str:
    """Text of a FastMCP tool result. ``None`` (the test doubles) is empty, not an error."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    data = getattr(result, "data", None)
    if isinstance(data, str) and data:
        return data
    if isinstance(data, dict):
        inner = data.get("result")
        if isinstance(inner, str) and inner:
            return inner
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _call_remote_tool(url: str, name: str, args: dict) -> str:
    """Call an MCP tool on *url*. Returns the tool text.

    Raises on timeout, transport error, or a ``[nhj:error]`` result. A hung LAN
    daemon must not hang the hook to the agent's 30s hook timeout.
    """
    import asyncio
    from fastmcp import Client
    from nhj.lan_auth import secret

    token = secret()
    # Omit auth when the secret is blank. The test doubles only accept ``url``,
    # and a blank secret means the server is not checking.
    auth = {"auth": token} if token else {}

    async def _call():
        async with Client(url, **auth) as c:
            result = await c.call_tool(name, args)
        text = _tool_text(result)
        # The server returns a denial as ordinary tool text, not an MCP error.
        # Counting that as fired hides the alert the secret is meant to protect.
        if getattr(result, "is_error", False) or text.startswith("[nhj:error]"):
            raise RuntimeError(text or "remote tool error")
        return text

    return asyncio.run(asyncio.wait_for(_call(), timeout=_remote_timeout()))


def _fire_remote(url: str, ev) -> bool:
    try:
        # Full option set (incl. character), identical to the local path.
        _call_remote_tool(url, "nhj_vibe", ev.to_kwargs())
        return True
    except Exception as e:
        print(f"[nhj] remote dispatch failed ({url}): {e}", file=sys.stderr)
        return False


def _fire_remote_accent(url: str, intent: str) -> None:
    """Ask the remote daemon for a music-only accent. Never raises; never prints."""
    try:
        _call_remote_tool(url, "nhj_music_accent", {"intent": intent})
    except Exception:
        pass            # a music accent must never fail the hook


def main() -> int:
    resources.load_env()
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
        if get_flag("muzak", True):
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
    if os.environ.get("NHJ_DEBUG"):
        _log(f"scan waited={waited:.2f}s changed={changed} len={len(text)} tail={text[-50:]!r}")
    else:
        _log(f"scan waited={waited:.2f}s changed={changed} len={len(text)}")
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
        ev.source = source_label(payload.get("cwd", ""), remote=bool(remote_url))

        sent = _fire_remote(remote_url, ev) if remote_url else _fire_local(ev)
        if sent:
            fired += 1
            _log_fire("fired", who, intent, message)
        else:
            target = "remote" if remote_url else "local"
            _log_fire(f"failed target={target}", who, intent, message)

    _log(f"hook complete fired={fired}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
