"""Live-TTS voice levelling (matches the pre-rendered banks at ~-19 LUFS)."""
from __future__ import annotations

import io

import pytest

np = pytest.importorskip("numpy")
sf = pytest.importorskip("soundfile")

from nhj.adapters import audio


def _sine_wav(dbfs_rms: float, seconds: float = 1.0, rate: int = 24000) -> bytes:
    n = int(rate * seconds)
    amp = (10 ** (dbfs_rms / 20)) * np.sqrt(2)          # sine RMS = amp/√2
    t = np.arange(n) / rate
    sig = (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, sig, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _rms_dbfs(wav: bytes) -> float:
    data, _ = sf.read(io.BytesIO(wav), dtype="float32")
    return 20 * np.log10(float(np.sqrt(np.mean(data.astype(np.float64) ** 2))))


def _peak_dbfs(wav: bytes) -> float:
    data, _ = sf.read(io.BytesIO(wav), dtype="float32")
    return 20 * np.log10(float(np.max(np.abs(data))))


def test_quiet_voice_boosted_to_target():
    out = audio._normalize_voice_wav(_sine_wav(-30))
    assert abs(_rms_dbfs(out) - audio._VOICE_TARGET_DBFS) < 1.0


def test_loud_voice_cut_to_target():
    out = audio._normalize_voice_wav(_sine_wav(-8))
    assert abs(_rms_dbfs(out) - audio._VOICE_TARGET_DBFS) < 1.0


def test_peak_ceiling_respected_on_high_crest():
    # 5% full-scale sine in silence: low RMS, high peak → normalising would clip without the ceiling.
    rate, n = 24000, 24000
    sig = np.zeros(n, dtype=np.float32)
    burst = int(n * 0.05)
    t = np.arange(burst) / rate
    sig[:burst] = 0.98 * np.sin(2 * np.pi * 220 * t)
    buf = io.BytesIO()
    sf.write(buf, sig, rate, format="WAV", subtype="PCM_16")
    out = audio._normalize_voice_wav(buf.getvalue())
    assert _peak_dbfs(out) <= audio._VOICE_PEAK_CEIL_DBFS + 0.2     # never exceeds the ceiling


def test_silent_clip_untouched():
    buf = io.BytesIO()
    sf.write(buf, np.zeros(24000, dtype=np.float32), 24000, format="WAV", subtype="PCM_16")
    wav = buf.getvalue()
    assert audio._normalize_voice_wav(wav) == wav                  # near-silent → as-is


def test_garbage_bytes_never_raise():
    junk = b"not a wav at all"
    assert audio._normalize_voice_wav(junk) == junk                # returns input, no exception
