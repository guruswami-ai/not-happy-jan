# Multi-agent orchestration: when does the music play, and who speaks?

Real use is rarely one agent. You might have an **orchestrator** session that fans work out to
several **workers** (other sessions, background jobs, `claude -p` runs), plus unrelated sessions
in other projects. NHJ has to answer two questions: *when is the hold music on?* and *who is
allowed to talk over it?*

## The model

- A session is **busy** from `UserPromptSubmit` until its `Stop` (or `StopFailure` / `SessionEnd`).
- The hold music plays while **any** session is busy, and pauses when **all** are idle
  (ref-counted across sessions, with a locked read-modify-write so concurrent hooks cannot race).
- A busy session records its agent's process id (Linux). If the agent dies without running its
  Stop hook, the entry is evicted as soon as the process is gone, not after the 30-minute TTL.
- Claude Code **subagents** run inside their parent session and do not fire `Stop`, so they never
  speak on their own; only the parent's final marker does.

## The problem this policy solves

Without a policy, every worker that finishes speaks its result (preceded by "you still there?")
while the orchestrator, the call you are actually on hold for, is still going. The music is
ducked and interrupted once per worker.

## The policy: quiet while busy

| Event | Another session still busy | Nobody else busy |
|---|---|---|
| `ok`, `celebrate`, `step` | soft **chime** + silent channels; counted for the digest | **speak** (adds "N other tasks finished while you waited" if any were chimed) |
| `warn`, `err`, `attn` | **speak** (a human is needed) | **speak** |

- The silent channels (Vestaboard, displays, haptics) fire for every event regardless.
- The "you still there?" cue plays only if the music had actually paused.
- The digest is spoken by the next completion that is allowed to speak, normally the orchestrator's.

```text
t=0   orchestrator starts           -> hold music
t=7   worker 1 finishes             -> chime, board "ALL DONE worker-1"
t=12  worker 2 finishes             -> chime, board "ALL DONE worker-2"
t=19  orchestrator finishes         -> music stops, "The whole batch is finished.
                                       2 other tasks finished while you waited."
```

## Settings

```yaml
orchestration:
  mode: quiet-while-busy      # or always-speak (upstream behaviour: every event speaks)
  chime: ""                   # a file or directory; default audio/sfx/buttons
```

`NHJ_ORCHESTRATION=always-speak` overrides per process. To silence one project entirely (voice
and music) use `nhj voice off --project NAME`; `nhj mute` silences everything.

## Known limits and open questions

- **Blocked on you.** A session waiting at a permission prompt is still "busy", so the music keeps
  playing while the attn alert speaks over it. Pausing on a prompt is easy; resuming when you
  approve needs a per-tool-call signal (a `PostToolUse` hook) which costs a process start per tool
  call. Not enabled by default.
- **Unrelated sessions.** The policy cannot tell an orchestrated worker from an independent
  session in another project: both are chimed while anything else is busy. Use `nhj voice on --project X`
  plus `always-speak` if you want specific projects to always announce.
- **Who owns the music?** It is global, not per orchestrator. A per-orchestrator "call" (one hold
  music bed per group of sessions) would need sessions to declare a parent; not implemented.
