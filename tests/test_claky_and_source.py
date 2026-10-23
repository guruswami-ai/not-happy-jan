"""Session ``source`` plumbing, the Vestaboard (claky) adapter, and the Notification hook.

No network / audio / GPU: transports are monkeypatched and state lives in tmp_path.
"""
from __future__ import annotations

import io
import json

import pytest

from nhj import event, hook, notify_hook
from nhj.adapters import claky
from nhj.queue_manager import QueueManager


def test_source_roundtrips_through_the_queue(tmp_path):
    qm = QueueManager(queue_dir=str(tmp_path / "q"))
    ev = event.validate("err", message="boom", source="hog11:reachy")
    qm.add(**ev.to_kwargs())
    _, task = qm.claim_next()
    assert task["source"] == "hog11:reachy"


def test_source_is_optional_and_clamped():
    assert event.validate("ok").source == ""
    assert len(event.validate("ok", source="x" * 500).source) == 60


def test_source_label_local_and_remote(monkeypatch):
    assert hook.source_label("/home/paul/reachy/") == "reachy"
    assert hook.source_label("") == ""
    monkeypatch.setattr("socket.gethostname", lambda: "hog11.lan")
    assert hook.source_label("/x/proj", remote=True) == "hog11:proj"


def test_wrap_fits_board_and_never_splits_words():
    text = claky.compose("attn", "needs your permission to run a very long command name", "reachy")
    lines = text.split("\n")
    assert len(lines) <= claky.ROWS and all(len(ln) <= claky.COLS for ln in lines)
    assert lines[0].startswith("NEEDS YOU")


def test_compose_headlines_by_intent():
    assert claky.compose("err", "build broke").startswith("NOT HAPPY JAN")
    assert claky.compose("ok", "", "proj") == "ALL DONE proj"


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(claky, "_pending", lambda: tmp_path / "pending")
    monkeypatch.setattr(claky, "_last", lambda: tmp_path / "last")
    monkeypatch.setattr(claky, "_lock", lambda: tmp_path / "lock")
    spawned = []
    monkeypatch.setattr(claky.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    return tmp_path, spawned


def _cfg(monkeypatch, **c):
    monkeypatch.setattr(claky, "_cfg", lambda: c)


def test_adapter_unavailable_without_target(monkeypatch, board):
    _cfg(monkeypatch, transport="bifrost")
    monkeypatch.delenv("CLAKY_URL", raising=False)
    assert claky.ClakyAdapter().available() is False
    assert claky.ClakyAdapter().fire("ok", "x", None) is False


def test_adapter_writes_pending_and_starts_sender_once_configured(monkeypatch, board):
    tmp, spawned = board
    _cfg(monkeypatch, transport="local", host="10.0.0.1")
    assert claky.ClakyAdapter().fire("err", "build broke", None, source="reachy") is True
    assert (tmp / "pending").read_text().startswith("NOT HAPPY JAN reachy")
    assert len(spawned) == 1


def test_adapter_skips_step_and_filtered_intents_without_failing(monkeypatch, board):
    tmp, spawned = board
    _cfg(monkeypatch, transport="local", host="10.0.0.1")
    assert claky.ClakyAdapter().fire("step", "thinking", None) is True
    assert not (tmp / "pending").exists() and not spawned


def test_sender_waits_for_rate_limit_and_sends_only_newest(monkeypatch, board):
    tmp, _ = board
    _cfg(monkeypatch, transport="local", host="h", min_interval_s=0)
    sent = []
    monkeypatch.setattr(claky, "_send_local", lambda cfg, text: sent.append(text))
    (tmp / "pending").write_text("NEWEST")
    assert claky.sender_main() == 0
    assert sent == ["NEWEST"] and not (tmp / "pending").exists()


@pytest.mark.parametrize("kind,intent", [("permission_prompt", "attn"),
                                          ("elicitation_dialog", "attn"),
                                          ("idle_prompt", "warn")])
def test_notification_hook_maps_events(monkeypatch, kind, intent):
    fired = []
    monkeypatch.setattr(hook, "_fire_local", lambda ev: fired.append(ev) or True)
    monkeypatch.setattr(hook, "_log", lambda msg: None)   # do not write the real hook.log
    monkeypatch.delenv("NHJ_REMOTE_URL", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"notification_type": kind, "cwd": "/home/paul/reachy", "message": "ignored"})))
    assert notify_hook.main() == 0
    assert fired[0].intent == intent and fired[0].source == "reachy"


def test_stop_hook_unwinds_hold_music_on_a_fresh_install(tmp_path, monkeypatch):
    """Regression: prompt_hook defaults `muzak` to ON, so the Stop hook must too — otherwise a
    session with no saved flag starts the hold music and is never marked idle."""
    from nhj import inference_muzak
    monkeypatch.setenv("NHJ_STATE_FILE", str(tmp_path / "state.json"))      # no saved flags
    idle = []
    monkeypatch.setattr(inference_muzak, "mark_idle", lambda sid="": idle.append(sid))
    monkeypatch.setattr(hook, "_log", lambda m: None)
    monkeypatch.setattr(hook, "_fire_local", lambda ev: True)
    monkeypatch.setenv("NHJ_HOOK_WAIT", "0.05")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "s1", "cwd": "/x/proj", "transcript_path": str(tmp_path / "none.jsonl")})))
    hook.main()
    assert idle == ["s1"]
