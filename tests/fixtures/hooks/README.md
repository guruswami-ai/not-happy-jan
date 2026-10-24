# Hook payload fixtures (#47)

One JSON payload per (CLI × event) that NHJ's Python side consumes, pinned by
`tests/test_hook_fixtures.py`. When a vendor changes its hook shape, update the
fixture from a real capture and let the snapshot expectations move deliberately.

Provenance (same vocabulary as `docs/other-clis.md`):

| Directory | Status | Basis |
|---|---|---|
| `claude-code/` | Verified | Shapes the live Claude Code hooks in `hooks/hooks.json` run against daily; field set as read by `hook.py` / `prompt_hook.py` / `notify_hook.py` |
| `grok-build/` | Documented | camelCase keys (`sessionId`, `workspaceRoot`) per `docs/other-clis.md`; parsed by `cli._hook_identity` |
| `codex/` | Documented | `notify` argument JSON (`type`, `cwd`, `last-assistant-message`) per `integrations/hooks/codex-notify.sh` |
| `gemini/` | Documented | Claude-style stdin JSON with `hook_event_name` (`AfterAgent`) per `docs/other-clis.md` |

`__TRANSCRIPT__` in Claude fixtures is replaced by the test with a temp
transcript path. opencode events are covered by `tests/test_opencode_plugin.py`,
which drives the real plugin JS with synthetic events (verified live upstream).
