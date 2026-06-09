"""Event-contract tests (issue #3).

One model (nhj/event.py) drives MCP, local + remote hooks, queue and worker — so
they accept/validate/serialise/forward the same options. Covers validation,
inline parsing, local==remote payloads (incl. explicit character), and idempotent
hook install that preserves unrelated entries.
"""
from __future__ import annotations

import json
import subprocess
import sys
from io import StringIO

import pytest

from nhj import event


# ---- validation -----------------------------------------------------------

def test_validate_accepts_and_coerces():
    ev = event.validate("OK", message="hi", vibe_level="7.0", speed="1.5", emotion="Alert")
    assert ev.intent == "ok" and ev.vibe_level == 7 and ev.speed == 1.5 and ev.emotion == "alert"


@pytest.mark.parametrize("kw", [
    {"intent": "nope"},                       # bad enum
    {"intent": "ok", "vibe_level": 99},       # out of range
    {"intent": "ok", "vibe_level": "x"},      # not numeric
    {"intent": "ok", "speed": 9.0},           # out of range
    {"intent": "ok", "verbosity": "loud"},    # bad enum
    {"intent": "ok", "emotion": "happy"},     # bad enum
])
def test_validate_rejects(kw):
    with pytest.raises(event.VibeValidationError):
        event.validate(**kw)


# ---- inline parsing -------------------------------------------------------

def test_from_inline_reports_unknown_and_keeps_known():
    ev, unknown = event.from_inline("ok", {"speed": "1.2", "bogus": "x"}, "msg")
    assert ev.speed == 1.2 and unknown == ["bogus"]


def test_from_inline_marker_character_wins_over_inline():
    ev, _ = event.from_inline("ok", {"character": "bazza"}, "m", character="karren")
    assert ev.character == "karren"           # the [Karren:ok] 'who' beats inline character=


def test_options_survive_roundtrip():
    ev, _ = event.from_inline("err", {"vibe_level": "8", "voice_variant": "x"}, "boom", "karren")
    kw = ev.to_kwargs()
    assert kw["intent"] == "err" and kw["vibe_level"] == 8 and kw["character"] == "karren"
    assert kw["voice_variant"] == "x" and kw["message"] == "boom"


# ---- local == remote payload ----------------------------------------------

def test_local_dispatch_sends_full_event(monkeypatch):
    from nhj import hook
    captured = {}

    class FakeQM:
        def add(self, **kw): captured.update(kw)
        def start_worker_if_needed(self, *a, **k): pass

    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda *a, **k: FakeQM())
    ev = event.validate("err", message="boom", vibe_level=7, character="karren", speed=1.2)
    hook._fire_local(ev)
    assert captured == ev.to_kwargs()         # every field reaches the queue


def test_remote_dispatch_sends_same_full_event(monkeypatch):
    pytest.importorskip("fastmcp")
    from nhj import hook
    captured = {}

    class FakeClient:
        def __init__(self, url): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def call_tool(self, name, args): captured["name"] = name; captured["args"] = args

    monkeypatch.setattr("fastmcp.Client", FakeClient)
    ev = event.validate("err", message="boom", vibe_level=7, character="karren", speed=1.2)
    assert hook._fire_remote("http://x", ev) is True
    assert captured["name"] == "nhj_vibe"
    assert captured["args"] == ev.to_kwargs()  # identical to the local payload, incl. character


def test_remote_dispatch_failure_is_not_reported_as_fired(monkeypatch):
    from nhj import hook
    logs = []
    monkeypatch.setenv("NHJ_REMOTE_URL", "http://remote")
    monkeypatch.setenv("NHJ_HOOK_WAIT", "0")
    monkeypatch.setenv("NHJ_HOOK_MIN", "0")
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
        "session_id": "s1",
        "transcript_path": "/tmp/transcript.jsonl",
    })))
    monkeypatch.setattr(hook, "_seen_get", lambda sid: "baseline")
    monkeypatch.setattr(hook, "_seen_set", lambda sid, sig: None)
    monkeypatch.setattr(hook, "_read_last_assistant_text", lambda path: "[vibes:ok]")
    monkeypatch.setattr(hook, "_known_whos", lambda: {"vibes"})
    monkeypatch.setattr(hook, "_fire_remote", lambda url, ev: False)
    monkeypatch.setattr(hook, "_log", logs.append)

    assert hook.main() == 0
    assert any("hook complete fired=0" in line for line in logs)
    assert not any(line.startswith("fired who=") for line in logs)


def test_hook_log_path_is_user_specific():
    from nhj import hook, resources

    assert hook._LOG == resources.log_file("hook.log")


# ---- hook install idempotency ---------------------------------------------

def test_install_hook_idempotent_and_preserves_unrelated(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [
        {"matcher": "", "hooks": [{"type": "command", "command": "/usr/bin/other-tool"}]}
    ]}}))
    for _ in range(2):  # twice → idempotent
        r = subprocess.run([sys.executable, "-m", "nhj.cli", "install-hook", "--settings", str(settings)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    hooks = json.loads(settings.read_text())["hooks"]
    assert set(hooks) >= {"Stop", "StopFailure", "UserPromptSubmit", "SessionEnd"}  # all four installed
    stop_blocks = hooks["Stop"]
    # exec-form: NHJ entry has args=["-m", "nhj.hook"]; command is the python path
    nhj_count = sum(
        1 for b in stop_blocks for h in b["hooks"]
        if "-m" in (h.get("args") or []) and
           any("nhj" in a for a in (h.get("args") or []))
    )
    assert nhj_count == 1                                               # NHJ Stop hook not duplicated
    assert any("other-tool" in h.get("command", "")
               for b in stop_blocks for h in b["hooks"])               # unrelated entry preserved
    # StopFailure idempotent — only one NHJ entry
    sf_count = sum(
        1 for b in hooks["StopFailure"] for h in b["hooks"]
        if "-m" in (h.get("args") or []) and
           any("nhj.prompt_hook" == a for a in (h.get("args") or []))
    )
    assert sf_count == 1


# ---- StopFailure handling -------------------------------------------------

def test_stop_failure_calls_mark_idle(monkeypatch):
    """StopFailure must call inference_muzak.mark_idle — same as SessionEnd."""
    from nhj import prompt_hook, inference_muzak

    idle_calls = []
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
        "hook_event_name": "StopFailure",
        "session_id": "test-session-sf",
    })))
    monkeypatch.setattr(inference_muzak, "mark_idle", lambda sid: idle_calls.append(sid))
    # Suppress the warn vibe for this test
    monkeypatch.setattr("nhj.state.get_flag", lambda name, default=False: False)

    result = prompt_hook.main()
    assert result == 0
    assert idle_calls == ["test-session-sf"]


def test_stop_failure_warn_vibe_is_gated(monkeypatch):
    from nhj import prompt_hook, inference_muzak

    queued = {}

    class FakeQM:
        def add(self, **kw):
            queued.update(kw)

        def start_worker_if_needed(self):
            queued["started"] = True

    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
        "hook_event_name": "StopFailure",
        "session_id": "test-session-sf",
    })))
    monkeypatch.setattr(inference_muzak, "mark_idle", lambda sid: None)
    monkeypatch.setattr("nhj.state.get_flag", lambda name, default=False: name == "stop_failure_vibe")
    monkeypatch.setattr("nhj.queue_manager.QueueManager", lambda: FakeQM())

    assert prompt_hook.main() == 0
    assert queued["intent"] == "warn"
    assert "API error" in queued["message"]
    assert queued["started"] is True
