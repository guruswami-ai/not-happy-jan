#!/usr/bin/env bash
# Not-Happy-Jan installer — non-interactive, profile-driven
#
# Usage:
#   bash install.sh              # default: full experience, on-demand model loading
#   bash install.sh --full       # persistent TTS + MCP services (LaunchAgents, macOS)
#   bash install.sh --minimal    # hooks + config only; no model downloads or services
#   bash install.sh --full --listen 0.0.0.0   # explicit LAN exposure of MCP server
#
# One-line public install:
#   curl -fsSL https://raw.githubusercontent.com/guruswami-ai/not-happy-jan/main/install.sh | bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PYTHON="${PYTHON:-python3}"

# A piped/downloaded installer has no checkout beside it. Fetch the selected source
# archive, then delegate to the exact same checkout installer. NHJ_INSTALL_REF can
# select a branch, release tag, or commit; main is the public-alpha default.
if [ ! -f "$ROOT/pyproject.toml" ]; then
  command -v curl >/dev/null 2>&1 || {
    echo "error: curl is required to download the Not-Happy-Jan source archive." >&2
    exit 1
  }
  command -v tar >/dev/null 2>&1 || {
    echo "error: tar is required to unpack the Not-Happy-Jan source archive." >&2
    exit 1
  }
  INSTALL_REF="${NHJ_INSTALL_REF:-main}"
  SOURCE_ARCHIVE="${NHJ_SOURCE_ARCHIVE:-https://codeload.github.com/guruswami-ai/not-happy-jan/tar.gz/$INSTALL_REF}"
  BOOTSTRAP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/not-happy-jan.XXXXXX")"
  trap 'rm -rf "$BOOTSTRAP_ROOT"' EXIT
  echo "Downloading Not-Happy-Jan source ($INSTALL_REF)..."
  curl -fsSL "$SOURCE_ARCHIVE" -o "$BOOTSTRAP_ROOT/source.tar.gz"
  mkdir -p "$BOOTSTRAP_ROOT/source"
  tar -xzf "$BOOTSTRAP_ROOT/source.tar.gz" -C "$BOOTSTRAP_ROOT/source" --strip-components=1
  if [ ! -f "$BOOTSTRAP_ROOT/source/pyproject.toml" ] ||
     [ ! -f "$BOOTSTRAP_ROOT/source/install.sh" ]; then
    echo "error: downloaded archive is not a valid Not-Happy-Jan source tree." >&2
    exit 1
  fi
  bash "$BOOTSTRAP_ROOT/source/install.sh" "$@"
  exit $?
fi

# Optional-step failures are collected here and surfaced in the final summary so a
# successful-looking 'Setup complete' never hides a feature that did not install.
FOLLOWUPS=()
note_followup() { FOLLOWUPS+=("$1"); }
require_step() {
  local label="$1"
  shift
  if ! "$@"; then
    echo "error: required installation step failed: $label" >&2
    exit 1
  fi
}

if [[ "$(uname -s)" == "Darwin" ]]; then
  APP_SUPPORT="$HOME/Library/Application Support/not-happy-jan"
else
  APP_SUPPORT="${XDG_DATA_HOME:-$HOME/.local/share}/not-happy-jan"
fi
INSTALL_ROOT="${NHJ_INSTALL_DIR:-$APP_SUPPORT/runtime}"
BIN_DIR="${NHJ_BIN_DIR:-$HOME/.local/bin}"
VENV="$INSTALL_ROOT/.venv"
VENV_PYTHON="$VENV/bin/python"
ENV_PATH="$APP_SUPPORT/.env"

# ---------------------------------------------------------------------------
# Argument parsing — no interactive prompts; all profiles are explicit flags
# ---------------------------------------------------------------------------
PROFILE="default"      # default | full | minimal
LISTEN_ADDR="127.0.0.1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --full)      PROFILE="full";    shift ;;
    --minimal)   PROFILE="minimal"; shift ;;
    --listen)
      if [[ -z "${2:-}" ]]; then
        echo "error: --listen requires an address argument (e.g. --listen 0.0.0.0)" >&2
        exit 1
      fi
      LISTEN_ADDR="$2"; shift 2 ;;
    --listen=*)  LISTEN_ADDR="${1#--listen=}"; shift ;;
    -h|--help)
      echo "Usage: bash install.sh [--full] [--minimal] [--listen <addr>]"
      echo ""
      echo "  (no flags)          Default: full experience, on-demand model loading"
      echo "  --full              Persistent TTS + MCP LaunchAgents + warm model servers (macOS)"
      echo "  --minimal           Hooks + config only; no model downloads or services"
      echo "  --listen <addr>     Bind MCP server to <addr> (requires --full;"
      echo "                      default 127.0.0.1; use 0.0.0.0 for LAN access)"
      exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Run 'bash install.sh --help' for usage." >&2
      exit 1 ;;
  esac
done

# Bootstrap uv and its managed Python on a fresh Mac. The official installer is
# non-interactive and is kept out of shell profiles; this process prepends the
# install directory only for the current run.
if ! command -v uv >/dev/null 2>&1; then
  if [[ "${NHJ_NO_BOOTSTRAP:-0}" == "1" ]]; then
    echo "error: uv is required and automatic bootstrap is disabled (NHJ_NO_BOOTSTRAP=1)." >&2
    exit 1
  fi
  command -v curl >/dev/null 2>&1 || {
    echo "error: curl is required to install the uv runtime." >&2
    exit 1
  }
  echo "Installing uv and a managed Python runtime..."
  curl -LsSf https://astral.sh/uv/install.sh |
    env UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  command -v uv >/dev/null 2>&1 || {
    echo "error: uv bootstrap completed but '$HOME/.local/bin/uv' is unavailable." >&2
    exit 1
  }
fi

# --listen without --full is a no-op with a warning
if [[ "$LISTEN_ADDR" != "127.0.0.1" && "$PROFILE" != "full" ]]; then
  echo "warning: --listen only configures the persistent MCP service bind address and requires --full." >&2
  echo "         The stdio MCP server registered for all profiles is always local. Add --full to enable the persistent service." >&2
fi

echo "=== Not-Happy-Jan setup (profile: $PROFILE) ==="

# ---------------------------------------------------------------------------
# 1. Python runtime + package
# ---------------------------------------------------------------------------
if ! [ -f "$VENV_PYTHON" ]; then
  echo "Creating venv..."
  mkdir -p "$INSTALL_ROOT"
  if command -v uv &>/dev/null; then
    uv venv --python 3.12 "$VENV"
  else
    "$PYTHON" -m venv "$VENV"
  fi
fi

# --minimal stays lean: no [server] extra (fastapi/uvicorn/soundfile/mlx-audio),
# so low-RAM and non-Apple-Silicon hosts get hooks + MCP without the TTS stack.
if [[ "$PROFILE" == "minimal" ]]; then
  PKG_SPEC="$ROOT"
else
  PKG_SPEC="$ROOT[server]"
fi

echo "Installing NHJ runtime..."
if command -v uv &>/dev/null; then
  uv pip install --python "$VENV_PYTHON" --reinstall "$PKG_SPEC"
else
  "$VENV_PYTHON" -m pip install --upgrade --force-reinstall "$PKG_SPEC"
fi

mkdir -p "$BIN_DIR"
ln -sfn "$INSTALL_ROOT/.venv/bin/nhj" "$BIN_DIR/nhj"
case ":$PATH:" in
  *:"$BIN_DIR":*) ;;
  *)
    echo "warning: $BIN_DIR is not in PATH; add it to your shell profile to run 'nhj' directly." >&2
    ;;
esac

# ---------------------------------------------------------------------------
# 2. .env
# ---------------------------------------------------------------------------
if ! [ -f "$ENV_PATH" ]; then
  mkdir -p "$APP_SUPPORT"
  if [ -f "$ROOT/.env" ]; then
    cp "$ROOT/.env" "$ENV_PATH"
    echo "Migrated existing .env to the user config directory."
  else
    cp "$ROOT/.env.example" "$ENV_PATH"
  fi
  echo "Created .env — edit it with your device IPs and API keys."
fi

# --minimal installs no TTS model or [server] extra, so configure text-only
# operation. (Idempotent: applied on every minimal run, even an upgrade.)
if [[ "$PROFILE" == "minimal" ]]; then
  if grep -q '^TTS_ENGINE=' "$ENV_PATH"; then
    _tmp_env="$(mktemp)"
    sed 's/^TTS_ENGINE=.*/TTS_ENGINE=none/' "$ENV_PATH" > "$_tmp_env" && mv "$_tmp_env" "$ENV_PATH"
  else
    echo "TTS_ENGINE=none" >> "$ENV_PATH"
  fi
  echo "Minimal profile — set TTS_ENGINE=none (text-only; no model required)."
fi

# ---------------------------------------------------------------------------
# 3. Model download + service wiring — skipped for --minimal
# ---------------------------------------------------------------------------
if [[ "$PROFILE" != "minimal" ]]; then
  # 3a. Qwen3-TTS model download (Apple Silicon only)
  if [[ "$(uname -m)" == "arm64" ]]; then
    echo "Downloading Qwen3-TTS-0.6B-8bit model (on-demand loading enabled)..."
    "$VENV_PYTHON" -c "
from nhj.tts_server import _configure_hf_cache, _migrate_legacy_models
_configure_hf_cache(); _migrate_legacy_models()   # download into NHJ's managed cache; reuse any existing model
from mlx_audio.tts import load
print('Downloading mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit ...')
load('mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit', lazy=False)
print('Done.')
" || note_followup "TTS model download failed — re-run 'bash install.sh', or set TTS_ENGINE=none in $ENV_PATH for text-only."
  else
    echo "Apple Silicon not detected — set TTS_ENGINE=none in .env to run without voice."
    note_followup "Non-Apple-Silicon host — set TTS_ENGINE=none in $ENV_PATH (local TTS is Apple Silicon only)."
  fi

  # 3b. --full only: install persistent TTS LaunchAgent (macOS)
  if [[ "$PROFILE" == "full" && "$(uname -m)" == "arm64" ]]; then
    echo "Installing persistent TTS voice service (LaunchAgent, 127.0.0.1)..."
    "$VENV_PYTHON" -m nhj.cli install-tts \
      || note_followup "Persistent TTS service not installed — run 'nhj install-tts' to enable auto-start."
  fi

  # 4. Bundled media — character voices + audio (from the GitHub release; ~250 MB).
  #    OPTIONAL: NHJ runs without it (text/markers/hardware still fire). Never abort
  #    the install on a media fetch failure (offline, gated release, transient 404).
  echo "Downloading bundled media (voices + hold music)..."
  "$VENV_PYTHON" -m nhj.cli setup-media \
    || note_followup "Bundled media not fetched (voices + hold music) — run 'nhj setup-media' later; NHJ runs without it."

  # 4a. Dynamic LLM — ocker-bogan-nano (the full default experience: fresh in-character
  #     lines instead of the fixed bank). Needs llama.cpp; optional — the bundled bank
  #     still works without it. (--minimal skips this entire block.)
  echo "Installing dynamic LLM (ocker-bogan-nano)..."
  "$VENV_PYTHON" -m nhj.cli install-model \
    || note_followup "Dynamic LLM not installed — 'brew install llama.cpp' then 'nhj install-model' for freshly-generated lines (the bundled voice bank works without it)."

  # 4b. Model-server mode: default loads on demand + idle-unloads (no resident RAM
  #     at rest); --full keeps the daemons warm. Either is switchable later via
  #     'nhj servers on-demand' / 'nhj servers persistent'.
  if [[ "$PROFILE" == "full" ]]; then
    echo "Setting model servers to persistent (always warm)..."
    "$VENV_PYTHON" -m nhj.cli servers persistent \
      || note_followup "Could not set model servers to persistent — run 'nhj servers persistent'."
  else
    echo "Setting model servers to on-demand (load on first vibe, unload after idle)..."
    "$VENV_PYTHON" -m nhj.cli servers on-demand \
      || note_followup "Could not set model servers to on-demand — run 'nhj servers on-demand'."
  fi
fi

# ---------------------------------------------------------------------------
# 5. Claude Code hooks (all profiles)
# ---------------------------------------------------------------------------
echo "Installing Claude Code hooks..."
require_step "Claude Code hooks" "$VENV_PYTHON" -m nhj.cli install-hook

# Teach Claude to EMIT the [Jan:ok|…] markers the Stop hook speaks — without this the
# hooks never fire from normal activity (managed, idempotent block in user CLAUDE.md).
echo "Installing task-status markers (CLAUDE.md)..."
require_step "Claude markers" "$VENV_PYTHON" -m nhj.cli install-markers

# Install the Claude Code skill for wheel/non-checkout use.
echo "Installing Claude Code skill..."
require_step "Claude Code skill" "$VENV_PYTHON" -m nhj.cli install-skill

# ---------------------------------------------------------------------------
# 6. MCP server registration (all profiles — stdio, localhost-only by default)
# ---------------------------------------------------------------------------
echo "Registering NHJ MCP server with Claude Code (stdio transport, local)..."
require_step "MCP registration" "$VENV_PYTHON" -m nhj.cli install-mcp

# ---------------------------------------------------------------------------
# 7. --full: persistent MCP service; loopback unless network access is explicit
# ---------------------------------------------------------------------------
if [[ "$PROFILE" == "full" ]]; then
  echo "Installing persistent MCP service on $LISTEN_ADDR:8765..."
  require_step "persistent MCP service" \
    "$VENV_PYTHON" -m nhj.cli install-mcp-service --listen "$LISTEN_ADDR" --port 8765
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Setup complete (profile: $PROFILE) ==="
echo "  Runtime:       $INSTALL_ROOT"
echo "  Command:       $BIN_DIR/nhj"
echo "  Config (.env): $ENV_PATH"
echo "  Test:          nhj test ok"
echo "  List voices:   nhj voices"
echo "  List devices:  nhj devices"
echo "  MCP server:    stdio, registered with Claude Code (spawned per session, local)"
echo "  Voice bank:    bundled — characters speak in-character with no model needed"
if [[ "$PROFILE" != "minimal" ]]; then
  echo "  Build bank:    nhj build-bank --voice jan   # add your own pre-rendered lines"
fi
if [[ "$PROFILE" == "full" ]]; then
  echo "  TTS service:   LaunchAgent on 127.0.0.1:9992 (persistent, KeepAlive)"
  echo "  MCP service:   LaunchAgent on $LISTEN_ADDR:8765 (persistent, streamable-http)"
  echo "  Model servers: persistent (kept warm)"
  if [[ "$LISTEN_ADDR" != "127.0.0.1" ]]; then
    echo "  MCP network:   streamable-http bound to $LISTEN_ADDR (explicit opt-in — keep your firewall up)"
  fi
fi
echo ""
if [[ ${#FOLLOWUPS[@]} -gt 0 ]]; then
  echo "Follow-up required (optional features that did not install):"
  for item in "${FOLLOWUPS[@]}"; do
    echo "  - $item"
  done
  echo ""
fi
if [[ "$PROFILE" == "minimal" ]]; then
  echo "Minimal install — alerting system: hooks + MCP + the bundled voice bank."
  echo "  The cast speaks pre-recorded in-character lines (no model, no downloads)."
  echo "  Upgrade to the full experience (live TTS + dynamic LLM + hold music):"
  echo "                 bash install.sh         (default: the full experience)"
  echo "                 bash install.sh --full  (+ persistent MCP service)"
else
  echo "Add your voice reference files:"
  echo "  voices/<name>/ref.wav  — short (~5-10s) clean audio sample"
  echo "  voices/<name>/ref.txt  — verbatim transcript of ref.wav"
fi
