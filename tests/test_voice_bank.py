"""The bundled bare-marker voice bank ships in the package and resolves, so a minimal
install speaks in-character with no model download."""
from __future__ import annotations

from nhj import resources
from nhj.adapters import audio


def test_bundled_bank_dir_ships_with_the_cast():
    bank = resources.bundled("clips")
    assert bank.is_dir(), "bundled voice bank (clips/) is missing"
    # every cast member + their core feedback states are covered
    for voice, states in {
        "jan": ("ok", "step", "celebrate", "warn"),
        "bazza": ("warn", "err", "attn"),
        "karren": ("err", "attn"),
        "jan-whispering": ("ok", "warn"),
    }.items():
        for state in states:
            d = bank / voice / state
            assert d.is_dir() and list(d.glob("*.wav")), f"no bank clips for {voice}/{state}"


def test_pick_bank_clip_resolves_from_the_bundled_bank(tmp_path, monkeypatch):
    # point the cache at an empty dir so resolution must fall through to the bundled bank
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path / "empty-cache")
    for voice, state in (("karren", "err"), ("jan", "ok"), ("bazza", "attn")):
        clip = audio.pick_bank_clip(voice, state)
        assert clip and clip.endswith(".wav"), f"bank clip not found for {voice}/{state}"


def test_bundled_bank_has_no_message_templates():
    """Bank clips are bare markers — none should be a {message} template render."""
    bank = resources.bundled("clips")
    bad = [p.name for p in bank.rglob("*.wav") if "{" in p.name or "message" in p.name.lower()]
    assert not bad, f"template clips leaked into the bank: {bad}"


def test_bundled_bank_is_loudness_normalised():
    """Bank clips must be levelled to the voice target (≈ -19.5 dBFS RMS) like the rest
    of the audio — no clip louder than the target, so they sit consistently in the mix."""
    import pytest
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    from nhj.adapters.audio import _VOICE_TARGET_DBFS

    bank = resources.bundled("clips")
    for p in bank.rglob("*.wav"):
        data, _ = sf.read(p, dtype="float32", always_2d=False)
        mono = data.mean(axis=1) if getattr(data, "ndim", 1) > 1 else data
        rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64))))) or 1e-9
        dbfs = 20 * np.log10(rms)
        assert dbfs <= _VOICE_TARGET_DBFS + 0.5, f"{p.name} too loud: {dbfs:.1f} dBFS"
