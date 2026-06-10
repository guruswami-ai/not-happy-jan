# Not-Happy-Jan — Developer Guide

*The code-level companion to [ARCHITECTURE.md](ARCHITECTURE.md). Where that document
explains the **why** (the AI-pipeline design and its tradeoffs), this one is the **how**:
which module does what, the contracts they share, and how to extend the system.*

*← back to the [README](../README.md)*

---

## 1. The path of one vibe

Everything in NHJ exists to get a single event — *"the agent's turn just ended, here's the
outcome"* — from the agent to your senses. Follow that one event and the codebase falls into
place:

```
agent emits  [Karren:err|Build broke]   in its final message
        │
   Stop hook  (hook.py)                  ── scans the transcript tail for markers
        │                                   parses → event.from_inline() → VibeEvent
        ▼
   vibe queue  (queue_manager.py)         ── atomic .vibe file on disk; producer returns instantly
        │
   worker      (worker.py)                ── claims the item, resolves the character
        │                                   fans it out through the adapter chain
        ▼
   adapters    (adapters/*.py)            ── haptic → lametric → ulanzi → esp32 → audio
        │                                   each isolated; one failure never starves the rest
        ▼
   you         (mouse buzz · pixel tile · Jan's voice over the ducked hold music)
```

Two side-channels run alongside this main path:

- **Muzak** (`inference_muzak.py` + `duck_player.py`) — the `UserPromptSubmit` hook marks the
  session *busy* (music starts); `Stop`/`SessionEnd` mark it *idle* (music pauses). Independent
  of whether the turn emitted a marker.
- **Secret guard** (`prompt_hook.py` + `secret_scan.py`) — scans the *prompt* for leaked
  credentials before the turn runs.

---

## 2. Module map

`src/nhj/` — every module and its single responsibility.

### Core event path

| Module | Responsibility |
|---|---|
| `event.py` | **The canonical contract.** `VibeEvent` dataclass + one `validate()` used by *every* entry path (MCP tool, hooks, queue, worker). Field set, ranges, enums. |
| `hook.py` | Claude Code **`Stop` hook**. Polls the transcript tail until the final assistant message settles, scans for `[who:intent\|…]` markers, fires each (locally or to a remote MCP). Fire-once ledger per session. |
| `prompt_hook.py` | Claude Code **`UserPromptSubmit` hook**. Marks the session busy (muzak) and runs the secret guard. |
| `queue_manager.py` | **File-backed queue + worker lifecycle.** Atomic produce (`add`), atomic claim (`claim_next`/`ack`/`nack`), crashed-worker recovery, poison quarantine, single-worker election under `flock`. |
| `worker.py` | **The consumer.** Drains the queue, resolves the character for each task, dispatches through the adapter chain, honours the live `mute` flag, self-exits after 30 s idle. |
| `mcp_server.py` | **MCP surface.** Exposes the single `nhj_vibe` tool (validate → enqueue → return). stdio for local sessions, sse/streamable-http for a LAN daemon. |

### Personality & content

| Module | Responsibility |
|---|---|
| `characters.py` | The **Jan → Bazza → Karren ladder**. Loads `character.yaml`, picks a phrase band from the intensity dial, builds a `Delivery` (text + TTS params). Intent→rung routing + explicit-character override. |
| `boganify.py` | Optional **dynamic line generation** — rephrase the status in-character via any OpenAI-compatible LLM (`NHJ_DYNAMIC`). Any failure returns `None` → caller falls back to the static bank. |
| `censor.py` | Swear bleeping (`quack`/`beep`/`honk`/`off`). Applied to every line at the end of `Character.delivery()`. |
| `garble.py` | Jan's *incompetence* dial — umms, mishears, trailing-off, and peak-chaos "oblivious" lines. |
| `scenes.py` | Easter-egg hold vignettes (pub, party, durry) with ambient beds; fires rarely on a go-on-hold transition. |

### Audio

| Module | Responsibility |
|---|---|
| `adapters/audio.py` | TTS adapter. **Three-tier fidelity ladder:** pre-rendered bank → per-message cache → live Qwen3-TTS synth. Feeds the mixer's voice bus. |
| `duck_player.py` | The **single streaming mixer** (PyAV + sounddevice). One `OutputStream`, named buses (`music`/`fx`/`voice`/`ambient`), automatic sidechain ducking. The *only* audio path — nothing spawns `afplay`. |
| `inference_muzak.py` | **Muzak controller.** Busy-set of session IDs under a locked read-modify-write; plays while any session is busy, SIGSTOP-pauses when all idle. TTL-evicts crashed sessions. |
| `tts_server.py` | Warm **Qwen3-TTS daemon** (MLX). Loads the model once, serves `POST /tts/generate`. |
| `llm_server.py` | Thin supervisor that runs `llama-server` under the process title `NHJ LLM`. |
| `servers.py` / `serverctl.py` | Model-server lifecycle: `persistent` (KeepAlive LaunchAgents) vs `on-demand` (start on first vibe, idle-unload to free ~3 GB RAM). |

### Adapters (device channels)

| Module | Channel |
|---|---|
| `adapters/base.py` | The `NotificationAdapter` ABC (`fire()` + `available()`) — implement this to add a device. |
| `adapters/__init__.py` | `load_adapters()` — discovers adapters by entry-point name, skips unavailable ones. |
| `adapters/haptic.py` | Logitech MX Master firmware haptics over HID++. |
| `adapters/ulanzi.py` | Ulanzi TC001 / AWTRIX 3 LED matrix over MQTT or HTTP. |
| `adapters/lametric.py` · `divoom.py` · `esp32_bell.py` | LaMetric Time · Divoom Times Gate/Frame · ESP32 push bell. |

### Configuration & platform

| Module | Responsibility |
|---|---|
| `config.py` | Loads `default.yaml` + user `config.yaml` overrides (deep-merge). Exposes `INTENTS`, `ADAPTER_ORDER`, `section()`. |
| `state.py` | **Runtime state** — live `nhj set` / `mute` / mode dials, written under an `flock`, read fresh by the worker each cycle. |
| `modes.py` | **Mode macros** — one named preset that atomically sets every axis (muzak, audio FX, persona, dials, display, voice, haptic, ambient). |
| `resources.py` | Filesystem locations that resolve correctly in **both** a source checkout and a wheel install (bundled data vs user data/config/state/cache dirs). |
| `cli.py` | The `nhj` command (status, set, mode, muzak, test, install-hook, build-bank, …). |
| `procname.py` | Sets readable process titles (`NHJ worker`, `NHJ MCP`, `NHJ LLM`). |

---

## 3. The contracts

A handful of small, shared contracts hold the decoupled processes together. Get these right
and an extension drops in cleanly.

### 3.1 The marker grammar (agent → hook)

The agent emits a marker in its own final message. Two forms, case-insensitive:

```
[vibes:ok]                       intent only — auto-routes to a character by intent
[Jan:ok]                         explicit character override (the "who" before the colon)
[Karren:err|Build broke]         + message
[Bazza:warn|emotion=alert|Heads up]   + inline options
```

- **Intent** is constrained to `ok | err | warn | attn | celebrate | step` so ordinary prose
  like `[note:foo]` never matches (`hook.py:MARKER_RE`).
- The **who** must be `vibes` or a shipped character name (`voices/<name>/character.yaml`);
  anything else is treated as prose.
- A marker wrapped in backticks (`` `[Jan:ok]` `` — i.e. *discussed* rather than emitted) is
  skipped.
- Inline options after the message use `key=value` and map to `VibeEvent` fields
  (`vibe_level`, `pretext`, `verbosity`, `emotion`, `speed`, `voice_variant`, `character`).

### 3.2 `VibeEvent` (the validated event)

`event.py` is the single source of truth. Every entry path — the MCP tool, the local hook,
the remote hook — calls the same `validate()`, so they accept and reject identical inputs.

| Field | Type / range | Default | Meaning |
|---|---|---|---|
| `intent` | enum `ok·err·warn·attn·celebrate·step` | *(required)* | event type |
| `message` | str | `""` | free text; `""` → a random in-character phrase |
| `vibe_level` | int 1–10 | `5` | haptic intensity |
| `pretext` | str | `""` | spoken prefix (e.g. `"Node Om:"`) |
| `verbosity` | enum `low·medium·high` | `medium` | `low` suppresses the spoken message |
| `emotion` | enum `neutral·alert·confidential·celebrate` | `neutral` | TTS delivery style |
| `speed` | float 0.5–2.0 | `1.0` | TTS speed multiplier |
| `voice_variant` | str | `""` | per-character voice override |
| `character` | str | `""` | explicit character; `""` → route by intent |

> When you add a field, add it in **one** place (`VibeEvent` + `validate()`); the queue
> serialisation (`queue_manager.add`) and the MCP tool signature mirror it.

### 3.3 The queue file (producer → worker)

A producer writes an **atomic** `<ts>-<pid>-<seq>-<rand>.vibe` file (temp + fsync + rename)
and returns immediately. `intent`/`message`/`pretext` are base64-encoded (newline-safe); the
rest are plain `key=value`. The worker claims by an atomic rename to `*.proc.<pid>`, acks
(deletes) only after dispatch, and recovers a dead worker's `.proc` on the next claim. See the
module docstring in `queue_manager.py` for the full durability model.

### 3.4 The adapter interface (worker → device)

```python
class NotificationAdapter(ABC):
    def fire(self, intent, message, character, vibe_level=5, **kwargs) -> bool: ...
    def available(self) -> bool: ...
```

The worker calls `fire()` on every adapter `available()` reports as ready, in
`ADAPTER_ORDER`, each wrapped so one device's exception never blocks the next.

---

## 4. Configuration layering

Three layers, lowest precedence first. Each is read **fresh on the next vibe** — no restart.

1. **`config/default.yaml`** — shipped defaults (`config.py`). Bundled inside the wheel at
   `nhj/_bundled/config/`, or the repo `config/` in a checkout.
2. **User `config.yaml`** — deep-merged over the defaults (nested maps merge key-by-key;
   lists/scalars replace). Lives alongside `default.yaml` or in the platform config dir;
   `$NHJ_CONFIG_FILE` is an explicit override.
3. **Runtime state** (`state.py`) — what `nhj set` / `nhj mute` / `nhj mode` write. Layered on
   top of everything: **state > config > defaults**. Written under an `flock` so two sessions'
   hooks can't clobber each other.

A few settings also read an **env var** that wins at the point of use (e.g.
`NHJ_DEFAULT_CHARACTER`, `NHJ_<NAME>_LEVEL`, `NHJ_DYNAMIC*`, `NHJ_TRANSPORT`).

**Dials** are friendly names resolved to internal fields by `state.resolve_dial()`:
`ockerism`/`stress`/`karren` → `level` (1–11, 11 = swearing tier); `competence` → inverts
`chaos` (competence 10 = chaos 0 = flawless). This is the single source shared by `nhj set`
and the mode macros.

### `resources.py` — checkout vs wheel

The one module that knows where files live, so the same code works from a `src/` checkout and
an installed wheel:

- **bundled** (immutable, shipped *in* the wheel): config, hooks, `character.yaml` defs.
- **data** (user-writable, downloaded): voice `ref.{wav,txt}`, generated audio, the clip bank.
- **config / state / cache / log**: platform dirs (macOS Application Support, else XDG).

Override roots with `NHJ_DATA_DIR` / `NHJ_CONFIG_DIR` / `NHJ_STATE_DIR` / `NHJ_CACHE_DIR` /
`NHJ_LOG_DIR`.

---

## 5. Extension recipes

### Add a notification device (adapter)

1. Subclass `NotificationAdapter` (`adapters/base.py`): implement `available()` (is the device
   configured *and* reachable?) and `fire(...)` (best-effort; return `True`/`False`, never
   raise out — the worker isolates you, but be a good citizen).
2. Register the entry point in `pyproject.toml`:
   ```toml
   [project.entry-points."nhj.adapters"]
   mydevice = "nhj.adapters.mydevice:MyDeviceAdapter"
   ```
3. Add `mydevice` to `ADAPTER_ORDER` (config `adapters:`) where you want it in the chain.
   Audio sits last on purpose (slowest, most likely to block).
4. Read your own config via `config.section("mydevice")`.

A third-party package can register its own adapter the same way — no fork required.

### Add a character

Drop a `voices/<name>/character.yaml` (persona, `voice`, `dial`, `level`, `chaos`, and phrase
`bands:` — `low`/`mid`/`high`, optional `swearing:`). Add a `ref.wav`+`ref.txt` for the cloned
voice (or rely on the bank). Route intents to it via `config` `routing.rung_by_intent`, or call
it explicitly with a `[<name>:intent]` marker. Legacy flat `phrases:` maps still load.

### Add a mode

Add an entry under `modes:` in `config/default.yaml` (merged over `BUILTIN_MODES` in
`modes.py`) — any of `muzak · audio_mode · scenario · display · voice · haptic · censor ·
ambient · dials`. No code: a mode is composed configuration. `nhj mode <name>` applies it
atomically (clean reset → layer preset).

---

## 6. Development setup

```bash
git clone https://github.com/guruswami-ai/not-happy-jan
cd not-happy-jan
uv run --extra dev pytest -q          # tests live ONLY in tests/
```

Before opening a PR (from [CONTRIBUTING.md](../CONTRIBUTING.md)):

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
bash -n install.sh
uv build
```

Notes for working in the tree:

- **Tests are in `tests/` only** — `tests/test_collection_guard.py` enforces it; utility
  scripts elsewhere are not collected.
- The worker is **spawned fresh per cycle** and reads state live, so a `nhj set` takes effect on
  the next vibe with no restart. Test a channel end-to-end with `nhj test err -m "…"`.
- Logs land in the platform log dir (`hook.log`, `worker.log`); set `NHJ_*_DIR` env roots to
  redirect any filesystem location during local testing.
- Audio/TTS/LLM are **Apple-Silicon (MLX) for TTS**; run with `TTS_ENGINE=none` and
  `NHJ_DYNAMIC` unset to exercise the queue/adapter/bank paths without models.

---

*See also: [ARCHITECTURE.md](ARCHITECTURE.md) (design rationale) ·
[AUDIO-STANDARD.md](AUDIO-STANDARD.md) (mixer + loudness) ·
[configuration.md](configuration.md) · [dynamic-voices.md](dynamic-voices.md) ·
[modes.md](modes.md) · [integration.md](integration.md).*
