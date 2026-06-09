# Devices & extensibility

NHJ fires every alert at *all* configured outputs at once — voice, displays, haptics, a
bell, whatever you've wired up. Each output is a small **adapter** that's enabled only when
its config is present, so you run exactly the hardware you own and nothing else.

You don't need *any* hardware — the voice alone is the whole experience. But a glanceable
display is the single best add-on, and the cheapest.

## ⭐ Start here: a Ulanzi pixel clock (AWTRIX)

If you buy one thing, buy a **Ulanzi TC001** (~AU$60 / ~US$45). It's an 8×32 RGB pixel
matrix that, once flashed with the open **[AWTRIX 3](https://blueforcer.github.io/awtrix3/)**
firmware, NHJ drives directly — a colour-coded icon + an in-character headline per alert,
plus the rave party animation.

Why it's the recommended starter:
- **Trivial to flash** — browser-based installer, no toolchain, ~10 minutes start to finish.
- **No hub, no cloud** — talks plain HTTP on your LAN (or MQTT if you want many displays).
- **Cheap and self-contained** — it's a desk clock the rest of the time.

👉 **Full setup: [awtrix-display-setup.md](../awtrix-display-setup.md)** (flash → wifi →
point NHJ at it). HTTP is the easy path; MQTT is there when you want to drive several at once
or bridge to Home Assistant.

## Other supported devices

| Device | What it does | Enable with | Notes |
|---|---|---|---|
| **Logitech MX Master 4** | A firmware haptic waveform per alert, escalating insistence | opt-in — [haptic-mouse-setup.md](../haptic-mouse-setup.md) | The *only* mouse with a haptic motor; talks straight to it, no Logitech software |
| **LaMetric Time** | Icon + text + built-in sound | `LAMETRIC_DEVICE_IP` + `LAMETRIC_API_KEY` | Polished commercial pixel clock |
| **Divoom Times Gate / Frame** | Coloured text notification | `DIVOOM_DEVICE_IP` | |
| **ESP32 push bell** | Rings a real electromechanical bell | `ESP32_BELL_URL` | DIY — sketch in [`examples/esp32/`](../../examples/esp32/) |

## Bring your own device

Anything you can POST to can be an NHJ output — a Vestaboard, smart lights, a webhook, a
second pixel clock. An adapter is one small class:

```python
# mypkg/adapters.py
from nhj.adapters.base import NotificationAdapter

class MyDeviceAdapter(NotificationAdapter):
    def available(self) -> bool:
        return bool(os.getenv("MYDEVICE_URL"))      # enabled only when configured

    def fire(self, intent, message, character, vibe_level=5, **kw) -> bool:
        # intent ∈ ok|err|warn|attn|celebrate|step ; message is the line; character has the persona
        requests.post(os.getenv("MYDEVICE_URL"), json={"text": message, "intent": intent})
        return True
```

Register it via an entry point and NHJ discovers it — no core changes:

```toml
# pyproject.toml
[project.entry-points."nhj.adapters"]
mydevice = "mypkg.adapters:MyDeviceAdapter"
```

Add it to the `adapters:` list in `config/default.yaml` (fire order) and you're done. The
bundled adapters in [`src/nhj/adapters/`](../../src/nhj/adapters/) are the reference —
`ulanzi.py` is the most full-featured (icons, effects, per-intent treatments, MQTT+HTTP).
