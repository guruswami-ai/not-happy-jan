"""Mode macros — one named scene that sets ALL behaviours at once.

A *mode* is a bundle: muzak state, audio FX/persona, character dials, the display
behaviour, the TTS voice, and the haptic style. Switching modes is atomic — the
applicator resets to a clean baseline first, then layers the chosen preset on top,
so modes are mutually exclusive (no more rave + call-centre stacking).

Presets live in `config/default.yaml` under `modes:` (merged over BUILTIN_MODES),
so new scenes need no code. The granular commands (`nhj set`, `nhj muzak on/off`)
still tweak *within* the current mode.

A preset is a dict with any of:
  muzak:     off | on | continuous | rave        (default: continuous)
  audio_mode: normal | call-centre | agent-vibes (voice bus FX; default normal)
  scenario:  persona prompt for the dynamic LLM   ("" = none)
  display:   normal | party | off                 (AWTRIX behaviour)
  voice:     "" | <voice name>                    (global voice override — swaps the ref.wav
                                                   sample for ALL characters, e.g. voices/whisper/)
  haptic:    full | simple | off                  (mouse tap style)
  censor:    quack | beep | honk | off            (swear bleep; default quack on every mode)
  ambient:   "" | <bed name>                      (looping background bed under the mode)
  dials:     {jan: {ockerism: N, competence: N}, bazza: {...}, karren: {karren: N}}
             (friendly names — resolved to level/chaos like `nhj set`)
"""
from __future__ import annotations

from nhj.state import (set_flag, set_setting, get_setting, resolve_dial, update)


def _norm(v, default: str) -> str:
    """Coerce a preset value to a string, tolerating YAML's off/on -> bool gotcha."""
    if v is None:
        return default
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v).strip().lower()

# Default dials = the shipped character baseline (config/default.yaml characters:).
# Friendly names; the applicator resolves them to level/chaos.
_BASE_DIALS = {
    "jan":    {"ockerism": 4, "competence": 5},
    "bazza":  {"stress": 5, "competence": 8},
    "karren": {"karren": 7},
}

_CALLCENTRE_SCENARIO = (
    "You are a bored, apathetic government call-centre worker on a crackly phone line. "
    "You clearly don't care, you're mildly rude and annoyed the caller rang, you keep "
    "putting them on hold and fumbling the details. Flat affect, minimal effort, "
    "unhelpful — 'that's not my department'. Not bogan, just over it.")

_SPECFORCES_SCENARIO = (
    "You are a special-forces operator on comms during a covert op. Terse, calm, "
    "ultra-competent, whispering. Minimal words, military brevity ('copy', 'go', "
    "'target acquired', 'stand by'). No banter, no ocker — disciplined and quiet.")

_FULLBOGAN_SCENARIO = (
    "You are a sharp, switched-on Aussie bogan — you swear constantly and creatively, "
    "but you're genuinely brilliant at the job and nail it first go, no fumbling. You've "
    "got zero patience for stupid questions and will cheekily take the piss, but you're "
    "warm, matey, playful and clearly having a laugh. Crude, friendly, dead competent.")

BUILTIN_MODES = {
    "normal": {
        "muzak": "continuous", "audio_mode": "normal", "scenario": "",
        "display": "normal", "voice": "", "haptic": "full", "ambient": "",
        "dials": _BASE_DIALS,
    },
    "rave": {
        "muzak": "rave", "audio_mode": "normal", "scenario": "",
        "display": "party", "voice": "", "haptic": "full", "ambient": "",
        "dials": {"jan": {"ockerism": 8, "competence": 6}},
    },
    "call-centre": {
        "muzak": "continuous", "audio_mode": "call-centre", "scenario": _CALLCENTRE_SCENARIO,
        "display": "normal", "voice": "", "haptic": "full", "ambient": "callcentre",
        "dials": {"jan": {"ockerism": 1, "competence": 1},
                  "bazza": {"stress": 1, "competence": 2},
                  "karren": {"karren": 6}},
    },
    "quiet": {
        "muzak": "off", "audio_mode": "normal", "scenario": "",
        "display": "off", "voice": "", "haptic": "full", "ambient": "",
        "dials": _BASE_DIALS,
    },
    "special-forces": {
        "muzak": "off", "audio_mode": "normal", "scenario": _SPECFORCES_SCENARIO,
        "display": "off", "voice": "jan-whispering", "haptic": "simple",
        "ambient": "special-forces-radio",
        "dials": {"jan": {"ockerism": 3, "competence": 9},
                  "bazza": {"stress": 2, "competence": 9},
                  "karren": {"karren": 3}},
    },
    "went-full-bogan": {
        "muzak": "continuous", "audio_mode": "normal", "scenario": _FULLBOGAN_SCENARIO,
        "display": "normal", "voice": "", "haptic": "full", "censor": "off", "ambient": "",
        "dials": {"jan": {"ockerism": 11, "competence": 10},
                  "bazza": {"stress": 5, "competence": 10},
                  "karren": {"karren": 6}},
    },
}

_ALIASES = {
    "callcentre": "call-centre", "callcenter": "call-centre", "call-center": "call-centre",
    "party": "rave", "focus": "quiet", "silent": "quiet",
    "specialforces": "special-forces", "special_forces": "special-forces",
    "specforces": "special-forces", "spec-ops": "special-forces", "specops": "special-forces",
    "fullbogan": "went-full-bogan", "bogan": "went-full-bogan", "fullbogo": "went-full-bogan",
}


def _all_modes() -> dict:
    """BUILTIN_MODES with any config `modes:` block merged over the top (per-mode shallow merge)."""
    try:
        from nhj.config import section
        cfg = section("modes") or {}
    except Exception:
        cfg = {}
    merged = {k: dict(v) for k, v in BUILTIN_MODES.items()}
    for name, preset in cfg.items():
        if isinstance(preset, dict):
            merged.setdefault(name, {}).update(preset)
    return merged


def canonical(name: str) -> str:
    n = (name or "").strip().lower().replace("_", "-")
    return _ALIASES.get(n.replace("-", ""), _ALIASES.get(n, n))


def list_modes() -> list[str]:
    return list(_all_modes().keys())


def current_mode() -> str:
    return get_setting("mode", "normal")


def _apply_muzak(state: str) -> None:
    """Set the muzak flags for a mode and (re)start/stop the player to match."""
    from nhj import inference_muzak as im
    s = (state or "continuous").lower()
    if s in ("off", "false", "none"):
        set_flag("muzak", False); set_flag("muzak_continuous", False); set_flag("muzak_rave", False)
        im.stop()
    elif s == "rave":
        set_flag("muzak", True); set_flag("muzak_continuous", True); set_flag("muzak_rave", True)
        im.start()
    elif s in ("continuous", "cont", "background", "bg"):
        set_flag("muzak", True); set_flag("muzak_continuous", True); set_flag("muzak_rave", False)
    else:  # "on" = on-hold
        set_flag("muzak", True); set_flag("muzak_continuous", False); set_flag("muzak_rave", False)


def apply_mode(name: str) -> dict:
    """Apply a mode macro. Returns the resolved preset. Raises KeyError if unknown."""
    modes = _all_modes()
    key = canonical(name)
    if key not in modes:
        raise KeyError(key)
    p = {**BUILTIN_MODES.get(key, {}), **modes[key]}   # builtin defaults under config overrides

    display = _norm(p.get("display"), "normal")
    haptic = _norm(p.get("haptic"), "full")

    # --- settings / persona / display / voice / haptic ---
    set_setting("mode", key)
    set_setting("audio_mode", _norm(p.get("audio_mode"), "normal"))
    set_setting("audio_scenario", p.get("scenario", "") or "")
    set_setting("display", display)
    set_setting("voice_override", p.get("voice", "") or "")
    set_setting("haptic_style", haptic)
    set_setting("censor", _norm(p.get("censor"), "quack"))   # default quack; went-full-bogan = off
    set_setting("ambient_mode", _norm(p.get("ambient"), ""))

    # --- dials: resolve friendly names → level/chaos (the keys characters actually
    #     read), then swap in the whole set under one locked write so a mode switch is a
    #     clean reset that can't drop a concurrent `nhj set`. ---
    resolved: dict = {}
    dials = p.get("dials") or _BASE_DIALS
    for char, kv in dials.items():
        for dial, value in (kv or {}).items():
            field, stored, _ = resolve_dial(dial, int(value))
            resolved.setdefault(char, {})[field] = stored
    update(lambda s: s.__setitem__("dials", resolved))

    # --- muzak (last: may start/stop the player) ---
    _apply_muzak(_norm(p.get("muzak"), "continuous"))

    # --- display: drive the AWTRIX party tile on/off to match ---
    try:
        from nhj.adapters.ulanzi import UlanziAdapter
        UlanziAdapter().set_rave(display == "party")
    except Exception:
        pass

    return p
