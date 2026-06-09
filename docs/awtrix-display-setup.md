# AWTRIX display setup

NHJ can mirror agent-status vibes onto one or more **LED-matrix displays** (the
`ulanzi` adapter): a colour-coded icon + an in-character headline per intent, with a
`rave` party mode and the rest. This covers the hardware/network side.

There are two ways to drive the displays. **Start with HTTP — it's the easy path
and most people are running in ~10 minutes once the display is flashed.** Move to
MQTT only if you want more displays, Home Assistant, or cross-network reach.

## What you need

- **An Ulanzi TC001** smart pixel clock (8×32 RGB matrix on an ESP32) — **~AU$60 /
  ~US$40** from Amazon / AliExpress. (Other AWTRIX-capable boards work too.)
- **2.4 GHz Wi-Fi** it can join.
- *(MQTT path only)* an MQTT broker somewhere on your network.

---

## The easy path — HTTP (no broker, ~10 min)

1. **Flash AWTRIX 3.** Plug the TC001 in via USB-C and use the official web flasher
   (Chrome/Edge): <https://blueforcer.github.io/awtrix3/#/flasher>.
2. **Join Wi-Fi.** On first boot it starts a captive-portal hotspot (`AWTRIX_xxxx`) —
   connect and enter your Wi-Fi.
3. **Note its name.** Every AWTRIX advertises itself over mDNS as
   **`awtrix_<chipid>.local`** (the chip id is shown in the web UI / its `uid`).
   Use that name and you never care what IP DHCP hands it.
4. **Point NHJ at it** — in `.env` or `config/default.yaml`:
   ```yaml
   awtrix:
     transport: http
     displays: [awtrix_96895c.local]      # one, or several
     brightness: 70                       # optional (0-255)
   ```
   ```bash
   # or env:
   ULANZI_TRANSPORT=http
   ULANZI_HTTP_HOSTS=awtrix_96895c.local
   ```

That's it. NHJ POSTs notifications straight to the device, and on first run uploads
the bundled intent icons. Done.

### Make it blank-idle (recommended)
So the matrix is dark until something notifies: in the web UI turn off the built-in
apps (Time / Date / Temperature / Humidity / Battery), then **reboot the device
once** — AWTRIX builds its app loop at boot, so disabled apps only clear after a
restart. (API equivalent: `POST /api/settings {"TIM":false,"DAT":false,"TEMP":false,"HUM":false,"BAT":false}` then `POST /api/reboot`.)

> **mDNS caveat:** `.local` names are *link-local* — they resolve on the same LAN
> segment, but not across VLANs, subnets, or a VPN. On one flat home network (the
> common case) they're perfect. If yours is segmented, either use the device's IP
> (give it a static DHCP reservation) or use the MQTT path below.

---

## The extensible path — MQTT (more displays, Home Assistant, segmented networks)

Worth it when you want: **one publish to fan out to many displays**, **Home
Assistant auto-discovery**, a **shared bus** other devices also use, or reach
**across VLANs/subnets** (the display dials out to the broker, so NHJ never needs
the device's address).

1. Run an MQTT broker anywhere always-on ([Mosquitto](https://mosquitto.org/); a
   default LAN-only anonymous install is fine). **Don't** run it on the machine NHJ
   runs on if that's a laptop — the broker should be on stable, always-on infra.
2. Flash + Wi-Fi as above, then in the display's web UI → **Settings → MQTT**: set
   the **broker address** + port `1883`, blank creds (anonymous), and a unique
   **prefix** per device (e.g. `awtrix1`).
3. Point NHJ at the broker:
   ```yaml
   awtrix:
     transport: mqtt
     mqtt_host: mqtt.lan
     displays: [{prefix: awtrix1, http: awtrix_96895c.local}, ...]
   ```
   ```bash
   ULANZI_TRANSPORT=mqtt
   ULANZI_MQTT_HOST=mqtt.lan
   ULANZI_MQTT_PREFIXES=awtrix1,awtrix2
   ULANZI_HTTP_HOSTS=awtrix_96895c.local,awtrix_968ac8.local   # for one-time icon install
   ```
   (Icon install is always direct HTTP to each device — it never goes via the broker.)

---

## Notes

- **HTTP vs MQTT, quick rule:** flat home LAN + a few displays → **HTTP+`.local`**.
  Want HA / many displays / multiple VLANs / a shared bus → **MQTT**.
- **MQTT is pub/sub, not a queue** — a display offline when a vibe is published
  misses it (won't catch up). Fine for transient status.
- **Sound**: the TC001 has a buzzer (RTTTL only); NHJ leaves it silent — the voice
  channel handles audio.
- **Adding displays**: flash another, give it a name/prefix, add it to `displays:`.
