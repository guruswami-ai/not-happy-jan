"""_synth() handles both mlx-audio return protocols: iterator-of-segments and single
result (#69). Guards against a silent audio regression on an mlx-audio version bump."""
from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from nhj import tts_server


class _Seg:
    def __init__(self, audio):
        self.audio = audio


def _setup(monkeypatch, fake_out):
    class _Model:
        def generate(self, **kw):
            return fake_out

    monkeypatch.setattr(tts_server, "_model", _Model())
    monkeypatch.setattr(tts_server, "_resolve_voice", lambda v: ("/ref.wav", "ref text"))
    # Bypass soundfile: return the concatenated samples verbatim so we can assert shape.
    monkeypatch.setattr(tts_server, "_to_wav_bytes",
                        lambda audio, sr: np.asarray(audio, dtype=np.float32).tobytes())


def test_synth_iterator_protocol(monkeypatch):
    segs = [_Seg(np.array([0.1, 0.2], dtype=np.float32)),
            _Seg(np.array([0.3], dtype=np.float32))]
    _setup(monkeypatch, iter(segs))
    wav, reason = tts_server._synth("hi", "jan")
    assert reason is None
    assert np.frombuffer(wav, dtype=np.float32).size == 3


def test_synth_single_result_protocol(monkeypatch):
    _setup(monkeypatch, _Seg(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)))
    wav, reason = tts_server._synth("hi", "jan")
    assert reason is None
    assert np.frombuffer(wav, dtype=np.float32).size == 4
