"""`nhj uninstall` removes NHJ's runtime/config/caches/launcher + Claude integration,
leaves the user's Claude install, and NEVER deletes a source checkout."""
from __future__ import annotations

import json

from nhj import cli, resources


def _isolate_home(tmp_path, monkeypatch):
    """Isolate HOME to tmp AND stub subprocess so the service-stop NEVER runs a real
    launchctl/pkill against the machine running the tests. Returns the captured calls."""
    import subprocess as _sp
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("NHJ_APP_SUPPORT_DIR", "NHJ_DATA_DIR", "NHJ_CACHE_DIR", "NHJ_STATE_DIR",
              "NHJ_CONFIG_DIR", "NHJ_LOG_DIR", "NHJ_BIN_DIR"):
        monkeypatch.delenv(v, raising=False)
    calls: list = []
    monkeypatch.setattr(cli.subprocess, "run",
                        lambda args, **k: calls.append(args) or _sp.CompletedProcess(args, 0))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)   # pretend launchctl/pkill exist
    return calls


def test_uninstall_removes_install_and_keeps_claude(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)

    appsup = resources.app_support_dir(create=True)
    (appsup / ".env").write_text("NHJ_X=1")
    (appsup / "runtime").mkdir(parents=True, exist_ok=True)
    cache = resources.cache_dir(create=True)
    (cache / "tts-models").mkdir(parents=True, exist_ok=True)
    skill = resources.claude_skill_install_path()
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("skill")
    binlink = tmp_path / ".local" / "bin" / "nhj"
    binlink.parent.mkdir(parents=True, exist_ok=True)
    binlink.write_text("#!/bin/sh")

    claude = tmp_path / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    settings = claude / "settings.json"
    settings.write_text(json.dumps({
        "hooks": {"Stop": [{"matcher": "", "hooks": [{"command": "py", "args": ["-m", "nhj.hook"]}]}]},
        "mcpServers": {"not-happy-jan": {"command": "py"}, "other-tool": {"command": "keep"}},
        "userPref": "keep-me",
    }))
    cli.install_markers(claude_md=claude / "CLAUDE.md")   # adds the marker block

    cli.uninstall(yes=True)

    # NHJ install gone
    assert not appsup.exists()
    assert not cache.exists()
    assert not skill.exists()
    assert not binlink.exists()
    # Claude install survives; only NHJ's entries removed
    cfg = json.loads(settings.read_text())
    assert cfg["userPref"] == "keep-me"
    assert "other-tool" in cfg["mcpServers"]
    assert "not-happy-jan" not in cfg["mcpServers"]
    assert "nhj.hook" not in settings.read_text()
    assert "not-happy-jan:start" not in (claude / "CLAUDE.md").read_text()


def test_uninstall_never_deletes_a_source_checkout(tmp_path, monkeypatch):
    """In a dev checkout, data_dir() resolves to the repo root — it must be skipped."""
    _isolate_home(tmp_path, monkeypatch)
    # the live data_dir (in this test run it's the checkout root) must not be on the
    # deletion list — guarded by the pyproject.toml/.git check.
    d = resources.data_dir(create=False)
    if (d / "pyproject.toml").exists() or (d / ".git").exists():
        cli.uninstall(yes=True)
        assert d.exists(), "uninstall must never delete a source checkout"


def test_uninstall_stops_services_and_removes_plists(tmp_path, monkeypatch):
    """Uninstall must stop the TTS/LLM/MCP LaunchAgents (user-domain launchctl bootout),
    remove their plists, and kill on-demand processes — all without sudo."""
    calls = _isolate_home(tmp_path, monkeypatch)
    la = tmp_path / "Library" / "LaunchAgents"
    la.mkdir(parents=True, exist_ok=True)
    # the three current services + the legacy LLM label (renamed nhj-ocker-bogan-nano → nhj-llm)
    labels = (cli._TTS_LABEL, cli._LLM_LABEL, cli._MCP_LABEL, *cli._LEGACY_LLM_LABELS)
    for lbl in labels:
        (la / f"{lbl}.plist").write_text("<plist/>")

    cli.uninstall(yes=True)

    # plists gone — including the legacy LLM agent, so an upgrader is left clean
    assert not any((la / f"{lbl}.plist").exists() for lbl in labels)
    # one user-domain `launchctl bootout gui/<uid>/<label>` per label — never `system/` (no sudo)
    boots = [a for a in calls if a[:2] == ["launchctl", "bootout"]]
    assert len(boots) == len(labels)
    assert all(a[2].startswith("gui/") for a in boots)
    assert not any("system/" in " ".join(a) for a in calls)
    # stray on-demand processes pkill'd
    assert any(a and a[0] == "pkill" for a in calls)
