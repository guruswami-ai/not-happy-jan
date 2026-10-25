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

Real replies on this setup, two samples each, **before** the fact guard (#87):

| Status | Replies (unguarded) |
|---|---|
| `backup finished in 12 minutes` (Jan) | "Backup's done, mate. Took a bit of time, but we're good to go." / "Backup done in a fair while, mate." |
| `make failed with status 3` (Karren) | "Crikey, the make failed again. Hard luck." / "Bloody hell, this is a right royal pain in the arse..." |
| `nightly backup was interrupted` (Bazza) | "Blimey, the backup got cut short, mate. Gotta get that sorted." |

`pytest finished in 1 hour 5 minutes` came back as "the tests done in a hurry, mate."

**After the guard.** Spoken lines must keep every digit and number-word from the status (`12`, `3`, `1` and `5`); display headlines may drop a number but must not change it. A miss retries once at temperature 0.3, then NHJ falls back to the static phrase bank (still spoken). Measured against those same six replies: both backup samples and both make samples are rejected; the interrupted samples have no numbers, so they still pass; "in a hurry" is rejected. The fallback is the existing static bank, so the event is still spoken. Leave dynamic lines off if you need the status spoken verbatim.

## `nhj say`: speak exactly this text

```sh
nhj say "Deploy complete"                              # info
nhj say --severity error --character karren "The build failed on node 3"
make build || nhj say -s error "The build failed"
echo "backup finished" | nhj say -                     # "-" reads the words from stdin
nhj say --wait --timeout 20 "Tests passed"             # block until the worker has dispatched it
```

The words are the ones you give and nothing else: the arguments, or stdin only when the single argument is `-`. Command output, other arguments and the environment are never spoken. The character picks the **voice and delivery, never the words**: a `say` event is *literal*, so it skips the dynamic LLM rewrite, the phrase templates, garbling, the quiet-while-busy chime and the "N other tasks finished" digest, and it appears unedited on the display matrix. The user's `censor` setting still applies, because that is an output policy, not characterisation. `--rewrite` opts back in to the character's own rendering.

| Option | Meaning |
|---|---|
| `--severity, -s` | `success`, `info` (default), `warning`, `error`, `attention`, `celebrate`; they map onto the event intents `ok`, `step`, `warn`, `err`, `attn`, `celebrate`, which are also accepted |
| `--character, -c` | `jan`, `bazza` or `karren` (default: chosen by severity) |
| `--rewrite` | let the character rephrase it instead of speaking it exactly |
| `--wait`, `--timeout S` | wait (default 30 s) until the worker has finished with the event; the last words may still be playing |
| `--strict` | fail (status 10) when it cannot be spoken at all |
| `--max-chars N` | refuse longer text; default 240, never more than 500 (the speech server's own limit, and a literal line has no fallback) |

Text is cleaned (control characters become spaces, whitespace collapsed) and **refused, never truncated**, when it is over the limit. stdin is read through a bounded window (2000 characters including blanks) and the whole input is refused if that fills up, so nothing after it can be silently dropped. The words are never written to NHJ's logs (the queue file holds them until the worker has used them).

**If it cannot be spoken** (NHJ is muted, voice is off for this project, or no speech engine is configured), the text is printed to **stderr** instead, so a muted or headless run does not lose the message. Whether anything else still happens depends on why: if NHJ is **muted** (`nhj mute`), the worker drops the event on every channel, so stderr is all you get; if **voice is off for the project** or **no speech engine** is configured, the event is still queued and reaches the displays and the board. A literal `say` never falls back to a generic bundled clip or plays the "you still there?" cue first, because those would be words you did not give; if the exact text cannot be synthesised (and is not already cached), it is simply not spoken.

| Status | Meaning |
|---|---|
| `0` | delivered: queued (and, with `--wait`, dispatched). A muted call that printed the text also exits 0 |
| `2` | usage or bounds error (bad severity, empty or over-long text); nothing was queued |
| `10` | could not be delivered: the queue failed, the worker rejected the event, or `--strict` and it cannot be spoken |
| `124` | `--wait` timed out (like `timeout(1)`) |

Shell quoting is yours: `nhj say "$(cat secrets)"` will queue whatever the shell expands, and arguments are visible in `ps` while the command runs; use `-` and a pipe for anything sensitive.

## Not yet

`nhj summarize --speak` would need a deterministic reduction of the output to a short status line first, for the reasons above.
