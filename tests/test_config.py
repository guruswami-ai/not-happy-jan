"""Config loading smoke tests (issue #5 harness; extended by #6).

Verifies the shipped defaults load and the public config surface behaves in a
source checkout. The user `config.yaml` override is implemented and tested in #6.
"""
from __future__ import annotations

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
