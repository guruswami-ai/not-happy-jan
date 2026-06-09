"""Divoom display adapter — Times Gate, Times Frame, and compatible devices.

Divoom devices expose a local HTTP REST API for pushing text, animations,
and notifications. This adapter handles the Times Gate and Times Frame.

Configure via .env:
    DIVOOM_DEVICE_IP=192.168.1.xx
    DIVOOM_DEVICE_TYPE=TimesGate   # or TimesFrame, Ditoo, Pixoo, etc.
    DIVOOM_INTENTS=ok,err,warn,attn,celebrate  # omit to fire on all

API reference: https://divoom.com/pages/divoom-local-api
"""
from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

import requests

from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character

# Divoom device-type → base channel count (channels show on Times Gate LED panels)
_DEVICE_TYPES = {
    "TimesGate":  True,   # multi-panel, supports channel-based text
    "TimesFrame": True,
    "Ditoo":      False,
    "Pixoo":      False,
    "Pixoo64":    False,
}

# Simple intent → display color mapping (Divoom uses hex RGB)
_INTENT_COLORS = {
    "ok":        "#00FF00",  # green
    "err":       "#FF0000",  # red
    "warn":      "#FFAA00",  # amber
    "attn":      "#FF6600",  # orange
    "celebrate": "#FF00FF",  # magenta
    "step":      "#0088FF",  # blue
}


class DivoomAdapter(NotificationAdapter):
    def __init__(self, ip: Optional[str] = None, device_type: Optional[str] = None):
        self.ip          = ip          or os.getenv("DIVOOM_DEVICE_IP", "")
        self.device_type = device_type or os.getenv("DIVOOM_DEVICE_TYPE", "TimesGate")
        raw_intents      = os.getenv("DIVOOM_INTENTS", "")
        self.intents     = {i.strip() for i in raw_intents.split(",") if i.strip()} or None

    def available(self) -> bool:
        return bool(self.ip)

    def _post(self, command: dict) -> bool:
        """POST a Divoom protocol command to the device."""
        try:
            r = requests.post(
                f"http://{self.ip}:80/post",
                json=command,
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("error_code", -1) == 0
        except Exception:
            return False

    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, **kwargs) -> bool:
        if not self.available():
            return False
        if self.intents and intent not in self.intents:
            return True

        from nhj.config import INTENTS
        cfg    = INTENTS.get(intent, {})
        label  = cfg.get("label", intent.upper())
        text   = message or label
        color  = _INTENT_COLORS.get(intent, "#FFFFFF")

        # Divoom DisplayText command — works on Times Gate, Times Frame, Ditoo
        command = {
            "Command": "Draw/SendHttpText",
            "TextId":  1,
            "x":       0,
            "y":       0,
            "dir":     0,           # 0=left scroll, 1=right
            "font":    2,
            "TextWidth": 64,
            "speed":   80,
            "TextString": text[:64],  # display truncates anyway
            "color":   color,
            "align":   1,           # 1=left, 2=middle, 3=right
        }
        return self._post(command)
