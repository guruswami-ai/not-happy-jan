#!/usr/bin/env bash
# Move a LIVE (production) checkout to the current origin/main and restart what runs from it.
#
# The live checkout is a detached worktree that nobody edits; hooks, the `nhj` command and the
# systemd unit all point at it. Develop in other worktrees, merge by PR, then run this.
#   scripts/update-live.sh /path/to/nhj-live
set -euo pipefail

LIVE="${1:?usage: update-live.sh /path/to/live-checkout}"
cd "$LIVE"

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
  git --no-pager log --oneline "$before..$after" | head -20
fi

# Dependencies can change between commits; the install is editable, so code is picked up already.
if command -v uv >/dev/null 2>&1; then
  uv pip install -q --python "$LIVE/.venv/bin/python" -e "$LIVE[server]"
else
  "$LIVE/.venv/bin/python" -m pip install -q -e "$LIVE[server]"
fi

# Long-lived processes keep the old code until restarted (the worker and mixer respawn on demand).
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-enabled nhj-mcp.service >/dev/null 2>&1; then
  systemctl --user restart nhj-mcp.service
  echo "restarted nhj-mcp.service"
fi
pkill -f "[n]hj.inference_muzak" 2>/dev/null || true
echo "done. Verify with: $LIVE/.venv/bin/nhj voice status"
