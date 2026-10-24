"""Spool/temp filenames must be unique within one millisecond (#48 B3).

AudioAdapter.fire submits the return clip and the result back to back through
submit_audio; with {ms}-{pid} names the second write overwrote the first."""
from __future__ import annotations

import json
import time

from nhj import inference_muzak, resources
from nhj.adapters import audio


def test_submit_audio_same_millisecond_keeps_both_events(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000.0)
    monkeypatch.setattr(inference_muzak, "start_controller", lambda: True)
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
