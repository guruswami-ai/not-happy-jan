"""Not-Happy-Jan MCP server.

Exposes `nhj_vibe` (queue a multi-sensory event) and `nhj_music_accent`
(music-only accent on the daemon's bed). Both return immediately.

Transport is selected by env:
  NHJ_TRANSPORT  stdio (default) | sse | streamable-http
  NHJ_HOST       bind address for network transports (default 127.0.0.1)
  NHJ_PORT       port (default 8765)

stdio is correct for local Claude Code sessions. sse/streamable-http is
correct for a persistent daemon serving remote Claude sessions over LAN.
The network tools share ``NHJ_SHARED_SECRET``.
"""
from __future__ import annotations

import os
import signal

from fastmcp import FastMCP

from nhj import config, resources

if __name__ == "__main__":
    # Executable entry point: load .env before imports that capture paths.
    # Importing nhj.mcp_server as a library stays side-effect free; run()
    # repeats the idempotent bootstrap for direct callers.
    resources.load_env()

from nhj.queue_manager import QueueManager

mcp = FastMCP("Not-Happy-Jan")


def _unauthorised() -> str | None:
    """Block a network MCP call that does not carry NHJ_SHARED_SECRET.

    stdio is a local process and is not asked for a header. A blank secret
    keeps today's behaviour: loopback is the safety default, and the install
    commands warn before a non-loopback bind.
    """
    from nhj.lan_auth import allows, secret

    if not secret():
        return None
    if os.getenv("NHJ_TRANSPORT", "stdio").lower().strip() == "stdio":
        return None
    from fastmcp.server.dependencies import get_http_headers

    headers = get_http_headers(include={"authorization", "x-nhj-token"})
    if allows(headers):
        return None
    return "[nhj:error] unauthorised"


@mcp.tool()
def nhj_vibe(
    intent: str = "ok",
    message: str = "",
    vibe_level: int = 5,
    pretext: str = "",
    verbosity: str = "medium",
    emotion: str = "neutral",
    speed: float = 1.0,
    voice_variant: str = "",
    character: str = "",
    source: str = "",
    literal: bool = False,
) -> str:
    """Trigger a multi-sensory feedback event (haptic + visual + TTS).

    Args:
        intent:        Event type — ok | err | warn | attn | celebrate | step
        message:       Optional message text. Empty = random character phrase.
        vibe_level:    Haptic intensity 1-10.
        pretext:       Prefix spoken before the message (e.g. "Node Om:").
        verbosity:     low | medium | high — controls message detail.
        emotion:       TTS delivery style — neutral | alert | confidential | celebrate
        speed:         TTS speed multiplier (0.5–2.0). Backend-dependent.
        voice_variant: Override character voice variant if supported.
        character:     Explicit character override (e.g. "jan"); "" routes by intent.
        source:        Which session is speaking, e.g. "hog11:reachy" (shown on displays).
        literal:       Speak ``message`` exactly as given: no character rewrite, phrase templates
                       or garbling, and not downgraded to a chime. Default false.
    """
    denied = _unauthorised()
    if denied:
        return denied
    from nhj import event
    try:
        ev = event.validate(intent, message=message, vibe_level=vibe_level, pretext=pretext,
                            verbosity=verbosity, emotion=emotion, speed=speed,
                            voice_variant=voice_variant, character=character, source=source,
                            literal=literal)
    except event.VibeValidationError as e:
        return f"[nhj:error] {e}"
    qm = QueueManager()
    qm.add(**ev.to_kwargs())
    qm.start_worker_if_needed()
    return f"[nhj:{ev.intent}] queued"


@mcp.tool()
def nhj_music_accent(intent: str = "err") -> str:
    """Request a generative-music accent on the daemon's bed. No speech, no displays.

    Args:
        intent: Event type — err (breakdown) | ok | celebrate (release). Other
                intents are ignored. Same mapping as a local ``note_accent``.
    """
    denied = _unauthorised()
    if denied:
        return denied
    from nhj import inference_muzak
    inference_muzak.note_accent(intent)
    return f"[nhj:{intent}] noted"


def run() -> None:
    resources.load_env()
    config.get()                 # fail fast once, before serving
    from nhj.procname import set_title
    # The tool spawns the queue worker and does not wait for it. Ignore
    # SIGCHLD while serving so those children are reaped. Restored on the way
    # out: importing this module, or a run() that returns, must not leave the
    # caller's signal disposition changed.
    previous = signal.getsignal(signal.SIGCHLD)
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    set_title("NHJ MCP")
    try:
        transport = os.getenv("NHJ_TRANSPORT", "stdio").lower().strip()
        if transport == "stdio":
            mcp.run()
            return
        host = os.getenv("NHJ_HOST", "127.0.0.1")
        port = int(os.getenv("NHJ_PORT", "8765"))
        mcp.run(transport=transport, host=host, port=port)
    finally:
        signal.signal(signal.SIGCHLD, previous)


if __name__ == "__main__":
    run()
