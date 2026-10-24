# Working on this repo with more than one agent

Several coding agents (and a human) can work on Not-Happy-Jan at the same time, and a running
NHJ install executes code **from a checkout of this repo**. These rules keep them from clobbering
each other and from changing a live install by accident. They apply to every agent
(Claude Code, Codex, opencode, Muse, ...) and to people.

## One working directory per agent

A directory has exactly one checked-out branch, so two agents in one directory fight over it
(one `git checkout` and the other's next commit lands on the wrong branch). Use git worktrees:
same repository, separate directories.

```bash
git fetch origin
git worktree add ../nhj-<agent> -b <agent>/<topic> origin/main     # your own, e.g. ../nhj-claude
git worktree add --detach ../nhj-live origin/main                  # the live checkout, once
```

- Edit and commit **only** in your own worktree. Never `git checkout`, `git reset` or edit files in
  someone else's.
- Name branches `<agent>/<topic>` (`claude/…`, `codex/…`, `muse/…`). Keep them short-lived.
- `main` is protected: change it only through a pull request with the required checks green.
  Never push to `main`; never force-push a branch somebody else is using.
- **Stage and commit explicit paths.** A directory has one git *index*, so anything another agent
  has staged there is committed by your `git commit`. That is how an unreviewed file ended up in a
  merged PR. In your own worktree: `git add <paths>` (not `git add -A` / `git commit -a`), read
  `git diff --cached --stat` before every commit, and prefer `git commit <paths>`.
- Give each worktree its own virtual environment (`uv venv && uv pip install -e ".[server,dev]"`):
  an editable install is tied to the directory it was made from.

## The live checkout is read-only

Hooks, the `nhj` command and any service unit should point at a **detached worktree of `origin/main`
that nobody edits**. Updating it is a deliberate step after a merge:

```bash
scripts/update-live.sh /path/to/nhj-live    # fast-forwards to origin/main, reinstalls, restarts services
```

The script refuses to run if the live checkout has local modifications. Git-ignored runtime assets
(`.env`, `audio/`, `voices/`) belong in the live checkout; copy them in once.

## Before you open a PR

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests     # CI pins ruff through uv.lock; a newer global ruff reports more
```

The opencode plugin tests need `node`. Add or update tests with the change; docs live in `docs/`
and `mkdocs build --strict` must pass.

## Shared things (not per-worktree)

| Shared | Why it matters |
|---|---|
| Runtime state (queue, music/busy state, logs) in the platform state dir | Tests may write some log lines there; do not mistake them for real alerts |
| The mixer, the Vestaboard (one update per ~15 s) and the speaker | One physical output: do not loop test events |
| Model/voice servers and their ports | Do not restart or reconfigure a server another agent is using |
| `~/.claude`, `~/.config/opencode` and other agent config | Edits change every session; back up first |

## Gotchas worth knowing

- `pkill -f "nhj.something"` matches your own shell's command line and kills it; use `pkill -f "[n]hj.something"`.
- Agent CLIs run non-interactively (`opencode run`, `codex exec`, ...) may wait forever on an open stdin pipe: redirect `</dev/null`.
- On ZFS, `du` under-reports freshly written files; check sizes with `find … -printf '%s\n'`.
- A zero-byte WAV from the TTS server with HTTP 200 means it ran out of GPU memory, not success.
