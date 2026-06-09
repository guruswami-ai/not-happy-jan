"""Not-Happy-Jan MCP server.

Exposes `nhj_vibe` — a single tool that queues a multi-sensory feedback
event and returns immediately (non-blocking).

Transport is selected by env:
  NHJ_TRANSPORT  stdio (default) | sse | streamable-http
  NHJ_HOST       bind address for network transports (default 127.0.0.1)
  NHJ_PORT       port (default 8765)

stdio is correct for local Claude Code sessions. sse/streamable-http is
correct for a persistent daemon serving remote Claude sessions over LAN.
"""
from __future__ import annotations

import os
import signal

from dotenv import load_dotenv
from fastmcp import FastMCP

from nhj import resources

load_dotenv(resources.env_file())

signal.signal(signal.SIGCHLD, signal.SIG_IGN)

from nhj.queue_manager import QueueManager

mcp = FastMCP("Not-Happy-Jan")


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
    """
    from nhj import event
    try:
        ev = event.validate(intent, message=message, vibe_level=vibe_level, pretext=pretext,
                            verbosity=verbosity, emotion=emotion, speed=speed,
                            voice_variant=voice_variant, character=character)
    except event.VibeValidationError as e:
        return f"[nhj:error] {e}"
    qm = QueueManager()
    qm.add(**ev.to_kwargs())
    qm.start_worker_if_needed()
    return f"[nhj:{ev.intent}] queued"


def run() -> None:
    from nhj.procname import set_title
    set_title("NHJ MCP")
    transport = os.getenv("NHJ_TRANSPORT", "stdio").lower().strip()
    if transport == "stdio":
        mcp.run()
        return
    host = os.getenv("NHJ_HOST", "127.0.0.1")
    port = int(os.getenv("NHJ_PORT", "8765"))
    mcp.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    run()
