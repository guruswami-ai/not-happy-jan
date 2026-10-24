"""The config schema (#47): what a config file may contain.

One Pydantic model tree mirroring `config/default.yaml`. Every model forbids
unknown keys, so a typo (`adapterz:`, `levle: 4`) is a loud startup error
instead of a silently ignored override. Sections are individually optional so
partial user overrides validate as-is; user-defined character and mode *names*
stay open. Validation only — `nhj.config` returns the original merged mapping
unchanged, so YAML quirks callers already tolerate (bare `on`/`off` becoming
booleans) keep their existing meaning downstream.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Intent = Literal["ok", "err", "warn", "attn", "celebrate", "step"]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Forbid(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_enum(v, allowed: tuple[str, ...]):
    if v is None:
        return v
    probe = ("on" if v else "off") if isinstance(v, bool) else v
    if isinstance(probe, str) and probe.strip().lower() in allowed:
        return v
    raise ValueError(f"must be one of {', '.join(allowed)}")


class Routing(_Forbid):
    ladder: Optional[list[NonEmptyStr]] = None
    rung_by_intent: Optional[dict[Intent, NonEmptyStr]] = None
    default: Optional[NonEmptyStr] = None
    on_err: Optional[NonEmptyStr] = None
    on_attn: Optional[NonEmptyStr] = None

    @field_validator("ladder")
    @classmethod
    def _ladder_nonempty(cls, v):
        if v is not None and not v:
            raise ValueError("must not be empty")
        return v


class CharacterDials(_Forbid):
    level: Optional[Annotated[int, Field(ge=1, le=11)]] = None   # 11 = swearing tier
    chaos: Optional[Annotated[int, Field(ge=0, le=10)]] = None


class Mode(_Forbid):
    muzak: Optional[Union[str, bool]] = None
    audio_mode: Optional[str] = None
    scenario: Optional[str] = None
    display: Optional[Union[str, bool]] = None
    voice: Optional[str] = None
    haptic: Optional[Union[str, bool]] = None
    censor: Optional[str] = None
    ambient: Optional[str] = None
    ambient_gated: Optional[bool] = None
    dials: Optional[dict[str, dict[str, Annotated[int, Field(ge=0, le=11)]]]] = None

    @field_validator("muzak")
    @classmethod
    def _v_muzak(cls, v):
        return _check_enum(v, ("off", "on", "continuous", "cont", "background", "bg", "rave", "false", "none"))

    @field_validator("audio_mode")
    @classmethod
    def _v_audio(cls, v):
        return _check_enum(v, ("normal", "call-centre", "agent-vibes"))

    @field_validator("display")
    @classmethod
    def _v_display(cls, v):
        return _check_enum(v, ("normal", "party", "off"))

    @field_validator("haptic")
    @classmethod
    def _v_haptic(cls, v):
        return _check_enum(v, ("full", "simple", "off"))

    @field_validator("censor")
    @classmethod
    def _v_censor(cls, v):
        return _check_enum(v, ("quack", "beep", "honk", "off"))


class Playback(_Forbid):
    output_device: Optional[Union[str, int]] = None


class Orchestration(_Forbid):
    mode: Optional[str] = None
    chime: Optional[str] = None

    @field_validator("mode")
    @classmethod
    def _v_mode(cls, v):
        return _check_enum(v, ("quiet-while-busy", "always-speak"))


class Continuation(_Forbid):
    enabled: Optional[bool] = None
    command: Optional[list[str]] = None
    model_id: Optional[str] = None
    licence: Optional[str] = None
    prompt: Optional[str] = None
    keep_bars: Optional[Annotated[int, Field(ge=4, le=64)]] = None
    total_bars: Optional[Annotated[int, Field(ge=8, le=128)]] = None
    stable_phrases: Optional[Annotated[int, Field(ge=1)]] = None
    cooldown_phrases: Optional[Annotated[int, Field(ge=0)]] = None
    max_variants: Optional[Annotated[int, Field(ge=1, le=16)]] = None
    timeout_s: Optional[Annotated[Union[int, float], Field(gt=0)]] = None
    min_free_vram_mib: Optional[Annotated[int, Field(ge=0)]] = None


class Music(_Forbid):
    mode: Optional[str] = None
    pack: Optional[str] = None
    long_after_s: Optional[Annotated[Union[int, float], Field(ge=0)]] = None
    long_ramp_s: Optional[Annotated[Union[int, float], Field(gt=0)]] = None
    continuation: Optional[Continuation] = None

    @field_validator("mode")
    @classmethod
    def _v_mode(cls, v):
        return _check_enum(v, ("recorded", "generative"))


class Voice(_Forbid):
    default: Optional[Union[str, bool]] = None

    @field_validator("default")
    @classmethod
    def _v_default(cls, v):
        return _check_enum(v, ("on", "off", "true", "false", "yes", "no", "1", "0"))


class Audiocpp(_Forbid):
    url: Optional[str] = None
    voices_dir: Optional[str] = None
    voices: Optional[dict[str, str]] = None


class Claky(_Forbid):
    transport: Optional[str] = None
    url: Optional[str] = None
    token_env: Optional[str] = None
    host: Optional[str] = None
    key_env: Optional[str] = None
    intents: Optional[list[Intent]] = None
    min_interval_s: Optional[Annotated[Union[int, float], Field(ge=0)]] = None

    @field_validator("transport")
    @classmethod
    def _v_transport(cls, v):
        return _check_enum(v, ("bifrost", "local"))


class AwtrixDisplay(_Forbid):
    prefix: Optional[str] = None
    http: Optional[str] = None


class Awtrix(_Forbid):
    transport: Optional[str] = None
    mqtt_host: Optional[str] = None
    mqtt_port: Optional[Annotated[int, Field(ge=1, le=65535)]] = None
    mqtt_user: Optional[str] = None
    mqtt_pass: Optional[str] = None
    displays: Optional[list[Union[NonEmptyStr, AwtrixDisplay]]] = None
    brightness: Optional[Annotated[int, Field(ge=0, le=255)]] = None
    auto_brightness: Optional[bool] = None
    install_icons: Optional[bool] = None
    text_style: Optional[str] = None
    dynamic_text: Optional[bool] = None

    @field_validator("transport")
    @classmethod
    def _v_transport(cls, v):
        return _check_enum(v, ("http", "mqtt"))

    @field_validator("text_style")
    @classmethod
    def _v_text_style(cls, v):
        return _check_enum(v, ("ocker", "plain"))


class HapticTap(_Forbid):
    waveform: Optional[Union[str, int]] = None
    repeat: Optional[Annotated[int, Field(ge=1)]] = None


class Haptic(_Forbid):
    enabled: Optional[bool] = None
    intensity: Optional[Annotated[int, Field(ge=0, le=5)]] = None
    device_index: Optional[Annotated[int, Field(ge=0)]] = None
    taps: Optional[dict[Intent, HapticTap]] = None


class IntentEntry(_Forbid):
    haptic: Optional[Annotated[int, Field(ge=0)]] = None
    icon: Optional[str] = None
    sound: Optional[str] = None
    label: Optional[str] = None


class AudioBank(_Forbid):
    root: Optional[str] = None
    max_cache_entries: Optional[Annotated[int, Field(gt=0)]] = None


class NhjConfig(_Forbid):
    routing: Optional[Routing] = None
    characters: Optional[dict[str, CharacterDials]] = None
    flags: Optional[dict[str, bool]] = None
    modes: Optional[dict[str, Mode]] = None
    adapters: Optional[list[NonEmptyStr]] = None
    playback: Optional[Playback] = None
    orchestration: Optional[Orchestration] = None
    music: Optional[Music] = None
    voice: Optional[Voice] = None
    audiocpp: Optional[Audiocpp] = None
    claky: Optional[Claky] = None
    awtrix: Optional[Awtrix] = None
    haptic: Optional[Haptic] = None
    intents: Optional[dict[Intent, IntentEntry]] = None
    audio_bank: Optional[AudioBank] = None

    @field_validator("adapters")
    @classmethod
    def _adapters_nonempty(cls, v):
        if v is not None and not v:
            raise ValueError("must not be empty")
        return v


def validation_errors(cfg: dict) -> list[str]:
    """Human-readable schema errors for *cfg* (empty list = valid)."""
    try:
        NhjConfig.model_validate(cfg)
    except ValidationError as e:
        return [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
    return []
