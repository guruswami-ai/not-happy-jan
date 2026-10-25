# Scripts and automation: `nhj run`

NHJ is not only for coding agents. `nhj run` wraps any command and tells you how it ended, so a long build, backup or download can be monitored by ear (or by the Vestaboard and displays) without watching a terminal.

```sh
nhj run -- make build                         # speaks only if it fails, or takes 10 s or more
nhj run --min-seconds 0 -- ./nightly-backup   # announce every result
nhj run --only-failures -- pytest -q          # silent unless it fails
nhj run --label "nightly backup" -- ./backup.sh
make build || nhj vibe err -m "The build failed"   # one-off, no wrapping (already available)
```

## What it does

- Runs the command **without a shell**, with stdin, stdout and stderr untouched, and **exits with the command's own status**. NHJ never changes a script's result: if announcing fails, the command's status still wins. Death by signal N becomes `128+N`, as in a shell. A command that cannot start gives `127` (not found) or `126` (cannot execute: permission denied, exec format error, argument list too long, ...), with a one-line message naming the program and the OS reason, never its arguments.
- `nhj run` prints nothing itself, so it is safe in pipelines and cron.
- On exit it queues one event, the same way `nhj vibe` does, so it uses your characters, displays, voice settings, `nhj mute`, and the per-project voice rules:

| Outcome | Intent | Message | When it speaks |
|---|---|---|---|
| status 0 | `ok` | `<label> finished in 12 seconds` | only if it took at least `--min-seconds` (default 10) and not `--only-failures` |
| non-zero | `err` | `<label> failed with status 3` | always |
| killed by a signal, or exit 130 / 143 | `warn` | `<label> was interrupted` | only if it took at least `--min-seconds` (pressing Ctrl-C on a quick command stays silent) |

- **Labels and privacy.** The label defaults to the command's *basename only* (`make`, never `make TOKEN=...`), because arguments can hold secrets. Neither arguments nor the command's output are ever spoken, logged or sent to a model. `--label` lets you choose the name; control characters are stripped and it is capped at 40 characters.
- Ctrl-C reaches the whole foreground group, so the command handles it itself; `nhj run` keeps waiting for it and then reports. SIGTERM and SIGHUP are forwarded to the command.

## Short spoken lines and the local model

The message above is deliberately short and factual. With dynamic lines on (`NHJ_DYNAMIC=1`, the bundled `ocker-bogan-nano` model), the character rephrases it as one short spoken line; with them off, the message is spoken as written. That model was trained on exactly this shape: a system prompt naming the character, ockerism level (1-11) and competence, then a `Status update: <short status>` line, replying with one short in-character line. It is a **rephraser of a short status, not a summariser of long text**, which is why NHJ builds the status itself instead of handing the model your command output.

Real replies on this setup, two samples each:

| Status | Replies |
|---|---|
| `backup finished in 12 minutes` (Jan) | "Backup's done, mate. Took a bit of time, but we're good to go." / "Backup done in a fair while, mate." |
| `make failed with status 3` (Karren) | "Crikey, the make failed again. Hard luck." / "Bloody hell, this is a right royal pain in the arse..." |
| `nightly backup was interrupted` (Bazza) | "Blimey, the backup got cut short, mate. Gotta get that sorted." |

Be aware that the rephrasing is stylistic and **does not reliably keep numbers or facts**: it dropped a duration, invented "again", and described a 1 hour 5 minute run as "in a hurry". If exact figures matter, leave dynamic lines off for scripts (the message is then spoken verbatim) or rely on the Vestaboard and display text.

## Not yet

`nhj say "text"` (direct speech with `--severity`, `--wait`, bounded text and status codes) is the second phase of #16. `nhj summarize --speak` would need a deterministic reduction of the output to a short status line first, for the reasons above.
