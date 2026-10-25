"""Config loading smoke tests (issue #5 harness; extended by #6).

Verifies the shipped defaults load and the public config surface behaves in a
source checkout. The user `config.yaml` override is implemented and tested in #6.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from nhj import config


def test_defaults_load_in_checkout():
    # The bundled config/default.yaml resolves and parses to a non-empty mapping.
    assert isinstance(config.CFG, dict) and config.CFG


def test_section_returns_dict_never_none():
    assert isinstance(config.section("intents"), dict)
    assert config.section("does-not-exist-xyz") == {}     # absent ⇒ {}, never None


def test_intents_cover_the_core_set():
    for intent in ("ok", "err", "warn", "attn", "celebrate", "step"):
        assert intent in config.INTENTS
        assert "label" in config.INTENTS[intent]


def test_adapter_order_is_nonempty_list():
    assert isinstance(config.ADAPTER_ORDER, list) and config.ADAPTER_ORDER


def _reset_lazy_config(monkeypatch):
    for name in ("CFG", "INTENTS", "ADAPTER_ORDER"):
        monkeypatch.delattr(config, name, raising=False)
    monkeypatch.setattr(config, "_CFG", None)


def test_get_loads_once_and_caches(monkeypatch):
    _reset_lazy_config(monkeypatch)
    calls = []
    loaded = {"voice": {"default": False}}

    def fake_load(force_validate=False):
        calls.append(force_validate)
        return loaded

    monkeypatch.setattr(config, "load_config", fake_load)
    assert config.get() is loaded
    assert config.get() is loaded
    assert config.section("voice") == {"default": False}
    assert calls == [False]


def test_reload_replaces_config_and_lazy_derivations(monkeypatch):
    _reset_lazy_config(monkeypatch)
    versions = iter([
        {"adapters": ["audio"], "intents": {"ok": {"label": "First"}}},
        {"adapters": ["haptic"], "intents": {"ok": {"label": "Second"}}},
    ])
    monkeypatch.setattr(config, "load_config", lambda force_validate=False: next(versions))

    assert config.ADAPTER_ORDER == ["audio"]
    assert config.INTENTS["ok"]["label"] == "First"
    assert config.reload()["adapters"] == ["haptic"]
    assert config.ADAPTER_ORDER == ["haptic"]
    assert config.INTENTS["ok"]["label"] == "Second"


def test_failed_reload_keeps_previous_config(monkeypatch):
    _reset_lazy_config(monkeypatch)
    loaded = {"adapters": ["audio"]}
    monkeypatch.setattr(config, "load_config", lambda force_validate=False: loaded)
    assert config.get() is loaded

    def broken_load(force_validate=False):
        raise config.ConfigError("broken")

    monkeypatch.setattr(config, "load_config", broken_load)
    with pytest.raises(config.ConfigError):
        config.reload()
    assert config.get() is loaded


def test_lazy_compatibility_from_imports(monkeypatch):
    _reset_lazy_config(monkeypatch)
    loaded = {"adapters": ["audio"], "intents": {"ok": {"label": "Ready"}}}
    monkeypatch.setattr(config, "load_config", lambda force_validate=False: loaded)

    from nhj.config import ADAPTER_ORDER, CFG, INTENTS

    assert CFG is loaded
    assert ADAPTER_ORDER == ["audio"]
    assert INTENTS == {"ok": {"label": "Ready"}}


def test_materialised_cfg_is_authoritative_for_get_and_section(monkeypatch):
    _reset_lazy_config(monkeypatch)
    marker = {"awtrix": {"transport": "http"}}
    monkeypatch.setattr(config, "CFG", marker)
    monkeypatch.setattr(
        config,
        "load_config",
        lambda force_validate=False: (_ for _ in ()).throw(AssertionError("unexpected load")),
    )
    assert config.get() is marker
    assert config.section("awtrix") == {"transport": "http"}


def test_importing_config_does_not_read_selected_config(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("routing: [unclosed\n")
    code = (
        "import os; before = os.environ.get('NHJ_CONFIG_FILE'); "
        "from nhj import config; "
        "print('lazy', config._CFG is None, 'CFG' in vars(config), os.environ.get('NHJ_CONFIG_FILE') == before)\n"
        "try:\n    config.get()\n"
        "except config.ConfigError:\n    print('raised-on-get')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "NHJ_CONFIG_FILE": str(bad)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "lazy True False True" in out.stdout
    assert "raised-on-get" in out.stdout


def test_config_file_can_be_selected_after_import(tmp_path):
    selected = tmp_path / "selected.yaml"
    selected.write_text("adapters: [audio]\n")
    code = (
        "import os, sys; from nhj import config; "
        "print('before', config._CFG is None); "
        "os.environ['NHJ_CONFIG_FILE'] = sys.argv[1]; "
        "print('after', config.get()['adapters'], config.ADAPTER_ORDER)"
    )
    env = dict(os.environ)
    env.pop("NHJ_CONFIG_FILE", None)
    out = subprocess.run(
        [sys.executable, "-c", code, str(selected)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "before True" in out.stdout
    assert "after ['audio'] ['audio']" in out.stdout
