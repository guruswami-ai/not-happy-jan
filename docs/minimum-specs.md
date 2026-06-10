# Minimum specs & the three ways to run NHJ

*← [Not-Happy-Jan docs](index.md)*

Not-Happy-Jan scales from *"runs on anything, quietly"* to *"the full Australian bogan
call-centre, live."* It's all the one install — these tiers are just switches.

| Tier | What you get | Needs | Resident RAM |
|---|---|---|---|
| **Silent** | markers + haptics/displays, no audio | any Mac · `nhj mute` | ~nil |
| **Pre-recorded** | the cast speaks from the **bundled** voice bank (ships in the package) | any Mac (`--minimal` install) | ~nil (no model loaded) |
| **Full experience ⭐** | **live cloned voices + the dynamic bogan brain** | Apple Silicon M1+, 16 GB rec. | ~3.2 GB while warm |

## Tier 0 — Silent / text-only

Run `nhj mute` (or use a host with no audio output). NHJ still fires its `[vibes:]`
events to whatever you've wired up — the haptic mouse, a Ulanzi pixel display, an ESP32
bell — with no sound. The lightest possible footprint; runs on any Mac. (Note: `TTS_ENGINE=none`
alone is **not** silent — it disables only live synthesis, and the bundled bank still speaks.)

## Tier 1 — Pre-recorded samples (the "she'll be right" tier)

A **`--minimal` install** runs NHJ entirely from the **bundled voice bank** — a set of
pre-rendered character lines that ships **inside the package**. Out of the box, with no
model and no downloads, Jan, Bazza and Karren speak for each feedback state **with nothing
resident** — the mixer just plays a WAV. You get the cloned voices and the whole
call-centre flow on any Mac.

The trade-off: a **finite repertoire**. The lines come from a fixed bank, so they repeat,
and you don't get fresh, dial-scaled slang or the full swearing ladder. Great for older
Macs, RAM-tight laptops, or anyone who wants the vibe without the heavyweight bits.
(`nhj build-bank` lets you pre-render **your own** lines on top, if you've added a custom voice.)

## Tier 2 — The full experience ⭐ (where the magic happens)

This is what Not-Happy-Jan is *for*. Turn on the two local models and the whole thing
comes alive:

- **Local Qwen3-TTS** (~2.2 GB) — every line is synthesised **live, in the character's
  own cloned voice**. Nothing is canned.
- **`ocker-bogan-nano`** (~0.94 GB) — NHJ's own **custom swear-bear-bogan LLM**, crafted
  from the highest-quality drunken Macca's reviews and the unfiltered depths of Australian
  Reddit. It writes a **fresh** line in character every single time, scaling from polite
  reception all the way to full-tilt, ockerism-11, bleeped-or-not swearing.

Together they deliver the **full Australian bogan call-centre experience**: Jan picks up
and mangles your variable names, Bazza escalates when the build goes warm, and Karren has
a complete meltdown when it all falls over — every time in a live voice, saying something
you've never heard before.

### Specs for the full experience

| | Requirement |
|---|---|
| **Chip** | **Apple Silicon (M1 or later)** — TTS and the LLM run on Metal / MLX |
| **RAM** | 8 GB works · **16 GB recommended** (~3.2 GB resident while both daemons are warm) |
| **Disk** | ~5 GB — TTS model (~2.2 GB) + ocker-bogan-nano (~0.94 GB) + media (~250 MB) |
| **Network** | once, to fetch the models + media — then **100% local & private** |

### About that ~3.2 GB — and how to get it back

![Models are downloaded once into ~/Library/Caches (about 5 GB) and served two ways. On-demand (default): the first vibe starts the server, it serves the line holding about 3 GB of RAM, then after about ten minutes idle it unloads and frees the RAM, repeating each cycle. Persistent (full profile): the daemons stay always warm at about 3.2 GB resident for sub-second replies with no cold start.](assets/diagrams/model-lifecycle.svg){ width="560" .center }

Warm daemons give sub-second replies (no cold-start), at ~2.1 GB (TTS) + ~1.1 GB (LLM)
resident while loaded — nothing on a Mac Studio, noticeable on a 16 GB laptop.

The **default install uses on-demand model loading**; `bash install.sh --full` opts into
the always-warm persistent daemons instead. You can also switch at runtime:

```bash
nhj servers on-demand     # start servers on the first vibe, unload after idle
nhj servers persistent    # back to always-warm
nhj servers               # show the current mode + which servers are up
```

In on-demand mode nothing is resident at rest: the first dynamic/TTS vibe starts the
server, and a supervisor **reaps it after ~10 min idle** (`NHJ_SERVER_IDLE`), so you only
pay the ~3 GB while NHJ is actually talking — at the cost of a one-off cold-start on the
first vibe after a quiet spell. The default install sets up the dynamic LLM; set
`NHJ_DYNAMIC=0` to run TTS-only, and `TTS_ENGINE=none` to fall back to the bundled bank
(which pays nothing).

## Switching tiers

| Want… | Do |
|---|---|
| Silent | `nhj mute` |
| Pre-recorded only (the bundled bank) | `bash install.sh --minimal` — no models; the cast speaks from the bundled bank |
| The full experience (default install) | `bash install.sh` — TTS + the `ocker-bogan-nano` LLM + media, loaded on demand |
| The full experience (persistent daemons) | `bash install.sh --full` — same, kept warm — see **[Install profiles](install-profiles.md)** |

Where the models and media live on disk is covered in
**[Configuration & requirements](configuration.md)**.
See **[Install profiles](install-profiles.md)** for the full breakdown of default · full · minimal.
