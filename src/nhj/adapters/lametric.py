"""LaMetric Time notification adapter."""
from __future__ import annotations

import os
from typing import Optional, TYPE_CHECKING

import urllib3
import requests

from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ICONS = {
    "ok":        "i2663",
    "err":       "i555",
    "warn":      "i600",
    "attn":      "i1100",
    "celebrate": "i4200",
    "progress":  "i2324",
}

SOUNDS = {
    "positive4":    "positive4",
    "negative1":    "negative1",
    "notification": "notification",
    "knock-knock":  "knock-knock",
    "win":          "win",
}


class LaMetricAdapter(NotificationAdapter):
    def __init__(self, ip: Optional[str] = None, api_key: Optional[str] = None):
        self.ip      = ip      or os.getenv("LAMETRIC_DEVICE_IP", "")
        self.api_key = api_key or os.getenv("LAMETRIC_API_KEY", "")
        self.port    = 4343
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            s = requests.Session()
            s.auth    = ("dev", self.api_key)
            s.verify  = False
            s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
            self._session = s
        return self._session

    def available(self) -> bool:
        return bool(self.ip and self.api_key)

    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, **kwargs) -> bool:
        if not self.available():
            return False
        from nhj.config import INTENTS
        cfg = INTENTS.get(intent, {})
        icon_id  = ICONS.get(cfg.get("icon", intent), "i2663")
        sound_id = cfg.get("sound")
        text     = message or cfg.get("label", intent.upper())

        body: dict = {
            "priority": "critical",
            "model": {
                "cycles": 1,
                "frames": [{"icon": icon_id, "text": text}],
            },
        }
        if sound_id:
            body["model"]["sound"] = {
                "category": "notifications",
                "id": SOUNDS.get(sound_id, sound_id),
                "repeat": 1,
            }
        try:
            r = self._get_session().post(
                f"https://{self.ip}:{self.port}/api/v2/device/notifications",
                json=body, timeout=5,
            )
            r.raise_for_status()
            return True
        except Exception:
            return False
