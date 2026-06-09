"""A minimal install (TTS_ENGINE=none, no model) must still speak via the bundled bank —
for bare markers AND message vibes (fallback) — instead of going silent."""
from __future__ import annotations

from pathlib import Path

from nhj.adapters import audio
from nhj.characters import Character


def test_minimal_plays_bundled_bank_with_tts_none(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", Path("/nonexistent-user-cache"))   # force bundled
    monkeypatch.setattr("nhj.state.get_flag", lambda *a, **k: False)             # skip return-clip
    emitted = []
    monkeypatch.setattr(audio, "_emit_voice", lambda path, **k: emitted.append(path))

    a = audio.AudioAdapter()
    assert a.available() is True                       # bank is always available

    char = Character.load("karren")
    assert a.fire("err", "", char, vibe_level=5) is True            # bare marker
    assert a.fire("err", "the build broke", char, vibe_level=5) is True  # message → bank fallback

    assert len(emitted) == 2
    # resolves to the bundled bank (checkout: <root>/clips, wheel: nhj/_bundled/clips)
    assert all(p.replace("\\", "/").endswith(".wav") and "/clips/karren/err/" in p.replace("\\", "/")
               for p in emitted)
