"""Hook-payload fixtures (#47): one payload per (CLI x event) under
tests/fixtures/hooks/, pinned against what NHJ derives from it — identity
(label, session) for every CLI, and the actual hook routing for Claude Code."""
from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path

import pytest

from nhj import cli, hook, inference_muzak, notify_hook, prompt_hook

_FIX = Path(__file__).parent / "fixtures" / "hooks"


def _load(rel: str, transcript: Path | None = None) -> dict:
    text = (_FIX / rel).read_text()
    if transcript is not None:
        text = text.replace("__TRANSCRIPT__", str(transcript))
    return json.loads(text)


def test_fixture_inventory():
    got = sorted(str(p.relative_to(_FIX)) for p in _FIX.rglob("*.json"))
    assert got == [
        "claude-code/notification.json",
        "claude-code/session-end.json",
        "claude-code/stop-failure.json",
        "claude-code/stop.json",
        "claude-code/user-prompt-submit.json",
        "codex/notify.json",
        "gemini/after-agent.json",
        "grok-build/stop.json",
    ]


@pytest.mark.parametrize("rel,expected", [
    ("claude-code/user-prompt-submit.json", ("proj-alpha", "claude-fixture-1")),
    ("claude-code/stop.json", ("proj-alpha", "claude-fixture-1")),
    ("claude-code/notification.json", ("proj-alpha", "claude-fixture-1")),
    ("grok-build/stop.json", ("proj-beta", "grok-fixture-1")),          # camelCase keys
    ("codex/notify.json", ("proj-gamma", "proj-gamma")),               # thread-id not a session key
    ("gemini/after-agent.json", ("proj-delta", "gemini-fixture-1")),
])
def test_hook_identity_per_cli(rel, expected):
    assert cli._hook_identity("", _load(rel)) == expected


def _stdin(monkeypatch, payload: dict):
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))


def test_user_prompt_submit_marks_busy_and_seeds_baseline(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps(
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "prev"}]}}) + "\n")
    busy, seeded = [], []
    monkeypatch.setattr(inference_muzak, "mark_busy", busy.append)
    monkeypatch.setattr(hook, "_seen_set", lambda sid, sig: seeded.append((sid, sig)))
    _stdin(monkeypatch, _load("claude-code/user-prompt-submit.json", transcript))
    assert prompt_hook.main() == 0
    assert busy == ["claude-fixture-1"]
    assert seeded and seeded[0][0] == "claude-fixture-1" and seeded[0][1]


@pytest.mark.parametrize("rel", ["claude-code/stop-failure.json", "claude-code/session-end.json"])
def test_terminal_events_mark_idle(monkeypatch, rel):
    idle = []
    monkeypatch.setattr(inference_muzak, "mark_idle", idle.append)
    _stdin(monkeypatch, _load(rel))
    assert prompt_hook.main() == 0
    assert idle == ["claude-fixture-1"]


def test_stop_scans_marker_and_fires(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps(
        {"message": {"role": "assistant",
                     "content": [{"type": "text", "text": "all done [vibes:ok|Fixture finished]"}]}}) + "\n")
    fired = []
    monkeypatch.setenv("NHJ_HOOK_WAIT", "0")
    monkeypatch.setenv("NHJ_HOOK_MIN", "0")
    monkeypatch.setattr(hook, "_seen_get", lambda sid: "baseline")
    monkeypatch.setattr(hook, "_seen_set", lambda sid, sig: None)
    monkeypatch.setattr(hook, "_fire_local", lambda ev: fired.append(ev) or True)
    monkeypatch.setattr(inference_muzak, "mark_idle", lambda sid: None)
    _stdin(monkeypatch, _load("claude-code/stop.json", transcript))
    assert hook.main() == 0
    assert [(e.intent, e.message, e.source) for e in fired] == [("ok", "Fixture finished", "proj-alpha")]


def test_stop_hook_caps_marker_message_at_500_before_validation(tmp_path, monkeypatch):
    long_message = "x" * 600
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps(
        {"message": {"role": "assistant",
                     "content": [{"type": "text", "text": f"done [vibes:ok|{long_message}]"}]}}) + "\n")
    fired = []
    monkeypatch.setenv("NHJ_HOOK_WAIT", "0")
    monkeypatch.setenv("NHJ_HOOK_MIN", "0")
    monkeypatch.setattr(hook, "_seen_get", lambda sid: "baseline")
    monkeypatch.setattr(hook, "_seen_set", lambda sid, sig: None)
    monkeypatch.setattr(hook, "_fire_local", lambda ev: fired.append(ev) or True)
    monkeypatch.setattr(inference_muzak, "mark_idle", lambda sid: None)
    _stdin(monkeypatch, _load("claude-code/stop.json", transcript))

    assert hook.main() == 0                 # capped by the hook, so the turn never fails
    assert [e.message for e in fired] == ["x" * 500]


def test_notification_maps_permission_prompt(monkeypatch):
    fired = []
    monkeypatch.setattr(hook, "_fire_local", lambda ev: fired.append(ev) or True)
    monkeypatch.setattr(hook, "_log", lambda msg: None)
    _stdin(monkeypatch, _load("claude-code/notification.json"))
    assert notify_hook.main() == 0
    assert [(e.intent, e.message, e.source) for e in fired] == [
        ("attn", "needs your permission", "proj-alpha")]
