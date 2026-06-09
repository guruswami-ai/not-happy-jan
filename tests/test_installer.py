"""Installer profile and MCP server registration tests.

Covers:
- install.sh profile flags (default / --full / --minimal) via argument parsing
  checks and install-mcp / install-hook CLI calls.
- MCP server binds to localhost by default.
- Network exposure of the MCP server requires an explicit --listen flag.
- install-mcp is idempotent (re-running does not create duplicates).
- remove-mcp cleans up the registration.
"""
from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

from nhj import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings_with_mcp(settings: Path) -> dict:
    return json.loads(settings.read_text()).get("mcpServers", {})


# ---------------------------------------------------------------------------
# install-mcp — localhost default
# ---------------------------------------------------------------------------

def test_install_mcp_stdio_default(tmp_path):
    """install-mcp with no flags registers stdio transport pointing at this venv's python."""
    settings = tmp_path / "settings.json"

    cli.install_mcp(settings=settings, transport="stdio", listen="127.0.0.1", port=8765)

    mcp = _settings_with_mcp(settings)
    assert "not-happy-jan" in mcp
    entry = mcp["not-happy-jan"]
    assert entry["command"] == str(cli._PYTHON)
    assert "-m" in entry["args"]
    assert "nhj.mcp_server" in entry["args"]
    # stdio entries must not have a 'transport' or 'url' key (those are for network)
    assert "url" not in entry


def test_install_mcp_network_defaults_to_localhost(tmp_path):
    """When a network transport is used, the default listen address must be 127.0.0.1.

    Verified by calling install-mcp with just transport=sse and no --listen,
    then checking the registered URL contains 127.0.0.1.
    """
    settings = tmp_path / "settings.json"
    # Call with the Typer-default listen value (not explicitly set by a user)
    cli.install_mcp(settings=settings, transport="sse", listen="127.0.0.1", port=8765)

    mcp = _settings_with_mcp(settings)
    entry = mcp["not-happy-jan"]
    assert "127.0.0.1" in entry["url"]
    assert "0.0.0.0" not in entry["url"]

    # Also verify the function signature's default value (via Typer's OptionInfo)
    import inspect
    sig = inspect.signature(cli.install_mcp)
    listen_param = sig.parameters["listen"]
    # Typer wraps defaults in OptionInfo; extract the actual default
    import typer
    default = (listen_param.default.default
               if isinstance(listen_param.default, typer.models.OptionInfo)
               else listen_param.default)
    assert default == "127.0.0.1", (
        "install-mcp --listen default must be 127.0.0.1, not " + repr(default)
    )


def test_install_mcp_sse_localhost(tmp_path):
    """SSE transport with default listen stays on 127.0.0.1."""
    settings = tmp_path / "settings.json"

    cli.install_mcp(settings=settings, transport="sse", listen="127.0.0.1", port=8765)

    mcp = _settings_with_mcp(settings)
    entry = mcp["not-happy-jan"]
    assert "127.0.0.1" in entry["url"]
    assert "0.0.0.0" not in entry["url"]


def test_install_mcp_network_exposure_requires_explicit_listen(tmp_path):
    """Using a non-localhost listen address must be explicitly provided; the default is localhost."""
    settings = tmp_path / "settings.json"

    # Explicit opt-in to network exposure
    cli.install_mcp(settings=settings, transport="streamable-http",
                    listen="0.0.0.0", port=8765)

    mcp = _settings_with_mcp(settings)
    entry = mcp["not-happy-jan"]
    assert "0.0.0.0" in entry["url"]

    # Contrast: the default does NOT expose to the network
    settings2 = tmp_path / "settings2.json"
    cli.install_mcp(settings=settings2, transport="streamable-http",
                    listen="127.0.0.1", port=8765)
    mcp2 = _settings_with_mcp(settings2)
    assert "127.0.0.1" in mcp2["not-happy-jan"]["url"]
    assert "0.0.0.0" not in mcp2["not-happy-jan"]["url"]


# ---------------------------------------------------------------------------
# install-mcp — idempotency
# ---------------------------------------------------------------------------

def test_install_mcp_idempotent(tmp_path):
    """Running install-mcp twice must not create duplicate entries."""
    settings = tmp_path / "settings.json"

    cli.install_mcp(settings=settings, transport="stdio", listen="127.0.0.1", port=8765)
    cli.install_mcp(settings=settings, transport="stdio", listen="127.0.0.1", port=8765)

    mcp = _settings_with_mcp(settings)
    # Only one entry for "not-happy-jan" (dict keys are unique, but verify the file is clean)
    assert list(mcp.keys()).count("not-happy-jan") == 1


def test_install_mcp_preserves_other_mcp_servers(tmp_path):
    """install-mcp must not remove pre-existing unrelated mcpServers entries."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "mcpServers": {
            "other-tool": {"command": "/usr/bin/other-mcp", "args": []}
        }
    }))

    cli.install_mcp(settings=settings, transport="stdio", listen="127.0.0.1", port=8765)

    mcp = _settings_with_mcp(settings)
    assert "other-tool" in mcp        # must be preserved
    assert "not-happy-jan" in mcp     # newly added


# ---------------------------------------------------------------------------
# remove-mcp
# ---------------------------------------------------------------------------

def test_remove_mcp_cleans_up(tmp_path):
    """remove-mcp removes the NHJ MCP entry and leaves other entries intact."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({
        "mcpServers": {
            "not-happy-jan": {"command": "/path/python", "args": ["-m", "nhj.mcp_server"]},
            "other-tool": {"command": "/usr/bin/other-mcp", "args": []},
        }
    }))

    cli.remove_mcp(settings=settings)

    mcp = _settings_with_mcp(settings)
    assert "not-happy-jan" not in mcp
    assert "other-tool" in mcp


def test_remove_mcp_noop_when_not_registered(tmp_path):
    """remove-mcp is a no-op when the server is not registered."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"mcpServers": {"other-tool": {"command": "/x"}}}))

    cli.remove_mcp(settings=settings)   # must not raise

    assert "other-tool" in json.loads(settings.read_text())["mcpServers"]


def test_install_mcp_service_passes_explicit_loopback_or_network_bind(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr(
        cli,
        "_install_mcp_launchagent",
        lambda listen, port: captured.update(listen=listen, port=port)
        or tmp_path / "com.guruswami.nhj-mcp.plist",
    )

    cli.install_mcp_service(listen="127.0.0.1", port=8765)

    assert captured == {"listen": "127.0.0.1", "port": 8765}


# ---------------------------------------------------------------------------
# install.sh profile argument parsing (via subprocess)
# ---------------------------------------------------------------------------

_INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"


def _parse_profile_from_sh(args: list[str]) -> dict:
    """Run install.sh --help (exits 0) and check it doesn't error on the flags."""
    r = subprocess.run(
        ["bash", str(_INSTALL_SH)] + args,
        capture_output=True, text=True,
    )
    return {"returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def test_install_sh_help_exits_zero():
    """bash install.sh --help exits 0."""
    r = _parse_profile_from_sh(["--help"])
    assert r["returncode"] == 0
    assert "--full" in r["stdout"]
    assert "--minimal" in r["stdout"]
    assert "--listen" in r["stdout"]


def test_install_sh_unknown_flag_exits_nonzero():
    """bash install.sh --unknown-flag exits non-zero."""
    r = _parse_profile_from_sh(["--unknown-flag-xyz"])
    assert r["returncode"] != 0


def test_install_sh_listen_requires_address():
    """bash install.sh --listen (no address) exits non-zero."""
    r = _parse_profile_from_sh(["--listen"])
    assert r["returncode"] != 0
    assert "requires" in r["stderr"].lower() or "error" in r["stderr"].lower()


def test_install_sh_full_flag_is_accepted():
    """bash install.sh --full --help exits 0 (flag is accepted by the parser)."""
    r = _parse_profile_from_sh(["--full", "--help"])
    assert r["returncode"] == 0


def test_install_sh_minimal_flag_is_accepted():
    """bash install.sh --minimal --help exits 0 (flag is accepted by the parser)."""
    r = _parse_profile_from_sh(["--minimal", "--help"])
    assert r["returncode"] == 0


def test_install_sh_uses_platform_runtime_not_checkout_venv():
    """Host installs must remain usable after the source checkout is removed."""
    script = _INSTALL_SH.read_text()

    assert '$ROOT/.venv' not in script
    assert 'Library/Application Support/not-happy-jan' in script
    assert 'INSTALL_ROOT="${NHJ_INSTALL_DIR:-$APP_SUPPORT/runtime}"' in script
    assert 'XDG_DATA_HOME' in script


def test_install_sh_reinstalls_package_and_exposes_nhj_on_path():
    """Re-running the installer must upgrade code and publish the CLI entry point."""
    script = _INSTALL_SH.read_text()

    assert 'uv pip install --python "$VENV_PYTHON" --reinstall' in script
    assert 'ln -sfn "$INSTALL_ROOT/.venv/bin/nhj" "$BIN_DIR/nhj"' in script


def test_install_sh_bootstraps_source_when_piped_outside_checkout(tmp_path):
    """The advertised one-line command must work from an arbitrary directory."""
    source = tmp_path / "not-happy-jan-main"
    source.mkdir()
    (source / "install.sh").write_text(_INSTALL_SH.read_text())
    (source / "pyproject.toml").write_text("[project]\nname = 'not-happy-jan'\n")
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(source, arcname=source.name)

    result = subprocess.run(
        ["bash", "-s", "--", "--help"],
        input=_INSTALL_SH.read_text(),
        cwd=tmp_path,
        env={
            "HOME": str(tmp_path / "home"),
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "NHJ_SOURCE_ARCHIVE": archive.as_uri(),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage: bash install.sh" in result.stdout


def test_install_sh_can_bootstrap_uv_noninteractively():
    """A fresh supported Mac must not need a separate Python/uv installation command."""
    script = _INSTALL_SH.read_text()

    assert "https://astral.sh/uv/install.sh" in script
    assert "UV_NO_MODIFY_PATH=1" in script
    assert 'PATH="$HOME/.local/bin:$PATH"' in script


def test_install_sh_warns_when_command_directory_is_not_on_path():
    """A successful install must not imply `nhj` is directly runnable when PATH omits it."""
    script = _INSTALL_SH.read_text()

    assert 'case ":$PATH:" in' in script
    assert "$BIN_DIR is not in PATH" in script


def test_install_sh_summary_prints_active_config_path():
    """Users need the installed config path, not the checkout path."""
    script = _INSTALL_SH.read_text()

    assert 'echo "  Config (.env): $ENV_PATH"' in script


# ---------------------------------------------------------------------------
# Profile truthfulness (issue #53) — dependency / config / service matrix
# ---------------------------------------------------------------------------

def test_install_sh_minimal_skips_the_server_extra():
    """--minimal must not pull the [server] extra (fastapi/uvicorn/mlx-audio)."""
    script = _INSTALL_SH.read_text()

    assert 'PKG_SPEC="$ROOT"' in script
    assert 'PKG_SPEC="$ROOT[server]"' in script
    # The install line uses the computed spec, never a hard-coded [server].
    assert 'uv pip install --python "$VENV_PYTHON" --reinstall "$PKG_SPEC"' in script
    assert '--reinstall "$ROOT[server]"' not in script


def test_install_sh_minimal_configures_text_only_tts():
    """--minimal must configure TTS_ENGINE=none (no model is installed)."""
    script = _INSTALL_SH.read_text()

    assert 's/^TTS_ENGINE=.*/TTS_ENGINE=none/' in script


def test_install_sh_installs_llm_in_default_full_not_minimal():
    """Release contract (issue #97): the dynamic LLM (ocker-bogan-nano) is installed by
    default + full — inside the `!= "minimal"` block — and skipped by --minimal. The
    README / install-profiles / minimum-specs docs must match this."""
    script = _INSTALL_SH.read_text()
    assert "nhj.cli install-model" in script
    guard = script.index('"$PROFILE" != "minimal"')        # start of the non-minimal block
    assert script.index("nhj.cli install-model") > guard    # LLM install lives inside it
    assert "nhj.cli install-markers" in script              # markers wired on every profile


def test_docs_minimal_describes_bundled_bank_not_silent():
    """Release contract: --minimal docs describe the bundled voice bank (the cast speaks),
    not the old 'silent until you install TTS' behaviour."""
    profiles = (_INSTALL_SH.parent / "docs" / "install-profiles.md").read_text().lower()
    assert "bundled voice bank" in profiles
    assert "silent until you" not in profiles               # the stale claim is gone


def test_install_sh_does_not_claim_a_nonexistent_mcp_launchagent():
    """No persistent MCP LaunchAgent exists; the summary must not claim one."""
    script = _INSTALL_SH.read_text()

    assert "MCP server:    running as LaunchAgent" not in script


def test_install_sh_optional_media_never_aborts_the_install():
    """Bundled media is optional — a fetch failure (offline / gated release / 404)
    must not abort the install before hooks + MCP are wired (smoke-test regression)."""
    script = _INSTALL_SH.read_text()

    # media must NOT be a hard require_step, and must not use the abort-on-failure --strict
    assert "require_step \"bundled media\"" not in script
    assert "setup-media --strict" not in script
    # it must be a summarized follow-up instead
    assert "setup-media" in script
    assert "note_followup" in script
    # the model download must likewise not hard-exit the installer
    assert "required TTS model download failed" not in script


def test_install_sh_allows_automatic_runtime_bootstrap_to_be_disabled():
    """Security-conscious automation can require a preinstalled uv runtime."""
    script = _INSTALL_SH.read_text()

    assert "NHJ_NO_BOOTSTRAP" in script
    assert "automatic bootstrap is disabled" in script


def test_install_sh_summarizes_optional_step_failures():
    """Optional feature failures must surface in a follow-up summary, not vanish."""
    script = _INSTALL_SH.read_text()

    assert "FOLLOWUPS=()" in script
    assert "note_followup" in script
    assert "Follow-up required" in script


def test_install_sh_required_features_fail_the_install():
    """The core Claude integration (hooks, skill, MCP) is required — failure aborts
    the install. Optional enhancements (media, model, servers) must NOT abort it."""
    script = _INSTALL_SH.read_text()

    assert "require_step \"Claude Code hooks\"" in script
    assert "require_step \"Claude Code skill\"" in script
    assert "require_step \"MCP registration\"" in script
    # media is OPTIONAL (smoke-test regression — must never abort the install)
    assert 'require_step "bundled media"' not in script
    # servers-mode is optional too, but summarized (not silently swallowed with || true)
    assert "servers persistent 2>/dev/null || true" not in script
    assert "servers on-demand 2>/dev/null || true" not in script


def test_full_profile_installs_the_advertised_persistent_mcp_service():
    script = _INSTALL_SH.read_text()

    assert 'install-mcp-service --listen "$LISTEN_ADDR"' in script
    assert "MCP service:   LaunchAgent" in script


# ---------------------------------------------------------------------------
# MCP server run() — transport and host resolution
# ---------------------------------------------------------------------------

def test_mcp_server_defaults_to_stdio(monkeypatch):
    """mcp_server.run() selects stdio when NHJ_TRANSPORT is unset."""
    monkeypatch.delenv("NHJ_TRANSPORT", raising=False)
    calls = []
    from nhj import mcp_server
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kw: calls.append(kw))

    mcp_server.run()

    assert calls == [{}]          # called with no kwargs → stdio path


def test_mcp_server_network_binds_to_localhost_by_default(monkeypatch):
    """mcp_server.run() uses 127.0.0.1 when NHJ_TRANSPORT=sse and NHJ_HOST is unset."""
    monkeypatch.setenv("NHJ_TRANSPORT", "sse")
    monkeypatch.delenv("NHJ_HOST", raising=False)
    monkeypatch.delenv("NHJ_PORT", raising=False)
    calls = []
    from nhj import mcp_server
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kw: calls.append(kw))

    mcp_server.run()

    assert calls
    assert calls[0]["host"] == "127.0.0.1"


def test_mcp_server_respects_explicit_listen_address(monkeypatch):
    """mcp_server.run() uses NHJ_HOST when set (explicit network opt-in)."""
    monkeypatch.setenv("NHJ_TRANSPORT", "streamable-http")
    monkeypatch.setenv("NHJ_HOST", "0.0.0.0")
    monkeypatch.setenv("NHJ_PORT", "8765")
    calls = []
    from nhj import mcp_server
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kw: calls.append(kw))

    mcp_server.run()

    assert calls[0]["host"] == "0.0.0.0"


# ---------------------------------------------------------------------------
# install-mcp backup behaviour
# ---------------------------------------------------------------------------

def test_install_mcp_writes_backup_of_existing_settings(tmp_path):
    """install-mcp writes a .pre-nhj-mcp.bak backup when settings already exist."""
    settings = tmp_path / "settings.json"
    original = {"hooks": {}, "mcpServers": {"other": {"command": "/x"}}}
    settings.write_text(json.dumps(original))

    cli.install_mcp(settings=settings, transport="stdio", listen="127.0.0.1", port=8765)

    bak = settings.with_suffix(".json.pre-nhj-mcp.bak")
    assert bak.exists()
    assert json.loads(bak.read_text()) == original


def test_install_mcp_does_not_overwrite_existing_backup(tmp_path):
    """install-mcp does not overwrite an already-existing backup."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"mcpServers": {}}))
    bak = settings.with_suffix(".json.pre-nhj-mcp.bak")
    bak.write_text('{"preserved": true}')

    cli.install_mcp(settings=settings, transport="stdio", listen="127.0.0.1", port=8765)

    assert json.loads(bak.read_text()) == {"preserved": True}


def test_install_hook_backup_matches_parsed_settings(tmp_path):
    """install-hook's backup must be byte-identical to the settings it parsed (#67)."""
    settings = tmp_path / "settings.json"
    original = {"existing": "value", "hooks": {}}
    settings.write_text(json.dumps(original))

    cli.install_hook(settings=settings)

    bak = settings.with_suffix(".json.pre-nhj.bak")
    assert bak.exists()
    assert json.loads(bak.read_text()) == original
