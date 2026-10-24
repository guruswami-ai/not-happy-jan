"""A dependency-light trance synth (numpy + scipy) that renders the default stem pack.

138 BPM, A minor, Am-F-C-G, 16 bars. Tempo is exact and every layer wraps around the loop
point (tails continue into bar 1), so the pack loops seamlessly. Layers are separate stems
(kick, hats, clap, bass, pad, arp) so the director can fade them in and out. Deterministic:
same seed, same audio. Original code, MIT like the rest of NHJ.
"""
from __future__ import annotations

import numpy as np
from scipy import signal

from nhj.music.pack import SR, loop_samples, samples_per_bar, save_pack

BPM = 138.0
BARS = 16
LAYERS = ("kick", "hats", "clap", "bass", "pad", "arp")
_BEAT = 60.0 / BPM
_STEP = _BEAT / 4
_PROG = [([57, 60, 64], 33), ([53, 57, 60], 29), ([60, 64, 67], 36), ([55, 59, 62], 31)]   # Am F C G
_GAIN = {"kick": 1.0, "hats": 0.9, "clap": 0.8, "bass": 1.0, "pad": 0.8, "arp": 0.7}


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _sos(kind: str, fc, order: int = 4):
    return signal.butter(order, fc, kind, fs=SR, output="sos")


def _env(n: int, tau: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau * SR))


def _saw(f: float, n: int, phase: float = 0.0) -> np.ndarray:
    return 2 * ((phase + np.cumsum(np.full(n, f / SR))) % 1.0) - 1


class _Voices:
    """One-shot voices; the rng is passed in so the whole render is deterministic."""

    def __init__(self, rng: np.random.Generator):
        self.rng = rng

    def kick(self) -> np.ndarray:
        n = int(0.30 * SR)
        t = np.arange(n) / SR
        ph = 2 * np.pi * np.cumsum(48 + 110 * np.exp(-t / 0.032)) / SR
        return (np.sin(ph) * np.exp(-t / 0.12) + 0.25 * self.rng.standard_normal(n) * np.exp(-t / 0.004)) * 0.95

    def hat(self, open_: bool) -> np.ndarray:
        n = int((0.16 if open_ else 0.045) * SR)
        s = signal.sosfilt(_sos("hp", 7500), self.rng.standard_normal(n)) * _env(n, 0.07 if open_ else 0.015)
        return s * (0.30 if open_ else 0.18)

    def clap(self) -> np.ndarray:
        n = int(0.35 * SR)
        s = np.zeros(n)
        for k in range(3):
            o = int(k * 0.011 * SR)
            s[o:] += self.rng.standard_normal(n - o) * _env(n - o, 0.012 if k < 2 else 0.09)
        return signal.sosfilt(_sos("bp", [900, 4500]), s) * 0.35


def _bass_note(f: float, n: int) -> np.ndarray:
    t = np.arange(n)
    s = signal.sosfilt(_sos("lp", 420), _saw(f, n)) * 0.9 + np.sin(2 * np.pi * f * t / SR) * 0.7
    return s * np.minimum(1, t / (0.003 * SR)) * np.minimum(1, (n - t) / (0.03 * SR)) * 0.55


def _supersaw(f: float, n: int, side: int) -> np.ndarray:
    det = np.array([-14, -8, -3, 0, 3, 8, 14]) / 1200.0
    s = np.zeros(n)
    for i, d in enumerate(det):
        s += _saw(f * 2 ** d, n, phase=(i * 0.137 + (0.3 if side else 0.0)) % 1.0)
    t = np.arange(n)
    return signal.sosfilt(_sos("lp", 3800), s / len(det)) * np.minimum(1, t / (0.35 * SR)) * np.minimum(
        1, (n - t) / (0.25 * SR))


def _pluck(f: float, n: int) -> np.ndarray:
    tone = 0.6 * _saw(f, n) + 0.4 * np.sign(np.sin(2 * np.pi * f * np.arange(n) / SR))
    return signal.sosfilt(_sos("lp", 5200), tone) * _env(n, 0.09) * 0.28


def _reverb(x: np.ndarray, rng: np.random.Generator, secs: float, wet: float) -> np.ndarray:
    n = int(secs * SR)
    ir = signal.sosfilt(_sos("lp", 6000), rng.standard_normal((n, 2)) * _env(n, secs / 4.0)[:, None], axis=0)
    ir /= np.sqrt((ir ** 2).sum())
    pad = np.concatenate([x, x, x])                      # circular convolution: the tail wraps the loop
    tail = np.stack([signal.fftconvolve(pad[:, c], ir[:, c])[:len(pad)] for c in (0, 1)], axis=1)
    return x * (1 - wet) + tail[len(x):2 * len(x)] * wet


def render_stems(seed: int = 7, bars: int = BARS) -> dict[str, np.ndarray]:
    """The six stems, each (loop_samples, 2) float32, peak-normalised to -3 dBFS (before gain)."""
    rng = np.random.default_rng(seed)
    v = _Voices(rng)
    n_total = loop_samples(BPM, bars)
    spb = samples_per_bar(BPM)
    stems = {k: np.zeros((n_total, 2)) for k in LAYERS}

    def at(bar: int, step: float = 0.0) -> int:
        return int(round(bar * spb + step * _STEP * SR))

    def put(layer: str, sig: np.ndarray, pos: int, gain: float = 1.0) -> None:
        idx = (pos + np.arange(len(sig))) % n_total
        stems[layer][idx] += (sig if sig.ndim == 2 else np.stack([sig, sig], axis=1)) * gain

    for bar in range(bars):
        for beat in range(4):
            put("kick", v.kick(), at(bar, beat * 4))
        for beat in (1, 3):
            put("clap", v.clap(), at(bar, beat * 4))
        for st in range(16):
            if st % 4 == 2:
                put("hats", v.hat(True), at(bar, st))
            else:
                put("hats", v.hat(False), at(bar, st), 0.5 if st % 2 == 0 else 0.8)
        tones, root = _PROG[bar % 4]
        for st in (2, 6, 10, 14):                        # rolling off-beat bass
            put("bass", _bass_note(_hz(root), int(0.24 * _BEAT * 4 * SR)), at(bar, st))
        nbar = int(round(spb)) + int(0.4 * SR)
        for side in (0, 1):
            chord = sum(_supersaw(_hz(m), nbar, side) for m in tones) / 3.0
            stems["pad"][(at(bar) + np.arange(nbar)) % n_total, side] += chord * 0.45
        notes = [tones[0], tones[1], tones[2], tones[1] + 12] * 4
        for st in range(16):
            p = _pluck(_hz(notes[st] + (12 if st % 8 >= 4 else 0)), int(1.6 * _STEP * SR))
            put("arp", np.stack([p * (0.8 if st % 2 else 1.0), p * (1.0 if st % 2 else 0.8)], axis=1), at(bar, st))

    echo = np.zeros_like(stems["arp"])                   # dotted-eighth echo on the arp
    d = int(3 * _STEP * SR)
    for k, g in ((1, 0.42), (2, 0.2)):
        echo += np.roll(stems["arp"][:, ::-1], d * k, axis=0) * g
    stems["arp"] = stems["arp"] + echo

    def pump(depth: float, rel: float = 0.14) -> np.ndarray:        # sidechain "pump" on every beat
        return (1 - depth * np.exp(-((np.arange(n_total) / SR) % _BEAT) / rel))[:, None]

    stems["pad"] = _reverb(stems["pad"], rng, 2.2, 0.4) * pump(0.85)
    stems["bass"] *= pump(0.55, 0.09)
    stems["arp"] *= pump(0.5)
    stems["clap"] = _reverb(stems["clap"], rng, 1.1, 0.3)
    out = {}
    for k, x in stems.items():
        peak = float(np.max(np.abs(x))) or 1.0
        out[k] = (x / peak * 10 ** (-3.0 / 20) * _GAIN[k]).astype(np.float32)
    return out


def build_default_pack(dest, seed: int = 7):
    return save_pack(dest, "nhj-synth-trance", BPM, BARS, render_stems(seed), {
        "generator": "nhj.music.synth", "seed": seed, "licence": "MIT (original NHJ code)",
        "style": "uplifting trance, A minor, Am-F-C-G"})
