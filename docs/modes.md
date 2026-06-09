# Modes — switch the whole vibe at once

*← back to the [Not-Happy-Jan README](../README.md)*

A **mode** is a one-word macro that reconfigures *everything* together — hold music, audio FX, persona, dials, the displays, the TTS voice and the mouse haptics — and clears whatever the previous mode left behind. They're mutually exclusive, so you never end up with (say) a rave *and* a call-centre running at once.

```bash
nhj mode rave            # or just ask Claude: "switch to rave mode"
nhj mode normal          # reset everything to the sensible baseline
nhj mode                 # show current mode + list them all
```

| Mode | What it does |
|---|---|
| **normal** | The reset baseline — continuous hold music, characters' own voices, default dials. |
| **rave** | Full-volume party muzak (ducks only under alerts) + a cycling party animation on the displays. |
| **call-centre** | Phone-line-compressed voice **and tinny hold music**, a bored/apathetic government-operator persona, plus a looping room-tone bed. *"Your call is important to us…"* |
| **quiet** / **focus** | No music, displays off — haptic-only. Heads-down work with zero audio/visual noise. |
| **special-forces** | Whispered comms voice (swaps the reference voice sample for *all* characters), simple single-tap haptics, displays dark, and a looping radio bed. Silent and terse. |
| **went-full-bogan** | Maximum **unbleeped** swearing + razor-sharp competence — no patience for stupid questions, but warm and playful. (Mind the screen-share.) |

Modes are **config-driven** — each is a preset in `../config/default.yaml` under `modes:`, so you can retune the bundled ones or add your own (a mode can set `muzak`, `audio_mode`, `ambient`, `scenario`, `display`, `voice`, `haptic`, `censor` and per-character `dials`). The old granular toggles (`nhj muzak rave`, `nhj callcentre on`) are now aliases that apply the matching mode; `nhj set` / `nhj muzak` still fine-tune *within* a mode.

> ### ⚠️ A word on the workplace
> **Swearing is off by default** — out of the box Jan keeps it clean, and even at full tilt the strong words are bleeped to a spoken *"quack"*. But NHJ can absolutely be NSFW if you want it to be: flip on `went-full-bogan` mode (or `nhj censor off`) and you get the **full, unfiltered Aussie bogan experience**. That's brilliant solo; it is **not** what you want narrating your screen-share in an open-plan office. Consider yourself warned. 🤙
>
> **Everything is yours to dial.** Every sound and visual is customisable — pick exactly how much (or how little) you want to be bothered. Silence the four hours of custom muzak with `nhj muzak off`, drop to haptic-only `quiet` mode, or slip into **`special-forces`** mode — a whispered voice and a single discreet buzz — for those midnight inference sessions behind enemy lines.

The audio side of modes (the phone bandpass, room beds, ducking) is detailed in **[Audio Standard & Pipeline](AUDIO-STANDARD.md)**.
