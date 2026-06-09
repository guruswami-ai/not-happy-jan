"""Config override tests (issue #6).

Exercises defaults-only, nested merge, list replacement, the precedence chain
(alongside < user-dir < explicit path), and loud failure on malformed config.
The loader is pointed at temp files via the resources seam.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from nhj import characters, config, resources
from nhj.adapters import ulanzi

_DEFAULT = (
    "routing:\n"
    "  rung_by_intent:\n"
    "    ok: jan\n"
    "    err: karren\n"
    "adapters: [haptic, audio]\n"
)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Redirect the loader to a temp default.yaml + temp user config dir."""
    default = tmp_path / "default.yaml"
    default.write_text(_DEFAULT)
    udir = tmp_path / "userconfig"
    udir.mkdir()
    monkeypatch.setattr(resources, "config_file", lambda: default)
    monkeypatch.setattr(resources, "config_dir", lambda create=True: udir)
    monkeypatch.delenv("NHJ_CONFIG_FILE", raising=False)
    return type("Env", (), {"default": default, "udir": udir, "tmp": tmp_path})


def test_no_override_returns_defaults(cfg):
    c = config.load_config()
    assert c["adapters"] == ["haptic", "audio"]
    assert c["routing"]["rung_by_intent"]["err"] == "karren"


def test_missing_override_is_normal(cfg):
    assert config.load_config()["adapters"] == ["haptic", "audio"]   # no error


def test_nested_override_preserves_siblings(cfg):
    (cfg.udir / "config.yaml").write_text("routing:\n  rung_by_intent:\n    warn: bazza\n")
    rbi = config.load_config()["routing"]["rung_by_intent"]
    assert rbi["warn"] == "bazza"                          # added
    assert rbi["ok"] == "jan" and rbi["err"] == "karren"   # defaults preserved


def test_list_replaced_wholesale(cfg):
    (cfg.udir / "config.yaml").write_text("adapters: [audio]\n")
    assert config.load_config()["adapters"] == ["audio"]   # replaced, not merged


def test_userdir_overrides_alongside(cfg):
    (cfg.default.parent / "config.yaml").write_text("adapters: [haptic]\n")   # (2) alongside
    (cfg.udir / "config.yaml").write_text("adapters: [audio]\n")              # (3) user dir, higher
    assert config.load_config()["adapters"] == ["audio"]


def test_explicit_path_wins(cfg, monkeypatch):
    explicit = cfg.tmp / "explicit.yaml"
    explicit.write_text("adapters: [haptic]\n")
    (cfg.udir / "config.yaml").write_text("adapters: [audio]\n")
    monkeypatch.setenv("NHJ_CONFIG_FILE", str(explicit))   # (4) explicit, highest
    assert config.load_config()["adapters"] == ["haptic"]


def test_override_discovery_does_not_create_config_dir(tmp_path, monkeypatch):
    config_root = tmp_path / "missing"
    monkeypatch.setenv("NHJ_CONFIG_DIR", str(config_root))
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("override discovery must not create directories")
        ),
    )

    paths = config._override_paths()

    assert config_root / "config.yaml" in paths


def test_legacy_config_override_is_read_after_new_user_dir(tmp_path, monkeypatch):
    default = tmp_path / "default.yaml"
    default.write_text(_DEFAULT)
    new_dir = tmp_path / "Library" / "Application Support" / "not-happy-jan"
    legacy_dir = tmp_path / ".config" / "nhj"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "config.yaml").write_text("adapters: [audio]\n")

    monkeypatch.setattr(resources, "config_file", lambda: default)
    monkeypatch.setattr(resources, "config_dir", lambda create=True: new_dir)
    monkeypatch.setattr(resources, "legacy_config_dir", lambda: legacy_dir)
    monkeypatch.delenv("NHJ_CONFIG_FILE", raising=False)

    assert config.load_config()["adapters"] == ["audio"]


def test_malformed_yaml_raises(cfg):
    (cfg.udir / "config.yaml").write_text("routing: [unclosed\n")
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_non_mapping_toplevel_raises(cfg):
    (cfg.udir / "config.yaml").write_text("- just\n- a\n- list\n")
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_deep_merge_semantics():
    assert config._deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 9}}) == {"a": {"x": 1, "y": 9}}
    assert config._deep_merge({"l": [1, 2]}, {"l": [9]}) == {"l": [9]}            # list replaces
    assert config._deep_merge({"a": 1}, {"a": {"n": True}}) == {"a": {"n": True}}  # scalar→dict replaces


def test_character_routing_uses_cached_config(monkeypatch):
    marker = {"routing": {"rung_by_intent": {"ok": "jan"}}}
    monkeypatch.setattr(config, "CFG", marker)
    monkeypatch.setattr(config, "load_config",
                        lambda: (_ for _ in ()).throw(AssertionError("unexpected reload")))
    assert characters._cfg() is marker


def test_config_files_are_read_as_utf8():
    seen = {}

    class ConfigPath:
        def read_text(self, *, encoding=None):
            seen["encoding"] = encoding
            return "label: café\n"

        def __str__(self):
            return "config.yaml"

    assert config._read_mapping(ConfigPath()) == {"label": "café"}
    assert seen["encoding"] == "utf-8"


def test_non_utf8_config_raises_path_aware_config_error(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_bytes(b"label: \xff\n")

    with pytest.raises(config.ConfigError, match=re.escape(str(bad))):
        config._read_mapping(bad)


def test_unreadable_config_raises_path_aware_config_error():
    class BrokenPath:
        def read_text(self, *, encoding=None):
            raise OSError("permission denied")

        def __str__(self):
            return "broken-config.yaml"

    with pytest.raises(config.ConfigError, match="broken-config.yaml"):
        config._read_mapping(BrokenPath())


def test_awtrix_uses_merged_config(monkeypatch):
    merged = {"transport": "http", "displays": ["override.lan"]}
    monkeypatch.setattr(config, "CFG", {"awtrix": merged})
    assert ulanzi._awtrix_cfg() == merged


@pytest.mark.parametrize(
    ("intent", "env_name", "env_value"),
    [
        ("ok", "NHJ_DEFAULT_CHARACTER", "bazza"),
        ("err", "NHJ_ERROR_CHARACTER", "bazza"),
    ],
)
def test_character_environment_override_wins_routing(monkeypatch, intent, env_name, env_value):
    monkeypatch.setattr(config, "CFG", {
        "routing": {
            "rung_by_intent": {"ok": "jan", "err": "karren"},
            "default": "jan",
            "on_err": "karren",
        },
        "characters": {},
    })
    monkeypatch.setenv(env_name, env_value)
    monkeypatch.setattr(
        characters.Character,
        "load",
        classmethod(lambda cls, name, defs_root: cls(name=name, voice=name)),
    )

    assert characters.resolve_character(intent).name == env_value
