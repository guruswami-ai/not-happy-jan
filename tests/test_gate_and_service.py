"""Voice opt-in gate (precedence, worker + CLI wiring) and the Linux systemd unit."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from nhj import cli, gate, worker


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("NHJ_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(gate.config, "section", lambda name: {})


def test_default_is_on_and_config_default_off_is_honoured(monkeypatch):
    assert gate.voice_allowed("reachy") is True
    monkeypatch.setattr(gate.config, "section", lambda name: {"default": "off"})
    assert gate.voice_allowed("reachy") is False


def test_yaml_boolean_default_is_understood(monkeypatch):
    # YAML 1.1 parses a bare `default: off` / `default: on` as False / True.
    monkeypatch.setattr(gate.config, "section", lambda name: {"default": False})
    assert gate.voice_allowed("x") is False and gate.describe()["default"] == "off"
    monkeypatch.setattr(gate.config, "section", lambda name: {"default": True})
    assert gate.voice_allowed("x") is True


def test_precedence_project_beats_global_beats_default(monkeypatch):
    monkeypatch.setattr(gate.config, "section", lambda name: {"default": "off"})
    gate.set_rule("on", "reachy")                 # project opts IN while default is off
    assert gate.voice_allowed("reachy") and not gate.voice_allowed("other")
    gate.set_rule("on")                           # global on
    assert gate.voice_allowed("other")
    gate.set_rule("off", "noisy")                 # project opts OUT despite global on
    assert not gate.voice_allowed("noisy") and gate.voice_allowed("other")
    gate.set_rule(None, "noisy"); gate.set_rule(None)   # clearing falls back to default
    assert not gate.voice_allowed("noisy")


def test_remote_source_matches_project_rule_but_full_rule_wins():
    gate.set_rule("off", "reachy")
    assert not gate.voice_allowed("hog11:reachy")
    gate.set_rule("on", "hog11:reachy")
    assert gate.voice_allowed("hog11:reachy") and not gate.voice_allowed("mmmm:reachy")


def test_bad_rule_value_rejected():
    with pytest.raises(ValueError):
        gate.set_rule("loud", "x")


def test_worker_skips_voice_adapters_for_opted_out_project(monkeypatch):
    from nhj.adapters.base import NotificationAdapter

    class Silent(NotificationAdapter):
        fired = 0
        def available(self): return True
        def fire(self, *a, **k): Silent.fired += 1; return True

    class Loud(Silent):
        channel = "voice"
        fired = 0
        def fire(self, *a, **k): Loud.fired += 1; return True

    gate.set_rule("off", "quiet")
    worker._dispatch({"intent": "ok", "source": "quiet"}, [Silent(), Loud()])
    assert (Silent.fired, Loud.fired) == (1, 0)          # board/displays still fire
    worker._dispatch({"intent": "ok", "source": "chatty"}, [Silent(), Loud()])
    assert (Silent.fired, Loud.fired) == (2, 1)


def test_cli_voice_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = CliRunner()
    assert r.invoke(cli.cli, ["voice", "off"]).exit_code == 0
    assert not gate.voice_allowed(tmp_path.name)
    assert r.invoke(cli.cli, ["voice", "on", "--global"]).exit_code == 0
    assert not gate.voice_allowed(tmp_path.name)          # project rule still wins
    assert r.invoke(cli.cli, ["voice", "clear"]).exit_code == 0
    assert gate.voice_allowed(tmp_path.name)
    assert r.invoke(cli.cli, ["voice", "bogus"]).exit_code == 2


def test_mcp_unit_text_is_a_valid_user_unit():
    t = cli._mcp_unit_text("/venv/bin/python", "127.0.0.1", 8765, "/data", "/logs/mcp.log")
    assert "ExecStart=/venv/bin/python -m nhj.mcp_server" in t
    assert "NHJ_HOST=127.0.0.1" in t and "NHJ_PORT=8765" in t
    assert "WantedBy=default.target" in t and "Restart=on-failure" in t
