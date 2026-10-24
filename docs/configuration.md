# Configuration & requirements

*← [Not-Happy-Jan docs](index.md)*

Edit `.env` — all settings are documented there. For the full audio control surface (ducking, modes, hold sounds, scenes, filters) see **[Audio Standard & Pipeline](AUDIO-STANDARD.md)**; for the dynamic-LLM settings see **[Dynamic voices](dynamic-voices.md)**.

## Config file (`config.yaml`) overrides

NHJ ships its defaults in a read-only `default.yaml` (bundled inside the wheel; `config/default.yaml` in a source checkout). To change routing, modes, adapter order, intents, or device settings, create your own **`config.yaml`** — you never edit the shipped defaults.

**Where it goes** (loaded lowest → highest precedence, later wins):

1. the shipped `default.yaml`
2. `config.yaml` **beside** `default.yaml` — source-checkout convenience (`config/config.yaml`)
3. **`~/.config/nhj/config.yaml`** — the supported location for installed/packaged use (`$XDG_CONFIG_HOME/nhj`)
4. **`$NHJ_CONFIG_FILE`** — an explicit path, highest precedence

**Merge semantics:** nested **mappings merge key-by-key** (so you override just the keys you care about — everything else keeps its default); **lists and scalars replace** wholesale.

```yaml
# ~/.config/nhj/config.yaml — override only what you need
routing:
  rung_by_intent:
    warn: karren        # send warnings to Karren (other intents keep their defaults)
adapters: [haptic, audio]   # a list → replaces the default adapter order entirely
```

**Precedence vs env vars:** for settings that also read an environment variable — `NHJ_DEFAULT_CHARACTER`, `NHJ_ERROR_CHARACTER`, `NHJ_<NAME>_LEVEL` / `_CHAOS`, and the audio/device `NHJ_*` vars — the **env var wins** over `config.yaml`. Everything else comes from the merged YAML.

A **missing** `config.yaml` is normal. A `config.yaml` that **exists but is malformed** (invalid YAML, or a top level that isn't a mapping) **fails loudly** with the offending path — it is never silently ignored. Keys and values are also checked against the config schema (`src/nhj/config_schema.py`): an unknown key (a typo like `adapterz:`), a bad type/range/enum is an error naming the source file, while partial overrides and your own character/mode names stay valid. Run **`nhj config check`** to load and validate the merged config without starting anything. Fix or remove the file.



### All environment variables (generated)

Generated from `src/nhj/env_registry.py` — edit the registry, not this table (a test fails when they drift). “Read” is when a change takes effect: `live` = next call/new process, `startup` = restart that server, `import` = new process reading paths/config.

<!-- env-registry:start -->
| Variable | Type | Default | Overrides (YAML) | Read | Purpose |
|---|---|---|---|---|---|
| `NHJ_<NAME>_<DIAL>` | int | — | `characters.<name>` | live | Per-character dial override, e.g. NHJ_JAN_LEVEL / NHJ_KARREN_CHAOS (pattern, not a literal name) |
| `NHJ_AMBIENT_DUCK` | float | `0.35` | — | live | Duck depth for ambient (non-music) underlay |
| `NHJ_AUDIOCPP_URL` | url | — | `audiocpp.url` | live | audio.cpp (Breeze-TTS) server base URL |
| `NHJ_AUDIOCPP_VOICES` | path | — | `audiocpp.voices_dir` | live | Server voices dir holding <name>.txt transcripts |
| `NHJ_BIN_DIR` | path | — | — | live | Directory the `nhj` launcher is written to on install |
| `NHJ_CENSOR` | enum | `quack` | `modes.<mode>.censor` | live | Profanity censor style: quack/beep/honk/off |
| `NHJ_COMP` | bool | `0` | — | live | Mixer bus compressor on/off |
| `NHJ_COMP_DRIVE` | float | `1.6` | — | live | Mixer bus compressor drive |
| `NHJ_CONFIG_FILE` | path | — | — | import | Use exactly this config file instead of the search order |
| `NHJ_DEBUG` | bool | — | — | live | Enable `[nhj]` debug lines on stderr (any set value) |
| `NHJ_DEFAULT_CHARACTER` | str | — | `routing.default` | live | Fallback character when routing gives none |
| `NHJ_DYNAMIC` | bool | — | — | live | Dynamic in-character lines on (set to 1) |
| `NHJ_DYNAMIC_API_KEY` | str | `not-needed` | — | live | Bearer token for the dynamic-lines endpoint |
| `NHJ_DYNAMIC_BASE_URL` | url | `http://localhost:9991/v1` | — | live | OpenAI-compatible endpoint for dynamic lines |
| `NHJ_DYNAMIC_MODEL` | str | `local` | — | live | Model name sent to the dynamic-lines endpoint |
| `NHJ_DYNAMIC_TEMP` | float | `0.85` | — | live | Sampling temperature for dynamic lines |
| `NHJ_DYNAMIC_TIMEOUT` | float | `8` | — | live | Deadline for one dynamic-lines call (seconds) |
| `NHJ_DYNAMIC_TOP_P` | float | `0.95` | — | live | Top-p for dynamic lines |
| `NHJ_ECHO_CONTEXT` | str | — | — | live | Verbatim extra context line hooks add when echoing a marker |
| `NHJ_ERROR_CHARACTER` | str | — | `routing.on_err` | live | Character forced for err intents |
| `NHJ_FAILED_TRANSFER` | bool | `1` | — | live | Failed-transfer gag sting on/off |
| `NHJ_FAILED_TRANSFER_PROB` | float | `0.05` | — | live | Probability of a failed-transfer sting |
| `NHJ_FAILED_TRANSFER_VOLUME` | float | `0.9` | — | live | Failed-transfer sting level |
| `NHJ_FOLLOW_DEVICE` | bool | `1` | — | startup | Mixer follows the macOS default output device (0 disables) |
| `NHJ_FOLLOW_DEVICE_POLL` | float | `4` | — | startup | Default-device watcher interval (seconds) |
| `NHJ_HANDSET` | bool | `1` | — | live | Handset pickup/putdown clunks on/off |
| `NHJ_HANDSET_VOLUME` | float | `0.9` | — | live | FX handset sting level |
| `NHJ_HAPTIC_DEVICE_INDEX` | int | — | `haptic.device_index` | live | Which HID++ receiver slot the haptic mouse is on |
| `NHJ_HAPTIC_ENABLED` | bool | — | `haptic.enabled` | live | Haptic mouse adapter on/off |
| `NHJ_HOOK_MIN` | float | `0.4` | — | live | Minimum seconds before the Stop hook accepts a flushed message |
| `NHJ_HOOK_POLL` | float | `0.12` | — | live | Transcript poll interval while waiting for the flush |
| `NHJ_HOOK_WAIT` | float | `3.0` | — | live | Seconds the Stop hook waits for the final message to flush |
| `NHJ_HOST` | str | `127.0.0.1` | — | startup | MCP HTTP bind address (LAN exposure is opt-in) |
| `NHJ_LONGHOLD` | bool | `1` | — | live | Long-hold music floor ramp on/off |
| `NHJ_LONGHOLD_AFTER` | float | `5` | — | live | Seconds of hold before the long-hold ramp starts |
| `NHJ_LONGHOLD_DUCK` | float | `0.15` | — | live | Music floor reached at the end of the long-hold ramp |
| `NHJ_LONGHOLD_RAMP` | float | `4` | — | live | Long-hold duck ramp duration (seconds) |
| `NHJ_MEDIA_BASE` | url | — | — | live | Base URL media downloads are fetched from |
| `NHJ_MODE` | str | — | `modes` | live | Active mode name override (otherwise live state) |
| `NHJ_MODE_AMBIENT_VOLUME` | float | `0.5` | — | live | Mode ambient clip level |
| `NHJ_MUSIC_MODE` | enum | — | `music.mode` | startup | Hold-music source: recorded (default) or generative (stem pack + director) |
| `NHJ_MUZAK_DIR` | path | — | — | live | Extra/replacement music directory for the hold bed |
| `NHJ_MUZAK_DUCK_GAIN` | float | `0.22` | — | live | Music level while ducking under speech (0-1) |
| `NHJ_MUZAK_INTRO` | bool | `1` | — | live | Play the hold intro sting |
| `NHJ_MUZAK_INTRO_VOLUME` | float | `0.9` | — | live | Hold intro sting level |
| `NHJ_MUZAK_RETURN` | bool | `1` | — | live | Play the reconnect ('you still there?') return clip |
| `NHJ_MUZAK_STATE` | path | — | — | import | Override the music.json state file location |
| `NHJ_MUZAK_TRANSFER` | bool | — | — | live | Transfer-button beep on hold transitions |
| `NHJ_MUZAK_TRANSFER_VOLUME` | float | `0.85` | — | live | Transfer beep level |
| `NHJ_MUZAK_VOLUME` | float | `0.5` | — | live | Music bed level (0-1) |
| `NHJ_OPENMIC_PROB` | float | `0.05` | — | live | Probability of the open-mic scene |
| `NHJ_ORCHESTRATION` | enum | — | `orchestration.mode` | live | Multi-session policy: quiet-while-busy/always-speak |
| `NHJ_OUTPUT_DEVICE` | str | — | `playback.output_device` | live | Pinned audio output device (name or index) |
| `NHJ_PHONE_FX` | bool | — | — | live | Telephone bandpass/compression colour on voice clips |
| `NHJ_PORT` | int | `8765` | — | startup | MCP HTTP port |
| `NHJ_PUTDOWN` | bool | `1` | — | live | Handset putdown clunk on/off |
| `NHJ_RAVE_DISPLAY_INTERVAL` | float | `8` | — | live | Seconds between rave display party-tile cycles |
| `NHJ_REMOTE_TIMEOUT` | float | `5` | — | live | Deadline for one remote hook dispatch (seconds) |
| `NHJ_REMOTE_URL` | url | — | — | live | LAN MCP endpoint hooks dispatch to instead of the local queue |
| `NHJ_SCENES` | bool | `1` | — | live | Scene vignettes on/off |
| `NHJ_SCENES_PROB` | float | `0.08` | — | live | Probability a scene vignette plays per event |
| `NHJ_SERVERS` | enum | — | — | startup | TTS/LLM supervision: on-demand (spawn+reap) vs always-on service |
| `NHJ_SERVER_IDLE` | int | `600` | — | startup | Idle seconds before the on-demand supervisor reaps a server |
| `NHJ_STATE_FILE` | path | — | — | import | Override the live state.json location |
| `NHJ_TRANSPORT` | enum | `stdio` | — | startup | MCP server transport: stdio/streamable-http |
| `NHJ_TTS_HOST` | str | `127.0.0.1` | — | startup | TTS server bind address |
| `NHJ_TTS_MODEL` | str | — | — | startup | TTS model or cache directory the server loads |
| `NHJ_TTS_MODEL_TIER` | enum | — | — | live | Qwen model tier override (fast/full; else per-line default) |
| `NHJ_TTS_PORT` | int | `9992` | — | startup | TTS server port |
| `NHJ_TTS_URL` | url | `http://localhost:9992` | — | live | NHJ Qwen TTS server base URL |
| `NHJ_VOICES_DIR` | path | — | — | startup | Extra voices directory the TTS server merges at startup |
| `NHJ_VOICE_TARGET_DBFS` | float | `-19.5` | — | live | Peak target for voice loudness normalisation (dBFS) |
| `TTS_ENGINE` | enum | `qwen` | — | live | Live synthesis backend: qwen/audiocpp/os/piper/none |
| `DIVOOM_DEVICE_IP` | str | — | — | live | Divoom device IP address |
| `DIVOOM_DEVICE_TYPE` | str | `TimesGate` | — | live | Divoom device family |
| `DIVOOM_INTENTS` | list | — | — | live | Comma-separated intents the Divoom display reacts to |
| `ESP32_BELL_INTENTS` | list | — | — | live | Comma-separated intents that ring the ESP32 bell |
| `ESP32_BELL_URL` | url | — | — | live | ESP32 bell HTTP endpoint base URL |
| `HF_HOME` | path | — | — | startup | HuggingFace home (relocated into the NHJ cache if unset) |
| `HF_HUB_CACHE` | path | — | — | startup | HuggingFace hub cache directory |
| `HUGGINGFACE_HUB_CACHE` | path | — | — | startup | HuggingFace hub cache directory (legacy name) |
| `LAMETRIC_API_KEY` | str | — | — | live | LaMetric local API key |
| `LAMETRIC_DEVICE_IP` | str | — | — | live | LaMetric clock IP address |
| `ULANZI_BRIGHTNESS` | int | — | `awtrix.brightness` | live | AWTRIX panel brightness 0-255 |
| `ULANZI_MQTT_HOST` | str | — | `awtrix.mqtt_host` | live | AWTRIX MQTT broker host |
| `ULANZI_MQTT_PASS` | str | — | `awtrix.mqtt_pass` | live | AWTRIX MQTT password |
| `ULANZI_MQTT_PORT` | int | — | `awtrix.mqtt_port` | live | AWTRIX MQTT broker port |
| `ULANZI_MQTT_USER` | str | — | `awtrix.mqtt_user` | live | AWTRIX MQTT username |
| `ULANZI_TEXT_STYLE` | enum | — | `awtrix.text_style` | live | AWTRIX headline style: ocker/plain |
| `ULANZI_TRANSPORT` | enum | — | `awtrix.transport` | live | AWTRIX transport: http/mqtt |
| `USER` | str | — | — | startup | Login name (queue dir suffix, session labels) |
| `USERNAME` | str | — | — | startup | Login name fallback on Windows-style environments |
| `XDG_CACHE_HOME` | path | — | — | startup | XDG cache base for TTS model cache |
| `XDG_CONFIG_HOME` | path | — | — | startup | XDG config base for the user config dir |
| `XDG_DATA_HOME` | path | — | — | startup | XDG data base for installed media/data dir |
| `XDG_STATE_HOME` | path | — | — | startup | XDG state base for runtime state |
<!-- env-registry:end -->
## `.env` essentials

```bash
# Choose TTS backend
TTS_ENGINE=qwen          # local Qwen3-TTS (Apple Silicon, default)
# TTS_ENGINE=audiocpp    # audio.cpp / Breeze-TTS zero-shot server (Linux/CUDA); see linux.md
# NHJ_AUDIOCPP_URL=http://127.0.0.1:8092
# NHJ_OUTPUT_DEVICE=analog-stereo   # mixer output (index or name part); `nhj audio-devices`
# NHJ_ORCHESTRATION=quiet-while-busy  # or always-speak; see orchestration.md
# TTS_ENGINE=none        # run without voice

# Choose default character
NHJ_DEFAULT_CHARACTER=jan
# NHJ_ERROR_CHARACTER=karren  # Karren auto-routes on errors by default

# Enable devices
LAMETRIC_DEVICE_IP=192.168.1.xx
LAMETRIC_API_KEY=your-key

DIVOOM_DEVICE_IP=192.168.1.xx
DIVOOM_DEVICE_TYPE=TimesGate

ESP32_BELL_URL=http://192.168.1.xx/bell
ESP32_BELL_INTENTS=err,attn

# AWTRIX 3 / Ulanzi pixel displays — easy path (no broker): full setup in docs
ULANZI_TRANSPORT=http
ULANZI_HTTP_HOSTS=awtrix_96895c.local       # mDNS name; one display or many
# Extensible path (many displays / Home Assistant / cross-VLAN): use MQTT instead —
# ULANZI_TRANSPORT=mqtt ; ULANZI_MQTT_HOST=mqtt.lan ; ULANZI_MQTT_PREFIXES=awtrix1,awtrix2
# (or configure the `awtrix:` block in ../config/default.yaml — brightness, text_style, dynamic_text)
```

> **AWTRIX displays:** a ~$60 Ulanzi TC001, **running in ~10 minutes** — flash it,
> join Wi-Fi, point NHJ at its `awtrix_<chipid>.local` name over HTTP. Each `[vibes:…]`
> then shows a random icon from the intent's pool, a per-intent treatment (colour /
> effect / blink, `attn` holds), and an in-character headline (LLM-compressed when
> `NHJ_DYNAMIC` is on, else a curated ocker/plain phrase). Add an MQTT broker later
> for many displays, Home Assistant, or cross-network reach.
> Full guide: **[awtrix-display-setup.md](awtrix-display-setup.md)**.

## Inference muzak

Muzak for while the agent's *thinking* — Jan's idea of putting you "on hold." Off by default. Turn it on and it plays while the agent works, pausing the *instant* a character speaks, then resuming when you reply:

```bash
nhj muzak on        # enable the call-centre experience
nhj muzak off       # back to silence
nhj muzak status    # playing / paused / stopped
```

It's driven by Claude Code's lifecycle hooks: muzak starts/resumes on `UserPromptSubmit` (inference begins), pauses on `Stop` (a character picks up), and stops on `SessionEnd`. Pause/resume is exact — the track freezes and continues from the same sample.

The bundled 4+ hours of hold music (and the SFX / voice clips / ambient beds) install via `nhj setup-media`, which pulls the `audio/` and `voices/` bundles from the [`media`](https://github.com/guruswami-ai/not-happy-jan/releases/tag/media) GitHub release — re-run it anytime to refresh, or set `NHJ_MEDIA_BASE` to host the bundles elsewhere.

> The release assets are public, so `nhj setup-media` downloads them directly — no `gh` or authentication needed. (If you fork into a private repo, or otherwise gate the release, `setup-media` automatically falls back to `gh release download` when the plain URL returns 401/403/404.)

Drop your own tracks (`.m4a`, `.mp3`, `.wav`…) into `audio/music/`, or point `NHJ_MUZAK_DIR` at a folder; they shuffle-cycle. Set the level with `NHJ_MUZAK_VOLUME` (0–1, default `0.5`).

## TTS backends

Measured on an Apple M2 Ultra, mlx-audio 0.4.3 (warm daemon, `ref_text` supplied):

| Backend | Setup | RAM | Disk | Quality | Speed (warm) |
|---|---|---|---|---|---|
| Qwen3-TTS 0.6B-8bit (local, default) | Apple Silicon | **~2.2 GB** | ~1.9 GB | Good | ~0.45× RTF (≈2× realtime) |
| Qwen3-TTS 1.7B-4bit (local) | Apple Silicon | **~2.5 GB** | ~2.2 GB | Better | ~0.50× RTF |

The voice is fully local and self-contained — no cloud TTS, no API keys.

A typical notification (2–4 s of speech) synthesises in **~1–2 s** on a warm model. The first
call after start-up is slower (MLX graph compile) — which is why the TTS server is a **persistent
daemon** that keeps the model loaded. Common phrases are pre-rendered (`nhj build-bank`) and play
instantly; only novel messages hit the model.

By default on macOS, model artifacts live under `~/Library/Caches/not-happy-jan/`:

| Model | Default location | Runtime caller |
|---|---|---|
| Qwen3-TTS voice model | `~/Library/Caches/not-happy-jan/tts-models/` (`HF_HOME` for the TTS server; Hugging Face stores snapshots below it) | `nhj install-tts` loads the `com.guruswami.nhj-tts` LaunchAgent, which runs `python -m nhj.tts_server` on `127.0.0.1:9992`; audio events call `NHJ_TTS_URL` (`/tts/generate`). |
| `ocker-bogan-nano` dynamic LLM | `~/Library/Caches/not-happy-jan/ocker-bogan-nano/ocker-bogan-nano-Q4_K_M.gguf` | `nhj install-model` loads the `com.guruswami.nhj-ocker-bogan-nano` LaunchAgent, which runs `llama-server -m <that .gguf>` on `127.0.0.1:9991`; dynamic voices call `NHJ_DYNAMIC_BASE_URL` (`/v1/chat/completions`). |

Set `NHJ_CACHE_DIR` to move both NHJ-managed model caches. Set `HF_HOME`/`HF_HUB_CACHE` if you
want the TTS server to use an existing Hugging Face cache, or `NHJ_TTS_MODEL` to point Qwen3-TTS
at a specific repo id or local model path. Set `NHJ_DYNAMIC_*` to use a different LLM endpoint.

> **Note:** always run the local path via the warm `nhj start-server` daemon and supply each
> voice's `ref.txt`. Calling Qwen3-TTS without a reference transcript makes it load
> whisper-large-v3-turbo to transcribe the clip — an extra ~1.5 GB of RAM and latency. NHJ's
> server path avoids this by using `voices/<name>/ref.txt`.

## Where NHJ stores things (macOS)

Everything lives under your home folder — no `sudo`, and `nhj uninstall` removes all of it.

![Five macOS locations. ~/Library/Application Support/not-happy-jan holds config (.env, config.yaml), state.json, and the Python runtime. ~/Library/Caches/not-happy-jan holds the downloaded models (Qwen3-TTS, ocker-bogan-nano), the clip cache, and media. ~/Library/Logs/not-happy-jan holds the worker, tts, llm, mcp, and hook logs. ~/.claude holds NHJ's Claude Code hooks, MCP server, skill, and CLAUDE.md markers. ~/Library/LaunchAgents holds the nhj-tts, nhj-llm, and nhj-mcp services on the full profile.](assets/diagrams/storage-map.svg){ width="620" .center }

Override any root with `NHJ_CONFIG_DIR` / `NHJ_CACHE_DIR` / `NHJ_STATE_DIR` / `NHJ_LOG_DIR` / `NHJ_DATA_DIR`.

## Requirements & minimum specs

For the three ways to run NHJ — silent, pre-recorded samples, or the full live experience — and the RAM/disk each needs, see **[Minimum specs](minimum-specs.md)**.

| Tier | What you need | Notes |
|---|---|---|
| **Required** (core) | macOS **Apple Silicon (M1+)** *or* Linux (+ NVIDIA GPU for live voices) · Python 3.10+ · ~8 GB RAM (16 GB recommended) · ~5 GB disk | The Qwen3-TTS voice needs ~2.2 GB resident (Mac); Linux uses an audio.cpp server ([Linux guide](linux.md)). `TTS_ENGINE=none` runs it text-only (no voice). |
| **Recommended** (everything local) | **+ ocker-bogan-nano** dynamic voice brain | ~940 MB download, +~0.5 GB RAM. Everything stays on-device — the complete ocker-bogan experience, no cloud. |
| **Optional** | Hardware feedback — LaMetric · Ulanzi/AWTRIX · Divoom · ESP32 bell · haptic mouse | Visual/physical alerts beyond audio. Deferred/experimental in v1; set device IPs in `.env`. |

Local TTS is the **only** heavy component — everything else (hook, MCP, queue, mixer) is lightweight Python. NHJ is **self-contained** (macOS and Linux) — nothing phones home.

| You want… | Need | RAM | Disk |
|---|---|---|---|
| **Local Qwen voice** (offline, default) | **Apple Silicon Mac (M1+)** | 8 GB works · **16 GB recommended** | ~5 GB free |
| **Breeze zero-shot voice** (offline) | **Linux + NVIDIA GPU** | 8 GB+ | ~5 GB free |
| **Silent** (text/markers only) | Any Mac or Linux box | minimal | minimal |

Set `TTS_ENGINE=none` in `.env` to run without voice. Everything is local — no cloud, no API keys.
