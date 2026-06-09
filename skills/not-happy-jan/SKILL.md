---
name: not-happy-jan
description: Adjust Not-Happy-Jan feedback settings by running the nhj CLI when the user wants to tune voices, dials, muting, censoring, tests, or hold music.
allowed-tools: Bash
---

# Not-Happy-Jan settings

Use the `nhj` CLI to inspect and change Not-Happy-Jan settings. Changes take effect on the next vibe; no Claude restart is needed.

## Running the CLI

Use `nhj` if it's on `PATH`; otherwise use the project venv:
`<project>/.venv/bin/nhj <command>`.

## The dials

- **Jan** — `ockerism` (1 professional … 10 broad bogan … **11 swearing**); `competence`
- **Bazza** — `stress` (1 apathetic … 10 anxious/fast); `competence`
- **Karren** — `karren` (1 curt … 10 full "NOT HAPPY JAN" meltdown)

## Commands to run

| User says… | Run |
|---|---|
| "what are the current settings?" | `nhj status` |
| "make Jan more bogan" / "set Jan's ockerism to 8" | `nhj set jan.ockerism 8` |
| "make Jan less competent / muddled" | `nhj set jan.competence 3` |
| "let Jan swear" / "full bogan" | `nhj set jan.ockerism 11` or `nhj swearing on` |
| "stop bleeping / raw swears" / "change the bleep" | `nhj censor off` or `nhj censor beep` / `quack` / `honk` |
| "crank Karren to 11" | `nhj set karren.karren 10` |
| "make Bazza less anxious" | `nhj set bazza.stress 2` |
| "turn the hold music on / off" | `nhj muzak on` / `nhj muzak off` |
| "mute the notifications" | `nhj mute` |
| "unmute" | `nhj unmute` |
| "test an error vibe" | `nhj test err -m "test message"` |

## Notes

- Prefer `nhj status` before and after changes when the user asks what is currently set or wants confirmation.
- Runtime overrides live in NHJ's platform state location (`NHJ_STATE_FILE` or the platform state dir; `NHJ_STATE_DIR` can override the directory).
- Config lives in the checkout `.env` during source use, or the platform config location for installed/wheel use (`NHJ_CONFIG_DIR` can override it).
- `mute` silences all channels until `nhj unmute`.
- Swearing is censored by default; only run `nhj censor off` if the user explicitly asks for raw swears.
