#!/usr/bin/env bash
#
# go-public.sh — publish Not-Happy-Jan as a CLEAN public repository.
#
# A visibility flip would retroactively expose ALL commit history, every PR + review
# thread, and every issue — including internal infrastructure references. Instead this:
#   1. renames the current repo to a PRIVATE archive (full history/PRs/issues preserved),
#   2. creates a fresh PUBLIC repo from a single squashed snapshot of `main`,
#   3. re-creates the `media` release on the public repo,
#   4. enables private vulnerability reporting + branch protection (CI + secret-scan).
#
# Irreversible + outward-facing (this is the moment of going public). Requires a `gh`
# token with repo admin + repo-creation scope. No sudo. Refuses to run without --confirm.
#
#   bash scripts/go-public.sh --confirm
#
set -euo pipefail

[[ "${1:-}" == "--confirm" ]] || {
  echo "This publishes Not-Happy-Jan to the world. Re-run with --confirm once every"
  echo "non-flip item in docs/public-release-checklist.md is green."
  exit 2
}

OWNER="guruswami-ai"
NAME="not-happy-jan"
PUB="$OWNER/$NAME"
ARCHIVE_NAME="${NAME}-private"
ARCHIVE="$OWNER/$ARCHIVE_NAME"
DESC="Multi-sensory agent feedback for Claude Code — haptics, visual displays, and TTS personality characters"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

echo "==> Pre-flight"
gh auth status >/dev/null
gh release view media --repo "$PUB" >/dev/null 2>&1 \
  || { echo "FATAL: no 'media' release on $PUB to copy forward — aborting before any change."; exit 1; }

echo "==> 1/6  Archive: rename $PUB -> $ARCHIVE (stays PRIVATE; keeps all history/PRs/issues)"
gh repo rename "$ARCHIVE_NAME" --repo "$PUB" --yes

echo "==> 2/6  Pull the media release assets from the archive"
mkdir -p "$WORK/media"
gh release download media --repo "$ARCHIVE" --dir "$WORK/media"

echo "==> 3/6  Build a single-commit snapshot of main (no history, no infra leak)"
git clone -q --branch main --single-branch "https://github.com/$ARCHIVE.git" "$WORK/src"
git -C "$WORK/src" checkout -q --orphan publicmain
git -C "$WORK/src" add -A
git -C "$WORK/src" commit -q -m "Initial public alpha release

Not-Happy-Jan — multi-sensory agent feedback for Claude Code (haptics, visual
displays, and TTS personality characters). Published from a squashed snapshot;
development history is retained in a private archive."

echo "==> 4/6  Create the public repo + push the snapshot as main"
gh repo create "$PUB" --public --description "$DESC"
git -C "$WORK/src" push "https://github.com/$PUB.git" publicmain:main
gh repo edit "$PUB" --add-topic haptic --add-topic tts --add-topic claude-code \
  --add-topic mcp --add-topic agent --add-topic notification 2>/dev/null || true

echo "==> 5/6  Re-create the media release on the public repo"
gh release create media --repo "$PUB" "$WORK"/media/* \
  --title "Bundled media" \
  --notes "Voices + hold music fetched by \`nhj setup-media\`. Provenance + licensing: docs/media-provenance.md."

echo "==> 6/6  Controls: private vulnerability reporting + branch protection"
gh api --method PUT "repos/$PUB/private-vulnerability-reporting"
# enforce_admins=true → even the owner lands main changes via a PR whose checks pass.
gh api --method PUT "repos/$PUB/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["gitleaks", "package", "test (3.10)", "test (3.12)"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

cat <<NEXT

==> Public + clean. Remaining manual steps:
    - Run the literal public one-liner on a clean Mac, then uninstall:
        curl -fsSL https://raw.githubusercontent.com/$PUB/main/install.sh | bash
        nhj uninstall
    - Verify the SECURITY.md reporting link resolves on the public repo.
    - Update any local clones' remotes (the dev tree now points at the private archive).
    - CodeQL is deferred for the alpha; enable later if desired:
        gh api --method PUT repos/$PUB/code-scanning/default-setup -f state=configured

    Full development history / PRs / issues remain private at:
        https://github.com/$ARCHIVE
NEXT
