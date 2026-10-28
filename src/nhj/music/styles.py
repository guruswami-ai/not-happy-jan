"""The built-in stem-pack styles and how to build them.

    trance        the original 138 BPM demo pack (synth.py)
    lofi-rhodes   76 BPM boom-bap, electric piano, vibraphone melody, vinyl crackle
    lofi-nights   68 BPM brushed, nylon guitar, music-box melody, rain
    ambient-drift 60 BPM beatless, warm evolving wash, low drone, slow bells
    ambient-glass 50 BPM beatless, bright glassy wash, sparse chimes

Choose one with ``music.pack: lofi-rhodes`` (a bare style name; it is built into the data
directory the first time it is needed) or build it by hand: ``nhj music build-pack --style NAME``.
This module is light on purpose (no numpy/scipy import) so naming a style is free; the DSP lives
in ``stylesynth.py`` and is imported only when a pack is actually rendered.

Each style's ``arrangement`` is what the director plays for it. Lo-fi and ambient keep their
one-agent state complete on its own (a groove with chords, or a drone with a wash), because with a
single busy agent that is the state heard for the whole turn.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_ALL = ["kick", "hats", "clap", "bass", "pad", "arp"]


@dataclass(frozen=True)
class Style:
    name: str
    bpm: float
    bars: int
    blurb: str
    arrangement: dict
    test_bars: int = 4


STYLES: dict[str, Style] = {
    "trance": Style("trance", 138.0, 16, "the original demo: uplifting trance, builds with busy sessions", {}),
    "lofi-rhodes": Style(
        "lofi-rhodes", 76.0, 16, "boom-bap lo-fi: electric piano, vibraphone melody, vinyl crackle",
        {"ladder": [["kick", "clap", "bass", "arp"], ["kick", "clap", "bass", "arp", "hats"], _ALL],
         "breakdown": ["arp", "pad"], "breakdown_bars": 2, "release": _ALL, "tension": 0.3}),
    "lofi-nights": Style(
        "lofi-nights", 68.0, 16, "late-night lo-fi: brushes, nylon guitar, music box, rain",
        {"ladder": [["bass", "arp", "hats"], ["bass", "arp", "hats", "kick", "clap"], _ALL],
         "breakdown": ["arp", "pad"], "breakdown_bars": 2, "release": _ALL, "tension": 0.3}),
    "ambient-drift": Style(
        "ambient-drift", 60.0, 16, "warm beatless ambient: slow evolving wash, low drone, soft bells",
        {"ladder": [["pad", "bass"], ["pad", "bass", "hats"], ["pad", "bass", "hats", "arp"], _ALL],
         "breakdown": ["pad", "bass"], "breakdown_bars": 2, "release": _ALL, "tension": 0.0}, test_bars=8),
    "ambient-glass": Style(
        "ambient-glass", 50.0, 12, "bright glassy ambient: shimmering wash, sparse chimes",
        {"ladder": [["pad", "bass"], ["pad", "bass", "hats"], ["pad", "bass", "hats", "arp"], _ALL],
         "breakdown": ["pad", "bass"], "breakdown_bars": 2, "release": _ALL, "tension": 0.0}, test_bars=6),
}

# ---- style recipes (musical content; the sound design is in stylesynth.py) -------------------
_LOFI_RHODES = {
    "bpm": 76.0, "swing": 0.58, "keys": "rhodes", "lead": "vibe", "snare": "snare", "texture": "crackle",
    "texture_level": ("peak", -27), "keys_wet": 0.22, "drive": 1.25, "lowpass": 8500.0, "pad_bright": 900.0,
    "levels": {"kick": ("peak", -7), "clap": ("peak", -9), "hats": ("peak", -17), "bass": ("peak", -13),
               "arp": ("peak", -8), "pad": ("peak", -13)},
    "kick": [[0, 3, 6], [0, 3, 4]],
    "bass": [(0, 2.3, 0.75), (5, 1.1, 0.55)],
    "comp": [(0, 2.4, 0.8), (5, 1.5, 0.55)],
    # Am9 - Dm9 - G9 - Cmaj9, rootless voicings
    "prog": [{"root": 45, "voicing": [60, 64, 67, 71]}, {"root": 50, "voicing": [53, 57, 60, 64]},
             {"root": 43, "voicing": [59, 62, 65, 69]}, {"root": 48, "voicing": [64, 67, 71, 74]}],
    "melody": [
        [(1, 76, 3), (5, 74, 2), (8, 72, 3), (12, 74, 2), (16, 76, 4), (22, 72, 2), (26, 69, 6)],
        [(0, 79, 3), (4, 76, 2), (7, 74, 3), (12, 72, 4), (18, 74, 2), (22, 76, 3), (28, 72, 4)],
        [(2, 72, 2), (5, 74, 2), (8, 76, 4), (14, 79, 2), (17, 76, 3), (22, 74, 2), (26, 72, 5)],
        [(0, 69, 2), (3, 72, 3), (8, 74, 3), (12, 76, 2), (16, 74, 3), (20, 72, 2), (24, 69, 8)]],
}
_LOFI_NIGHTS = {
    "bpm": 68.0, "swing": 0.56, "keys": "nylon", "lead": "bell", "snare": "brush", "texture": "rain",
    "texture_level": ("rms", -33), "keys_wet": 0.3, "drive": 1.15, "lowpass": 7500.0, "pad_bright": 750.0,
    "levels": {"kick": ("peak", -8), "clap": ("peak", -10), "hats": ("peak", -22), "bass": ("peak", -10),
               "arp": ("peak", -9), "pad": ("peak", -13)},
    "kick": [[0, 4], [0, 3, 4]],
    "bass": [(0, 3.2, 0.7), (6, 1.0, 0.5)],
    "comp": [(0, 3.0, 0.8), (5, 1.5, 0.5)],
    # Dm9 - Bbmaj7 - Fmaj9 - C6/9
    "prog": [{"root": 50, "voicing": [50, 57, 60, 64, 65]}, {"root": 46, "voicing": [46, 53, 57, 62]},
             {"root": 53, "voicing": [41, 48, 52, 57, 62]}, {"root": 48, "voicing": [48, 55, 57, 62, 64]}],
    "melody": [
        [(0, 74, 4), (6, 77, 3), (10, 79, 4), (16, 81, 4), (22, 79, 3), (26, 77, 6)],
        [(2, 81, 3), (6, 79, 3), (10, 77, 4), (16, 74, 4), (24, 77, 3), (28, 74, 4)],
        [(0, 84, 3), (4, 81, 3), (8, 79, 4), (14, 77, 2), (18, 79, 3), (24, 81, 6)],
        [(2, 77, 3), (6, 79, 2), (10, 74, 4), (16, 77, 3), (20, 79, 3), (26, 74, 6)]],
}
_AMBIENT_DRIFT = {
    "bpm": 60.0, "chord_bars": 4, "tail_s": 3.0, "attack_s": 1.8, "shimmer": 0.2, "air_band": [4000, 9000],
    "pad_dark": 650.0, "pad_bright": 2800.0, "reverb_s": 4.0,
    "levels": {"pad": ("rms", -23), "bass": ("rms", -31), "hats": ("rms", -37), "arp": ("peak", -13),
               "clap": ("peak", -16), "kick": ("peak", -14)},
    "bell": ((1.0, 1.0, 3.2), (2.0, 0.3, 2.0), (3.0, 0.12, 1.2)),
    # Am(add9) - Fmaj7 - C(add9) - G(sus)
    "chords": [{"root": 33, "notes": [57, 60, 64, 71]}, {"root": 29, "notes": [53, 57, 60, 64]},
               {"root": 36, "notes": [60, 64, 67, 74]}, {"root": 31, "notes": [55, 60, 62, 67]}],
}
_AMBIENT_GLASS = {
    "bpm": 50.0, "chord_bars": 3, "tail_s": 3.5, "attack_s": 1.5, "shimmer": 0.45, "air_band": [5500, 11000],
    "pad_dark": 1500.0, "pad_bright": 6000.0, "reverb_s": 5.0,
    "levels": {"pad": ("rms", -23), "bass": ("rms", -32), "hats": ("rms", -37), "arp": ("peak", -12),
               "clap": ("peak", -14), "kick": ("peak", -15)},
    "bell": ((1.0, 1.0, 2.6), (2.76, 0.4, 1.5), (5.4, 0.18, 0.9), (8.93, 0.08, 0.5)),
    # Dmaj9 - Bm7 - Gmaj7(#11) - A6/9
    "chords": [{"root": 38, "notes": [62, 66, 69, 73, 76]}, {"root": 35, "notes": [59, 62, 66, 69, 73]},
               {"root": 43, "notes": [55, 59, 62, 66, 69]}, {"root": 33, "notes": [57, 61, 64, 66, 71]}],
}


def is_style(name) -> bool:
    return isinstance(name, str) and name in STYLES


def pack_subdir(name: str) -> str:
    return "default" if name == "trance" else name


def render(name: str, seed: int = 7, bars: int | None = None) -> dict:
    """The stems for *name* (a dict of layer name -> (N, 2) float32)."""
    if name not in STYLES:
        raise ValueError(f"unknown style {name!r}; choose one of: {', '.join(STYLES)}")
    n_bars = STYLES[name].bars if bars is None else bars
    if name == "trance":
        from nhj.music import synth
        return synth.render_stems(seed, n_bars)
    from nhj.music import stylesynth
    recipes = {"lofi-rhodes": (stylesynth.lofi, _LOFI_RHODES), "lofi-nights": (stylesynth.lofi, _LOFI_NIGHTS),
               "ambient-drift": (stylesynth.ambient, _AMBIENT_DRIFT), "ambient-glass": (stylesynth.ambient, _AMBIENT_GLASS)}
    fn, cfg = recipes[name]
    return fn(seed, n_bars, cfg)


def build(dest, name: str, seed: int = 7, bars: int | None = None) -> Path:
    """Render style *name* and write it as a pack at *dest*."""
    from nhj.music.pack import save_pack
    st = STYLES[name] if name in STYLES else None
    if st is None:
        raise ValueError(f"unknown style {name!r}; choose one of: {', '.join(STYLES)}")
    n_bars = st.bars if bars is None else bars
    stems = render(name, seed, n_bars)
    label = "nhj-synth-trance" if name == "trance" else f"nhj-synth-{name}"
    return save_pack(dest, label, st.bpm, n_bars, stems,
                     {"generator": "nhj.music.styles", "style": name, "seed": seed,
                      "licence": "MIT (original NHJ code)", "blurb": st.blurb},
                     st.arrangement or None)
