"""DSP for the lo-fi and ambient stem styles (numpy + scipy). Original code, MIT like the rest of NHJ.

Kept apart from ``styles.py`` so that listing or choosing a style never imports the heavy stack.
Every style renders the same six stem names the director knows (kick, bass, hats, clap, arp,
pad); each style says in its ``cfg`` and in its pack ``arrangement`` what those names mean.

Loop rules, all of which matter because the pack wraps around forever:
  * notes are placed modulo the loop length, so tails run into bar 1;
  * reverb and noise textures are filtered circularly (tile, filter, keep the middle);
  * tape wow/flutter complete a whole number of cycles per loop and every stem gets the
    SAME warp, so layers never drift apart;
  * one shared gain fits the full mix to a loudness target and a peak ceiling.
Deterministic: same seed, same audio.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal

from nhj.music.pack import SR, loop_samples, samples_per_bar

LAYERS = ("kick", "hats", "clap", "bass", "pad", "arp")
MIX_RMS_DB = -15.0           # loudness target of the full arrangement (RMS), so packs sit together
MIX_CEIL_DB = -1.5           # and its peak never goes above this


# ---- small helpers ------------------------------------------------------------------------
def _sos(kind: str, fc, order: int = 4):
    return signal.butter(order, fc, kind, fs=SR, output="sos")


def _hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def _decay(n: int, tau: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (tau * SR))


def _swell(n: int, attack: float, release: float) -> np.ndarray:
    i = np.arange(n)
    return np.minimum(1.0, i / (attack * SR + 1.0)) * np.minimum(1.0, (n - i) / (release * SR + 1.0))


def _sat(x: np.ndarray, drive: float) -> np.ndarray:
    return np.tanh(x * drive) / math.tanh(drive)


def _put(stem: np.ndarray, sig: np.ndarray, pos: int, gain: float = 1.0, pan: float = 0.0) -> None:
    """Add *sig* (mono or stereo) at *pos*, wrapping around the loop."""
    n = len(stem)
    idx = (pos + np.arange(len(sig))) % n
    if sig.ndim == 1:
        th = (max(-1.0, min(1.0, pan)) + 1.0) * math.pi / 4.0
        sig = np.stack([sig * math.cos(th) * math.sqrt(2), sig * math.sin(th) * math.sqrt(2)], axis=1)
    stem[idx] += sig * gain


def _circ(x: np.ndarray, sos) -> np.ndarray:
    """Filter a periodic signal so the result is periodic too (tile, filter, keep the middle)."""
    n = len(x)
    return signal.sosfilt(sos, np.concatenate([x, x, x]), axis=0)[n:2 * n]


def _reverb(x: np.ndarray, rng: np.random.Generator, secs: float, wet: float, lp: float = 6000.0) -> np.ndarray:
    """Circular reverb: the tail wraps the loop, so there is no seam."""
    n = int(secs * SR)
    ir = signal.sosfilt(_sos("lp", lp), rng.standard_normal((n, 2)) * _decay(n, secs / 4.0)[:, None], axis=0)
    ir /= np.sqrt((ir ** 2).sum())
    pad = np.concatenate([x, x, x])
    tail = np.stack([signal.oaconvolve(pad[:, c], ir[:, c])[:len(pad)] for c in (0, 1)], axis=1)
    return x * (1 - wet) + tail[len(x):2 * len(x)] * wet


def _tape(x: np.ndarray, wow: float = 0.0011, flutter: float = 0.0004) -> np.ndarray:
    """Tape wow and flutter as a smooth time warp with whole-cycle LFOs, so it wraps seamlessly."""
    n = len(x)
    secs = n / SR
    k1, k2 = max(1, round(0.5 * secs)), max(1, round(5.3 * secs))          # whole cycles per loop
    a1 = wow * SR / (2 * math.pi * k1 / secs)                                # samples of excursion for that depth
    a2 = flutter * SR / (2 * math.pi * k2 / secs)
    i = np.arange(n)
    pos = i + a1 * np.sin(2 * math.pi * k1 * i / n) + a2 * np.sin(2 * math.pi * k2 * i / n + 1.3)
    pad = 512
    ext = np.concatenate([x[-pad:], x, x[:pad]])
    p = pos + pad
    i0 = np.floor(p).astype(int)
    frac = (p - i0)[:, None]
    return ext[i0] * (1 - frac) + ext[i0 + 1] * frac


def _balance(stems: dict[str, np.ndarray], levels: dict[str, tuple]) -> dict[str, np.ndarray]:
    """Set each stem's own level: ("peak", dB) or ("rms", dB). Drums are judged by peak, sustained layers by RMS."""
    out = {}
    for k, x in stems.items():
        kind, db = levels[k]
        ref = float(np.max(np.abs(x))) if kind == "peak" else float(np.sqrt(np.mean(x ** 2)))
        out[k] = x * (10 ** (db / 20) / (ref or 1.0))
    return out


def _fit(stems: dict[str, np.ndarray], rms_db: float = MIX_RMS_DB, ceil_db: float = MIX_CEIL_DB) -> dict:
    """One shared gain: full mix to the loudness target, unless that would peak over the ceiling."""
    mix = sum(stems.values())
    rms = float(np.sqrt(np.mean(mix ** 2))) or 1.0
    peak = float(np.max(np.abs(mix))) or 1.0
    g = min(10 ** (rms_db / 20) / rms, 10 ** (ceil_db / 20) / peak)
    return {k: (v * g).astype(np.float32) for k, v in stems.items()}


# ---- voices -------------------------------------------------------------------------------
def _rhodes(f: float, n: int, vel: float, phase: float = 0.0) -> np.ndarray:
    """FM electric piano: sine carrier, ratio-1 modulator that decays, and a short bell-like tine."""
    t = _t(n)
    decay = 1.8 * (110.0 / f) ** 0.35
    idx = (0.9 + 1.5 * vel) * np.exp(-t / 0.5)
    y = np.sin(2 * np.pi * f * t + idx * np.sin(2 * np.pi * f * t))
    y += 0.2 * vel * np.sin(2 * np.pi * f * 7.0 * t) * np.exp(-t / 0.05)
    y *= np.exp(-t / decay) * (1.0 + 0.1 * np.sin(2 * np.pi * 4.6 * t + phase)) * _swell(n, 0.004, 0.09)
    return _sat(y * vel * 0.5, 1.6)


def _nylon(f: float, n: int, vel: float, rng: np.random.Generator) -> np.ndarray:
    """Plucked nylon string by additive synthesis: upper partials die quickly."""
    t = _t(n)
    y = np.zeros(n)
    for h in range(1, 11):
        tau = 1.5 / (1 + 0.85 * (h - 1))
        y += np.sin(2 * np.pi * f * h * math.sqrt(1 + 0.0003 * h * h) * t) * np.exp(-t / tau) / h
    k = min(n, int(0.02 * SR))
    y[:k] += signal.sosfilt(_sos("lp", 3500, 2), rng.standard_normal(k)) * _decay(k, 0.004) * 0.25
    return y * _swell(n, 0.002, 0.12) * vel * 0.8


def _bell(f: float, n: int, ratios=((1.0, 1.0, 3.0), (2.76, 0.35, 1.6), (5.4, 0.15, 0.9), (8.93, 0.07, 0.5)),
          vel: float = 1.0) -> np.ndarray:
    t = _t(n)
    y = sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-t / tau) for r, a, tau in ratios)
    return y * _swell(n, 0.002, 0.2) * vel * 0.4


def _vibe(f: float, n: int, vel: float = 0.8) -> np.ndarray:
    t = _t(n)
    y = (np.sin(2 * np.pi * f * t) * np.exp(-t / 1.6) + 0.25 * np.sin(2 * np.pi * f * 4 * t) * np.exp(-t / 0.5)
         + 0.08 * np.sin(2 * np.pi * f * 10 * t) * np.exp(-t / 0.2))
    return y * (1 + 0.18 * np.sin(2 * np.pi * 5.5 * t)) * _swell(n, 0.003, 0.15) * vel * 0.5


def _warm(f: float, n: int, bright: float, side: int, attack: float = 0.5, release: float = 0.9) -> np.ndarray:
    """Soft detuned saw pad under a low-pass."""
    s = np.zeros(n)
    for i, d in enumerate((-7, 0, 7)):
        ph = (i * 0.31 + 0.17 * side) % 1.0
        s += 2 * ((ph + np.cumsum(np.full(n, f * 2 ** (d / 1200) / SR))) % 1.0) - 1
    return signal.sosfilt(_sos("lp", bright, 2), s / 3.0) * _swell(n, attack, release)


def _sub(f: float, n: int, soft: float = 0.12) -> np.ndarray:
    t = _t(n)
    y = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * f * 2 * t)
    return _sat(y * 0.9, 1.3) * _swell(n, 0.01, soft) * 0.6


def _kick(rng: np.random.Generator) -> np.ndarray:
    n = int(0.38 * SR)
    t = _t(n)
    y = np.sin(2 * np.pi * np.cumsum(46 + 60 * np.exp(-t / 0.05)) / SR) * np.exp(-t / 0.14)
    y += 0.12 * rng.standard_normal(n) * np.exp(-t / 0.003)
    return signal.sosfilt(_sos("lp", 2500, 2), y) * 0.95


def _snare(rng: np.random.Generator, vel: float = 1.0) -> np.ndarray:
    n = int(0.3 * SR)
    t = _t(n)
    noise = signal.sosfilt(_sos("bp", [1400, 5200], 2), rng.standard_normal(n)) * np.exp(-t / 0.075)
    body = np.sin(2 * np.pi * 185 * t) * np.exp(-t / 0.05)
    return signal.sosfilt(_sos("lp", 7000, 2), 0.55 * noise + 0.45 * body) * vel


def _hat(rng: np.random.Generator, open_: bool, vel: float) -> np.ndarray:
    n = int((0.18 if open_ else 0.05) * SR)
    return signal.sosfilt(_sos("hp", 6500, 2), rng.standard_normal(n)) * _decay(n, 0.07 if open_ else 0.016) * vel * 0.3


def _brush(rng: np.random.Generator, vel: float) -> np.ndarray:
    n = int(0.24 * SR)
    t = _t(n)
    env = np.minimum(1.0, t / 0.03) * np.exp(-t / 0.09)
    return signal.sosfilt(_sos("bp", [2200, 7500], 2), rng.standard_normal(n)) * env * vel * 0.35


def _crackle(rng: np.random.Generator, n: int, density: float = 22.0) -> np.ndarray:
    """Vinyl: sparse pops plus a faint hiss, built to wrap seamlessly."""
    x = np.zeros(n)
    k = rng.poisson(density * n / SR)
    x[rng.integers(0, n, k)] = rng.exponential(0.22, k) * rng.choice([-1.0, 1.0], k)
    x += rng.standard_normal(n) * 0.006
    return _circ(x, _sos("bp", [900, 8000], 2))


def _rain(rng: np.random.Generator, n: int) -> np.ndarray:
    """Steady rain: filtered noise whose level wanders slowly, periodic by construction."""
    noise = rng.standard_normal(n)
    wander = _circ(rng.standard_normal(n), _sos("lp", 0.6, 2))
    wander = 0.65 + 0.35 * wander / (np.max(np.abs(wander)) or 1.0)
    return _circ(noise, _sos("bp", [1800, 7500], 2)) * wander * 0.35


# ---- lo-fi --------------------------------------------------------------------------------
def lofi(seed: int, bars: int, cfg: dict) -> dict[str, np.ndarray]:
    """Boom-bap lo-fi. Roles: kick, clap = snare/brush, hats = shaker + texture, bass,
    arp = chord comping (keys or guitar), pad = melody + a soft pad."""
    rng = np.random.default_rng(seed)
    bpm = cfg["bpm"]
    n = loop_samples(bpm, bars)
    beat = 60.0 / bpm
    swing = cfg["swing"]
    stems = {k: np.zeros((n, 2)) for k in LAYERS}

    def at(bar: int, e: int, jitter: float = 0.0) -> int:           # e: 0..7 eighth step in the bar
        b, off = divmod(e, 2)
        return int(round(((bar * 4 + b + (swing if off else 0.0)) * beat + jitter) * SR))

    for bar in range(bars):
        ch = cfg["prog"][bar % len(cfg["prog"])]
        for e in cfg["kick"][bar % len(cfg["kick"])]:
            _put(stems["kick"], _kick(rng), at(bar, e, rng.normal(0, 0.004)), 0.7 + 0.2 * rng.random())
        for e in (2, 6):
            if cfg["snare"] == "brush":
                _put(stems["clap"], _brush(rng, 1.0), at(bar, e, 0.01 + rng.normal(0, 0.004)), 0.8)
            else:
                _put(stems["clap"], _snare(rng, 0.7), at(bar, e, 0.012 + rng.normal(0, 0.004)), 0.8 + 0.1 * rng.random())
        if rng.random() < 0.4:
            _put(stems["clap"], _snare(rng, 0.25), at(bar, 7, rng.normal(0, 0.004)), 0.5)
        for e in range(8):
            v = (0.55 if e % 2 == 0 else 0.3) * (0.85 + 0.3 * rng.random())
            if cfg["snare"] == "brush":
                if e % 2 == 0:
                    _put(stems["hats"], _brush(rng, v * 0.5), at(bar, e, rng.normal(0, 0.003)), 0.6)
            else:
                _put(stems["hats"], _hat(rng, e == 7 and bar % 2 == 1, v), at(bar, e, rng.normal(0, 0.003)),
                     0.9, pan=0.25 if e % 2 else -0.15)
        root_f = _hz(ch["root"])
        for e, beats, vel in cfg["bass"]:
            length = int(beats * beat * SR)
            _put(stems["bass"], _sub(root_f, length) * vel, at(bar, e, rng.normal(0, 0.004)))
        for e, beats, vel in cfg["comp"]:
            length = int(beats * beat * SR)
            for j, m in enumerate(ch["voicing"]):
                f = _hz(m)
                if cfg["keys"] == "rhodes":
                    note = _rhodes(f, length, vel * (0.9 + 0.2 * rng.random()), phase=j)
                else:
                    note = _nylon(f, length, vel * (0.85 + 0.3 * rng.random()), rng)
                _put(stems["arp"], note, at(bar, e, j * 0.011 + rng.normal(0, 0.003)), 1.0, pan=-0.3 + 0.2 * j)
        pad_len = int((4.0 * beat + 1.0) * SR)
        for side in (0, 1):
            for m in ch["voicing"][:3]:
                _put(stems["pad"][:, side:side + 1], _warm(_hz(m - 12), pad_len, cfg["pad_bright"], side)[:, None],
                     at(bar, 0), 0.16)

    blocks = max(1, bars // 4)
    for blk in range(blocks):
        for step, midi, ln in cfg["melody"][blk % len(cfg["melody"])]:
            note = (_vibe if cfg["lead"] == "vibe" else lambda f, nn: _bell(f, nn, vel=0.8))(
                _hz(midi), int(max(ln, 2) * beat * 0.5 * SR + 0.8 * SR))
            _put(stems["pad"], note, at(blk * 4 + step // 8, step % 8, rng.normal(0, 0.004)), 0.8,
                 pan=0.2 if step % 2 else -0.2)

    tex = _crackle(rng, n) if cfg["texture"] == "crackle" else _rain(rng, n)
    stems["arp"] = _reverb(stems["arp"], rng, 1.4, cfg["keys_wet"], 5000)
    stems["pad"] = _reverb(stems["pad"], rng, 2.0, 0.28, 5000)
    stems["clap"] = _reverb(stems["clap"], rng, 0.8, 0.18, 5000)
    out = {}
    for k, x in stems.items():
        x = _tape(_sat(x, cfg["drive"]))
        out[k] = _circ(x, _sos("lp", cfg["lowpass"], 2))
    out = _balance(out, cfg["levels"])
    tex = _circ(tex, _sos("lp", cfg["lowpass"], 2))
    kind, db = cfg["texture_level"]                                  # pops are judged by peak, steady rain by RMS
    ref = float(np.max(np.abs(tex))) if kind == "peak" else float(np.sqrt(np.mean(tex ** 2)))
    out["hats"] = out["hats"] + np.stack([tex / (ref or 1.0) * 10 ** (db / 20)] * 2, axis=1)
    return _fit(out)


# ---- ambient ------------------------------------------------------------------------------
def ambient(seed: int, bars: int, cfg: dict) -> dict[str, np.ndarray]:
    """Beatless ambient. Roles: pad = the wash, bass = low drone, hats = air, arp = slow bells,
    clap = high chimes, kick = a soft sub swell every two bars. `bars` is a multiple of chord_bars."""
    rng = np.random.default_rng(seed)
    bpm, cbars = cfg["bpm"], cfg["chord_bars"]
    if bars % cbars:
        raise ValueError(f"bars ({bars}) must be a multiple of chord_bars ({cbars})")
    n = loop_samples(bpm, bars)
    spb = samples_per_bar(bpm)
    beat = 60.0 / bpm
    stems = {k: np.zeros((n, 2)) for k in LAYERS}
    seg = int(round(cbars * spb))
    tail = int(cfg["tail_s"] * SR)

    for ci in range(bars // cbars):
        ch = cfg["chords"][ci % len(cfg["chords"])]
        start = int(round(ci * cbars * spb))
        L = seg + tail
        for side in (0, 1):
            for m in ch["notes"]:
                f = _hz(m)
                v = np.zeros(L)
                for d in (-5, 0, 5):
                    ph = rng.random() * 2 * math.pi
                    v += np.sin(2 * np.pi * f * 2 ** ((d + 2 * side - 1) / 1200) * _t(L) + ph)
                v += cfg["shimmer"] * np.sin(2 * np.pi * f * 2 * _t(L) + rng.random() * 6.28)
                _put(stems["pad"][:, side:side + 1], (v * _swell(L, cfg["attack_s"], tail / SR))[:, None] / 3.0, start, 0.2)
        drone = _hz(ch["root"])
        for side in (0, 1):
            d = (np.sin(2 * np.pi * drone * _t(L)) + 0.25 * np.sin(2 * np.pi * drone * 1.5 * _t(L)) + 0.12 * np.sin(2 * np.pi * drone * 2 * _t(L)))
            _put(stems["bass"][:, side:side + 1], (d * _swell(L, cfg["attack_s"], tail / SR))[:, None], start, 0.28)

    for k in range(0, bars, 2):                                             # soft sub swell
        root = cfg["chords"][(k // cbars) % len(cfg["chords"])]["root"]
        ln = int(0.8 * spb)
        env = np.minimum(1.0, _t(ln) / 0.35) * np.exp(-_t(ln) / 1.3)
        _put(stems["kick"], np.sin(2 * np.pi * _hz(root) * _t(ln)) * env, int(round(k * spb)), 0.6)

    for k in range(bars * 2):                                               # slow bells, one every two beats
        ch = cfg["chords"][(k // (cbars * 2)) % len(cfg["chords"])]
        m = int(rng.choice(ch["notes"])) + 12 * int(rng.choice([0, 0, 1]))
        ln = int(4.0 * SR)
        _put(stems["arp"], _bell(_hz(m), ln, cfg["bell"], vel=0.5 + 0.3 * rng.random()), int(round(k * 2 * beat * SR)),
             0.8, pan=float(rng.uniform(-0.6, 0.6)))
    for k in range(bars):                                                   # high chimes
        if rng.random() < 0.65:
            ch = cfg["chords"][(k // cbars) % len(cfg["chords"])]
            m = int(rng.choice(ch["notes"])) + 24
            _put(stems["clap"], _bell(_hz(m), int(4.5 * SR), cfg["bell"], vel=0.35), int(round((k + rng.random()) * spb)), 0.7,
                 pan=float(rng.uniform(-0.8, 0.8)))

    # air: noise with a slow swell, periodic by construction
    air = _circ(rng.standard_normal(n), _sos("bp", cfg["air_band"], 2))
    cyc = max(1, bars // 2)
    sw = (0.5 + 0.5 * np.sin(2 * np.pi * cyc * np.arange(n) / n)) ** 2
    stems["hats"] = np.stack([air * sw * 0.18, _circ(rng.standard_normal(n), _sos("bp", cfg["air_band"], 2)) * sw[::-1] * 0.18], axis=1)

    # brightness drifts over the loop: crossfade a dark and a bright version of the wash
    w = (0.5 + 0.5 * np.sin(2 * np.pi * max(1, bars // cbars) * np.arange(n) / n))[:, None]
    dark = _circ(stems["pad"], _sos("lp", cfg["pad_dark"], 2))
    bright = _circ(stems["pad"], _sos("lp", cfg["pad_bright"], 2))
    stems["pad"] = _reverb(dark * (1 - w) + bright * w, rng, cfg["reverb_s"], 0.45, 7000)
    stems["arp"] = _reverb(stems["arp"], rng, cfg["reverb_s"], 0.4, 7000)
    stems["clap"] = _reverb(stems["clap"], rng, cfg["reverb_s"], 0.5, 8000)
    echo = np.zeros_like(stems["arp"])
    for dly, g in ((1.4, 0.3), (2.9, 0.15)):
        echo += np.roll(stems["arp"][:, ::-1], int(dly * SR), axis=0) * g
    stems["arp"] = stems["arp"] + echo
    return _fit(_balance(stems, cfg["levels"]))
