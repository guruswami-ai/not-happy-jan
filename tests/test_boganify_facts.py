"""Fact guard on dynamic lines (#87): numbers in the status must survive rephrasing."""
from __future__ import annotations

import json

import pytest

from nhj import boganify
from nhj.characters import Character


class _Char:
    name = "Jan"
    personality = "You are the receptionist."
    dial = "ockerism"
    level = 4


class _Resp:
    def __init__(self, text: str):
        self._payload = json.dumps({"choices": [{"message": {"content": text}}]}).encode()

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _stub(monkeypatch, replies: list[str], temps: list[float] | None = None):
    """BYO endpoint so we never touch the live LLM; feed canned /chat/completions replies."""
    monkeypatch.setenv("NHJ_DYNAMIC", "1")
    monkeypatch.setenv("NHJ_DYNAMIC_BASE_URL", "http://dynamic.test/v1")
    monkeypatch.setenv("NHJ_DYNAMIC_TEMP", "0.85")
    it = iter(replies)

    def urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        if temps is not None:
            temps.append(body["temperature"])
        try:
            text = next(it)
        except StopIteration as e:
            raise AssertionError("unexpected extra /chat/completions call") from e
        return _Resp(text)

    monkeypatch.setattr(boganify.urllib.request, "urlopen", urlopen)


# ---- extraction ---------------------------------------------------------------------------------
@pytest.mark.parametrize("text,expect", [
    ("backup finished in 12 minutes", {"12"}),
    ("make failed with status 3", {"3"}),
    ("pytest finished in 1 hour 5 minutes", {"1", "5"}),
    ("one hour five minutes", {"1", "5"}),
    ("twelve minutes", {"12"}),
    ("nightly backup was interrupted", set()),
    ("", set()),
])
def test_facts_extracts_digits_and_number_words(text, expect):
    assert boganify.facts(text) == frozenset(expect)


def test_keeps_facts_requires_every_status_number():
    status = "make failed with status 3"
    assert boganify.keeps_facts(status, "make failed with status 3, mate")
    assert boganify.keeps_facts(status, "status three, hard luck")
    assert not boganify.keeps_facts(status, "the make failed again")
    assert not boganify.keeps_facts(status, "failed with status 4")
    assert boganify.keeps_facts("nightly backup was interrupted", "got cut short, mate")


def test_display_may_drop_numbers_but_must_not_change_them():
    status = "backup finished in 12 minutes"
    assert boganify.display_facts_ok(status, "BACKUP DONE")
    assert boganify.display_facts_ok(status, "12 MIN")
    assert not boganify.display_facts_ok(status, "13 MIN")


# ---- generate() with a stub endpoint ------------------------------------------------------------
def test_a_line_that_keeps_the_number_is_accepted(monkeypatch):
    _stub(monkeypatch, ["Make died with status 3, mate."])
    out = boganify.generate(_Char(), "err", "make failed with status 3", 4)
    assert out == "Make died with status 3, mate."


def test_a_line_that_drops_or_changes_the_number_falls_back(monkeypatch):
    temps: list[float] = []
    _stub(monkeypatch, [
        "the make failed again",
        "Crikey, this is a right royal pain",
    ], temps)
    out = boganify.generate(_Char(), "err", "make failed with status 3", 4)
    assert out is None
    assert temps == [0.85, 0.3]


def test_retry_at_lower_temperature_can_keep_the_number(monkeypatch):
    temps: list[float] = []
    _stub(monkeypatch, [
        "Backup's done, mate.",
        "Backup finished in twelve minutes, mate.",
    ], temps)
    out = boganify.generate(_Char(), "ok", "backup finished in 12 minutes", 4)
    assert out == "Backup finished in twelve minutes, mate."
    assert temps == [0.85, 0.3]


def test_no_numbers_in_the_status_leaves_the_line_alone(monkeypatch):
    _stub(monkeypatch, ["Blimey, the backup got cut short, mate."])
    out = boganify.generate(_Char(), "warn", "nightly backup was interrupted", 4)
    assert out == "Blimey, the backup got cut short, mate."


def test_hour_and_minute_counts_must_both_survive(monkeypatch):
    _stub(monkeypatch, ["the tests done in a hurry, mate.", "still in a rush"])
    assert boganify.generate(_Char(), "ok", "pytest finished in 1 hour 5 minutes", 4) is None


def test_http_failure_does_not_retry(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setenv("NHJ_DYNAMIC_BASE_URL", "http://dynamic.test/v1")

    def boom(req, timeout=None):
        calls["n"] += 1
        raise TimeoutError("down")

    monkeypatch.setattr(boganify.urllib.request, "urlopen", boom)
    assert boganify.generate(_Char(), "ok", "backup finished in 12 minutes", 4) is None
    assert calls["n"] == 1


def test_claude_live_samples_are_rejected_when_they_drop_facts():
    """The six replies recorded in docs/scripts.md before the guard (plus the 1 h 5 min miss)."""
    assert not boganify.keeps_facts(
        "backup finished in 12 minutes",
        "Backup's done, mate. Took a bit of time, but we're good to go.")
    assert not boganify.keeps_facts(
        "backup finished in 12 minutes",
        "Backup done in a fair while, mate.")
    assert not boganify.keeps_facts(
        "make failed with status 3",
        "Crikey, the make failed again. Hard luck.")
    assert not boganify.keeps_facts(
        "make failed with status 3",
        "Bloody hell, this is a right royal pain in the arse...")
    assert boganify.keeps_facts(
        "nightly backup was interrupted",
        "Blimey, the backup got cut short, mate. Gotta get that sorted.")
    assert not boganify.keeps_facts(
        "pytest finished in 1 hour 5 minutes",
        "the tests done in a hurry, mate.")


# ---- generate_display() -------------------------------------------------------------------------
def test_display_accepts_a_headline_that_drops_the_number(monkeypatch):
    _stub(monkeypatch, ["BACKUP DONE mate"])
    assert boganify.generate_display(_Char(), "ok", "backup finished in 12 minutes") == "BACKUP DONE"


def test_display_rejects_a_changed_number_then_falls_back(monkeypatch):
    temps: list[float] = []
    _stub(monkeypatch, ["13 MIN LATE", "14 MIN"], temps)
    assert boganify.generate_display(_Char(), "ok", "backup finished in 12 minutes") is None
    assert temps == [0.85, 0.3]


# ---- caller still speaks; NHJ_DYNAMIC off is untouched ------------------------------------------
def test_delivery_still_speaks_the_static_line_when_the_guard_rejects(monkeypatch):
    _stub(monkeypatch, ["the make failed again", "still no number"])
    text = Character.load("jan").delivery("err", "make failed with status 3").text
    assert text
    assert text != "the make failed again"


def test_dynamic_off_does_not_call_the_model(monkeypatch):
    monkeypatch.delenv("NHJ_DYNAMIC", raising=False)
    called = []
    monkeypatch.setattr(
        boganify.urllib.request, "urlopen",
        lambda *a, **k: called.append(1) or (_ for _ in ()).throw(AssertionError("called")))
    text = Character.load("jan").delivery("ok", "backup finished in 12 minutes").text
    assert text
    assert called == []
