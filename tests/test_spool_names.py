"""Spool/temp filenames must be unique within one millisecond (#48 B3).

AudioAdapter.fire submits the return clip and the result back to back through
submit_audio; with {ms}-{pid} names the second write overwrote the first."""
from __future__ import annotations

import json
import os
import subprocess
import time

from nhj import inference_muzak, resources
from nhj.adapters import audio

# Bound at collection, before conftest stubs the module attribute (#80).
_REAL_START_CONTROLLER = inference_muzak.start_controller


def test_submit_audio_same_millisecond_keeps_both_events(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(inference_muzak, "start_controller", lambda: True)
    assert inference_muzak.submit_audio("/tmp/a.wav", bus="voice")
    assert inference_muzak.submit_audio("/tmp/b.wav", bus="voice")
    events = sorted(resources.audio_events_dir(create=False).glob("*.json"))
    assert len(events) == 2
    paths = {json.loads(e.read_text())["path"] for e in events}
    assert paths == {"/tmp/a.wav", "/tmp/b.wav"}


def test_start_controller_does_not_spawn_when_the_test_flag_is_set(monkeypatch):
    """Worker subprocesses inherit NHJ_TEST_NO_MIXER; the real start_controller must no-op."""
    popped = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popped.append(a))
    assert os.environ.get("NHJ_TEST_NO_MIXER")
    assert _REAL_START_CONTROLLER() is True
    assert popped == []


def test_submit_audio_keeps_both_events_without_a_per_test_stub():
    """#80: conftest stubs start_controller, so submit_audio must not spawn a mixer
    that could eat one of the spool files before the glob."""
    assert inference_muzak.submit_audio("/tmp/a.wav", bus="voice")
    assert inference_muzak.submit_audio("/tmp/b.wav", bus="voice")
    events = sorted(resources.audio_events_dir(create=False).glob("*.json"))
    assert len(events) == 2
    paths = {json.loads(e.read_text())["path"] for e in events}
    assert paths == {"/tmp/a.wav", "/tmp/b.wav"}


def test_write_temp_wav_same_millisecond_keeps_both_files(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(audio, "_TMP_ROOT", tmp_path)
    p1 = audio._write_temp_wav(b"one")
    p2 = audio._write_temp_wav(b"two")
    assert p1 != p2
    from pathlib import Path
    assert Path(p1).read_bytes() == b"one"
    assert Path(p2).read_bytes() == b"two"
