#!/usr/bin/env bash
# Build the media bundles that `nhj setup-media` downloads from the GitHub release.
#   voices.tar.gz   — the shippable character voice samples (ref.wav + ref.txt)
#   audio.tar.gz    — the audio/ tree: music (m4a) + sfx/voice/ambient clips (wav)
# Output: dist/  (gitignored). Attach both as assets on the dedicated `media` release
# (decoupled from code versions); setup-media pulls .../releases/download/media/{voices,audio}.tar.gz
#
# NOTE: clips under audio/{sfx,voice,ambient} are referenced as .wav by the code,
# so they are bundled AS-IS (do NOT re-encode them). Music in audio/music is
# already AAC .m4a. Source masters live in ../archive/ and are deliberately excluded.
#
# Usage: bash scripts/build-media-bundles.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
DIST="$ROOT/dist"; mkdir -p "$DIST"
PYTHON="${PYTHON:-python3}"

# macOS bsdtar otherwise injects `._*` AppleDouble entries (resource forks / xattrs) into
# the archive — junk metadata in the published bundle. Disable it for every tar below.
export COPYFILE_DISABLE=1

# Provenance gate (issue #52): every bundled file must be listed in the media
# manifest, and macOS metadata is rejected. Each bundle also carries the media
# license so redistribution terms travel with the assets.
validate() { "$PYTHON" "$ROOT/scripts/validate_media_bundle.py" "$@"; }

cp "$ROOT/scripts/media-license.txt" "$ROOT/MEDIA-LICENSE.txt"
trap 'rm -f "$ROOT/MEDIA-LICENSE.txt"' EXIT

# --- voices: only the ones cleared for redistribution ------------------------
# jan/bazza/karren are the core trio; jan-whispering powers special-forces mode.
VOICES=(jan bazza karren jan-whispering)
voice_paths=()
for v in "${VOICES[@]}"; do
  for f in ref.wav ref.txt; do
    [ -f "voices/$v/$f" ] && voice_paths+=("voices/$v/$f") || echo "  ! missing voices/$v/$f"
  done
done
echo "→ voices.tar.gz (${#voice_paths[@]} files + license)"
validate "${voice_paths[@]}" MEDIA-LICENSE.txt
tar czf "$DIST/voices.tar.gz" --exclude='.DS_Store' --exclude='._*' "${voice_paths[@]}" MEDIA-LICENSE.txt

# --- audio: the whole audio/ tree, as-is (music m4a + clips wav) -------------
echo "→ audio.tar.gz (music + sfx + voice samples + ambient + license)"
audio_paths=()
while IFS= read -r f; do audio_paths+=("$f"); done < <(
  # Skip macOS metadata and underscore-prefixed working dirs (_candidates, _replaced).
  find audio -type f ! -name '.DS_Store' ! -name '._*' ! -path '*/_*' | sort
)
validate "${audio_paths[@]}" MEDIA-LICENSE.txt
tar czf "$DIST/audio.tar.gz" --exclude='.DS_Store' --exclude='._*' "${audio_paths[@]}" MEDIA-LICENSE.txt

echo ""
echo "Built:"; ls -lh "$DIST"/*.tar.gz | awk '{print "  "$5"\t"$9}'
cat <<EOF

Attach to the dedicated 'media' release (decoupled from code versions):
  # first time:
  gh release create media dist/voices.tar.gz dist/audio.tar.gz --title "Media bundles" --notes "..."
  # update in place later (URL stays constant):
  gh release upload media dist/voices.tar.gz dist/audio.tar.gz --clobber
Then verify (uses .../releases/download/media by default):
  nhj setup-media --force
EOF
