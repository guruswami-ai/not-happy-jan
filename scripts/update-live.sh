#!/usr/bin/env bash
# Move a LIVE (production) checkout to the current origin/main and restart what runs from it.
#
# The live checkout is a detached worktree that nobody edits; hooks, the `nhj` command and the
# service units all point at it. Develop in other worktrees, merge by PR, then run this.
#   scripts/update-live.sh /path/to/nhj-live
set -euo pipefail

LIVE_ARG="${1:?usage: update-live.sh /path/to/live-checkout}"
LIVE="$(cd "$LIVE_ARG" && pwd -P)"          # absolute BEFORE any cd: later paths are built from it
cd "$LIVE"

# Never touch a checkout somebody is working in: the live one is detached by construction.
if [ "$(git rev-parse --abbrev-ref HEAD)" != "HEAD" ]; then
  echo "error: $LIVE is on a branch; the live checkout must already be detached." >&2
  echo "       (first-time setup: git worktree add --detach <dir> origin/main; see AGENTS.md)" >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "error: $LIVE has local modifications; the live checkout must stay clean." >&2
  exit 1
fi

git fetch -q origin
before="$(git rev-parse --short HEAD)"
git checkout -q --detach origin/main
after="$(git rev-parse --short HEAD)"

if [ "$before" = "$after" ]; then
  echo "live checkout already at origin/main ($after)"
else
  echo "live checkout: $before -> $after"
  # --max-count limits inside git; piping to `head` would SIGPIPE git and abort under pipefail
  git --no-pager log --oneline --max-count=20 "$before..$after"
fi

# Dependencies can change between commits; the install is editable, so code is picked up already.
if [ -x "$LIVE/.venv/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv pip install -q --python "$LIVE/.venv/bin/python" -e "$LIVE[server,audio,haptic,displays]"
  else
    "$LIVE/.venv/bin/python" -m pip install -q -e "$LIVE[server,audio,haptic,displays]"
  fi
else
  echo "note: no $LIVE/.venv; skipped the dependency install" >&2
fi

# Long-lived processes keep the old code until restarted (the worker respawns on demand).
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-enabled nhj-mcp.service >/dev/null 2>&1; then
  systemctl --user restart nhj-mcp.service && echo "restarted nhj-mcp.service"
fi
if command -v launchctl >/dev/null 2>&1 && launchctl print "gui/$(id -u)/com.guruswami.nhj-mcp" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/com.guruswami.nhj-mcp" && echo "restarted com.guruswami.nhj-mcp"
fi

# The mixer renames itself (setproctitle: "NHJ muzak"), so `pkill -f nhj.inference_muzak` cannot find it.
# Stop the controller recorded in the music state, but only if that pid still looks like the mixer
# (a recycled pid must not be killed). It respawns on the next audio event.
if [ -x "$LIVE/.venv/bin/python" ]; then
  pid="$("$LIVE/.venv/bin/python" -c 'from nhj import inference_muzak as m; print(m._read().get("controller_pid") or "")' 2>/dev/null || true)"
  if [ -n "$pid" ] && ps -o args= -p "$pid" 2>/dev/null | grep -Eq 'NHJ muzak|nhj\.inference_muzak'; then
    kill "$pid" && echo "stopped the mixer (pid $pid); it restarts on the next event"
  fi
fi
echo "done. Verify with: $LIVE/.venv/bin/nhj voice status"
