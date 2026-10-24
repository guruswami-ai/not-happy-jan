"""Multi-agent policy: speak vs chime, the digest, dead-session eviction, hold-call cue."""
from __future__ import annotations

import subprocess
import sys
import threading

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
    assert mz._any_busy() is False                                # pure: a dead owner does not count
    assert "crashed" in (mz._read().get("busy") or {})            # the getter did not write
    mz.evict_stale()
    assert "crashed" not in (mz._read().get("busy") or {}) and "crashed" not in (mz._read().get("pids") or {})


def test_concurrent_mark_busy_keeps_every_session():
    n = 40
    threads = [threading.Thread(target=mz.mark_busy, args=(f"s{i}",)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    busy = mz._read().get("busy") or {}
    assert set(busy) == {f"s{i}" for i in range(n)}


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


# ---- agent-CLI commands: nhj vibe / busy / idle -----------------------------
import json  # noqa: E402

from typer.testing import CliRunner  # noqa: E402

from nhj import cli  # noqa: E402


def _run(args, payload=None):
    return CliRunner().invoke(cli.cli, args, input=json.dumps(payload) if payload is not None else "")


def test_busy_and_idle_use_the_hook_payload(monkeypatch):
    r = _run(["busy"], {"session_id": "qwen-1", "cwd": "/work/proj"})
    assert r.exit_code == 0 and r.output == ""
    assert "qwen-1" in mz._read()["busy"]
    assert _run(["idle"], {"session_id": "qwen-1", "cwd": "/work/proj"}).exit_code == 0
    assert "qwen-1" not in mz._read().get("busy", {})


def test_busy_respects_voice_opt_out():
    gate.set_rule("off", "quiet")
    _run(["busy"], {"session_id": "s", "cwd": "/work/quiet"})
    assert not mz._read().get("busy")


def test_vibe_takes_its_source_from_the_payload_cwd(monkeypatch):
    seen = []
    from nhj import queue_manager
    monkeypatch.setattr(queue_manager.QueueManager, "add", lambda self, **kw: seen.append(kw))
    monkeypatch.setattr(queue_manager.QueueManager, "start_worker_if_needed", lambda self: None)
    assert _run(["vibe", "ok", "-m", "done"], {"cwd": "/work/reactor"}).exit_code == 0
    assert seen[0]["source"] == "reactor" and seen[0]["message"] == "done"
    assert _run(["vibe", "ok", "-s", "explicit"], {"cwd": "/work/reactor"}).exit_code == 0
    assert seen[1]["source"] == "explicit"
    assert _run(["vibe", "bogus"]).exit_code == 2                      # invalid intent is rejected


def test_stdin_json_never_blocks_or_raises_on_garbage():
    assert _run(["vibe", "ok"], None).exit_code == 0
    r = CliRunner().invoke(cli.cli, ["idle"], input="not json")
    assert r.exit_code == 0


# ---- digest delivery (review findings) --------------------------------------
def _speak(monkeypatch, message, synth):
    """Run AudioAdapter.fire for an `ok` with the audiocpp engine and a stubbed synth."""
    seen = {}
    monkeypatch.setenv("TTS_ENGINE", "audiocpp")
    monkeypatch.setattr(audio, "_audiocpp_cfg", lambda: {})
    monkeypatch.setattr(audio, "cache_hit", lambda *a, **k: None)
    monkeypatch.setattr(audio, "pick_return_clip", lambda: None)
    monkeypatch.setattr(audio, "pick_bank_clip", lambda v, i: "/bank.wav")
    monkeypatch.setattr(audio, "_normalize_voice_wav", lambda w: w)
    monkeypatch.setattr(audio, "_save_and_cache", lambda *a, **k: None)
    monkeypatch.setattr(audio, "_write_temp_wav", lambda w: "/tmp/x.wav")
    monkeypatch.setattr(audio, "_emit_voice", lambda path, **k: seen.setdefault("path", path))

    def fake(text, *a, **k):
        seen["text"] = text
        return synth
    monkeypatch.setattr(audio, "synth_audiocpp", fake)
    audio.AudioAdapter().fire("ok", message, resolve_character("ok"))
    return seen


def test_digest_is_spoken_and_cleared_once_delivered(monkeypatch):
    orchestration.note_chime("a"); orchestration.note_chime("b")
    seen = _speak(monkeypatch, "all done", b"RIFF" + b"\0" * 2000)
    assert "2 other tasks finished while you waited." in seen["text"]
    assert orchestration.peek_digest() == 0


def test_bare_marker_keeps_the_digest_for_the_next_spoken_completion(monkeypatch):
    orchestration.note_chime("a")
    seen = _speak(monkeypatch, "", b"RIFF" + b"\0" * 2000)         # bare marker -> bundled clip
    assert seen["path"] == "/bank.wav" and "text" not in seen
    assert orchestration.peek_digest() == 1                          # not lost


def test_failed_synthesis_keeps_the_digest(monkeypatch):
    orchestration.note_chime("a")
    _speak(monkeypatch, "all done", None)                            # synth failed -> bank fallback
    assert orchestration.peek_digest() == 1


def test_clear_digest_only_drops_what_was_spoken():
    for s in "abc":
        orchestration.note_chime(s)
    orchestration.clear_digest(2)                                    # one more arrived meanwhile
    assert orchestration.peek_digest() == 1


# ---- claky env overrides (review finding) -----------------------------------
def test_env_only_local_transport_is_effective_everywhere(monkeypatch):
    from nhj.adapters import claky
    monkeypatch.setattr(claky.config, "section", lambda n: {"transport": "bifrost", "url": "", "token_env": "NOPE_TOKEN"})
    monkeypatch.setattr(claky.Path, "read_text", lambda self: "")      # no ~/.env fallback token
    monkeypatch.delenv("NOPE_TOKEN", raising=False)
    assert claky.ClakyAdapter().available() is False                  # bifrost, nothing configured
    monkeypatch.setenv("CLAKY_TRANSPORT", "local"); monkeypatch.setenv("CLAKY_HOST", "10.0.0.9")
    assert claky._cfg()["transport"] == "local" and claky._cfg()["host"] == "10.0.0.9"
    assert claky.ClakyAdapter().available() is True


# ---- second round of review findings ----------------------------------------
import time as _time  # noqa: E402


def test_manual_music_session_is_not_a_working_agent():
    """`nhj mode rave` / `nhj muzak start` add a never-expiring __manual__ busy entry: it must keep the
    music on but must not turn every real completion into a chime."""
    mz._update(lambda d: d.setdefault("busy", {}).update({"__manual__": _time.time() + 10 * 365 * 86400}))
    assert mz._any_busy() is True and mz._real_busy() is False
    assert orchestration.voice_mode("ok") == "speak"
    _busy("worker")
    assert mz._real_busy() is True and orchestration.voice_mode("ok") == "chime"


def test_claky_sender_does_not_lose_a_message_published_while_it_sends(tmp_path, monkeypatch):
    from nhj.adapters import claky
    monkeypatch.setattr(claky, "_pending", lambda: tmp_path / "pending")
    monkeypatch.setattr(claky, "_last", lambda: tmp_path / "last")
    monkeypatch.setattr(claky, "_lock", lambda: tmp_path / "lock")
    monkeypatch.setattr(claky.config, "section", lambda n: {"transport": "local", "host": "h", "min_interval_s": 0})
    sent = []

    def send(cfg, text):
        sent.append(text)
        if text == "A":
            claky._publish("B")          # a newer vibe arrives mid-send
    monkeypatch.setattr(claky, "_send_local", send)
    claky._publish("A")
    assert claky.sender_main() == 0
    assert sent == ["A", "B"] and not (tmp_path / "pending").exists()


def test_playback_output_device_zero_from_config_is_honoured(monkeypatch):
    from nhj import duck_player
    monkeypatch.delenv("NHJ_OUTPUT_DEVICE", raising=False)
    import nhj.config as cfgmod
    monkeypatch.setattr(cfgmod, "section", lambda n: {"output_device": 0})
    devs = [{"name": "first", "max_output_channels": 2}, {"name": "second", "max_output_channels": 2}]
    assert duck_player.output_device(devs) == 0
    monkeypatch.setattr(cfgmod, "section", lambda n: {"output_device": ""})
    assert duck_player.output_device(devs) is None


def test_finish_marks_idle_before_it_announces_and_uses_the_payload_once(monkeypatch):
    order = []
    from nhj import queue_manager
    monkeypatch.setattr(mz, "mark_idle", lambda sid="": order.append(("idle", sid)))
    monkeypatch.setattr(queue_manager.QueueManager, "add", lambda self, **kw: order.append(("vibe", kw["source"], kw["intent"])))
    monkeypatch.setattr(queue_manager.QueueManager, "start_worker_if_needed", lambda self: None)
    r = _run(["finish", "ok", "-m", "done"], {"session_id": "s9", "cwd": "/work/elsewhere"})
    assert r.exit_code == 0 and r.output == ""
    assert order == [("idle", "s9"), ("vibe", "elsewhere", "ok")]
    assert _run(["finish", "bogus"], {"session_id": "s9"}).exit_code == 2
