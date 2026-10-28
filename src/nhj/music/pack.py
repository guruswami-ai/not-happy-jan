"""Stem packs: a directory of equal-length, bar-aligned WAV loops plus a manifest.

    manifest.json   {"format": 1, "name": ..., "bpm": 138, "bars": 16, "sr": 48000,
                     "layers": {"kick": "kick.wav", ...}, "provenance": {...},
                     "arrangement": {...}}            (optional, see below)

``arrangement`` lets a pack say which of its layers play for 1, 2, 3+ busy sessions
(``ladder``: a list of layer-name lists, last one is the full arrangement), which layers
make the ``err`` breakdown (``breakdown_bars`` long) and the ``ok`` release (default: the full arrangement), and how
much of the rising long-turn sweep it takes (``tension``, 0..1; 0 turns it off). Without it
the trance defaults in ``director.py`` apply.

Every layer has exactly ``round(bars * 4 * 60 / bpm * sr)`` samples, so the whole pack loops
seamlessly and the director can switch layers on bar lines. Only 16-bit/32-bit PCM or float
WAV written by this module is read here (stdlib ``wave``); external audio goes through the
mixer's PyAV decoder instead.
"""
from __future__ import annotations

import json
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SR = 48000
FORMAT = 1
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.wav$")      # plain file names only: no paths


class PackError(ValueError):
    pass


def samples_per_bar(bpm: float, sr: int = SR) -> float:
    return 4 * 60.0 / bpm * sr


def loop_samples(bpm: float, bars: int, sr: int = SR) -> int:
    return int(round(bars * samples_per_bar(bpm, sr)))


@dataclass
class Pack:
    name: str
    bpm: float
    bars: int
    layers: dict[str, np.ndarray]                    # name -> (N, 2) float32
    provenance: dict = field(default_factory=dict)
    arrangement: dict = field(default_factory=dict)

    @property
    def spb(self) -> float:
        return samples_per_bar(self.bpm)

    @property
    def n(self) -> int:
        return loop_samples(self.bpm, self.bars)

    def mix(self, layers, bars: int | None = None, start_bar: int = 0) -> np.ndarray:
        """Sum of *layers* over ``bars`` bars from ``start_bar`` (wrapping), as float32."""
        count = self.bars if bars is None else bars
        a = int(round(start_bar * self.spb))
        b = int(round((start_bar + count) * self.spb))
        idx = np.arange(a, b) % self.n
        out = np.zeros((b - a, 2), np.float32)
        for name in layers:
            if name in self.layers:
                out += self.layers[name][idx]
        return out


def _write_wav(path: Path, data: np.ndarray) -> None:
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _read_wav(path: Path) -> np.ndarray:
    try:
        with wave.open(str(path), "rb") as w:
            ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(frames)
    except (wave.Error, EOFError, OSError) as e:
        raise PackError(f"{path.name}: cannot read WAV ({e})") from e
    if rate != SR or ch != 2 or width != 2:
        raise PackError(f"{path.name}: need 48 kHz stereo 16-bit PCM (got {rate} Hz, {ch} ch, {width * 8} bit)")
    return (np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0).reshape(-1, 2)


def parse_arrangement(raw, layer_names, where: str = "arrangement") -> dict:
    """Validate an arrangement block; returns a clean dict (possibly empty). Unknown layers are errors."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise PackError(f"{where}: must be an object")
    known = set(layer_names)

    def names(value, what):
        if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value):
            raise PackError(f"{where}.{what}: must be a non-empty list of layer names")
        bad = sorted(set(value) - known)
        if bad:
            raise PackError(f"{where}.{what}: unknown layer(s) {', '.join(bad)}")
        return list(dict.fromkeys(value))

    out: dict = {}
    if "ladder" in raw:
        steps = raw["ladder"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 8:
            raise PackError(f"{where}.ladder: must be a list of 1-8 layer lists")
        out["ladder"] = [names(st, f"ladder[{i}]") for i, st in enumerate(steps)]
    for key in ("breakdown", "release"):
        if key in raw:
            out[key] = names(raw[key], key)
    if "breakdown_bars" in raw:
        bb = raw["breakdown_bars"]
        if isinstance(bb, bool) or not isinstance(bb, int) or not 1 <= bb <= 8:
            raise PackError(f"{where}.breakdown_bars: must be a whole number of bars, 1-8")
        out["breakdown_bars"] = bb
    if "tension" in raw:
        try:
            t = float(raw["tension"])
        except (TypeError, ValueError) as e:
            raise PackError(f"{where}.tension: must be a number 0..1") from e
        if not 0.0 <= t <= 1.0:
            raise PackError(f"{where}.tension: must be between 0 and 1")
        out["tension"] = t
    return out


def save_pack(dest, name: str, bpm: float, bars: int, layers: dict[str, np.ndarray],
              provenance: dict | None = None, arrangement: dict | None = None) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    n = loop_samples(bpm, bars)
    files = {}
    for lname, data in layers.items():
        if not _NAME.match(f"{lname}.wav"):
            raise PackError(f"bad layer name {lname!r}")
        if data.shape != (n, 2):
            raise PackError(f"layer {lname!r} has shape {data.shape}, expected {(n, 2)}")
        _write_wav(dest / f"{lname}.wav", data)
        files[lname] = f"{lname}.wav"
    manifest = {"format": FORMAT, "name": name, "bpm": bpm, "bars": bars, "sr": SR,
                "layers": files, "provenance": provenance or {}}
    arr = parse_arrangement(arrangement, layers)
    if arr:
        manifest["arrangement"] = arr
    tmp = dest / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2))
    tmp.replace(dest / "manifest.json")                  # manifest last: a half-written pack is invisible
    return dest


def load_pack(path) -> Pack:
    root = Path(path)
    try:
        m = json.loads((root / "manifest.json").read_text())
    except (OSError, ValueError) as e:
        raise PackError(f"{root}: unreadable manifest.json ({e})") from e
    if not isinstance(m, dict) or m.get("format") != FORMAT or m.get("sr") != SR:
        raise PackError(f"{root}: unsupported manifest (need format {FORMAT}, sr {SR})")
    try:
        bpm, bars = float(m["bpm"]), int(m["bars"])
        files = dict(m["layers"])
    except (KeyError, TypeError, ValueError) as e:
        raise PackError(f"{root}: manifest missing bpm/bars/layers ({e})") from e
    if not (40 <= bpm <= 240) or not (1 <= bars <= 256) or not files:
        raise PackError(f"{root}: bpm/bars/layers out of range")
    n = loop_samples(bpm, bars)
    layers: dict[str, np.ndarray] = {}
    for lname, fname in files.items():
        if not isinstance(fname, str) or not _NAME.match(fname):
            raise PackError(f"{root}: layer {lname!r} file {fname!r} must be a plain .wav name")
        data = _read_wav(root / fname)
        if len(data) != n:
            raise PackError(f"{root}: layer {lname!r} has {len(data)} samples, expected {n} "
                            f"({bars} bars at {bpm} BPM)")
        layers[str(lname)] = data
    return Pack(str(m.get("name", root.name)), bpm, bars, layers, dict(m.get("provenance") or {}),
                parse_arrangement(m.get("arrangement"), layers, f"{root}: arrangement"))
