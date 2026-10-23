"""audio.cpp (Breeze) TTS backend: request shape, voice map, and the empty-body OOM trap."""
from __future__ import annotations

import pytest

from nhj.adapters import audio

WAV = b"RIFF" + b"\x00" * 2000


class _Resp:
    def __init__(self, content=WAV, status=200):
        self.content, self.status = content, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(self.status)


@pytest.fixture
def voices(tmp_path):
    (tmp_path / "karen.txt").write_text("  Attention please.  \n")
    return tmp_path


def test_request_shape_sends_per_voice_reference_text(voices, monkeypatch):
    seen = {}
    monkeypatch.setattr(audio.requests, "post", lambda url, json, timeout: seen.update(url=url, body=json) or _Resp())
    assert audio.synth_audiocpp("Build broke", "http://x:8092/", "karen", str(voices)) == WAV
    assert seen["url"] == "http://x:8092/v1/audio/speech"
    b = seen["body"]
    assert b["model"] == "breeze" and b["voice"] == "karen" and b["input"] == "Build broke"
    assert b["options"]["reference_text"] == "Attention please."
    assert "instruction" not in b and "instruction" not in b["options"]   # fights the clone


@pytest.mark.parametrize("body", [b"", b"RIFF", b"x" * 5000])
def test_empty_or_non_wav_body_is_a_failure(voices, monkeypatch, body):
    monkeypatch.setattr(audio.requests, "post", lambda *a, **k: _Resp(body))
    assert audio.synth_audiocpp("hi", "http://x", "karen", str(voices)) is None


def test_missing_transcript_or_http_error_returns_none(voices, monkeypatch):
    assert audio.synth_audiocpp("hi", "http://x", "nobody", str(voices)) is None
    monkeypatch.setattr(audio.requests, "post", lambda *a, **k: _Resp(status=500))
    assert audio.synth_audiocpp("hi", "http://x", "karen", str(voices)) is None


def test_voice_map_and_passthrough():
    cfg = {"voices": {"jan": "ozlady"}}
    assert audio.audiocpp_voice("jan", cfg) == "ozlady"
    assert audio.audiocpp_voice("bazza", cfg) == "bazza"
