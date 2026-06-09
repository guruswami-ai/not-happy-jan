"""ESP32 electromechanical bell adapter.

Sends an HTTP POST to the ESP32's webhook endpoint. The ESP32 sketch in
examples/esp32/push_bell.ino listens for POST /bell and fires a relay
pulse to ring the bell.

Configure via .env:
    ESP32_BELL_URL=http://192.168.1.xx/bell
    ESP32_BELL_INTENTS=err,attn   # comma-separated; omit to ring on all intents
"""
from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

import requests

from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character


class ESP32BellAdapter(NotificationAdapter):
    def __init__(self, url: Optional[str] = None):
        self.url     = url or os.getenv("ESP32_BELL_URL", "")
        raw_intents  = os.getenv("ESP32_BELL_INTENTS", "")
        self.intents = {i.strip() for i in raw_intents.split(",") if i.strip()} or None

    def available(self) -> bool:
        return bool(self.url)

    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, **kwargs) -> bool:
        if not self.available():
            return False
        if self.intents and intent not in self.intents:
            return True  # configured to skip this intent — not a failure
        try:
            r = requests.post(
                self.url,
                json={"intent": intent, "message": message},
                timeout=5,
            )
            r.raise_for_status()
            return True
        except Exception:
            return False
