# NHJ Audio Standard & Pipeline

How Not-Happy-Jan's audio is built, stored, mixed, and tuned. This is the canonical
reference for the asset fidelity tiers, the single-stream mixer, the on/off-hold sequence,
the experience modes, and every runtime control. Written to be teachable — the *why* sits
next to the *what*.

---

## 1. The one architectural fact that drives everything

All of NHJ's audio runs through **one** `sounddevice.OutputStream` — a single mixer
(`src/nhj/duck_player.py`) that sums named buses on one timeline. Every source, whatever
its format, is **decoded to 48 kHz stereo float32 at playback** (`_decode_file`, `_Decoder`
via PyAV). 

The consequence: **storage format sets only the fidelity *ceiling* and the file *size*, not
the playback path.** A mono 24 kHz clunk and a 48 kHz-stereo copy of it sound identical once
they hit the bus — so storing SFX at 48 kHz stereo is pure waste. We store each asset class
at the lowest tier that loses nothing audible for its role, and let the mixer upsample.

---

## 2. Fidelity tiers

| Class | Format | Channels / rate | Why |
|---|---|---|---|
| **Music** (hold bed) | AAC `.m4a`, ~128 kbps | **stereo 48 k** | The showcase — "very cool music." Compressed is transparent; stereo matters. |
| **Tones** (`call_on_hold`, `call_off_hold`, `failed_transfer`) | PCM `.wav` s16 | **mono 24 k** | Point sounds; phone tones live < 4 kHz. No stereo/HF to lose. |
| **Handset** (pickup/putdown pool) | PCM `.wav` s16 | **mono 24 k** | Mono-recorded clunks. 24 k Nyquist (12 kHz) covers the transient rattle. |
| **Ambient beds** (pub/party/beach/outdoor/callcentre/special-forces-radio) | PCM `.wav` s16 | **mono 24 k** | Scene beds play one-shot; mode beds can loop under voice. Stereo width is inaudible there. |
| **Voice** (scenes / intros / returns / hot-mic asides) | PCM `.wav` s16 | **mono 24 k** | TTS-native (Qwen3-TTS emits 24 k mono). Already optimal. |

**Rule of thumb:** *music is high; everything else is mono 24 k.* One consistent SFX/voice
tier. Cutting the SFX + ambient from 48 k-stereo to this standard took them from ~10.5 MB to
~2.7 MB with zero audible change.

> Masters are never thrown away — full-res source `.m4a` live in `archive/originals/`
> (gitignored), so any asset can be re-derived at a different window/level. The whole
> `audio/` tree is gitignored; only the *code* that builds and plays it is tracked.

---

## 3. The buses

The mixer (`Mixer._callback`) sums four buses every ~21 ms block:

| Bus | Concurrency | Ducking | Carries |
|---|---|---|---|
| **music** | single bed (looping playlist) | ducks under speech/fx; rides the long-session floor | the hold music |
| **fx** | concurrent one-shots | none (sits on top) | going-on-hold putdown + tone |
| **voice** | **sequential queue** (FIFO) | — | off-hold tone+pickup, scene/aside lines, the character verdict |
| **ambient** | concurrent low beds | ducked to `NHJ_AMBIENT_DUCK` (0.35) under voice | scene beds, call-centre room tone, looping mode beds |

**Auto-sidechain:** whenever any fx/voice is sounding, the music target drops to
`NHJ_MUZAK_DUCK_GAIN` (0.22). A per-block linear ramp on `_music_gain` smooths the dip and
recovery. `pause()` freezes the music bed at its exact sample position (skips the bed in the
callback) so it resumes seamlessly; voice/fx/ambient always sound regardless of pause.

**Voice levelling:** the pre-rendered banks are loudness-normalised offline (~-19 LUFS);
live TTS comes out at whatever level the model produced, so the adapter levels each live
clip to the same target (`_normalize_voice_wav` — full-clip RMS gain to `NHJ_VOICE_TARGET_DBFS`,
default -19.5 dBFS, with a -1.5 dBFS peak ceiling) before caching/playback. Timbre-safe (pure
gain); on any error it returns the clip untouched so levelling can never break playback. Live
and banked lines then sit consistently on the bus.

**Cross-process:** the Stop-hook → worker → adapter pipeline doesn't touch audio directly —
it drops play-events (JSON) into `~/.config/nhj/audio-events/`, which the mixer ingests via
`pump_events()`. One timeline, no racing `afplay` processes.

---

## 4. The on/off-hold sequence (gapless by construction)

The call-centre metaphor: Claude working = **you're on hold** (music). Claude done = Jan
**comes off hold** and delivers the verdict.

```
Going ON hold   :  [intro / scene / hot-mic aside]  →  putdown clunk + call_on_hold tone  →  music
Coming OFF hold :  call_off_hold tone + handset pickup  →  the character's verdict (or silence)
```

**Gapless guarantee** — the requirement was *music → click → receiver* with no audible
gaps. Two timed one-shots can't promise that, so `Mixer.play_sequence()` **concatenates** the
tone + clunk into a *single buffer* (zero samples between, per-part gain baked in) and queues
it on the **sequential voice bus**. The character's verdict — when its TTS lands — queues
straight behind it on the same FIFO, so it butts on with no gap. If no verdict fires, you
simply get tone + clunk → silence.

> The verdict is produced by a separate process (TTS takes 1–3 s). If it's slower than the
> ~1.5 s tone+clunk, there's a short natural beat before she speaks — like a real pickup. The
> three sounds that *must* be gapless always are; the verdict-after-clunk is gapless when the
> line is ready in time.

The handset pool is shared and **serves as both pickup and putdown** — a random clip each
time keeps it from sounding canned. Rarely (`NHJ_FAILED_TRANSFER_PROB`) a botched-transfer
gag replaces the off-hold pair.

---

## 5. Experience modes — `nhj mode <…>` (or `NHJ_MODE`)

| Mode | Hold music | Voice filter | Extras |
|---|---|---|---|
| **normal** | yes | — | full hold-music experience (default) |
| **agent-vibes** | **no** | — | just the off-hold notification (tone + pickup) + the verdict. Going-on-hold is silent. A lighter daily driver. |
| **call-centre** | yes | **phone** bandpass (300–3400 Hz + drive) on the whole voice bus | looping call-centre room bed (murmur + key ticks) under the whole mode |

Set persistently with `nhj mode call-centre` (writes the `audio_mode` and `ambient_mode` settings), or per-run
with `NHJ_MODE=agent-vibes`. The controller re-reads the mode every tick, so it applies live.

---

## 6. Long-session ducking

Long inferences shouldn't drone the bed at constant volume. The music **floor** eases from
full down to `NHJ_LONGHOLD_DUCK` (0.15) once a continuous inference passes
`NHJ_LONGHOLD_AFTER` (20 s), over a `NHJ_LONGHOLD_RAMP` (4 s) glide, then **snaps back to
full** when it goes idle and the next hold begins. It rides the same `music_floor` target the
sidechain uses, so it composes cleanly with speech ducking. On by default; `NHJ_LONGHOLD=0`
disables.

---

## 7. Compression — `NHJ_COMP=1` (off by default)

A soft-knee `tanh` saturation + makeup on the master sum, replacing the bare clip limiter —
glues the music + voice + fx and tames inter-bus peaks. Off by default so it doesn't colour
the stock sound; `NHJ_COMP_DRIVE` (1.6) sets the knee. (This is instantaneous waveshaping, not
a time-constant compressor — the long-session ducking handles macro-dynamics; this just
controls peaks.)

---

## 8. Levels — runtime dials, not baked

Masters are **peak-normalised** (−1 dBFS for SFX, loudnorm −20 LUFS for beds); their *role*
level is applied at play time via `NHJ_*_VOLUME` env vars. This keeps re-balancing a one-line
change — no re-encoding — which matters for tuning and for the teaching narrative. SFX that
always pair with a line stay **separate** (the sequential bus concatenates them gaplessly at
runtime), preserving full variation rather than welding fixed pairs.

---

## 9. Modular / swappable voices

Voice routing goes through `character_by_name()` → a `voices/<name>/` directory, and every
spoken clip is an independent voice-bus one-shot. Swapping a character for a different TTS,
a fully dynamic voice, or **the user's own recordings** is just pointing a character at a
different voice set — no pipeline change. *This is exactly why SFX are kept separate from
speech: a pre-merged clunk+line would weld that door shut.*

---

## 9a. Speech speed — applied once, at playback

Speed is applied in exactly **one** place: by **resampling the clip at playback** on the
voice bus. It is *never* sent to the TTS backend — the deployed Qwen model ignores a
synthesis-speed kwarg, and applying it at both synthesis and playback would double it.
Resampling at playback means **bank, cache, and live-synthesised** clips all speed up
identically (they're stored speed-neutral; the cache stays reusable at any speed).

Precedence: an explicit `speed` from the marker/MCP (e.g. `[Jan:ok|speed=1.3]`) overrides
the character band's speed; `speed=1.0` defers to the band's natural pace. Trade-off:
resampling **shifts pitch** (faster = higher) — a pitch-preserving time-stretch would need
a DSP dependency we deliberately don't take.

## 9b. Output-stream resilience

The single `OutputStream` is opened and kept alive by a bounded-backoff keeper
(`StreamKeeper` in `duck_player.py`). A transient PortAudio failure on the **initial open**
no longer kills the controller, and when the default output device switches (or a reopen
fails) it **retries with capped exponential backoff** — even if the device name doesn't
change again — instead of going dead until restart. Failures stay observable (one log line
each) without a tight error loop; queued voice keeps buffering and plays once the stream
recovers.

---

## 10. Control reference

### Modes & settings
| Control | Default | Effect |
|---|---|---|
| `nhj mode normal\|agent-vibes\|call-centre` / `NHJ_MODE` | normal | experience mode |
| `NHJ_MUZAK_PLAYER` | `duck` | selects this single-stream controller |
| `NHJ_MUZAK_VOLUME` | 0.5 | master gain |

### Ducking
| Control | Default | Effect |
|---|---|---|
| `NHJ_MUZAK_DUCK_GAIN` | 0.22 | music floor while speech/fx sounds (sidechain) |
| `NHJ_AMBIENT_DUCK` | 0.35 | ambient bed level under voice |
| `NHJ_LONGHOLD` / `_AFTER` / `_RAMP` / `_DUCK` | 1 / 20 / 4 / 0.15 | long-session muzak fade |

### Hold sounds
| Control | Default | Effect |
|---|---|---|
| `NHJ_MUZAK_TRANSFER` / `_VOLUME` | 1 / 0.85 | on/off-hold tones on/off, level |
| `NHJ_HANDSET` / `_VOLUME` | 1 / 0.9 | handset pickup/putdown on/off, level |
| `NHJ_PUTDOWN` | 1 | putdown clunk going on hold |
| `NHJ_FAILED_TRANSFER` / `_PROB` / `_VOLUME` | 1 / 0.05 / 0.9 | botched-transfer gag |
| `NHJ_MUZAK_INTRO` / `_VOLUME` | 1 / 0.9 | "hold please" intro |

### Scenes & asides
| Control | Default | Effect |
|---|---|---|
| `NHJ_SCENES` / `NHJ_SCENES_PROB` | 1 / 0.08 | easter-egg hold vignettes |
| `NHJ_OPENMIC_PROB` | 0.05 | hot-mic asides (she forgets to hit hold) |

### Filters
| Control | Default | Effect |
|---|---|---|
| `NHJ_COMP` / `NHJ_COMP_DRIVE` | 0 / 1.6 | master soft-knee saturation |
| `NHJ_MODE_AMBIENT_VOLUME` | 0.5 | looping mode-bed level |

---

## 11. Building assets

```bash
# Scene + hot-mic lines (TTS on :9992) + fill MISSING placeholder beds (never clobbers reals)
nhj build-scenes                 # --force-beds to regenerate the synthetic placeholders

# Music tracks live as audio/music/inference_muzak_NNN.m4a (stereo 48k AAC) — kept as-is.
```

SFX one-shots and beds are derived from masters in `archive/originals/` with ffmpeg at the standard:

```bash
# SFX tone:  trim silence → peak-normalise → mono 24k
ffmpeg -i src.m4a -af "<trim>,volume=<g>dB" -ar 24000 -ac 1 -c:a pcm_s16le out.wav

# Ambient bed:  windowed → loudnorm -20 LUFS → 0.4s fades → mono 24k
ffmpeg -ss <t> -t 12 -i src.m4a -af "loudnorm=I=-20:TP=-1.5:LRA=11,afade=in,afade=out" \
       -ar 24000 -ac 1 -c:a pcm_s16le bed.wav

# Handset pool:  energy-envelope splice of a multi-hit take → many normalised mono-24k clips
#                (merge a clunk's internal micro-gaps; drop blips; pad; de-click edges)
```

Synthetic placeholders (`scenes.generate_placeholder_beds`, `generate_callcentre_bed`) emit
at the standard so out-of-box demos match production.

---

## 12. TODO / roadmap

- **Real CC0 field recordings** to replace the synthetic `outdoor` and `callcentre` beds (and
  any beds you want richer). The hooks are ready — drop a `.wav` in `audio/ambient/`.
- **Typing / phone one-shots** sprinkled in call-centre mode (presently folded into the synth
  bed's key-ticks).
- **Modular-voice UX** — expose the voice-swap (different TTS / user-recorded voices) as a
  first-class setting; the architecture already supports it.
- **Time-constant compressor** (attack/release) if the instantaneous saturation proves too
  blunt for "glue."
