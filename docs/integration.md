# Integration — Claude Code, other agents & devices

*← back to the [Not-Happy-Jan README](../README.md)*

## Claude Code integration

NHJ installs four Claude Code lifecycle hooks: **Stop** (scans each response for `[vibes:INTENT]` markers and speaks the result), **StopFailure** (marks the session idle immediately when a turn ends with an API error, so hold music stops right away), plus **UserPromptSubmit** + **SessionEnd** (the hold-music busy/idle tracking and the secret guard). StopFailure warning vibes are opt-in with `stop_failure_vibe`.

```bash
nhj install-hook   # adds the four hooks to ~/.claude/settings.json (backs it up first)
```

NHJ also ships a Claude Code **skill** for changing Jan/Bazza/Karren settings from plain-language requests.

After installing hooks, verify they are registered by running `/hooks` in the Claude Code chat window. The NHJ events should appear with command entries and timeouts.

- **Plugin install:** Claude Code discovers the skill from the plugin's top-level
  `skills/not-happy-jan/SKILL.md`.
- **NHJ repo checkout:** the same top-level `skills/` file is the canonical source used by
  the plugin and package build.
- **Wheel / non-checkout install:** copy the bundled skill into Claude's personal skills dir:

```bash
nhj install-skill  # writes ~/.claude/skills/not-happy-jan/SKILL.md
```

### Verify the skill

```bash
ls ~/.claude/skills/not-happy-jan/SKILL.md   # wheel installs after `nhj install-skill`
nhj status                                   # confirm the CLI is available to the skill
```

The skill uses `nhj status`, `nhj set`, and related CLI commands. Live overrides follow NHJ's
resource conventions: checkout `.env` during source use, otherwise the platform config dir
(`NHJ_CONFIG_DIR` override), plus the platform state dir / `NHJ_STATE_FILE` for runtime state.

> **Supersedes legacy `[vibes:]` Stop hooks.** NHJ uses the same `[vibes:]` marker
> contract as some earlier agent-feedback tools, so `install-hook` *supersedes* a
> legacy shared-marker Stop hook rather than stacking on top:
> - It first backs up `~/.claude/settings.json` → **`settings.json.pre-nhj.bak`**.
> - If it finds a legacy shared-`[vibes:]`-marker Stop hook, it **removes that entry**
>   before adding NHJ's — otherwise both would fire and you'd hear each notification **twice**.
> - Only the `settings.json` hook *entry* is touched; no other tool's files are removed.
> - `nhj remove-hook` removes NHJ's hooks (restore from the backup to revert).
>
> If you've no prior agent-feedback Stop hook, it simply adds NHJ's.

Claude emits markers anywhere in its response:
```
[vibes:ok]
[vibes:err|Build broke on line 47]
[vibes:warn|emotion=alert|Heads up]
[vibes:celebrate]
```

## The event contract (options)

The marker hook, the MCP tool (`nhj_vibe`), the remote dispatch, the queue, and the worker all share **one** option set and **one** validator (`nhj/event.py`), so an `[Jan:ok|…]` marker and an `nhj_vibe(...)` call accept, validate, and forward exactly the same options:

| Option | Type / range | Meaning |
|---|---|---|
| `intent` | `ok\|err\|warn\|attn\|celebrate\|step` | event type (required) |
| `message` | text | `""` → a random in-character phrase |
| `vibe_level` | int 1–10 | haptic intensity |
| `pretext` | text | spoken prefix (e.g. `Node Om:`) |
| `verbosity` | `low\|medium\|high` | message detail |
| `emotion` | `neutral\|alert\|confidential\|celebrate` | TTS delivery |
| `speed` | float 0.5–2.0 | TTS speed multiplier |
| `voice_variant` | text | per-character voice override |
| `character` | name | explicit override (`""` routes by intent) |

Inline marker form: `[Jan:err|emotion=alert|speed=1.2|Build broke]` (the leading non-`key=value` chunk is the message; the `who` before the colon is the `character`).

- **Local and remote dispatch send an identical payload** — the explicit-character override (`[Jan:ok]`) works the same whether it fires locally or over `NHJ_REMOTE_URL`.
- **Validation happens once, at the boundary.** Out-of-range / unknown-enum values produce a clear error: the MCP tool returns `[nhj:error] …`; a bad marker is logged and skipped so it never crashes the session.
- **Unknown options are reported, not silently accepted** (logged to `/tmp/nhj-hook.log`).

## Other IDEs / agents

NHJ exposes an MCP tool `nhj_vibe` that works with any MCP-compatible agent:

| Agent | Integration method |
|---|---|
| Claude Code | Stop hook (marker-based) or MCP tool |
| OpenCode | MCP tool via `.mcp.json` |
| Cursor | MCP tool via MCP server config |
| Aider | `--after-completion` shell hook |
| Cline | MCP tool |
| Amazon Q | Hook command |
| JetBrains AI | MCP server |

For remote agents (fleet nodes) set `NHJ_REMOTE_URL` to the NHJ SSE daemon on your local machine.

## OpenCode integration

OpenCode uses MCP for tool integration. Configure NHJ by adding to your `~/.config/opencode/opencode.json`:

```json
{
  "mcp": {
    "not-happy-jan": {
      "type": "local",
      "command": ["nhj", "serve-mcp"],
      "enabled": true
    }
  }
}
```

Or install automatically:

```bash
nhj install-opencode
```

The MCP server exposes the `nhj_vibe` tool for triggering feedback events:

```python
# Example OpenCode usage
await nhj_vibe(intent="ok", message="Task completed successfully")
await nhj_vibe(intent="err", message="Build failed on line 47")
await nhj_vibe(intent="celebrate")
```

**Requirements:**
- NHJ installed and on PATH
- MCP server transport set to stdio (default)
- OpenCode configured to load local MCP tools

## Security nudge — Karren catches leaked secrets

If you paste something secret-shaped into the chat (an API key, password, token,
private key…), Karren tells you off: *"Oi! You just dropped a bloody API key in the
chat. NOT. HAPPY."*

**Off by default (opt-in).** Enable with:

```bash
nhj set secret_guard on
```

**Important caveats — read before enabling:**

- **Heuristic only.** The detector matches the *shape* of a secret with local
  regular expressions. It will produce **false positives** (normal text that
  happens to resemble a key format) and **false negatives** (real secrets that
  don't match any pattern). Do not rely on it as a security control.
- **Local-only, no harvesting.** `../src/nhj/secret_scan.py` only returns a
  category string (`"an API key"`) or `None` — it **never captures, returns,
  logs, stores, or transmits the secret value**. Karren's scold names only the
  **category**, never the secret itself.
- **Zero network.** The only effect is a local spoken nudge. It's ~40 auditable
  lines.

This is a *convenience reminder*, not a security guarantee.

## Adding a notification device

> 💡 New to the hardware? See **[docs/devices/](devices/README.md)** — which devices are supported, and why a **Ulanzi pixel clock** (~AU$60, ~10-min flash) is the recommended place to start.

Implement `NotificationAdapter` and register it via entry points:

```python
# mypkg/adapters.py
from nhj.adapters.base import NotificationAdapter

class MyDeviceAdapter(NotificationAdapter):
    def available(self) -> bool: ...
    def fire(self, intent, message, character, vibe_level, **kw) -> bool: ...
```

```toml
# pyproject.toml
[project.entry-points."nhj.adapters"]
mydevice = "mypkg.adapters:MyDeviceAdapter"
```
