"""Vestaboard ("claky") adapter — agent status on a 6x22 split-flap board.

Shows each NHJ vibe as a short headline + which session it came from + the message.
The board accepts roughly one update per 15 s and a flap change takes seconds, so this
adapter NEVER blocks the worker (which also plays audio): ``fire`` writes the newest
message to a pending file and starts a detached sender that honours the rate limit.
Newer messages replace older pending ones — the board shows the latest, not a backlog.

Config — config/default.yaml ``claky:`` block (env overrides in brackets):
    transport:   bifrost | local                      [CLAKY_TRANSPORT]
    url:         bifrost MCP URL (transport: bifrost)  [CLAKY_URL]
    token_env:   env var holding the bifrost bearer token (default BIFROST_KV_KEY)
    host:        board host for the Local API (transport: local)  [CLAKY_HOST]
    key_env:     env var holding the Local API key (default VESTABOARD_LOCAL_KEY)
    intents:     which intents to show (default: all but ``step``)
    min_interval_s: 15

Bifrost transport calls the gateway's ``claky-send_message`` MCP tool (the board's own
formatter handles layout). Secrets are read from the environment at send time.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from typing import TYPE_CHECKING

from nhj import config, resources
from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character

COLS, ROWS = 22, 6
HEADLINES = {
    "ok": "ALL DONE", "celebrate": "WOO HOO", "warn": "HEADS UP",
    "err": "NOT HAPPY JAN", "attn": "NEEDS YOU", "step": "WORKING",
}


def _cfg() -> dict:
    """The claky settings with the documented env overrides applied in ONE place, so the
    availability check and the detached sender always agree on the effective transport."""
    cfg = dict(config.section("claky"))
    for key, env in (("transport", "CLAKY_TRANSPORT"), ("url", "CLAKY_URL"), ("host", "CLAKY_HOST")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def wrap(text: str) -> str:
    """Word-wrap to the board grid so words are never split mid-word."""
    lines, cur = [], ""
    for w in text.split():
        w = w[:COLS]
        if cur and len(cur) + 1 + len(w) > COLS:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:ROWS])


def compose(intent: str, message: str, source: str = "") -> str:
    head = HEADLINES.get(intent, intent.upper())
    return wrap(" ".join(x for x in (head, source, message) if x))


def _pending():
    return resources.state_file("claky-pending")


def _last():
    return resources.state_file("claky-last")


def _lock():
    return resources.state_file("claky-lock")


def _env_token(cfg: dict) -> str:
    """The bearer token: the process env first, then ``~/.env`` (where this host keeps its
    shared service keys), so hook-spawned workers need no secret copied into NHJ's own .env."""
    name = cfg.get("token_env") or "BIFROST_KV_KEY"
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in (Path.home() / ".env").read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _send_bifrost(cfg: dict, text: str) -> None:
    url = cfg.get("url", "")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "claky-send_message", "arguments": {"message": text}}}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {_env_token(cfg)}", "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    urllib.request.urlopen(req, timeout=15).read()


def _send_local(cfg: dict, text: str) -> None:
    """Vestaboard Local API: needs a character grid, so use the cloud-free text->codes map."""
    host = cfg.get("host", "")
    key = os.environ.get(cfg.get("key_env") or "VESTABOARD_LOCAL_KEY", "")
    codes = {c: i + 1 for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
    codes.update({c: 27 + i for i, c in enumerate("1234567890")})
    codes.update({"!": 37, "@": 38, "#": 39, "$": 40, "(": 41, ")": 42, "-": 44, "+": 46,
                  "&": 47, "=": 48, ";": 49, ":": 50, "'": 52, '"': 53, "%": 54, ",": 55,
                  ".": 56, "/": 59, "?": 60})
    lines = text.upper().split("\n")[:ROWS]
    grid = []
    for row in range(ROWS):
        line = (lines[row] if row < len(lines) else "")[:COLS]
        pad = (COLS - len(line)) // 2
        cells = [0] * pad + [codes.get(c, 0) for c in line]
        grid.append(cells + [0] * (COLS - len(cells)))
    req = urllib.request.Request(f"http://{host}:7000/local-api/message", data=json.dumps(grid).encode(),
                                 headers={"X-Vestaboard-Local-Api-Key": key, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=15).read()


def sender_main() -> int:
    """Detached sender: take the lock, honour the rate limit, send the newest pending text."""
    import fcntl
    cfg = _cfg()
    interval = float(cfg.get("min_interval_s", 15))
    lock = open(_lock(), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0   # another sender is already waiting; it will pick up the newest pending text
    pend, last = _pending(), _last()
    while pend.exists():
        try:
            wait = interval - (time.time() - float(last.read_text()))
        except (OSError, ValueError):
            wait = 0
        if wait > 0:
            time.sleep(wait)
        # Claim atomically (rename) BEFORE reading: a newer message that fire() publishes after
        # this point lands in a fresh `pending` and is sent by the next iteration, not deleted.
        claimed = pend.with_name(pend.name + ".claimed")
        try:
            os.replace(pend, claimed)
            text = claimed.read_text()
            claimed.unlink()
        except OSError:
            break
        try:
            (_send_local if cfg.get("transport") == "local" else _send_bifrost)(cfg, text)
        except Exception as e:  # noqa: BLE001
            print(f"[nhj] claky: send failed: {e!r}", file=sys.stderr)
        last.write_text(str(time.time()))
    return 0


def _publish(text: str) -> None:
    """Replace the pending message atomically (temp file + rename) so a sender never sees a
    half-written one."""
    pend = _pending()
    tmp = pend.with_name(f"{pend.name}.tmp-{os.getpid()}")
    tmp.write_text(text)
    os.replace(tmp, pend)


class ClakyAdapter(NotificationAdapter):
    def available(self) -> bool:
        cfg = _cfg()
        if cfg.get("transport") == "local":
            return bool(cfg.get("host"))
        return bool(cfg.get("url") and _env_token(cfg))

    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, source: str = "", **kwargs) -> bool:
        if not self.available():
            return False
        allowed = _cfg().get("intents") or [i for i in HEADLINES if i != "step"]
        if intent not in allowed:
            return True   # configured to skip this intent — not a failure
        _publish(compose(intent, message, source))
        subprocess.Popen([sys.executable, "-m", "nhj.adapters.claky"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True


if __name__ == "__main__":
    sys.exit(sender_main())
