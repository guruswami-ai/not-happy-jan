"""Tests for the prompt hook's secret-guard path (nhj/prompt_hook.py).

Key invariants verified here:
 - When a secret-shaped string is found, the queued message contains only the
   CATEGORY label — never the prompt text itself.
 - Clean prompts produce no queue interaction.
 - The secret_guard runtime flag gates the feature; when False the scanner is
   never called and no queue item is enqueued.
 - The hook's main() function handles events correctly without crashing.
"""
from __future__ import annotations

import json
from io import StringIO

import pytest

from nhj import prompt_hook


# ---- helper: capture queue calls without real QueueManager ------------------

class _FakeQM:
    def __init__(self):
        self.added: list[dict] = []
        self.started = False

    def add(self, **kw):
        self.added.append(dict(kw))

    def start_worker_if_needed(self):
        self.started = True


# ---- _scold_for_secret: message locality ------------------------------------

def test_scold_message_contains_only_category_not_prompt(monkeypatch):
    """The queued message must contain the category label, NOT the prompt text."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)

    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    prompt = f"Here is my key: {aws_key}"
    prompt_hook._scold_for_secret(prompt)

    assert len(fake_qm.added) == 1, "Expected exactly one queued item"
    queued_message = fake_qm.added[0]["message"]

    # The message must reference the category ("an AWS access key"), not the key value.
    assert "an AWS access key" in queued_message
    assert aws_key not in queued_message
    # The full prompt text must never appear in the queued message.
    assert prompt not in queued_message


def test_scold_does_not_queue_when_no_secret(monkeypatch):
    """A clean prompt must produce no queue item."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)

    prompt_hook._scold_for_secret("please refactor this function")

    assert fake_qm.added == [], "No item must be queued for a clean prompt"
    assert not fake_qm.started


def test_scold_queues_attn_intent(monkeypatch):
    """Secret detection must enqueue an attn intent so Karren handles it."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)

    prompt_hook._scold_for_secret("sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ")

    assert fake_qm.added[0]["intent"] == "attn"
    assert fake_qm.added[0]["emotion"] == "alert"


def test_scold_starts_worker(monkeypatch):
    """After queuing, the worker must be started."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)

    prompt_hook._scold_for_secret("sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ")

    assert fake_qm.started


# ---- _scold_for_secret: no network ------------------------------------------

def test_scold_does_not_make_network_calls(monkeypatch):
    """The scold path must be entirely local — no network calls of any kind."""
    import socket as _socket

    def _no_network(*a, **kw):
        raise AssertionError("Network call attempted in secret-guard path!")

    monkeypatch.setattr(_socket, "create_connection", _no_network)
    monkeypatch.setattr(_socket, "getaddrinfo", _no_network)
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: _FakeQM())

    prompt_hook._scold_for_secret("xox" + "b-1234567890-abcdefghijklmno")
    # No exception → no network call was made.


# ---- _scold_for_secret: all categories leave category labels only -----------

@pytest.mark.parametrize("prompt_fragment,category", [
    ("-----BEGIN RSA PRIVATE KEY-----",                 "a private key"),
    ("AKIA" + "IOSFODNN7EXAMPLE",                       "an AWS access key"),
    ("ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZab",               "a GitHub token"),
    ("xox" + "b-1234567890-abcdefghijklmno",                 "a Slack token"),
    ("AIzaSyD-9tSrke72I6ox_kAkzAkEfGHIJKLMNOPQRS",     "a Google API key"),
    ("sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ",         "an API key"),
    ("password: supersecretpassword123",                 "a password"),
])
def test_scold_message_never_contains_the_secret(monkeypatch, prompt_fragment, category):
    """For each category: the queued message must name the category but not the secret."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)

    prompt_hook._scold_for_secret(prompt_fragment)

    assert fake_qm.added, f"Expected a queued item for {prompt_fragment!r}"
    msg = fake_qm.added[0]["message"]
    assert category in msg, f"Category label missing from message: {msg!r}"
    assert prompt_fragment not in msg, f"Secret fragment leaked into message: {msg!r}"


# ---- main(): secret_guard flag controls the feature -------------------------

def _make_payload(prompt: str = "", event: str = "UserPromptSubmit") -> str:
    return json.dumps({
        "hook_event_name": event,
        "session_id": "test-session-1234",
        "transcript_path": "/tmp/nonexistent.jsonl",
        "prompt": prompt,
    })


def test_main_skips_scold_when_secret_guard_disabled(monkeypatch):
    """When secret_guard flag is False the scanner must not be called at all."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)
    monkeypatch.setattr("sys.stdin", StringIO(_make_payload("sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ")))

    # Disable secret_guard and muzak so the only interesting path is the secret guard.
    monkeypatch.setattr("nhj.state.get_flag", lambda key, default=False: False)

    rc = prompt_hook.main()

    assert rc == 0
    # No queue item enqueued — guard was off.
    assert fake_qm.added == []


def test_main_calls_scold_when_secret_guard_enabled(monkeypatch):
    """When secret_guard flag is True the scanner IS called and a match is queued."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)
    monkeypatch.setattr("sys.stdin", StringIO(_make_payload("sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ")))

    # Enable only the secret_guard flag; leave muzak off.
    def fake_get_flag(key, default=False):
        return key == "secret_guard"
    monkeypatch.setattr("nhj.state.get_flag", fake_get_flag)

    rc = prompt_hook.main()

    assert rc == 0
    assert fake_qm.added, "Expected a queued item when secret_guard is on"
    assert "an API key" in fake_qm.added[0]["message"]


def test_main_clean_prompt_never_queues_anything(monkeypatch):
    """A clean prompt with secret_guard enabled must produce no queue item."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)
    monkeypatch.setattr("sys.stdin", StringIO(_make_payload("can you help me refactor this?")))

    def fake_get_flag(key, default=False):
        return key == "secret_guard"
    monkeypatch.setattr("nhj.state.get_flag", fake_get_flag)

    rc = prompt_hook.main()

    assert rc == 0
    assert fake_qm.added == []


def test_main_session_end_never_queues(monkeypatch):
    """SessionEnd events must never enqueue a secret-guard item."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)
    # Even with a secret in the payload, SessionEnd should short-circuit.
    monkeypatch.setattr("sys.stdin", StringIO(_make_payload(
        "sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ", event="SessionEnd"
    )))

    def fake_get_flag(key, default=False):
        return False

    def fake_mark_idle(sid):
        pass

    monkeypatch.setattr("nhj.state.get_flag", fake_get_flag)
    monkeypatch.setattr("nhj.inference_muzak.mark_idle", fake_mark_idle)

    rc = prompt_hook.main()

    assert rc == 0
    assert fake_qm.added == []


def test_main_returns_zero_on_invalid_json(monkeypatch):
    """A broken payload must not crash the hook — it must return 0 gracefully."""
    monkeypatch.setattr("sys.stdin", StringIO("not-valid-json"))
    rc = prompt_hook.main()
    assert rc == 0


def test_main_queued_message_does_not_contain_prompt_text(monkeypatch):
    """End-to-end: the queue item must never carry the raw prompt text."""
    fake_qm = _FakeQM()
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: fake_qm)

    secret_fragment = "sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
    full_prompt = f"Here is my prod key for review: {secret_fragment}"
    monkeypatch.setattr("sys.stdin", StringIO(_make_payload(full_prompt)))

    def fake_get_flag(key, default=False):
        return key == "secret_guard"
    monkeypatch.setattr("nhj.state.get_flag", fake_get_flag)

    prompt_hook.main()

    assert fake_qm.added
    msg = fake_qm.added[0]["message"]
    assert full_prompt not in msg
    assert secret_fragment not in msg


# ---- secret_guard default is opt-in (False) ---------------------------------

def test_secret_guard_default_is_off(tmp_path):
    """The secret_guard flag must default to False — the feature is opt-in."""
    from nhj.state import get_flag
    # With no state file present (NHJ_STATE_FILE pointing at a non-existent path),
    # get_flag("secret_guard") must return False.
    import os
    original = os.environ.get("NHJ_STATE_FILE")
    try:
        os.environ["NHJ_STATE_FILE"] = str(tmp_path / "state_does_not_exist.json")
        default_value = get_flag("secret_guard")
    finally:
        if original is None:
            os.environ.pop("NHJ_STATE_FILE", None)
        else:
            os.environ["NHJ_STATE_FILE"] = original

    assert default_value is False, (
        "secret_guard must default to False (opt-in). "
        "Users must explicitly enable it with: nhj set secret_guard on"
    )
