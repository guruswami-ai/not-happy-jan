"""The canonical NHJ vibe-event contract.

ONE field set + ONE validator, shared by the MCP tool, the local and remote
marker hooks, the queue, and the worker — so every integration path accepts,
validates, serialises, and forwards exactly the same options.

Fields (all optional except ``intent``):
  intent         ok | err | warn | attn | celebrate | step
  message        free text, at most 500 characters ("" → a random in-character phrase)
  vibe_level     haptic intensity, int 1–10
  pretext        spoken prefix (e.g. "Node Om:")
  verbosity      low | medium | high
  emotion        neutral | alert | confidential | celebrate
  speed          TTS speed multiplier, 0.5–2.0   (applied by the audio layer — see #4)
  voice_variant  optional per-character voice override
  character      explicit character ("" → route by intent)
  source         who is speaking: "project" or "host:project" ("" → unknown); set by the hook /
                 remote agent so displays and voices can say WHICH session the event came from
  literal        speak ``message`` exactly as given (``nhj say``): no dynamic LLM rewrite, phrase
                 templates, garbling or preamble, and not downgraded to a chime. Default False. It is
                 left out of ``to_kwargs()`` when False so older daemons never see an unknown option.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

INTENTS   = ("ok", "err", "warn", "attn", "celebrate", "step")
MAX_MESSAGE_CHARS = 500
VERBOSITY = ("low", "medium", "high")
EMOTIONS  = ("neutral", "alert", "confidential", "celebrate")
#: Options accepted as inline marker kwargs ([Jan:ok|speed=1.2|…]) and MCP args.
OPTION_FIELDS = ("vibe_level", "pretext", "verbosity", "emotion", "speed", "voice_variant", "character")


class VibeValidationError(ValueError):
    """An event field is missing, out of range, or not an allowed value."""


@dataclass
class VibeEvent:
    intent: str
    message: str = ""
    vibe_level: int = 5
    pretext: str = ""
    verbosity: str = "medium"
    emotion: str = "neutral"
    speed: float = 1.0
    voice_variant: str = ""
    character: str = ""
    source: str = ""
    literal: bool = False

    def to_kwargs(self) -> dict:
        """Field dict — the exact keyword args for QueueManager.add and the MCP tool.

        ``literal`` is included only when True: every existing caller and any older remote daemon
        keeps seeing exactly the fields it knew."""
        d = asdict(self)
        if not d["literal"]:
            del d["literal"]
        return d


def _as_int(name: str, v, lo: int, hi: int) -> int:
    try:
        number = float(str(v).strip())
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError
        i = int(number)
    except (TypeError, ValueError):
        raise VibeValidationError(f"{name} must be an integer {lo}–{hi}, got {v!r}")
    if not (lo <= i <= hi):
        raise VibeValidationError(f"{name} must be {lo}–{hi}, got {i}")
    return i


def _as_float(name: str, v, lo: float, hi: float) -> float:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        raise VibeValidationError(f"{name} must be a number {lo}–{hi}, got {v!r}")
    if not (lo <= f <= hi):
        raise VibeValidationError(f"{name} must be {lo}–{hi}, got {f}")
    return f


def _as_bool(name: str, v) -> bool:
    if isinstance(v, bool):
        return v
    text = str(v).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("", "0", "false", "no", "off", "none"):
        return False
    raise VibeValidationError(f"{name} must be true or false, got {v!r}")


def _as_message(v) -> str:
    text = str(v or "")
    if len(text) > MAX_MESSAGE_CHARS:
        raise VibeValidationError(
            f"message must be at most {MAX_MESSAGE_CHARS} characters, got {len(text)}")
    return text


def _as_enum(name: str, v, allowed: tuple[str, ...]) -> str:
    s = str(v).strip().lower()
    if s not in allowed:
        raise VibeValidationError(f"{name} must be one of {', '.join(allowed)}, got {v!r}")
    return s


def validate(intent, *, message="", vibe_level=5, pretext="", verbosity="medium",
             emotion="neutral", speed=1.0, voice_variant="", character="", source="",
             literal=False) -> VibeEvent:
    """Build a validated VibeEvent. Raises :class:`VibeValidationError` on a bad value.

    Coerces strings (inline marker options arrive as strings) and clamps enums to
    lower-case, so the MCP tool and the hooks reject the same inputs identically.
    """
    return VibeEvent(
        intent=_as_enum("intent", intent, INTENTS),
        message=_as_message(message),
        vibe_level=_as_int("vibe_level", vibe_level, 1, 10),
        pretext=str(pretext or ""),
        verbosity=_as_enum("verbosity", verbosity, VERBOSITY),
        emotion=_as_enum("emotion", emotion, EMOTIONS),
        speed=_as_float("speed", speed, 0.5, 2.0),
        voice_variant=str(voice_variant or ""),
        character=str(character or "").strip().lower(),
        source=str(source or "").strip()[:60],
        literal=_as_bool("literal", literal),
    )


def from_inline(intent: str, options: dict, message: str = "",
                character: str = "") -> tuple[VibeEvent, list[str]]:
    """Validate inline marker options into a VibeEvent.

    Returns ``(event, unknown_keys)``. Unknown option keys are reported (the caller
    logs them) rather than silently accepted. An explicit marker character (the
    ``who`` in ``[Jan:ok]``) takes precedence over any inline ``character=``.
    """
    unknown = [k for k in options if k not in OPTION_FIELDS]
    fields = {k: v for k, v in options.items() if k in OPTION_FIELDS and k != "character"}
    who = character or options.get("character", "")
    return validate(intent, message=message, character=who, **fields), unknown
