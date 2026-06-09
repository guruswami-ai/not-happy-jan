"""Not-Happy-Jan background worker.

Drains the vibe queue and dispatches each item through the configured
adapter chain: haptic → visual displays → TTS audio.

Spawned by the MCP server / hook; auto-exits after 30s idle.
"""
from __future__ import annotations

import sys
import time
import math

from dotenv import load_dotenv

from nhj import resources

load_dotenv(resources.env_file())

from nhj.adapters import load_adapters
from nhj.characters import character_by_name, resolve_character
from nhj.config import ADAPTER_ORDER
from nhj.queue_manager import QueueManager
from nhj.state import get_flag


def _parse_speed(value) -> float:
    try:
        speed = float(value if value not in (None, "") else 1.0)
    except (TypeError, ValueError) as e:
        raise ValueError(f"invalid speed {value!r}") from e
    if not math.isfinite(speed) or not 0.5 <= speed <= 2.0:
        raise ValueError(f"invalid speed {value!r}: expected 0.5..2.0")
    return speed


def _dispatch(task, adapters) -> None:
    """Render one claimed task through the adapter chain (errors are contained)."""
    intent        = task.get("intent", "ok")
    message       = task.get("message", "")
    vibe_level    = int(task.get("vibe_level", 5))
    pretext       = task.get("pretext", "")
    verbosity     = task.get("verbosity", "medium")
    emotion       = task.get("emotion", "neutral") or "neutral"
    voice_variant = task.get("voice_variant", "") or ""
    who           = task.get("character", "") or ""
    speed = _parse_speed(task.get("speed", 1.0))

    # Full message = pretext + inline message
    full_message = f"{pretext} {message}".strip() if pretext else message
    if verbosity == "low":
        full_message = ""

    # [Jan:ok]/[Bazza:err] explicitly name the character; [vibes:ok] auto-routes by intent.
    character = character_by_name(who, intent) if who else resolve_character(intent)

    for adapter in adapters:
        try:
            adapter.fire(
                intent=intent,
                message=full_message,
                character=character,
                vibe_level=vibe_level,
                emotion=emotion,
                voice_variant=voice_variant,
                speed=speed,
            )
        except Exception as e:
            print(f"[nhj] adapter {adapter.__class__.__name__} error: {e}",
                  file=sys.stderr)


def main() -> None:
    from nhj.procname import set_title
    set_title("NHJ worker")
    qm = QueueManager()
    adapters = load_adapters(ADAPTER_ORDER)

    idle = 0
    try:
        while idle < 30:
            claimed = qm.claim_next()
            if not claimed:
                idle += 1
                time.sleep(1)
                continue
            idle = 0
            token, task = claimed
            try:
                # Live mute toggle (nhj mute) — drop the vibe silently.
                if not get_flag("mute"):
                    _dispatch(task, adapters)
            except Exception as e:
                print(f"[nhj] dispatch error: {e}", file=sys.stderr)
                qm.nack(token, str(e))
            else:
                # Acknowledge only after dispatch returns. A hard crash before this
                # leaves the .proc file for recovery on the next claim.
                qm.ack(token)
    finally:
        qm.release_worker_slot()


if __name__ == "__main__":
    main()
