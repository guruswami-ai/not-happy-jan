"""censor.apply() — markdown emphasis woven into a swear stem must still be bleeped (#82)."""
from __future__ import annotations

import pytest

from nhj import censor


@pytest.fixture(autouse=True)
def _force_quack(monkeypatch):
    monkeypatch.setenv("NHJ_CENSOR", "quack")
    monkeypatch.setattr(censor, "mode", lambda: "quack")


def test_plain_profanity_still_censored():
    assert censor.apply("fuck") == "quack"
    assert censor.apply("fucked off") == "quacked off"
    assert censor.apply("fucking legend") == "quacking legend"


def test_capitalisation_preserved():
    assert censor.apply("Fucked") == "Quacked"


def test_markdown_emphasis_inside_word_censored():
    assert censor.apply("f**uck") == "quack"
    assert censor.apply("f_uck") == "quack"
    assert censor.apply("s*h*i*t") == "quack"
    assert censor.apply("**fuck**") == "**quack**"


def test_no_false_positives():
    for clean in ("secret", "witch", "scunt", "assess"):
        assert censor.apply(clean) == clean


def test_off_mode_passes_through(monkeypatch):
    monkeypatch.setattr(censor, "mode", lambda: "off")
    assert censor.apply("fuck") == "fuck"
