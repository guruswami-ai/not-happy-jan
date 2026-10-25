# Not-Happy-Jan — Specification

**Status:** NORMATIVE. Accepted by the maintainer on 2026-10-26 ("accept all"
on the ten decisions gathered for #47; see issue #88). Derived from current
behaviour during the [AUDIT-2026-10-24](docs/audit/AUDIT-2026-10-24.md).
This file lives at the repository root. Behaviour changes land as a SPEC
edit + tests first, then code.

Normative words: MUST / SHOULD / MAY as in RFC 2119.

---

## 1. Purpose, platforms, budgets summary

NHJ provides multi-sensory status feedback (voice, hold music, haptics, displays,
bell) for AI coding agents, 100% on the user's machine. It MUST never block, fail,
or noticeably slow the coding agent it observes.

- **Supported platforms:** macOS (Apple Silicon) full; Linux (x86_64, XDG) full
  minus MLX TTS (audio.cpp backend instead). Windows is explicitly
  out of scope for v1 (the code cannot import there today: unconditional
  `fcntl`, POSIX services and paths); revisit only with real demand.
- **Python:** ≥3.10. **Install:** user-context only, no sudo, fully reversible
  (`nhj uninstall`).
- **Privacy:** inference MUST be local by default. The only network listeners are
  127.0.0.1 by default; LAN exposure is explicit opt-in. LAN mode uses the
  optional `NHJ_SHARED_SECRET`: when it is set, network callers MUST present
  it; when it is blank there is no check, which is trusted-network only and
  MUST be documented as such. No secret is required for loopback.

## 2. The vibe event (canonical contract)

Implemented by `nhj.event.validate` / `VibeEvent`; this section is its spec.
Every producer (MCP tool, hooks, CLI, other CLIs) and consumer (queue, worker,
adapters) MUST use exactly these fields. Unknown fields MUST be rejected or
reported, never silently forwarded.

| Field | Type | Default | Rule |
|---|---|---|---|
| `intent` | enum | — (required) | `ok err warn attn celebrate step` |
| `message` | string | `""` | free text, at most 500 characters; `""` → random in-character bank phrase. Over-length is rejected with `VibeValidationError`, never truncated so partial text is spoken. Hooks cap marker messages at 500 characters before validation so an agent turn never fails because of NHJ. |
| `vibe_level` | int | 5 | 1–10 |
| `pretext` | string | `""` | spoken prefix |
| `verbosity` | enum | `medium` | `low medium high`; `low` → message suppressed at worker |
| `emotion` | enum | `neutral` | `neutral alert confidential celebrate` |
| `speed` | float | 1.0 | 0.5–2.0; applied exactly once, at playback; 1.0 defers to character band |
| `voice_variant` | string | `""` | per-character voice override (advisory) |
| `character` | string | `""` | `""` → route by intent (§7) |
| `source` | string | `""` | `project` or `host:project`, ≤60 chars; set by hooks, not by the model |

Validation errors raise `VibeValidationError`; hooks MUST catch, log, and skip the
marker (never fail the agent session); the MCP tool returns `[nhj:error] …`.

## 3. Marker syntax (agent → hook side channel)

```
marker  := "[" who ":" intent ("|" part)* "]"
who     := "vibes" | <character name>        ; case-insensitive
intent  := "ok"|"err"|"warn"|"attn"|"celebrate"|"step"
part    := key "=" value | message-text      ; first non-kv part is the message
key     := vibe_level|pretext|verbosity|emotion|speed|voice_variant|character
```

Rules (today's `hook.py` behaviour, now normative):
- Only in the **final assistant message** of a turn; multiple markers fire in order.
- Fire-once per (session, final-message digest).
- A marker immediately wrapped in backticks on either side does not fire. Markers
  elsewhere in code spans/blocks DO fire. This adjacent-backtick-only rule is
  retained for v1; proper fence parsing is scheduled only if false fires are
  reported.
- Unknown `who` = prose, ignored. Unknown option keys are logged, not fatal.
- `[Character:intent]` overrides intent routing; inline `character=` loses to the
  explicit who.

## 4. Agent-CLI hook contracts

Claude Code is the reference implementation. For every supported CLI there MUST be
a captured real payload fixture per event under `tests/fixtures/hooks/<cli>/` and a
snapshot test. Fixtures exist for Claude Code (UserPromptSubmit, Stop, StopFailure,
SessionEnd, Notification — identity + real hook routing pinned by
`tests/test_hook_fixtures.py`), Grok Build and Gemini (identity), and Codex
`notify`; opencode's plugin events are covered by `tests/test_opencode_plugin.py`.
Provenance per CLI (Verified vs Documented) lives in the fixtures README. The
table below is the target coverage; new CLIs land fixtures with their support.

| NHJ action | Claude Code | Codex | opencode | Others |
|---|---|---|---|---|
| mark session busy (+ secret guard if enabled) | `UserPromptSubmit` | notify (turn start n/a — busy implied by process) | plugin event | per-doc |
| scan final message for markers, mark idle | `Stop` | agent-turn-complete notify | plugin event | per-doc |
| mark idle (error) | `StopFailure` | — | — | — |
| mark idle (session gone) | `SessionEnd` | — | — | — |
| attention cue | `Notification` | — | — | — |

Hook processes MUST: exit 0 on any input (including empty/malformed stdin),
complete local dispatch in ≤3.5 s worst case (flush-wait budget §9), and time out
remote dispatch at ≤5 s. Transcript access MUST be tail-bounded (audit R1) —
cost independent of session length.

## 5. Configuration

Precedence (low → high): bundled `config/default.yaml` → `config.yaml` beside it
(checkout) → user config dir → legacy `~/.config/nhj` (read fallback) →
`$NHJ_CONFIG_FILE`. Mappings merge key-by-key; lists/scalars replace. Malformed
files are a loud `ConfigError`, never a silent fallback.

Implemented (config-schema slice of #47):
- A schema (pydantic, `extra="forbid"`, `nhj.config_schema`) defines every key,
  type, range, and enum in `default.yaml`. Each file is validated with its path
  named; partial overrides and user-defined character/mode names stay valid.
  `nhj config check` loads and validates the merged config on demand.

Implemented (env-registry slice of #47):
- Every env var is registered in one module (`nhj.env_registry`): name, type,
  default, which YAML key it overrides, and read timing (live/startup/import).
  A test fails when code reads an unregistered literal env var, and the table
  in `docs/configuration.md` is generated from the registry (sync-tested).
  Call sites still read `os.getenv` directly; migrating them onto typed
  registry lookups is incremental follow-up, not a flag day.

Still open (audit Q2):
- Config is frozen at process start. Long-lived processes (MCP daemon, mixer)
  require restart to pick up edits, except live state (§6) which is read per event.

Key sections and their contracts: `routing` (the shipped defaults cover all 6
intents; overrides may be partial), `characters.<name>.level` 1–11 (11 = swearing pool, then runtime
censor decides audibility), `chaos` 0–10, `modes.<name>` (mutually exclusive,
applying one resets the last), `adapters` (ordered list, `audio` last),
`orchestration.mode` (`quiet-while-busy`|`always-speak`), `voice.default` (on|off),
plus adapter sections `awtrix`, `claky`, `haptic`, `audiocpp`, `playback`.

## 6. Runtime state files

All cross-process JSON state MUST be mutated only via: `flock` on a sibling lock
file + write to a **unique** temp file + atomic rename. (Several files violate
this today — audit B1/B2; Phase 0 fixes them.)

| File | Owner(s) | Content / rule |
|---|---|---|
| `state.json` | CLI, hooks, worker (read) | `dials{char{key:int}}`, `flags`, `settings`, `voice_gate{global, projects{}}`; live overrides layered over YAML |
| `music.json` | hooks, mixer controller, worker | `busy{session: ts}`, `pids{session: pid}`, `quiet_done[]` (≤50), `stop`, `controller_pid`; eviction: ts older than 1800 s OR owner pid dead; eviction MUST go through the locked update, not a getter |
| `hook-seen.json` | prompt/Stop hooks | `{session: {sig, ts}}`, TTL 86400 s, hard cap 50 entries |
| vibe queue dir | producers, worker | §6.1 protocol below |
| audio event spool | any process → mixer | one JSON per clip `{path,bus,gain,rate,effect,ephemeral}`; filenames MUST be collision-proof (ts+pid+seq+rand) |
| heartbeats | `servers.ensure` / supervisor | `<name>.heartbeat` epoch float, atomic write |

### 6.1 Queue protocol (implemented by `queue_manager.py`)

- Enqueue: atomic create `<ts>-<pid>-<seq>-<rand>.vibe`, 0600, dir 0700.
  Text fields base64-encoded. `QueueManager.add` runs `event.validate` first
  and raises `VibeValidationError` instead of writing a bad file.
- Claim: atomic rename `.vibe` → `.proc.<worker-pid>` (oldest first). Unparseable
  files, and files `event.validate` rejects, go straight to `.poison` (never
  retried, never block the queue). At most one live worker via flock
  election; PID recorded before the lock is released.
- Ack only after dispatch completes. Crash before ack → reclaimable: worker dead
  (PID liveness) or lease expired → requeue, max 3 retries → `.poison`.
- The claim carries a 30 s lease renewed by a 10 s heartbeat during dispatch,
  up to a 180 s dispatch budget. Past that budget the heartbeat stops, so a
  hung claim's lease can expire and the event can be reclaimed (audit B5).

## 7. Dispatch: routing, gating, adapters

Worker drains the queue (30 s idle self-exit) and per event:
1. If `mute` flag: drop silently (all channels).
2. Character: explicit `character` else `routing.rung_by_intent[intent]`;
   dials resolved state > env > YAML > character default.
3. Voice gate (`gate.py`): project rule > `host:project` > global > `voice.default`.
   Gated-off projects still fire silent channels.
4. Orchestration: in `quiet-while-busy`, `ok|celebrate|step` while any session is
   busy → chime + count; `warn|err|attn` always speak; next spoken completion
   appends the digest sentence.
5. Adapters fire in config order, each isolated: an exception in one MUST NOT
   prevent the rest, and MUST be debug-logged. `fire(intent, message, character,
   vibe_level, emotion, voice_variant, speed, source, voice_mode)` — adapters
   accept `**kwargs` for forward compatibility. Adapter network calls MUST have
   timeouts ≤5 s (displays) except TTS/LLM below.

### 7.1 Voice rendering ladder (audio adapter)

In order: (1) pre-rendered bank clip (bare markers), (2) message cache
(sha256 voice+band+text, LRU ≤300), (3) live synthesis — `TTS_ENGINE`
`qwen` (POST `::9992/tts/generate`, 30 s), `audiocpp` (60 s), the CPU tier
(`os` system voice or `piper` local model), or `none`. The CPU tier is the
no-GPU option between the cache and model synthesis; Kokoro MAY be added as
an optional engine later and does not block v1.
Dynamic mode (`NHJ_DYNAMIC=1`) rephrases via any OpenAI-compatible endpoint
(8 s timeout) before synthesis; any failure falls back to the static phrase bank.
Censor is applied to all rendered text per current mode. Speed applies once at
playback (§2). All playback goes through the single mixer; no other audio path
may exist.

### 7.2 Hold music

Plays while the busy-set (§6) is non-empty or continuous mode; pauses the moment
it empties, then plays the off-hold sequence before the voice line. Sequence and
levels are normative in `docs/AUDIO-STANDARD.md`. Controller exits after 600 s idle.

## 8. Failure behaviour

- **Never break the agent:** hooks exit 0; worker/adapter failures degrade to
  fewer channels; missing models degrade live → cache → bank; missing devices are
  skipped. There is no state in which NHJ makes the agent wait for inference.
- **Never silently:** every swallowed exception MUST emit a `resources.debug`
  line; adapter load failures MUST be visible via `nhj status`. (Phase 2 sweep.)
- **Logs:** `hook.log`, `worker.log`, `<server>-server.log`. Each rotates at
  1 MB with 3 backups. Log lines carry intent, source and message *length*
  by default — not message text — except under `NHJ_DEBUG` (audit S2).
- **Secret guard (opt-in):** regex shape-detection on the user prompt; MUST only
  ever surface a category label — the matched value MUST NOT be stored, logged,
  or transmitted (`secret_scan.py` contract, tested).

## 9. Resource budgets (normative targets)

Confirmed for v1 as targets; measurement hardening may follow without
blocking acceptance of this specification.

| Budget | Target |
|---|---|
| Added latency in Stop hook (marker present, warm) | p95 ≤ 500 ms incl. flush-wait; hard cap 3.5 s |
| Added latency in UserPromptSubmit | p95 ≤ 150 ms |
| Hook CPU vs transcript size | constant (tail read ≤256 KB) |
| Idle RAM, on-demand profile | ~0 model RAM; mixer+worker < 150 MB |
| Idle RAM, full profile | ≤ 3.5 GB resident (TTS+LLM) |
| Time to first sound after Stop | bank/chime ≤ 300 ms; live voice ≤ 2 s warm |
| Mixer idle CPU | < 1% of one core (tick ≤ 8 Hz when silent) |
| Disk growth | cache ≤300 clips; temp spool ≤80 files; logs capped (§8) |

## 10. Packaging and dependencies

- Wheel ships bundled config/hooks/characters/clips bank (`pyproject.toml`
  force-include); media (voice refs, music) installs to the user data dir, never
  the package. A wheel install MUST work with no source checkout (CI smoke test).
- Dependency split (audit R4): the core install is lean; heavy wheels live in
  extras — `[audio] av/sounddevice/numpy/scipy`, `[haptic] hidapi`,
  `[displays] paho-mqtt`, `[server] fastapi/uvicorn/soundfile` (plus
  `mlx-audio` on macOS arm64 only). The dev extra carries the wheels the test
  suite imports. Dev tooling is pinned (`ruff` explicit `select`, audit T2).
  CI MUST cover every platform claimed in §1 (audit T3).
