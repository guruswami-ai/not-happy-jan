"""`nhj voices` must distinguish bundled-bank availability from a missing live-TTS
reference, so a clean minimal install (bank present, no ref.wav) reads as usable rather
than broken. Regression for the bug where every voice showed `ref.wav: missing` with no
indication the bundled bank was installed and working."""
from __future__ import annotations

from typer.testing import CliRunner

from nhj import resources
from nhj.adapters import audio
from nhj.cli import cli


def test_bank_clip_count_finds_the_bundled_bank(tmp_path, monkeypatch):
    # Empty user cache → resolution must fall through to the packaged bank.
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path / "empty-cache")
    for voice in ("jan", "bazza", "karren", "jan-whispering"):
        assert audio.bank_clip_count(voice) > 0, f"no bundled bank counted for {voice}"


def test_bank_clip_count_is_zero_for_an_unknown_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path / "empty-cache")
    assert audio.bank_clip_count("no-such-voice") == 0


def test_bank_clip_count_dedupes_user_cache_over_bundled(tmp_path, monkeypatch):
    """A user-built clip shadowing a bundled one (same intent+filename) counts once."""
    bundled = resources.bundled("clips")
    voice = "jan"
    intent = next(d.name for d in (bundled / voice).iterdir() if d.is_dir())
    sample = next((bundled / voice / intent).glob("*.wav"))

    cache = tmp_path / "cache"
    dst = cache / voice / intent
    dst.mkdir(parents=True)
    (dst / sample.name).write_bytes(sample.read_bytes())   # same (intent, filename)
    monkeypatch.setattr(audio, "_CLIPS_ROOT", cache)

    bundled_total = sum(
        1 for _ in (bundled / voice).glob("*/*.wav"))
    # The shadowing copy must not inflate the count beyond the bundled set.
    assert audio.bank_clip_count(voice) == bundled_total


def test_voices_command_reports_bank_not_just_missing_ref(tmp_path, monkeypatch):
    """End-to-end: with no live references, `nhj voices` still shows the cast as ready
    via the bundled bank — never a blanket 'missing' that implies no voice at all."""
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path / "empty-cache")

    # Simulate a minimal install: no ref.wav anywhere.
    from nhj.characters import Character
    monkeypatch.setattr(Character, "ref_wav", lambda self, *a, **k: None)

    result = CliRunner().invoke(cli, ["voices"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "ready" in out, "minimal install should report a usable voice"
    assert "unavailable" not in out, "bundled-bank voices must not read as unavailable"
    assert "clips" in out, "bank availability (clip count) should be shown"
