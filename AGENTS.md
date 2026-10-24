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

## Collaboration protocol (GitHub is the shared board)

All agents act through the **same GitHub account**, so authorship does not say who did what. The
rules below make that explicit and keep two agents from doing the same work.

1. **Tag everything.** Start every issue comment, PR body and review with the agent's tag:
   `[claude]`, `[muse]`, `[grok]`, ... Untagged comments are assumed to be the maintainer's.
2. **Look, then claim.** Run `scripts/agent-status.sh` first (worktrees, claims, open PRs, duplicate
   warnings). To claim an issue, comment `[agent] Claiming. Branch: <agent>/<issue>-<slug>` **and**
   add the label `agent:<name>`. The first claim wins. A claim with no pushed branch after 24 h
   lapses; anyone may take it over after saying so on the issue.
3. **One issue, one branch, one PR.** Branch `<agent>/<issue>-<slug>` (the number lets tools spot
   duplicates), PR body `Fixes #N`, rebased on `origin/main` before review is requested.
4. **Every PR gets a cross-review by a different agent** before the maintainer merges. The author
   adds `needs-review:<other-agent>`; the reviewer posts a review comment (format below) and adds
   `reviewed`. GitHub will not let one account approve its own PR, so the comment plus the label
   *is* the signal. Do not review your own PR.
5. **Review format:**

   ```
   [reviewer] Review of <commit sha>
   Verdict: approve | changes-requested | comment
   Checked: what you read or ran (tests, lint, a live check), in your own worktree
   Findings: P1/P2/P3 · file:line · what is wrong and why · suggested fix
   ```

   Review the diff at a specific commit; run the tests in a detached worktree of the branch
   (`git worktree add --detach ../nhj-<you>-review origin/<branch>`). **Never push to the author's
   branch**: leave findings, or open a PR against it.
6. **Agents never merge to `main`.** The maintainer does, once CI is green and a cross-review exists.
7. **Overlapping files.** If your change touches code an open claim or PR also touches, say so on the
   issue and agree an order; whoever merges second rebases and re-runs the tests.
8. **Disagreements** go to the maintainer: label `blocked`, comment with both positions, stop work on
   the disputed part.
