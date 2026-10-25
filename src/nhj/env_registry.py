"""Environment variable registry (#47): the single source of truth for every
environment variable NHJ reads.

Each entry names the type, the default (as written in ``.env``), the YAML key it
overrides (if any), and when it is read:

- ``live``    — read at the point of use (new processes / new calls see edits)
- ``startup`` — read when a long-lived process (server, mixer, supervisor) starts
- ``import``  — captured at module import (config/state path resolution)

``tests/test_env_registry.py`` fails when code under ``src/nhj`` reads a literal
env var that is not registered here, and when the generated table in
``docs/configuration.md`` is stale. Call sites migrate onto typed lookup
incrementally; until then this registry is descriptive, enforced by that test.
Scope: ``nhj`` (ours), ``provider`` (device/service credentials), ``system``
(OS/HuggingFace variables we only read).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class EnvVar:
    name: str
    type: str                       # str | int | float | bool | path | url | enum | list
    default: Optional[str]          # as written in .env; None = unset unless set
    yaml: Optional[str]             # YAML key this overrides, if any
    timing: str                     # live | startup | import
    description: str
    scope: str = "nhj"              # nhj | provider | system


def _e(name, type, default, yaml, timing, description, scope="nhj"):
    return EnvVar(name, type, default, yaml, timing, description, scope)


_ENTRIES = [
    # --- core paths / process -------------------------------------------------
    _e("NHJ_CONFIG_FILE", "path", None, None, "import", "Use exactly this config file instead of the search order"),
    _e("NHJ_STATE_FILE", "path", None, None, "import", "Override the live state.json location"),
    _e("NHJ_BIN_DIR", "path", None, None, "live", "Directory the `nhj` launcher is written to on install"),
    _e("NHJ_MEDIA_BASE", "url", None, None, "live", "Base URL media downloads are fetched from"),
    _e("NHJ_DEBUG", "bool", None, None, "live", "Enable `[nhj]` debug lines on stderr (any set value)"),
    _e("USER", "str", None, None, "startup", "Login name (queue dir suffix, session labels)", "system"),
    _e("USERNAME", "str", None, None, "startup", "Login name fallback on Windows-style environments", "system"),
    _e("XDG_CACHE_HOME", "path", None, None, "startup", "XDG cache base for TTS model cache", "system"),
    _e("XDG_CONFIG_HOME", "path", None, None, "startup", "XDG config base for the user config dir", "system"),
    _e("XDG_DATA_HOME", "path", None, None, "startup", "XDG data base for installed media/data dir", "system"),
    _e("XDG_STATE_HOME", "path", None, None, "startup", "XDG state base for runtime state", "system"),
    # --- hooks ----------------------------------------------------------------
    _e("NHJ_HOOK_WAIT", "float", "3.0", None, "live", "Seconds the Stop hook waits for the final message to flush"),
    _e("NHJ_HOOK_MIN", "float", "0.4", None, "live", "Minimum seconds before the Stop hook accepts a flushed message"),
    _e("NHJ_HOOK_POLL", "float", "0.12", None, "live", "Transcript poll interval while waiting for the flush"),
    _e("NHJ_REMOTE_URL", "url", "", None, "live", "LAN MCP endpoint hooks dispatch to instead of the local queue"),
    _e("NHJ_REMOTE_TIMEOUT", "float", "5", None, "live", "Deadline for one remote hook dispatch (seconds)"),
    _e("NHJ_ECHO_CONTEXT", "str", "", None, "live", "Verbatim extra context line hooks add when echoing a marker"),
    # --- characters / modes ---------------------------------------------------
    _e("NHJ_DEFAULT_CHARACTER", "str", None, "routing.default", "live", "Fallback character when routing gives none"),
    _e("NHJ_ERROR_CHARACTER", "str", None, "routing.on_err", "live", "Character forced for err intents"),
    _e("NHJ_<NAME>_<DIAL>", "int", None, "characters.<name>", "live",
       "Per-character dial override, e.g. NHJ_JAN_LEVEL / NHJ_KARREN_CHAOS (pattern, not a literal name)"),
    _e("NHJ_MODE", "str", None, "modes", "live", "Active mode name override (otherwise live state)"),
    _e("NHJ_CENSOR", "enum", "quack", "modes.<mode>.censor", "live", "Profanity censor style: quack/beep/honk/off"),
    _e("NHJ_ORCHESTRATION", "enum", None, "orchestration.mode", "live", "Multi-session policy: quiet-while-busy/always-speak"),
    _e("NHJ_SCENES", "bool", "1", None, "live", "Scene vignettes on/off"),
    _e("NHJ_SCENES_PROB", "float", "0.08", None, "live", "Probability a scene vignette plays per event"),
    _e("NHJ_OPENMIC_PROB", "float", "0.05", None, "live", "Probability of the open-mic scene"),
    # --- dynamic lines (boganify LLM) -----------------------------------------
    _e("NHJ_DYNAMIC", "bool", "", None, "live", "Dynamic in-character lines on (set to 1)"),
    _e("NHJ_DYNAMIC_BASE_URL", "url", "http://localhost:9991/v1", None, "live", "OpenAI-compatible endpoint for dynamic lines"),
    _e("NHJ_DYNAMIC_MODEL", "str", "local", None, "live", "Model name sent to the dynamic-lines endpoint"),
    _e("NHJ_DYNAMIC_API_KEY", "str", "not-needed", None, "live", "Bearer token for the dynamic-lines endpoint"),
    _e("NHJ_DYNAMIC_TEMP", "float", "0.85", None, "live", "Sampling temperature for dynamic lines"),
    _e("NHJ_DYNAMIC_TOP_P", "float", "0.95", None, "live", "Top-p for dynamic lines"),
    _e("NHJ_DYNAMIC_TIMEOUT", "float", "8", None, "live", "Deadline for one dynamic-lines call (seconds)"),
    # --- TTS / audio ------------------------------------------------------------
    _e("TTS_ENGINE", "enum", "qwen", None, "live", "Live synthesis backend: qwen/audiocpp/os/piper/none"),
    _e("NHJ_TTS_URL", "url", "http://localhost:9992", None, "live", "NHJ Qwen TTS server base URL"),
    _e("NHJ_TTS_MODEL_TIER", "enum", None, None, "live", "Qwen model tier override (fast/full; else per-line default)"),
    _e("NHJ_AUDIOCPP_URL", "url", None, "audiocpp.url", "live", "audio.cpp (Breeze-TTS) server base URL"),
    _e("NHJ_AUDIOCPP_VOICES", "path", None, "audiocpp.voices_dir", "live", "Server voices dir holding <name>.txt transcripts"),
    _e("NHJ_OS_TTS_VOICE", "str", None, None, "live", "CPU tier: espeak voice / say -v name (TTS_ENGINE=os)"),
    _e("NHJ_PIPER_MODEL", "path", None, None, "live", "CPU tier: Piper .onnx voice file (TTS_ENGINE=piper)"),
    _e("NHJ_PIPER_SPEAKER", "int", None, None, "live", "CPU tier: speaker id for multi-speaker Piper models"),
    _e("NHJ_PHONE_FX", "bool", "", None, "live", "Telephone bandpass/compression colour on voice clips"),
    _e("NHJ_VOICE_TARGET_DBFS", "float", "-19.5", None, "live", "Peak target for voice loudness normalisation (dBFS)"),
    _e("NHJ_OUTPUT_DEVICE", "str", "", "playback.output_device", "live", "Pinned audio output device (name or index)"),
    _e("NHJ_FOLLOW_DEVICE", "bool", "1", None, "startup", "Mixer follows the macOS default output device (0 disables)"),
    _e("NHJ_FOLLOW_DEVICE_POLL", "float", "4", None, "startup", "Default-device watcher interval (seconds)"),
    # --- hold music / mixer ------------------------------------------------------
    _e("NHJ_MUZAK_DIR", "path", "", None, "live", "Extra/replacement music directory for the hold bed"),
    _e("NHJ_MUZAK_STATE", "path", None, None, "import", "Override the music.json state file location"),
    _e("NHJ_MUZAK_VOLUME", "float", "0.5", None, "live", "Music bed level (0-1)"),
    _e("NHJ_MUSIC_MODE", "enum", None, "music.mode", "startup", "Hold-music source: recorded (default) or generative (stem pack + director)"),
    _e("NHJ_MUZAK_DUCK_GAIN", "float", "0.22", None, "live", "Music level while ducking under speech (0-1)"),
    _e("NHJ_MUZAK_RETURN", "bool", "1", None, "live", "Play the reconnect ('you still there?') return clip"),
    _e("NHJ_MUZAK_INTRO", "bool", "1", None, "live", "Play the hold intro sting"),
    _e("NHJ_MUZAK_INTRO_VOLUME", "float", "0.9", None, "live", "Hold intro sting level"),
    _e("NHJ_MUZAK_TRANSFER", "bool", None, None, "live", "Transfer-button beep on hold transitions"),
    _e("NHJ_MUZAK_TRANSFER_VOLUME", "float", "0.85", None, "live", "Transfer beep level"),
    _e("NHJ_AMBIENT_DUCK", "float", "0.35", None, "live", "Duck depth for ambient (non-music) underlay"),
    _e("NHJ_COMP", "bool", "0", None, "live", "Mixer bus compressor on/off"),
    _e("NHJ_COMP_DRIVE", "float", "1.6", None, "live", "Mixer bus compressor drive"),
    _e("NHJ_HANDSET_VOLUME", "float", "0.9", None, "live", "FX handset sting level"),
    _e("NHJ_FAILED_TRANSFER_PROB", "float", "0.05", None, "live", "Probability of a failed-transfer sting"),
    _e("NHJ_FAILED_TRANSFER_VOLUME", "float", "0.9", None, "live", "Failed-transfer sting level"),
    _e("NHJ_FAILED_TRANSFER", "bool", "1", None, "live", "Failed-transfer gag sting on/off"),
    _e("NHJ_HANDSET", "bool", "1", None, "live", "Handset pickup/putdown clunks on/off"),
    _e("NHJ_PUTDOWN", "bool", "1", None, "live", "Handset putdown clunk on/off"),
    _e("NHJ_RAVE_DISPLAY_INTERVAL", "float", "8", None, "live", "Seconds between rave display party-tile cycles"),
    _e("NHJ_MODE_AMBIENT_VOLUME", "float", "0.5", None, "live", "Mode ambient clip level"),
    _e("NHJ_LONGHOLD", "bool", "1", None, "live", "Long-hold music floor ramp on/off"),
    _e("NHJ_LONGHOLD_AFTER", "float", "5", None, "live", "Seconds of hold before the long-hold ramp starts"),
    _e("NHJ_LONGHOLD_RAMP", "float", "4", None, "live", "Long-hold duck ramp duration (seconds)"),
    _e("NHJ_LONGHOLD_DUCK", "float", "0.15", None, "live", "Music floor reached at the end of the long-hold ramp"),
    # --- servers -----------------------------------------------------------------
    _e("NHJ_SERVERS", "enum", None, None, "startup", "TTS/LLM supervision: on-demand (spawn+reap) vs always-on service"),
    _e("NHJ_SERVER_IDLE", "int", "600", None, "startup", "Idle seconds before the on-demand supervisor reaps a server"),
    _e("NHJ_TRANSPORT", "enum", "stdio", None, "startup", "MCP server transport: stdio/streamable-http"),
    _e("NHJ_HOST", "str", "127.0.0.1", None, "startup", "MCP HTTP bind address (LAN exposure is opt-in)"),
    _e("NHJ_PORT", "int", "8765", None, "startup", "MCP HTTP port"),
    _e("NHJ_SHARED_SECRET", "str", "", None, "live",
       "Shared secret for the TTS server and the network MCP. Blank means no check"),
    _e("NHJ_TTS_HOST", "str", "127.0.0.1", None, "startup", "TTS server bind address"),
    _e("NHJ_TTS_PORT", "int", "9992", None, "startup", "TTS server port"),
    _e("NHJ_TTS_MODEL", "str", None, None, "startup", "TTS model or cache directory the server loads"),
    _e("NHJ_VOICES_DIR", "path", "", None, "startup", "Extra voices directory the TTS server merges at startup"),
    _e("HF_HOME", "path", None, None, "startup", "HuggingFace home (relocated into the NHJ cache if unset)", "system"),
    _e("HF_HUB_CACHE", "path", None, None, "startup", "HuggingFace hub cache directory", "system"),
    _e("HUGGINGFACE_HUB_CACHE", "path", None, None, "startup", "HuggingFace hub cache directory (legacy name)", "system"),
    # --- device adapters (provider env) -----------------------------------------
    _e("NHJ_HAPTIC_ENABLED", "bool", None, "haptic.enabled", "live", "Haptic mouse adapter on/off"),
    _e("NHJ_HAPTIC_DEVICE_INDEX", "int", None, "haptic.device_index", "live", "Which HID++ receiver slot the haptic mouse is on"),
    _e("ULANZI_TRANSPORT", "enum", None, "awtrix.transport", "live", "AWTRIX transport: http/mqtt", "provider"),
    _e("ULANZI_MQTT_HOST", "str", None, "awtrix.mqtt_host", "live", "AWTRIX MQTT broker host", "provider"),
    _e("ULANZI_MQTT_PORT", "int", None, "awtrix.mqtt_port", "live", "AWTRIX MQTT broker port", "provider"),
    _e("ULANZI_MQTT_USER", "str", None, "awtrix.mqtt_user", "live", "AWTRIX MQTT username", "provider"),
    _e("ULANZI_MQTT_PASS", "str", None, "awtrix.mqtt_pass", "live", "AWTRIX MQTT password", "provider"),
    _e("ULANZI_TEXT_STYLE", "enum", None, "awtrix.text_style", "live", "AWTRIX headline style: ocker/plain", "provider"),
    _e("ULANZI_BRIGHTNESS", "int", None, "awtrix.brightness", "live", "AWTRIX panel brightness 0-255", "provider"),
    _e("LAMETRIC_DEVICE_IP", "str", "", None, "live", "LaMetric clock IP address", "provider"),
    _e("LAMETRIC_API_KEY", "str", "", None, "live", "LaMetric local API key", "provider"),
    _e("DIVOOM_DEVICE_IP", "str", "", None, "live", "Divoom device IP address", "provider"),
    _e("DIVOOM_DEVICE_TYPE", "str", "TimesGate", None, "live", "Divoom device family", "provider"),
    _e("DIVOOM_INTENTS", "list", "", None, "live", "Comma-separated intents the Divoom display reacts to", "provider"),
    _e("ESP32_BELL_URL", "url", "", None, "live", "ESP32 bell HTTP endpoint base URL", "provider"),
    _e("ESP32_BELL_INTENTS", "list", "", None, "live", "Comma-separated intents that ring the ESP32 bell", "provider"),
]

REGISTRY: dict[str, EnvVar] = {e.name: e for e in _ENTRIES}


def lookup(name: str) -> Optional[EnvVar]:
    return REGISTRY.get(name)


def render_docs_table() -> str:
    """The markdown block embedded in docs/configuration.md (kept in sync by test)."""
    head = ("| Variable | Type | Default | Overrides (YAML) | Read | Purpose |\n"
            "|---|---|---|---|---|---|")
    rows = []
    for e in sorted(REGISTRY.values(), key=lambda x: (x.scope != "nhj", x.name)):
        default = f"`{e.default}`" if e.default else "—"
        yaml = f"`{e.yaml}`" if e.yaml else "—"
        rows.append(f"| `{e.name}` | {e.type} | {default} | {yaml} | {e.timing} | {e.description} |")
    return head + "\n" + "\n".join(rows)


__all__ = ["EnvVar", "REGISTRY", "lookup", "render_docs_table"]
