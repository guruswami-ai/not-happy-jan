"""Logitech MX Master 4 haptic adapter (HID++ 2.0, feature 0x19B0).

The MX Master 4 is the first Logitech mouse with a haptic motor. It stores a
fixed *library* of named waveforms in firmware (SHARP_COLLISION, HAPPY_ALERT,
COMPLETED, FIREWORK, KNOCK, …) — we don't upload patterns, we just ask the
mouse to *play* one by id. NHJ maps each agent-vibe intent to a waveform.

Protocol (verified empirically against an MX Master 4 on a Bolt receiver, and
matching pwr-Solaar/Solaar + lukasfri/mx4notifications):

  Transport : the Logitech receiver's HID++ interface — usage_page 0xFF00.
  Play call : a *short* HID++ report (report id 0x10, 7 bytes):
                [0x10, device_index, feature_index, func<<4|swid, waveform, 0, 0]
              feature 0x19B0 function 0x4 = "play waveform"; param0 = waveform id.
  device_index : 0x02 on the test rig (resolve via ping; 0x02 is the fallback).
  feature_index: runtime-assigned per device — resolve via IRoot.getFeature(0x19B0);
                 0x0B is the observed fallback. NEVER assume a fixed index blindly.

See docs/haptic-mouse-setup.md for the full write-up and the Logi-Options+
HapticWebPlugin alternative.
"""
from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character

VENDOR_ID = 0x046D            # Logitech
HID_PP_USAGE_PAGE = 0xFF00    # the HID++ (vendor) interface on the receiver
HAPTIC_FEATURE = 0x19B0       # HID++ 2.0 HAPTIC feature id
HAPTIC_PLAY_FN = 0x4          # function index within the haptic feature
SWID = 0x0E                   # software id (echoed in responses; value is arbitrary)

# Fallbacks if runtime discovery fails (observed on an MX Master 4 / Bolt).
FALLBACK_DEVICE_INDEX = 0x02
FALLBACK_FEATURE_INDEX = 0x0B

# The mouse's firmware waveform library (Solaar HapticWaveForms).
WAVEFORMS = {
    "sharp_state_change": 0x00, "damp_state_change": 0x01,
    "sharp_collision": 0x02,    "damp_collision": 0x03,
    "subtle_collision": 0x04,   "happy_alert": 0x05,
    "angry_alert": 0x06,        "completed": 0x07,
    "square": 0x08,             "wave": 0x09,
    "firework": 0x0A,           "mad": 0x0B,
    "knock": 0x0C,              "jingle": 0x0D,
    "ringing": 0x0E,            "whisper_collision": 0x1B,
}

# Built-in defaults — used when config/default.yaml has no `haptic.taps` for an
# intent. The config block is the user-facing way to retune these (see the file).
# waveform: the *feel*; repeat: insistence (higher-stakes intents repeat more).
DEFAULT_TAPS = {
    "step":      {"waveform": "subtle_collision", "repeat": 1},
    "ok":        {"waveform": "completed",        "repeat": 1},
    "celebrate": {"waveform": "firework",         "repeat": 1},
    "warn":      {"waveform": "knock",            "repeat": 2},
    "err":       {"waveform": "angry_alert",      "repeat": 2},
    "attn":      {"waveform": "ringing",          "repeat": 3},
}
DEFAULT_WAVEFORM = "wave"
DEFAULT_INTENSITY = 5    # firmware strength 0-5 (sent as the play param; null = device default)


def resolve_waveform(name_or_id) -> int:
    """Accept a waveform name ('completed'), a numeric id, or fall back to wave."""
    if isinstance(name_or_id, int):
        return name_or_id & 0xFF
    s = str(name_or_id).strip().lower()
    if s in WAVEFORMS:
        return WAVEFORMS[s]
    if s.isdigit():
        return int(s) & 0xFF
    return WAVEFORMS[DEFAULT_WAVEFORM]


def _find_path():
    try:
        import hid
    except ImportError:
        return None
    for d in hid.enumerate(VENDOR_ID):
        if d.get("usage_page") == HID_PP_USAGE_PAGE:
            return d["path"]
    return None


def _open(path):
    import hid
    try:
        dev = hid.Device(path=path)          # newer cython-hidapi API
        return dev
    except Exception:
        dev = hid.device(); dev.open_path(path)   # legacy API
        return dev


def _read(dev, ms=300) -> bytes:
    try:
        try:
            return dev.read(20, ms)              # newer API: (size, timeout_ms)
        except TypeError:
            dev.set_nonblocking(0)
            return dev.read(20, ms)              # legacy API
    except Exception:
        return b""


def _discover(dev, prefer: Optional[int] = None) -> tuple[int, int]:
    """Return (device_index, feature_index) by pinging IRoot.getFeature(0x19B0).
    Falls back to the observed constants if the device doesn't answer.
    `prefer` tries a specific device index first (from config/env)."""
    order = [prefer] if prefer is not None else []
    order += [d for d in (FALLBACK_DEVICE_INDEX, 0x01, 0xFF) if d != prefer]
    for devidx in order:
        # IRoot (index 0), func 0 getFeature, swid -> 0x00<<4 | SWID
        dev.write(bytes([0x10, devidx, 0x00, SWID,
                         (HAPTIC_FEATURE >> 8) & 0xFF, HAPTIC_FEATURE & 0xFF, 0x00]))
        for _ in range(5):
            r = _read(dev)
            if r and len(r) >= 5 and r[1] == devidx and r[2] != 0x8F and r[4]:
                return devidx, r[4]
    return FALLBACK_DEVICE_INDEX, FALLBACK_FEATURE_INDEX


def play(waveform, repeat: int = 1, intensity: Optional[int] = None,
         device_index: Optional[int] = None) -> bool:
    """Play a named/id waveform on the MX Master 4. Returns True on a clean write.

    intensity: firmware strength 0-5 sent as the play param (None -> 0/default).
    device_index: override the HID++ slot (None -> auto-discover)."""
    path = _find_path()
    if not path:
        return False
    wf = resolve_waveform(waveform)
    p1 = max(0, min(5, int(intensity))) if intensity is not None else 0
    try:
        dev = _open(path)
    except Exception:
        return False
    try:
        devidx, featidx = _discover(dev, prefer=device_index)
        report = bytes([0x10, devidx, featidx, (HAPTIC_PLAY_FN << 4) | SWID, wf, p1, 0x00])
        import time
        for i in range(max(1, repeat)):
            dev.write(report)
            if i + 1 < repeat:
                time.sleep(0.18)
        return True
    except Exception:
        return False
    finally:
        try:
            dev.close()
        except Exception:
            pass


class HapticAdapter(NotificationAdapter):
    def __init__(self):
        self._detected: Optional[bool] = None
        self._cfg = self._load_cfg()

    @staticmethod
    def _load_cfg() -> dict:
        try:
            from nhj.config import section
            return section("haptic")
        except Exception:
            return {}

    def _enabled(self) -> bool:
        env = os.getenv("NHJ_HAPTIC_ENABLED")
        if env is not None:
            return env.strip().lower() in ("1", "true", "yes", "on")
        return bool(self._cfg.get("enabled", False))

    def available(self) -> bool:
        # Opt-in: must be enabled in config/env AND have an MX Master 4 present.
        if not self._enabled():
            return False
        if self._detected is None:
            self._detected = _find_path() is not None
        return self._detected

    def _tap(self, intent: str) -> dict:
        taps = self._cfg.get("taps") or {}
        return taps.get(intent) or DEFAULT_TAPS.get(
            intent, {"waveform": DEFAULT_WAVEFORM, "repeat": 1})

    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, **kwargs) -> bool:
        # Mode can override the tap style: full = per-intent ladder; simple = one
        # plain tap for everything (e.g. special-forces); off = no haptic.
        try:
            from nhj.state import get_setting
            style = (get_setting("haptic_style", "full") or "full").lower()
        except Exception:
            style = "full"
        if style == "off":
            return True
        if style == "simple":
            return play("sharp_collision", repeat=1,
                        intensity=self._cfg.get("intensity", DEFAULT_INTENSITY),
                        device_index=self._cfg.get("device_index"))
        tap = self._tap(intent)
        wf = tap.get("waveform", DEFAULT_WAVEFORM)
        # Escalating insistence: per-intent repeat, +1 for a hot vibe_level.
        repeat = int(tap.get("repeat", 1)) + (1 if vibe_level >= 8 else 0)
        intensity = self._cfg.get("intensity", DEFAULT_INTENSITY)
        devidx = self._cfg.get("device_index")
        env_idx = os.getenv("NHJ_HAPTIC_DEVICE_INDEX")
        if env_idx and env_idx.isdigit():
            devidx = int(env_idx)
        return play(wf, repeat=repeat, intensity=intensity,
                    device_index=int(devidx) if devidx is not None else None)
