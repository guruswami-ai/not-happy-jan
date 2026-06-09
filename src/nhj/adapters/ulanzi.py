"""ULANZI TC001 / AWTRIX 3 adapter — agent-status vibes on an LED matrix.

Shows each NHJ intent on one or more Ulanzi TC001 displays running AWTRIX 3
(Blueforcer awtrix-light), over MQTT (broadcast) or HTTP. Each fire picks a random
icon from the intent's pool, applies a per-intent visual treatment (colour, effect,
blink, hold), and shows in-character text. No audio (the voice channel handles that).

Text resolution per fire:
  - with a message:  dynamic LLM 2-3 word in-character compression of it
                     (when NHJ_DYNAMIC + dynamic_text), else the message verbatim.
  - bare marker:     a random curated phrase from the intent's pool (text_style:
                     ocker | plain). The small LLM is NOT used on bare markers — it
                     has nothing to summarise and tends to ramble.

Config — config/default.yaml `awtrix:` block (env overrides in brackets):
    transport:    mqtt | http                 [ULANZI_TRANSPORT]
    mqtt_host:    broker host                  [ULANZI_MQTT_HOST]
    mqtt_port:    1883                         [ULANZI_MQTT_PORT]
    mqtt_user/pass:                            [ULANZI_MQTT_USER / ULANZI_MQTT_PASS]
    displays:     [awtrix1.lan, ...]  (str = host, prefix = first label; or {prefix, http})
                                               [ULANZI_MQTT_PREFIXES + ULANZI_HTTP_HOSTS]
    brightness:   0-255 or null (null = leave alone)   [ULANZI_BRIGHTNESS]
    auto_brightness: true|false|null
    install_icons: true   — upload bundled intent icons to each display on first run
    text_style:   ocker | plain
    dynamic_text: true    — LLM compresses the message into a headline when NHJ_DYNAMIC is on

The visual language (pools / treatments / phrases) lives in this module as sensible
defaults; edit here to tune it.
"""
from __future__ import annotations

import json
import os
import random
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import requests
import urllib3

from nhj import config
from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_ASSETS = Path(__file__).resolve().parent / "awtrix_assets"

# ── visual language ───────────────────────────────────────────────────────────
# Icon pool per intent (LaMetric IDs; random pick each fire). The .gif for each is
# bundled in awtrix_assets/ and auto-installed to the displays.
POOLS = {
    "ok":        ["234", "544", "8612", "635", "3279", "4701", "613"],   # check/thumbsup/star/wink/beer
    "step":      ["8351", "11457", "35196", "4449", "6723", "1311"],     # spinner/hourglass/pacman/coffee
    "warn":      ["609", "3491", "8750", "6134", "1077"],                # warning/caution/!
    "err":       ["1264", "2493", "148", "4287", "5464", "8520", "3047"],# cross/skull/bomb/explosion/poop
    "attn":      ["2883", "3003", "555", "625", "5933", "112"],          # bell/eyes/hand/?/alarm
    "celebrate": ["1078", "1798", "23334", "16635", "24443", "8110", "32989"],  # fireworks/trophy/parrot/beer/champagne
}

# Per-intent treatment. Severity -> visual intensity: ok/step calm, warn/err blink,
# attn holds + radar sweep, celebrate goes big.
TREATMENTS = {
    "ok":        {"color": "#22C55E", "duration": 5},
    "step":      {"color": "#64748B", "duration": 3, "noScroll": True},
    "warn":      {"color": "#F59E0B", "duration": 8, "blinkText": 600},
    "err":       {"color": "#EF4444", "duration": 10, "blinkText": 450},
    "attn":      {"color": "#3B82F6", "duration": 20, "effect": "Radar"},   # prominent but self-clears (no hold — a held notif blocks all other updates)
    "celebrate": {"rainbow": True,    "duration": 8, "effect": "Fireworks"},
}

# Curated fallback phrase pools (random pick) for bare markers — always sensible.
FALLBACK = {
    "ocker": {
        "ok":        ["Sweet as", "Too easy", "Done & dusted", "Bewdy!"],
        "step":      ["Crackin' on", "On it", "Chippin' away", "Workin' on it"],
        "warn":      ["Oi, careful", "Heads up", "Watch it", "Bit dodgy"],
        "err":       ["Bugger!", "She's cooked", "Stuffed it", "Pear-shaped"],
        "attn":      ["Need ya!", "You're up", "Oi, over here", "Sort this"],
        "celebrate": ["Ripper!", "Bewdy!", "Get amongst it", "Bloody legend"],
    },
    "plain": {
        "ok":        ["Done", "Complete", "Success"],
        "step":      ["Working", "In progress", "Step done"],
        "warn":      ["Heads up", "Warning", "Caution"],
        "err":       ["Error", "Failed", "Broke"],
        "attn":      ["Needs you", "Action needed", "Your input"],
        "celebrate": ["Milestone!", "Nailed it", "Big win"],
    },
}

# Rave party mode: a persistent custom app whose word + text-colour STYLE animate/cycle.
# Words stay short so they always read in the built-in font; the style is the show.
RAVE_WORDS = ["MUZAK", "RAVE", "MODE", "BOOG", "D-FLOOR", "CARN!", "SICK!", "STOKED", "ONYA"]
# Bright full-screen effects for the effect-background style (black text reads over them).
PARTY_EFFECTS = ["Plasma", "PlasmaCloud", "ColorWaves", "Pacifica", "SwirlIn", "SwirlOut"]
PARTY_COLORS = ["#FF2D78", "#FFD400", "#22E0FF", "#39FF14", "#FF7A00", "#A855F7", "#FF4DFF", "#00FFB3"]


def _awtrix_cfg() -> dict:
    return config.section("awtrix")


def _csv(name: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


class UlanziAdapter(NotificationAdapter):
    def __init__(self):
        cfg = _awtrix_cfg()
        self.transport = (os.getenv("ULANZI_TRANSPORT") or cfg.get("transport") or "mqtt").lower()
        self.mqtt_host = os.getenv("ULANZI_MQTT_HOST") or cfg.get("mqtt_host", "")
        self.mqtt_port = int(os.getenv("ULANZI_MQTT_PORT") or cfg.get("mqtt_port", 1883))
        self.mqtt_user = os.getenv("ULANZI_MQTT_USER") or cfg.get("mqtt_user", "")
        self.mqtt_pass = os.getenv("ULANZI_MQTT_PASS") or cfg.get("mqtt_pass", "")
        self.text_style = (os.getenv("ULANZI_TEXT_STYLE") or cfg.get("text_style") or "ocker").lower()
        self.install_icons = cfg.get("install_icons", True)
        self.dynamic_text = cfg.get("dynamic_text", True)
        self.brightness = self._opt_int(os.getenv("ULANZI_BRIGHTNESS"), cfg.get("brightness"))
        self.auto_brightness = cfg.get("auto_brightness")
        self.displays = self._resolve_displays(cfg)
        self._setup_done = False

    @staticmethod
    def _opt_int(env_val, cfg_val):
        v = env_val if env_val not in (None, "") else cfg_val
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _resolve_displays(self, cfg: dict) -> list[dict]:
        """Each display -> {'prefix': <mqtt prefix>, 'http': <host or None>}."""
        out: list[dict] = []
        entries = cfg.get("displays") or []
        if entries:
            for e in entries:
                if isinstance(e, dict):
                    out.append({"prefix": e.get("prefix") or "", "http": e.get("http")})
                else:  # bare string = host; prefix is its first label
                    out.append({"prefix": str(e).split(".")[0], "http": str(e)})
        else:  # env back-compat
            prefixes = _csv("ULANZI_MQTT_PREFIXES")
            https = _csv("ULANZI_HTTP_HOSTS") or _csv("ULANZI_DEVICE_IP")
            for i, p in enumerate(prefixes):
                http = https[i] if i < len(https) else (p if "." in p else None)
                out.append({"prefix": p, "http": http})
            if not prefixes and https:  # http transport, hosts only
                out = [{"prefix": h.split(".")[0], "http": h} for h in https]
        return [d for d in out if d["prefix"] or d["http"]]

    def available(self) -> bool:
        if not self.displays:
            return False
        return bool(self.mqtt_host) if self.transport == "mqtt" else any(d["http"] for d in self.displays)

    # ── text / payload ────────────────────────────────────────────────────────
    def _text(self, intent: str, message: str, character) -> str:
        if message:
            if self.dynamic_text and character is not None:
                try:
                    from nhj import boganify
                    if boganify.enabled():
                        line = boganify.generate_display(character, intent, message)
                        if line:
                            return line
                except Exception:
                    pass
            return message
        pool = FALLBACK.get(self.text_style, FALLBACK["ocker"]).get(intent) or [intent.upper()]
        return random.choice(pool)

    def _payload(self, intent: str, text: str) -> dict:
        t = TREATMENTS.get(intent, {"color": "#FFFFFF", "duration": 6})
        body = {
            "text": text,
            "icon": random.choice(POOLS.get(intent, ["234"])),
            "duration": t.get("duration", 6),
            "textCase": 2,
            "wakeup": True,
            "stack": False,   # latest status wins — don't queue a backlog you'd have to dismiss
        }
        if t.get("rainbow"):
            body["rainbow"] = True
        else:
            body["color"] = t.get("color", "#FFFFFF")
        for k in ("effect", "blinkText", "noScroll", "hold"):
            if k in t:
                body[k] = t[k]
        return body

    # ── one-time setup: brightness + bundled-icon install ──────────────────────
    def _ensure_setup(self) -> None:
        if self._setup_done:
            return
        self._setup_done = True  # attempt once per process regardless of outcome
        # Brightness + icon install touch each display over HTTP and can take a few
        # seconds; run them off the fire path so the first vibe still publishes
        # immediately (icons are cached on the device after the first run anyway).
        import threading
        threading.Thread(target=self._run_setup, daemon=True).start()

    def _run_setup(self) -> None:
        self._apply_brightness()
        if self.install_icons:
            self._install_icons()

    def _apply_brightness(self) -> None:
        s: dict = {}
        if self.brightness is not None:
            s["BRI"] = max(0, min(255, self.brightness))
            # a fixed brightness implies manual mode, else the light sensor overrides it
            if self.auto_brightness is None:
                s["ABRI"] = False
        if self.auto_brightness is not None:
            s["ABRI"] = bool(self.auto_brightness)
        if not s:
            return
        if self.transport == "mqtt":
            if not self._broker_up():
                return
            try:
                import paho.mqtt.publish as publish
                msgs = [{"topic": f"{d['prefix']}/settings", "payload": json.dumps(s)}
                        for d in self.displays if d["prefix"]]
                auth = {"username": self.mqtt_user, "password": self.mqtt_pass} if self.mqtt_user else None
                publish.multiple(msgs, hostname=self.mqtt_host, port=self.mqtt_port,
                                 auth=auth, client_id="nhj-ulanzi")
            except Exception:
                pass
        else:
            for d in self.displays:
                if d.get("http"):
                    try:
                        requests.post(f"http://{d['http']}/api/settings", json=s, timeout=3)
                    except Exception:
                        pass

    def _install_icons(self) -> None:
        needed = {i for pool in POOLS.values() for i in pool}
        for d in self.displays:
            host = d.get("http")
            if not host:
                continue
            try:
                r = requests.get(f"http://{host}/list?dir=/ICONS", timeout=4)
                present = {f["name"].rsplit(".", 1)[0] for f in r.json()}
            except Exception:
                continue
            for iid in needed - present:
                asset = _ASSETS / f"{iid}.gif"
                if not asset.is_file():
                    continue
                try:
                    b = "----awtrixicon"
                    body = (f'--{b}\r\nContent-Disposition: form-data; name="data"; '
                            f'filename="/ICONS/{iid}.gif"\r\nContent-Type: image/gif\r\n\r\n'
                            ).encode() + asset.read_bytes() + f"\r\n--{b}--\r\n".encode()
                    requests.post(f"http://{host}/edit", data=body,
                                  headers={"Content-Type": f"multipart/form-data; boundary={b}"},
                                  timeout=5)
                except Exception:
                    continue

    # ── fire ──────────────────────────────────────────────────────────────────
    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, **kwargs) -> bool:
        if not self.available():
            return False
        # Mode can blank the displays (quiet / special-forces). Rave still cycles
        # via the controller; this only suppresses per-vibe alert tiles.
        try:
            from nhj.state import get_setting
            if (get_setting("display", "normal") or "normal").lower() == "off":
                return False
        except Exception:
            pass
        self._ensure_setup()
        body = self._payload(intent, self._text(intent, message, character))
        result = self._fire_mqtt(body) if self.transport == "mqtt" else self._fire_http(body)
        if self._rave_on():            # refresh the party tile so it varies between alerts
            self._publish_custom("rave", self._party_tile())
        return result

    # ── rave party display ──────────────────────────────────────────────────────
    def _rave_on(self) -> bool:
        try:
            from nhj.state import get_flag
            return get_flag("muzak_rave", False)
        except Exception:
            return False

    def _party_tile(self) -> dict:
        # Cycle a short party word + an animated text-colour STYLE each refresh.
        body = {"text": random.choice(RAVE_WORDS), "textCase": 1}
        style = random.choice(["rainbow", "effect", "gradient", "blink"])
        if style == "rainbow":
            body["rainbow"] = True                       # animated per-letter spectrum
        elif style == "effect":
            body["color"] = "#000000"                    # black silhouette over a bright animated bg
            body["effect"] = random.choice(PARTY_EFFECTS)
        elif style == "gradient":
            body["gradient"] = random.sample(PARTY_COLORS, 2)
        else:  # blink
            body["color"] = random.choice(PARTY_COLORS)
            body["blinkText"] = random.choice([400, 600])
        return body

    def _publish_custom(self, name: str, payload) -> None:
        body = json.dumps(payload) if isinstance(payload, dict) else ""
        if self.transport == "mqtt":
            if not self._broker_up():
                return
            try:
                import paho.mqtt.publish as publish
                msgs = [{"topic": f"{d['prefix']}/custom/{name}", "payload": body}
                        for d in self.displays if d["prefix"]]
                auth = {"username": self.mqtt_user, "password": self.mqtt_pass} if self.mqtt_user else None
                publish.multiple(msgs, hostname=self.mqtt_host, port=self.mqtt_port,
                                 auth=auth, client_id="nhj-ulanzi")
            except Exception:
                pass
        else:
            for d in self.displays:
                if d.get("http"):
                    try:
                        requests.post(f"http://{d['http']}/api/custom/{name}", data=body, timeout=3)
                    except Exception:
                        pass

    def set_rave(self, on: bool) -> None:
        """Toggle a persistent party custom-app on the displays (called by `nhj muzak rave`)."""
        if not self.available():
            return
        self._ensure_setup()
        self._publish_custom("rave", self._party_tile() if on else "")

    def refresh_rave(self) -> None:
        """Publish a fresh random party tile — called on a cadence by the muzak controller
        so the displays actively cycle effects/icons during rave (not just on vibes)."""
        if self.available() and self._rave_on():
            self._publish_custom("rave", self._party_tile())

    def _broker_up(self) -> bool:
        """Fast reachability probe so a down broker fails in ~0.4s instead of
        blocking the worker on paho's connect timeout."""
        try:
            with socket.create_connection((self.mqtt_host, self.mqtt_port), timeout=0.4):
                return True
        except OSError:
            return False

    def _fire_mqtt(self, body: dict) -> bool:
        if not self._broker_up():
            return False
        try:
            import paho.mqtt.publish as publish
            msgs = [{"topic": f"{d['prefix']}/notify", "payload": json.dumps(body)}
                    for d in self.displays if d["prefix"]]
            auth = {"username": self.mqtt_user, "password": self.mqtt_pass} if self.mqtt_user else None
            publish.multiple(msgs, hostname=self.mqtt_host, port=self.mqtt_port,
                             auth=auth, client_id="nhj-ulanzi")
            return bool(msgs)
        except Exception:
            return False

    def _fire_http(self, body: dict) -> bool:
        ok = False
        for d in self.displays:
            host = d.get("http")
            if not host:
                continue
            try:
                requests.post(f"http://{host}/api/notify", json=body, timeout=3).raise_for_status()
                ok = True
            except Exception:
                continue
        return ok
