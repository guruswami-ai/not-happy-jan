"""Multi-agent policy: speak vs chime, the digest, dead-session eviction, hold-call cue."""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from nhj import gate, inference_muzak as mz, orchestration, worker
from nhj.adapters import audio
from nhj.characters import resolve_character


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NHJ_MUZAK_STATE", str(tmp_path / "music.json"))
    monkeypatch.setenv("NHJ_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("NHJ_ORCHESTRATION", raising=False)
    monkeypatch.setattr(mz, "start_controller", lambda: True)          # never spawn a mixer
    monkeypatch.setattr(orchestration.config, "section", lambda n: {})
    monkeypatch.setattr(gate.config, "section", lambda n: {})
    monkeypatch.setattr(mz, "_owner_pid", lambda: None)


def _busy(*sids):
    for s in sids:
        mz.mark_busy(s)


# ---- decision ---------------------------------------------------------------
def test_completion_chimes_only_while_other_sessions_are_busy():
    assert orchestration.voice_mode("ok") == "speak"            # nobody busy
    _busy("orchestrator")
    assert orchestration.voice_mode("ok") == "chime"
    assert orchestration.voice_mode("celebrate") == "chime"
    mz.mark_idle("orchestrator")
    assert orchestration.voice_mode("ok") == "speak"


@pytest.mark.parametrize("intent", ["warn", "err", "attn"])
def test_urgent_intents_always_speak(intent):
    _busy("orchestrator")
    assert orchestration.voice_mode(intent) == "speak"


def test_always_speak_mode_restores_upstream_behaviour(monkeypatch):
    _busy("orchestrator")
    monkeypatch.setenv("NHJ_ORCHESTRATION", "always-speak")
    assert orchestration.voice_mode("ok") == "speak"
    monkeypatch.setenv("NHJ_ORCHESTRATION", "nonsense")          # unknown value -> safe default
    assert orchestration.voice_mode("ok") == "chime"


# ---- digest ----------------------------------------------------------------
def test_digest_counts_resets_and_pluralises():
    assert orchestration.take_digest() == 0
    orchestration.note_chime("a"); orchestration.note_chime("b")
    assert orchestration.take_digest() == 2
    assert orchestration.take_digest() == 0
    assert orchestration.digest_sentence(1) == "1 other task finished while you waited."
    assert orchestration.digest_sentence(3).startswith("3 other tasks")
    assert orchestration.digest_sentence(0) == ""


# ---- worker + adapter wiring ------------------------------------------------
class _Spy:
    channel = "voice"
    def __init__(self): self.calls = []
    def available(self): return True
    def fire(self, intent, message, character, vibe_level=5, **kw): self.calls.append((intent, kw.get("voice_mode")))


def test_worker_tells_voice_adapters_to_chime_and_counts_it():
    spy = _Spy()
    _busy("orchestrator")
    worker._dispatch({"intent": "ok", "source": "worker1"}, [spy])
    worker._dispatch({"intent": "err", "source": "worker1"}, [spy])
    assert spy.calls == [("ok", "chime"), ("err", "speak")]
    assert orchestration.take_digest() == 1                       # only the chimed one counts


def test_opted_out_project_is_not_counted_or_chimed():
    gate.set_rule("off", "quiet")
    spy = _Spy()
    _busy("orchestrator")
    worker._dispatch({"intent": "ok", "source": "quiet"}, [spy])
    assert spy.calls == [] and orchestration.take_digest() == 0


def test_audio_adapter_chime_skips_tts_and_return_clip(monkeypatch):
    played = []
    monkeypatch.setattr(orchestration, "play_chime", lambda: played.append("chime") or True)
    monkeypatch.setattr(audio, "_emit_voice", lambda *a, **k: played.append("voice"))
    assert audio.AudioAdapter().fire("ok", "done", resolve_character("ok"), voice_mode="chime") is True
    assert played == ["chime"]


def test_return_clip_only_when_the_music_actually_paused(monkeypatch):
    emitted = []
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "pick_return_clip", lambda: "/ret.wav")
    monkeypatch.setattr(audio, "pick_bank_clip", lambda v, i: "/bank.wav")
    monkeypatch.setattr(audio, "_emit_voice", lambda path, **k: emitted.append(path))
    ch = resolve_character("err")
    _busy("orchestrator")                                          # music still playing
    audio.AudioAdapter().fire("err", "", ch)
    assert emitted == ["/bank.wav"]
    emitted.clear(); mz.mark_idle("orchestrator")                  # everyone idle -> music paused
    audio.AudioAdapter().fire("err", "", ch)
    assert emitted == ["/ret.wav", "/bank.wav"]


# ---- dead sessions ----------------------------------------------------------
def test_session_whose_agent_process_died_is_evicted(monkeypatch):
    p = subprocess.Popen([sys.executable, "-c", ""]); p.wait()
    monkeypatch.setattr(mz, "_owner_pid", lambda: p.pid)          # a pid that no longer exists
    mz.mark_busy("crashed")
    assert mz._read()["pids"]["crashed"] == p.pid
    assert mz._any_busy() is False
    assert "crashed" not in (mz._read().get("busy") or {}) and "crashed" not in (mz._read().get("pids") or {})


def test_live_owner_keeps_the_session_busy(monkeypatch):
    import os
    monkeypatch.setattr(mz, "_owner_pid", lambda: os.getpid())
    mz.mark_busy("alive")
    assert mz._any_busy() is True
    mz.mark_idle("alive")
    assert mz._any_busy() is False and not mz._read().get("pids")


def test_owner_pid_ignores_throwaway_shells(monkeypatch):
    monkeypatch.undo()                                             # real _owner_pid
    monkeypatch.setattr(mz.os, "getppid", lambda: 1)
    monkeypatch.setattr(mz.Path, "read_text", lambda self: "bash\n")
    assert mz._owner_pid() is None
