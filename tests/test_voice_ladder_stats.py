"""The voice ladder records the rung that served it, the engine that served it, and what it cost.

`SPEC.md` §9 sets a different budget for each rung (bank/chime ≤ 300 ms, live voice ≤ 2 s warm), so a
single "voice latency" number would have been useless: the whole point is knowing how often each rung is
hit and how long the expensive one takes. These tests drive the real ladder with the engines stubbed the
way the rest of the suite stubs them.

One thing the ladder made visible while these were being written: a repeated *message* is not a repeated
*line*. A non-literal vibe asks the character for a phrase each time (`Character._phrase` picks one), so
two identical messages can be two different cache keys and two syntheses. The cache tests below use
`literal=True` — the `nhj say` path — because that is the one where the words are pinned and the cache
rung can be reached on purpose.
"""
from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from nhj import stats as S
from nhj.adapters import audio
from nhj.characters import Character


def _wav_bytes(seconds: float = 0.05, rate: int = 48000) -> bytes:
    """A real, RIFF-consistent WAV. `cache_hit` checks the declared size against the file, so fake
    bytes would be deleted as a truncated entry and the cache rung would never be reached."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture
def speakable(tmp_path, monkeypatch):
    """A ladder that can speak, that talks to nobody, and that records into the hermetic state dir."""
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path / "clips")
    monkeypatch.setattr(audio, "_TMP_ROOT", tmp_path / "tmp")
    monkeypatch.setattr(audio, "_AUDIO_ROOT", tmp_path / "audio")
    monkeypatch.setattr("nhj.state.get_flag", lambda *a, **k: False)     # no return clip, no mute
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(audio, "_emit_voice",
                        lambda path, **kw: emitted.append((path, kw)))
    return emitted


def _rows():
    return S.read_voice()


# ---- the rungs -------------------------------------------------------------
def test_a_bare_marker_is_recorded_as_the_bank_rung_with_no_engine(speakable, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", Path("/nonexistent-user-cache"))   # force the bundled bank
    assert audio.AudioAdapter().fire("err", "", Character.load("karren")) is True
    rows = _rows()
    assert [r["outcome"] for r in rows] == ["bank"]
    assert rows[0]["engine"] == "-"                      # nothing synthesized anything
    assert "render_ms" not in rows[0]
    assert rows[0]["bytes"] > 44                         # a clip really was picked, and measured


def test_a_first_novel_line_is_the_synth_rung_with_the_engine_named(speakable, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "qwen")
    monkeypatch.setattr(audio, "synth_qwen", lambda *a, **k: _wav_bytes())
    assert audio.AudioAdapter().fire("ok", "the build is green", Character.load("jan")) is True
    row = _rows()[-1]
    assert row["outcome"] == "synth" and row["engine"] == "qwen"
    assert row["render_ms"] is not None and row["ladder_ms"] >= row["render_ms"]


def test_the_same_line_twice_reports_synth_then_cache(speakable, monkeypatch):
    """`literal` (the `nhj say` path) is what makes this deterministic: a normal vibe asks the
    character for a phrase each time, so two identical messages routinely produce two different lines
    and therefore two different cache keys. That is behaviour, not a bug — and it is why the hit rate is
    worth measuring rather than assuming."""
    monkeypatch.setenv("TTS_ENGINE", "qwen")
    calls: list[int] = []

    def synth(*a, **k):
        calls.append(1)
        return _wav_bytes()

    monkeypatch.setattr(audio, "synth_qwen", synth)
    a, char = audio.AudioAdapter(), Character.load("jan")
    assert a.fire("ok", "tests are green", char, literal=True) is True
    assert a.fire("ok", "tests are green", char, literal=True) is True
    rows = _rows()
    assert [r["outcome"] for r in rows] == ["synth", "cache"]
    assert len(calls) == 1                               # the second line never reached an engine
    assert "render_ms" not in rows[1]                    # a cache hit has no synthesis to blame or praise
    assert rows[1]["bytes"] > 44


def test_the_hit_rate_is_the_ratio_these_two_calls_just_produced(speakable, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "qwen")
    monkeypatch.setattr(audio, "synth_qwen", lambda *a, **k: _wav_bytes())
    a, char = audio.AudioAdapter(), Character.load("jan")
    a.fire("ok", "one novel line", char, literal=True)
    a.fire("ok", "one novel line", char, literal=True)
    hit = S.voice_summary()["hit_rate"]
    assert hit == {"answered_from_disk": 1, "spoken": 2, "rate": 0.5}


def test_the_chime_rung_is_measured_and_does_not_synthesize_anything(speakable, monkeypatch):
    monkeypatch.setattr("nhj.orchestration.play_chime", lambda: True)
    assert audio.AudioAdapter().fire("ok", "done", Character.load("jan"),
                                     voice_mode="chime") is True
    row = _rows()[-1]
    assert row["outcome"] == "chime" and "render_ms" not in row
    assert speakable == []                               # a chime is not a voice-bus clip


def test_a_chime_that_asked_and_got_nothing_is_recorded_as_silent(speakable, monkeypatch):
    monkeypatch.setattr("nhj.orchestration.play_chime", lambda: False)
    assert audio.AudioAdapter().fire("ok", "done", Character.load("jan"),
                                     voice_mode="chime") is False
    row = _rows()[-1]
    assert row["outcome"] == "none" and "chime" in row["error"]


# ---- failure paths: the counters that replace the log grep -----------------
def test_a_dead_engine_becomes_an_error_counter_naming_the_engine(speakable, monkeypatch):
    """The `audiocpp TTS error: 500` lines in worker.log, as a number on the rung that failed."""
    monkeypatch.setenv("TTS_ENGINE", "audiocpp")
    monkeypatch.setenv("NHJ_AUDIOCPP_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("NHJ_AUDIOCPP_VOICES", str(Path("/nonexistent-voices")))
    assert audio.AudioAdapter().fire("ok", "the build broke", Character.load("jan")) is True
    row = _rows()[-1]
    assert row["outcome"] == "fallback"                       # generic words, not the message
    assert row["engine"] == "audiocpp"
    assert "audiocpp" in row["error"]
    eng = S.voice_summary()["engines"]["audiocpp"]
    assert eng["errors"] == 1 and eng["renders"] == 1


def test_a_bank_served_without_a_synthesis_attempt_is_not_a_fallback(speakable, monkeypatch):
    """A minimal install serving its bank is the design working; a bank served after a failed
    synthesis is a degraded vibe. The two must not be the same row in the numbers."""
    monkeypatch.setenv("TTS_ENGINE", "none")                  # no synthesis attempted at all
    assert audio.AudioAdapter().fire("ok", "any message at all", Character.load("jan")) is True
    assert _rows()[-1]["outcome"] == "bank"


def test_a_literal_line_with_nothing_to_speak_is_recorded_as_none(speakable, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", Path("/nonexistent-user-cache"))
    ok = audio.AudioAdapter().fire("ok", "exactly these words", Character.load("jan"), literal=True)
    assert ok is False
    row = _rows()[-1]
    assert row["outcome"] == "none" and "literal" in row["error"]
    assert speakable == []


def test_a_line_with_no_bank_clip_at_all_is_recorded_not_skipped(speakable, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", Path("/nonexistent-user-cache"))
    monkeypatch.setattr(audio, "pick_bank_clip", lambda *a, **k: None)
    assert audio.AudioAdapter().fire("ok", "words", Character.load("jan")) is False
    row = _rows()[-1]
    assert row["outcome"] == "none" and row["error"]


def test_a_raising_ladder_is_still_measured_and_still_raises(speakable, monkeypatch):
    """`finally`, not `else`: a ladder that throws is exactly the traversal worth having on record."""
    def boom(path, **kw):
        raise RuntimeError("mixer vanished")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", Path("/nonexistent-user-cache"))
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "_emit_voice", boom)
    with pytest.raises(RuntimeError):
        audio.AudioAdapter().fire("err", "", Character.load("karren"))
    row = _rows()[-1]
    assert row["outcome"] == "bank" and row["error"].startswith("RuntimeError")


# ---- what must NOT be recorded --------------------------------------------
def test_the_ladder_records_exactly_one_line_per_attempt(speakable, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "qwen")
    monkeypatch.setattr(audio, "synth_qwen", lambda *a, **k: _wav_bytes())
    a, char = audio.AudioAdapter(), Character.load("jan")
    for i in range(4):
        a.fire("ok", f"line {i}", char)
    assert len(_rows()) == 4


def test_measurement_does_not_change_the_clips_that_are_played(speakable, monkeypatch):
    """The instrumented ladder must emit what the uninstrumented one did — same path, same speed."""
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", Path("/nonexistent-user-cache"))
    char = Character.load("karren")
    a = audio.AudioAdapter()
    a.fire("err", "", char)
    a.fire("err", "", char)
    paths = [p for p, _ in speakable]
    assert len(paths) == 2 and all(p.endswith(".wav") for p in paths)
    assert all("clips/karren/err/" in p.replace("\\", "/") for p in paths)


def test_the_recorded_clip_is_the_one_handed_to_the_mixer(speakable, monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "none")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", Path("/nonexistent-user-cache"))
    audio.AudioAdapter().fire("err", "", Character.load("karren"))
    row = _rows()[-1]
    played = Path(speakable[0][0])
    assert row["bytes"] == played.stat().st_size
