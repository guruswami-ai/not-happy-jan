"""Config schema validation (#47): typo'd keys and bad values fail loudly with
the source file named, while partial overrides and user-defined characters /
modes stay valid."""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from nhj import config, resources
from nhj.cli import cli

_DEFAULT = (
    "routing:\n"
    "  rung_by_intent:\n"
    "    ok: jan\n"
    "    err: karren\n"
    "adapters: [haptic, audio]\n"
)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    default = tmp_path / "default.yaml"
    default.write_text(_DEFAULT)
    udir = tmp_path / "userconfig"
    udir.mkdir()
    monkeypatch.setattr(resources, "config_file", lambda: default)
    monkeypatch.setattr(resources, "config_dir", lambda create=True: udir)
    monkeypatch.delenv("NHJ_CONFIG_FILE", raising=False)
    return type("Env", (), {"default": default, "udir": udir, "tmp": tmp_path})


def test_shipped_defaults_validate():
    raw = config._read_mapping(resources.config_file())
    assert config.validate_config(raw, source="default.yaml") is raw


def test_unknown_top_level_key_rejected(cfg):
    (cfg.udir / "config.yaml").write_text("adapterz: [audio]\n")   # typo
    with pytest.raises(config.ConfigError, match="adapterz"):
        config.load_config()


@pytest.mark.parametrize("override", [
    "adapters: audio\n",                                   # not a list
    "characters:\n  jan: {level: 12}\n",                   # level out of range
    "characters:\n  jan: {levle: 4}\n",                    # typo'd dial
    "orchestration:\n  mode: sometimes-quiet\n",           # bad enum
    "routing:\n  rung_by_intent:\n    great: jan\n",       # unknown intent
    "awtrix:\n  brightness: 999\n",                        # out of range
    "modes:\n  mine:\n    muzack: on\n",                   # typo'd mode field
    "claky:\n  intents: [ok, bogus]\n",                    # bad intent in a list
])
def test_invalid_values_rejected(cfg, override):
    (cfg.udir / "config.yaml").write_text(override)
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_custom_character_and_mode_stay_valid(cfg):
    (cfg.udir / "config.yaml").write_text(
        "characters:\n  mychar: {level: 3, chaos: 2}\n"
        "modes:\n  focus-hard:\n    muzak: 'off'\n    dials: {mychar: {competence: 9}}\n"
    )
    c = config.load_config()
    assert c["characters"]["mychar"]["level"] == 3
    assert c["modes"]["focus-hard"]["muzak"] == "off"


def test_yaml_bool_mode_values_stay_valid(cfg):
    # YAML 1.1 turns bare on/off into booleans; modes._norm already tolerates that.
    (cfg.udir / "config.yaml").write_text("modes:\n  mine:\n    muzak: off\n")
    assert config.load_config()["modes"]["mine"]["muzak"] is False  # returned unmodified


def test_unchanged_files_skip_revalidation_but_changes_do_not(cfg, monkeypatch):
    from nhj import config_schema
    config.load_config()                       # validates + stamps the fingerprint
    calls = []
    real = config_schema.validation_errors
    monkeypatch.setattr(config_schema, "validation_errors",
                        lambda c: calls.append(1) or real(c))
    config.load_config()                       # unchanged bytes: stamp hit, no validation
    assert calls == []
    (cfg.udir / "config.yaml").write_text("adapterz: [audio]\n")
    with pytest.raises(config.ConfigError):   # changed bytes: revalidated, typo caught
        config.load_config()
    assert calls


def test_config_check_command_ok_and_bad(monkeypatch):
    ok = CliRunner().invoke(cli, ["config", "check"])
    assert ok.exit_code == 0, ok.output
    assert "config valid" in ok.output

    def _bad(**kwargs):
        assert kwargs.get("force_validate") is True   # check must bypass the stamp
        raise config.ConfigError("bogus.yaml: invalid config — adapterz: unknown")

    monkeypatch.setattr(config, "load_config", _bad)
    bad = CliRunner().invoke(cli, ["config", "check"])
    assert bad.exit_code == 1
    assert "adapterz" in bad.output
