"""Easter-egg hold-on scenes — infrequent character vignettes.

Every so often, instead of a plain "hold please", Jan goes off on one: a little slice of
ocker life with a matching ambient bed behind her — the pub, a party, out the back for a
durry. Rare by design; pure charm. The kind of thing you screenshot and send to a mate.

  • Lines are pre-rendered to  audio/voice/scenes/<key>.wav  (Jan's voice).
    Regenerate any time with:  nhj build-scenes
  • Ambient beds live at       audio/ambient/<name>.wav.
    Placeholders ship; drop in your own CC0 SFX (pub murmur, party, outdoor) to replace them.

Gated by the `scenes` flag (`nhj scenes on|off`, default on) and NHJ_SCENES_PROB — the chance
a scene fires on any given "going on hold" transition (default 0.08 ≈ 1 in 12).
"""
from __future__ import annotations

import os
import random

from nhj import resources

# data_dir() is the repo root in a checkout, else the platform data dir (macOS Application
# Support, XDG ~/.local/share elsewhere) — so `_ROOT / "audio"` is the audio tree in both.
_ROOT = resources.data_dir()

# key       → the line Jan speaks (original; rendered to her voice) + the ambient bed to play.
# Keep them short and warm. ambient "" = no bed (just the line).
SCENES: list[dict] = [
    {"key": "birthday",   "ambient": "party",
     "line": "Righto, poppin' ya on hold a tick — some drongo's havin' a birthday and there's cake, and I'm not missin' out."},
    {"key": "durry",      "ambient": "outdoor",
     "line": "Just duckin' out for a durry, love — hold the line and I'll be back after me smoko."},
    {"key": "thunderbox", "ambient": "",
     "line": "Gotta dash to the thunderbox, won't be a sec — thanks for holdin', mate."},
    {"key": "tinny",      "ambient": "pub",
     "line": "Eh, it's five o'clock somewhere, isn't it? Hold up while I crack a tinny."},
    {"key": "kettle",     "ambient": "",
     "line": "Stickin' the kettle on, love — hold tight, won't be a jiffy."},
    {"key": "footy",      "ambient": "pub",
     "line": "Hang about — the footy's on and someone's about to kick a goal. Hold the line, mate."},
    {"key": "beach",      "ambient": "beach",
     "line": "Strewth, it's a scorcher — I've snuck down the beach for a quick dip. Hold tight, won't be a tick."},
]

# "Hot mic" — Jan FORGETS to hit hold and you overhear her talking ABOUT you (to Bazza, or
# the room) before she realises and slaps the button. Fourth-wall break; cheeky, not cruel.
# Rendered to audio/voice/scenes/<key>.wav alongside the scene lines (nhj build-scenes).
# No ambient bed — it's just an open line. Gated by NHJ_OPENMIC_PROB (default 0.05).
OPEN_MIC: list[dict] = [
    {"key": "om_drongo",  "line": "Oh, I've got this drongo on the line askin' the stupidest questions, honest to god."},
    {"key": "om_goose",   "line": "Bazza, you wouldn't believe the goose I'm talkin' to — what a numpty."},
    {"key": "om_another", "line": "Bazza! Got another one of those bloody drongo customers for ya, mate."},
    {"key": "om_classic", "line": "Yeah, nah, this one's a few snags short of a barbie, I tell ya."},
    {"key": "om_patience","line": "Give us strength — if this bloke asks me one more thing I'm chuckin' a sickie."},
]


def lines() -> dict[str, str]:
    """key → line, for the renderer (`nhj build-scenes`)."""
    return {s["key"]: s["line"] for s in SCENES}


def enabled() -> bool:
    try:
        from nhj.state import get_flag
        if not get_flag("scenes", True):
            return False
    except Exception:
        pass
    return os.getenv("NHJ_SCENES", "1").strip().lower() not in ("0", "off", "false", "no")


def _prob() -> float:
    try:
        return float(os.getenv("NHJ_SCENES_PROB", "0.08"))
    except ValueError:
        return 0.08


def scene_clip(key: str) -> str | None:
    p = _ROOT / "audio" / "voice" / "scenes" / f"{key}.wav"
    return str(p) if p.is_file() else None


def ambient_clip(name: str) -> str | None:
    if not name:
        return None
    p = _ROOT / "audio" / "ambient" / f"{name}.wav"
    return str(p) if p.is_file() else None


def maybe_pick(rng: random.Random | None = None) -> dict | None:
    """Return a scene to play this 'going on hold', or None (the usual case). Only scenes
    whose rendered line clip exists are eligible."""
    r = rng or random
    if not enabled() or r.random() >= _prob():
        return None
    avail = [s for s in SCENES if scene_clip(s["key"])]
    return r.choice(avail) if avail else None


def _openmic_prob() -> float:
    try:
        return float(os.getenv("NHJ_OPENMIC_PROB", "0.05"))
    except ValueError:
        return 0.05


def openmic_clip(key: str) -> str | None:
    p = _ROOT / "audio" / "voice" / "scenes" / f"{key}.wav"
    return str(p) if p.is_file() else None


def maybe_pick_open_mic(rng: random.Random | None = None) -> dict | None:
    """Return a hot-mic aside to overhear this 'going on hold', or None (the usual case).
    Only asides whose rendered clip exists are eligible."""
    r = rng or random
    if not enabled() or r.random() >= _openmic_prob():
        return None
    avail = [s for s in OPEN_MIC if openmic_clip(s["key"])]
    return r.choice(avail) if avail else None


# ---- asset generation (nhj build-scenes) ------------------------------------
def render_lines(tts_url: str = "http://localhost:9992", voice: str = "jan",
                 verbose: bool = False) -> int:
    """Render each scene line to audio/voice/scenes/<key>.wav in the given voice."""
    from nhj.adapters.audio import synth_qwen
    out = _ROOT / "audio" / "voice" / "scenes"
    out.mkdir(parents=True, exist_ok=True)
    done = 0
    for s in SCENES + OPEN_MIC:
        wav = synth_qwen(s["line"], tts_url, voice=voice, model_tier="fast")
        if wav:
            (out / f"{s['key']}.wav").write_bytes(wav)
            done += 1
            if verbose:
                print(f"  rendered {'aside' if s['key'].startswith('om_') else 'scene'}: {s['key']}")
    return done


def generate_placeholder_beds(secs: int = 8, sr: int = 24000,
                              overwrite: bool = False, verbose: bool = False) -> int:
    """Write crude synthetic ambient beds (pub / party / outdoor) so scenes are audible out
    of the box. These are PLACEHOLDERS — replace audio/ambient/<name>.wav with real
    CC0 field recordings for the proper effect.

    Emitted at the NHJ ambient/SFX standard: MONO 24k s16 (beds only ever play ducked under
    voice, so stereo/HF buys nothing — the mixer upsamples to 48k at playback regardless)."""
    import wave
    import numpy as np
    out = _ROOT / "audio" / "ambient"
    out.mkdir(parents=True, exist_ok=True)
    made = 0
    for kind in ("pub", "party", "outdoor"):
        dest = out / f"{kind}.wav"
        if dest.exists() and not overwrite:
            continue
        n = secs * sr
        rng = np.random.default_rng(abs(hash(kind)) % (2 ** 32))
        white = rng.standard_normal(n).astype(np.float64)
        brown = np.cumsum(white)
        brown /= (np.max(np.abs(brown)) + 1e-9)              # warm murmur base
        t = np.linspace(0, secs, n)
        if kind == "pub":
            swell = 0.6 + 0.4 * np.sin(2 * np.pi * 0.4 * t)   # crowd swell
            sig = brown * 0.7 * swell + 0.12 * white
        elif kind == "party":
            swell = 0.5 + 0.5 * np.sin(2 * np.pi * 0.8 * t)
            sig = brown * 0.5 * swell + 0.25 * white
        else:                                                # outdoor — low, airy, quiet
            sig = brown * 0.4
        sig /= (np.max(np.abs(sig)) + 1e-9)
        sig *= 0.5                                           # headroom
        pcm = (sig * 32767).astype(np.int16)
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        made += 1
        if verbose:
            print(f"  generated placeholder bed: {kind}.wav")
    return made


def generate_callcentre_bed(secs: int = 12, sr: int = 24000,
                            overwrite: bool = False, verbose: bool = False) -> int:
    """Synthesise a call-centre room bed — low office murmur + sparse keyboard ticks → mono 24k
    audio/ambient/callcentre.wav. PLACEHOLDER (swap for a real CC0 office room-tone).
    Played ducked under the characters' voices in call-centre mode (`nhj mode call-centre`)."""
    import wave
    import numpy as np
    dest = _ROOT / "audio" / "ambient" / "callcentre.wav"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        return 0
    n = secs * sr
    rng = np.random.default_rng(4242)
    brown = np.cumsum(rng.standard_normal(n))
    brown /= (np.max(np.abs(brown)) + 1e-9)
    sig = brown * 0.55                                        # background murmur
    for _ in range(secs * 6):                                # ~6 keystrokes/sec, short decaying ticks
        p = int(rng.integers(0, n - 200))
        L = int(rng.integers(40, 120))
        sig[p:p + L] += rng.standard_normal(L) * np.exp(-np.linspace(0, 6, L)) * 0.22
    sig /= (np.max(np.abs(sig)) + 1e-9)
    sig *= 0.5
    pcm = (sig * 32767).astype(np.int16)
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    if verbose:
        print("  generated call-centre bed: callcentre.wav")
    return 1
