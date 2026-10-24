"""TTS clip cache must not publish a partial WAV (#48 B7)."""
from __future__ import annotations

from pathlib import Path

import pytest

from nhj.adapters import audio


def test_save_publishes_the_whole_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path)
    wav = b"RIFF" + b"\x00" * 200
    audio._save_and_cache("jan", "hello", wav, "mid")
    hit = audio.cache_hit("jan", "hello", "mid")
    assert hit is not None
    assert Path(hit).read_bytes() == wav
    leftovers = [p.name for p in (tmp_path / "cache").iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_failed_write_does_not_become_a_cache_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path)
    real_write = Path.write_bytes

    def boom(self, data):
        real_write(self, data[:20])
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(OSError, match="disk full"):
        audio._save_and_cache("jan", "hello", b"RIFF" + b"\x00" * 200)
    assert audio.cache_hit("jan", "hello") is None
    assert list((tmp_path / "cache").glob("*.wav")) == []
