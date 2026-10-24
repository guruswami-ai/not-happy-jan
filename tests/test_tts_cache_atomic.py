"""TTS clip cache must not publish a partial WAV (#48 B7), and must not serve
one that is already truncated or keep orphan temp files forever (#59)."""
from __future__ import annotations

import os
import struct
import time
from pathlib import Path

import pytest

from nhj.adapters import audio


def _wav(payload: bytes = b"\x00" * 200) -> bytes:
    """Minimal RIFF bytes whose declared size matches the actual length."""
    return b"RIFF" + struct.pack("<I", len(payload) + 4) + b"WAVE" + payload


def test_save_publishes_the_whole_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path)
    wav = _wav()
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
        audio._save_and_cache("jan", "hello", _wav())
    assert audio.cache_hit("jan", "hello") is None
    assert list((tmp_path / "cache").glob("*.wav")) == []


def test_truncated_existing_entry_is_a_miss_and_is_removed(tmp_path, monkeypatch):
    """#59: a pre-#56 truncated clip already in the cache must not be served."""
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path)
    audio._save_and_cache("jan", "hello", _wav())
    cached = Path(audio.cache_hit("jan", "hello"))
    with cached.open("r+b") as f:
        f.truncate(150)                       # header still declares the old size
    assert audio.cache_hit("jan", "hello") is None
    assert not cached.exists()


def test_non_riff_cache_entry_is_a_miss_and_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path)
    p = audio._cache_path("jan", "hello")
    p.parent.mkdir(parents=True)
    p.write_bytes(b"NOPE" + b"\x00" * 200)
    assert audio.cache_hit("jan", "hello") is None
    assert not p.exists()


def test_stale_orphan_tmp_is_swept_fresh_one_is_kept(tmp_path, monkeypatch):
    """#59: a hard kill mid-write leaks a .*.tmp the *.wav prune never saw."""
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    old = cache_dir / ".abc.wav.123.dead.tmp"
    fresh = cache_dir / ".def.wav.456.live.tmp"
    old.write_bytes(b"partial")
    fresh.write_bytes(b"partial")
    past = time.time() - 7200
    os.utime(old, (past, past))

    audio._save_and_cache("jan", "hello", _wav())

    assert not old.exists()
    assert fresh.exists()
