# Haptic mouse feedback (Logitech MX Master 4)

NHJ can buzz a **Logitech MX Master 4** when an agent vibe fires — a physical
tap on your hand so you don't have to watch the terminal. The MX Master 4 is
(as of 2025) the only Logitech mouse with a haptic motor; the MX Master 3/3S and
every other model have no actuator and **cannot** do this.

```
Agent vibe → NHJ worker → haptic adapter → HID++ report → MX Master 4 motor
```

## Enable it (off by default)

The haptic adapter is **disabled by default** — most people don't have an MX
Master 4, so NHJ doesn't go looking for it. Turn it on in `config/default.yaml`
(or your own `config.yaml`):

```yaml
haptic:
  enabled: true
```

…or per-shell without editing config:

```bash
export NHJ_HAPTIC_ENABLED=1
```

Once enabled it talks **straight to the mouse over HID++** — no Logi Options+,
no Solaar, no driver, nothing else to install (just the `hid` Python module NHJ
already depends on). It still self-disables if no MX Master 4 is found, so
enabling it on a machine without one is harmless.

## How it works — the mouse stores the waveforms

You do **not** upload vibration patterns. The MX Master 4 ships with a fixed
*library* of named waveforms baked into firmware; NHJ just asks the mouse to
*play* one by id. The library (HID++ feature `0x19B0`, mirrors Solaar's
`HapticWaveForms`):

| id | name | id | name |
|----|------|----|------|
| 0x00 | `sharp_state_change` | 0x08 | `square` |
| 0x01 | `damp_state_change` | 0x09 | `wave` |
| 0x02 | `sharp_collision` | 0x0A | `firework` |
| 0x03 | `damp_collision` | 0x0B | `mad` |
| 0x04 | `subtle_collision` | 0x0C | `knock` |
| 0x05 | `happy_alert` | 0x0D | `jingle` |
| 0x06 | `angry_alert` | 0x0E | `ringing` |
| 0x07 | `completed` | 0x1B | `whisper_collision` |

### Intent → waveform + urgency (NHJ defaults)

The haptic fires on **every** vibe, independently of the voice — so you still get
a knock even when NHJ isn't talking (muzak off, low verbosity, headphones out).
More urgent intents repeat the waveform more times, so they *feel* more insistent:

| intent | waveform | repeats | feel |
|--------|----------|:-------:|------|
| `step` | `subtle_collision` | 1 | gentle progress tick |
| `ok` | `completed` | 1 | satisfying "done" tap |
| `celebrate` | `firework` | 1 | celebratory flourish |
| `warn` | `knock` | 2 | knock-knock |
| `err` | `angry_alert` | 2 | sharp double-jab |
| `attn` | `ringing` | 3 | insistent "look at me" |

A hot vibe (`vibe_level >= 8`) adds **one extra repeat** on top.

## Settings (the `haptic:` config block)

Everything lives under `haptic:` in `config/default.yaml`:

```yaml
haptic:
  enabled: false           # opt-in master switch (env: NHJ_HAPTIC_ENABLED=1)
  intensity: 5             # firmware vibration strength 0-5 (5 = strongest); null = device default
  device_index: null       # HID++ device slot; null = auto-discover (fallback 2)
  taps:                    # tap STYLE per intent — the "extended" tuning
    step:      { waveform: subtle_collision, repeat: 1 }
    ok:        { waveform: completed,        repeat: 1 }
    celebrate: { waveform: firework,         repeat: 1 }
    warn:      { waveform: knock,            repeat: 2 }
    err:       { waveform: angry_alert,      repeat: 2 }
    attn:      { waveform: ringing,          repeat: 3 }
```

| Setting | What it does |
|---------|--------------|
| `enabled` | Master on/off. Off by default. `NHJ_HAPTIC_ENABLED=1` overrides. |
| `intensity` | Firmware strength `0`–`5`, sent with every play. `null` leaves the device default. |
| `device_index` | Force the HID++ slot if auto-discovery picks wrong (e.g. multiple paired devices). `NHJ_HAPTIC_DEVICE_INDEX` overrides. |
| `taps.<intent>.waveform` | Which firmware waveform that intent plays — your "tap style per error type". Any name from the library table, or a numeric id. |
| `taps.<intent>.repeat` | How many times it repeats = how insistent it feels. |

**Change the tap style for a specific intent** — e.g. make errors a hard triple
`sharp_collision` and successes a gentle `jingle`:

```yaml
haptic:
  enabled: true
  taps:
    err: { waveform: sharp_collision, repeat: 3 }
    ok:  { waveform: jingle,          repeat: 1 }
```

Built-in fallbacks (used when `taps` omits an intent) live in `DEFAULT_TAPS` in
`src/nhj/adapters/haptic.py`.

> **Note on `intensity`:** the second parameter byte of the play command is the
> firmware vibration strength (`0`–`5`), verified by feel on an MX Master 4 — `5`
> is noticeably harder than `0`. It isn't in Logitech's public docs, so a future
> firmware *could* change it; `repeat` remains a fully reliable second lever for
> insistence.

## The HID++ protocol (for the curious / for porting)

NHJ talks to the mouse directly over the receiver's HID++ interface — no Logi
Options+ needed, works headless. The "play" message is a **short HID++ report**:

```
report id  device_idx  feature_idx  func<<4|swid  waveform  00  00
   0x10        0x02         0x0B          0x4E        0x07   00  00
```

- **device_idx** — the paired-device slot on the receiver. `0x02` on the test
  rig. Resolved by pinging; `0x02` is the fallback.
- **feature_idx** — the *runtime* index the device assigns to feature `0x19B0`.
  This is **not fixed** — it varies by device/firmware, so NHJ resolves it at
  runtime via `IRoot.getFeature(0x19B0)` (root feature index `0x00`, function
  `0x0`). Observed value `0x0B`; used as the fallback.
- **func<<4|swid** — `0x4E` = function `0x4` ("play waveform") + software-id `0xE`
  (the swid is echoed in replies so you can match them; the value is arbitrary).
- **waveform** — a byte from the table above.

The interface is the Logitech receiver collection with `usage_page == 0xFF00`.
Implementation: `src/nhj/adapters/haptic.py` (`play()` / `_discover()`).

Credit: protocol verified against
[pwr-Solaar/Solaar](https://github.com/pwr-Solaar/Solaar) and
[lukasfri/mx4notifications](https://github.com/lukasfri/mx4notifications).

### Quick manual test

```bash
.venv/bin/python -c "from nhj.adapters.haptic import play; play('firework')"
```

## Alternative: HapticWebPlugin via Logi Options+

If you'd rather not write raw HID (e.g. on Windows, or to avoid touching the
device while Logi Options+ holds it), there's a bridge approach: install the
[HapticWebPlugin](https://github.com/Fallstop/HapticWebPlugin) into Logi
Options+, which exposes a local HTTPS API, then POST to it:

```bash
curl -s -X POST "https://local.jmw.nz:41443/haptic/completed"
```

See [ChefJodlak/claude-code-logitech-haptic-plugin](https://github.com/ChefJodlak/claude-code-logitech-haptic-plugin)
for a ready-made hook using that route. NHJ uses the direct-HID route by default
because it needs no extra software and runs headless, but the two are
interchangeable — both just play a named waveform.

## Troubleshooting

- **No buzz, `available()` is False** — the `hid` Python module can't see a
  Logitech `0xFF00` interface. Check the receiver is plugged in; on Linux you may
  need a udev rule for hidraw access.
- **No buzz, `available()` is True** — wrong device/feature index. Run the probe
  to see what resolves: it pings `IRoot.getFeature(0x19B0)` across device indices.
- **It's an MX Master 3/3S** — no haptic motor; this adapter can't help. Use the
  LaMetric/AWTRIX display adapters or audio instead.
- **Conflicts with Logi Options+** — generally fine (the vendor page allows
  shared access), but if writes fail, quit Options+ or switch to the
  HapticWebPlugin route above.
