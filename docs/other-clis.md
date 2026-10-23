# Other agent CLIs

NHJ is not tied to Claude Code. Anything that can run a shell command (or load a plugin) at the
right moments can drive it. This page lists what each CLI offers, with copy-paste config.

## The three building blocks

| Command | Does | Use it from |
|---|---|---|
| `nhj vibe <intent> -m "text" [-s label]` | fire an event (voice, board, displays, haptics) | "finished", "needs you", "error" hooks |
| `nhj busy` / `nhj idle` | start / stop the hold music for this session; joins the [multi-agent policy](orchestration.md) | "prompt submitted" and "stop" hooks |
| `nhj_vibe` MCP tool (`nhj serve-mcp`, or `http://host:8765/mcp`) | the model fires events itself | agents with MCP but no hooks |

All three read the hook's JSON from stdin when there is one (for the project folder and session id),
print nothing, and exit 0, which matters for CLIs that parse hook output. Intents:
`ok` (finished), `attn` (needs you), `err`, `warn`, `celebrate`, `step`. With no `-s`, the label is the
project folder; pass `-s name` to override. Respect `nhj voice off` / `nhj mute` as usual.

For CLIs with Claude-style JSON hooks, [`integrations/hooks/claude-style.json`](https://github.com/guruswami-ai/not-happy-jan/blob/main/integrations/hooks/claude-style.json)
is a ready template.

## Status at a glance

"Verified" = exercised against the real tool on a real board and speaker. "Simulated" = the NHJ side was
tested with synthetic events. "Documented" = taken from the vendor's docs, **not run by us**: treat the
snippet as a starting point and run the smoke test before relying on it.

| CLI | Finished | Needs you | Error | Hold music | Status |
|---|---|---|---|---|---|
| **Claude Code** | `Stop` | `Notification` | `StopFailure` | yes (`UserPromptSubmit` + `Stop`) | **Verified** |
| **opencode** | `session.idle` | `permission.asked`, `question.asked` | `session.error` | yes (`session.status`) | **Verified** live on 1.19.4: finished (+ music), permission, and (on 1.17.13) error. `question.asked` only simulated |
| Qwen Code | `Stop` | `Notification` | `StopFailure` | yes | Documented |
| Grok Build (xAI) | `Stop` | `Notification` (semantics undocumented) | `StopFailure` | yes | Documented |
| Codex CLI | `notify` / `Stop` | `PermissionRequest` hook | none | `Stop` only | Documented |
| Gemini CLI | `AfterAgent` | `Notification` (tool permission only) | none | yes | Documented |
| Kimi Code CLI | `Stop` | `PermissionRequest` | `StopFailure` | yes | Documented |
| Pi | `agent_settled` (extension) | none (no permission prompts) | `turn_end` outcome | yes | Documented |
| Goose | `Stop` | none | `PostToolUseFailure` | yes | Documented |
| Cursor CLI | `stop` | none | `stop` status | partial | Documented (reports of gaps in `-p`) |
| Mistral Vibe | `post_agent` | none | none | no | Documented |
| Aider | `--notifications-command` | same single trigger | none | no | Documented |
| Muse Code (Meta) | not documented | not documented | not documented | | Not found |
| Amp, Crush | no suitable hook found | | | | Not found |

## Claude-style hooks: Qwen Code, Grok Build, Kimi, Codex

These share the Claude Code hook shape (JSON on stdin with `session_id`, `cwd`, `transcript_path`).

**Qwen Code** (`~/.qwen/settings.json`; timeouts are seconds; project hooks need a trusted folder)

```json
{"hooks": {
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "nhj busy", "timeout": 10}]}],
  "Stop":             [{"hooks": [{"type": "command", "command": "nhj idle; nhj vibe ok -m 'Qwen done'", "timeout": 10}]}],
  "Notification":     [{"hooks": [{"type": "command", "command": "nhj vibe attn -m 'Qwen needs you'", "timeout": 10}]}],
  "StopFailure":      [{"hooks": [{"type": "command", "command": "nhj idle; nhj vibe err -m 'Qwen error'", "timeout": 10}]}]
}}
```

Note: Qwen does not fire `StopFailure` for API errors in headless `-p` mode.

**Grok Build** (`~/.grok/hooks/nhj.json`, same shape; default timeout 5 s; project hooks need `/hooks-trust`).
Payload keys are camelCase (`sessionId`, `workspaceRoot`); NHJ accepts both. It also reads `.claude/settings.json` hooks.
The community `superagent-ai/grok-cli` is a different tool and is not covered.

**Kimi Code CLI** (`~/.kimi-code/config.toml`; some docs say `~/.kimi/`; only `event`, `command`, `matcher`, `timeout` keys)

```toml
[[hooks]]
event = "UserPromptSubmit"
command = "nhj busy"
[[hooks]]
event = "Stop"
command = "nhj idle; nhj vibe ok -m 'Kimi done'"
[[hooks]]
event = "PermissionRequest"
command = "nhj vibe attn -m 'Kimi needs approval'"
[[hooks]]
event = "StopFailure"
command = "nhj idle; nhj vibe err -m 'Kimi error'"
```

**Codex CLI** passes its `notify` payload as a single JSON **argument**, not on stdin, so use the wrapper:

```toml
# ~/.codex/config.toml
notify = ["/path/to/integrations/hooks/codex-notify.sh"]
```

Codex hooks (`~/.codex/hooks.json`, same Claude-style shape as above) add `PermissionRequest` for
approvals. Its `Stop` hook output must be empty or JSON; NHJ prints nothing. There is no error event.

## Gemini CLI

`~/.gemini/settings.json`. Timeouts are **milliseconds**; hook stdout must be clean JSON or empty, so
redirect NHJ's output. `Notification` currently only reports tool-permission requests.

```json
{"hooks": {
  "BeforeAgent": [{"matcher": "*", "hooks": [{"name": "nhj-busy", "type": "command", "command": "nhj busy >/dev/null", "timeout": 10000}]}],
  "AfterAgent":  [{"matcher": "*", "hooks": [{"name": "nhj-ok", "type": "command", "command": "nhj idle >/dev/null; nhj vibe ok -m 'Gemini done' >/dev/null", "timeout": 10000}]}],
  "Notification":[{"matcher": "*", "hooks": [{"name": "nhj-attn", "type": "command", "command": "nhj vibe attn -m 'Gemini needs approval' >/dev/null", "timeout": 10000}]}]
}}
```

## Pi

Pi has no shell hooks; it loads TypeScript **extensions** from `~/.pi/agent/extensions/`. It has no
permission prompts, so "needs you" only exists if your own extension asks. Use `agent_settled` (not
`agent_end`, which is not final). The package scope in the import has differed between versions
(`@mariozechner/...` vs `@earendil-works/...`): match your install.

```ts
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) {
  pi.on("agent_start",   async () => { await pi.exec("nhj", ["busy"]); });
  pi.on("agent_settled", async (e) => {
    await pi.exec("nhj", ["idle"]);
    await pi.exec("nhj", ["vibe", e.aborted ? "warn" : "ok", "-m", "pi done"]);
  });
  pi.on("turn_end", async (e) => { if (e.outcome === "error") await pi.exec("nhj", ["vibe", "err", "-m", "pi error"]); });
}
```

## opencode

Ships in the repo: `cp integrations/opencode/nhj.js ~/.config/opencode/plugins/nhj.js` (needs `nhj` on PATH,
or set `NHJ_BIN`). It maps `session.status` busy → `nhj busy`, `session.idle` → `nhj idle` (plus
`nhj vibe ok` for turns of `NHJ_OPENCODE_MIN_SECONDS`, default 60), `permission.asked` / `question.asked`
→ `attn`, `session.error` → `err`, and ignores subagent sessions. `question.asked` is not in opencode's
published event list but is delivered in practice; older builds called the permission event
`permission.updated`, which the plugin also accepts. `NHJ_OPENCODE_DEBUG=1` logs every event type.

Verified live against opencode 1.19.4 with a free-tier model (the free tier needs opencode 1.18 or newer): a plain
turn produced `session.created` → `session.status` (busy, then idle) → `session.idle`, which started and stopped the
hold music and announced "opencode is done"; a prompt that made the model call the `bash` tool produced
`permission.asked` → `permission.replied` → `session.idle`, and the board read "NEEDS YOU … OPENCODE NEEDS YOUR PERMISSION".
The event names were unchanged between 1.17.13 and 1.19.4.

## Goose, Cursor, Vibe, Aider (minimal)

- **Goose**: a hooks plugin at `~/.agents/plugins/nhj/hooks/hooks.json` (plus `plugin.json`) with `Stop` → `nhj idle; nhj vibe ok`
  and `UserPromptSubmit` → `nhj busy`. `Stop` can force the agent to continue on exit code 2: keep NHJ's exit 0.
- **Cursor CLI**: `~/.cursor/hooks.json` `{"version":1,"hooks":{"stop":[{"command":"./hooks/nhj-stop.sh"}]}}`; the script reads
  `status` (`completed|aborted|error`) from stdin and calls `nhj vibe ok|warn|err`. Reports say `stop` is not emitted in `-p` mode.
- **Mistral Vibe**: `~/.vibe/hooks.toml` `[[hooks]] name="nhj" type="post_agent" command="nhj vibe ok -m 'Vibe done'"`.
- **Aider**: `.aider.conf.yml` `notifications: true` and `notifications_command: "nhj vibe ok -m 'aider ready'"`. One trigger only,
  no payload.

## Not covered

**Muse Code** (Meta's `muse`): its settings file has a `hooks` block, but we found no official documentation of the format,
so nothing is promised. **Amp** and **Crush** expose no finish/error hook we could find. For any of these, the `nhj_vibe` MCP
tool (the model fires it) or wrapping the command (`my-agent ...; nhj vibe ok`) still works.

## Smoke test any integration

1. `nhj audio-devices` and `nhj test ok` confirm the speaker and board work before blaming the CLI.
2. Run the CLI non-interactively where it has such a mode (`codex exec`, `gemini -p`, `qwen -p`, `opencode run`, `pi -p`),
   with **stdin closed (`</dev/null`)**: several of these read piped stdin to the end, so under a script or tool runner whose
   stdin is an open pipe they appear to hang before doing anything (`opencode run` did exactly this). Then watch for the event: `tail -f` the NHJ `hook.log` / `worker.log` (`nhj` logs live under the platform log dir).
3. Check `nhj` sees the session: `python -c "from nhj import inference_muzak as m; print(m._read())"` shows the busy set.
4. Headless modes do not always fire every hook (see each section): test the interactive path too.
