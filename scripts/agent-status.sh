#!/usr/bin/env bash
# Who is working on what: worktrees (presence), agent claims, open PRs, and duplicate-work warnings.
#   scripts/agent-status.sh            # uses the repo of the current directory
set -uo pipefail
REPO="${NHJ_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
dups=()

echo "== worktrees (every agent works in its own) =="
declare -A seen_issue
slugs=()   # "agent|slug" for every agent-prefixed branch, to compare across agents
while read -r path branch; do
  [ -z "$path" ] && continue
  dirty=$(git -C "$path" status --short 2>/dev/null | grep -vc '^??' || true)
  last=$(git -C "$path" log -1 --format='%cr' 2>/dev/null || echo "?")
  printf '  %-34s %-34s %s modified, last commit %s\n' "$path" "${branch:-(detached)}" "$dirty" "$last"
  if [[ "$branch" =~ ^([a-z]+)/(.+)$ ]]; then
    slug="${BASH_REMATCH[2]}"; slug="${slug#[0-9]*-}"; slugs+=("${BASH_REMATCH[1]}|$slug|$branch")
  fi
  if [[ "$branch" =~ ^[a-z]+/([0-9]+)- ]]; then
    n="${BASH_REMATCH[1]}"
    [ -n "${seen_issue[$n]:-}" ] && dups+=("issue #$n is on two branches: ${seen_issue[$n]} and $branch")
    seen_issue[$n]="$branch"
  fi
done < <(git worktree list --porcelain | awk '/^worktree /{p=$2} /^branch /{b=$2; sub("refs/heads/","",b)} /^$/{if(p){print p, b}; p=""; b=""} END{if(p)print p, b}')

# Two different agents on branches whose slugs contain one another are probably doing the same job.
for a in "${slugs[@]:-}"; do for b in "${slugs[@]:-}"; do
  [ -z "$a" ] && continue
  IFS='|' read -r aa sa ba <<<"$a"; IFS='|' read -r ab sb bb <<<"$b"
  [[ "$ba" < "$bb" && "$aa" != "$ab" && ${#sa} -ge 6 && ${#sb} -ge 6 && ( "$sa" == *"$sb"* || "$sb" == *"$sa"* ) ]] \
    && dups+=("branches look like the same work: $ba and $bb")
done; done

echo; echo "== claims (issues labelled agent:*) =="
gh issue list -R "$REPO" --state open --json number,title,labels \
  --jq '.[]|select([.labels[].name|startswith("agent:")]|any)|"  #\(.number) \([.labels[].name|select(startswith("agent:"))]|join(",")) \(.title[0:60])"'
multi=$(gh issue list -R "$REPO" --state open --json number,labels \
  --jq '.[]|select([.labels[].name|select(startswith("agent:"))]|length>1)|"issue #\(.number) is claimed by more than one agent"')
while IFS= read -r l; do [ -n "$l" ] && dups+=("$l"); done <<<"$multi"

echo; echo "== open PRs and review state =="
gh pr list -R "$REPO" --json number,title,headRefName,labels,statusCheckRollup \
  --jq '.[]|"  #\(.number) \(.headRefName) | \(.title[0:50]) | labels: \([.labels[].name]|join(",")) | checks: \([.statusCheckRollup[]?.conclusion]|map(select(.!=null))|group_by(.)|map("\(length) \(.[0])")|join(","))"'

echo
if [ ${#dups[@]} -gt 0 ]; then
  echo "!! possible duplicate work:"; printf '   - %s\n' "${dups[@]}"
else
  echo "no duplicate claims detected (name branches <agent>/<issue>-<slug> so they can be matched)"
fi
