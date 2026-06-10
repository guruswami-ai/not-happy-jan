# What is Not-Happy-Jan?

**Not-Happy-Jan (NHJ) is multi-sensory feedback for AI coding agents.** When your agent
finishes a task, someone picks up the phone and tells you — cheerfully if it went well,
*furiously* if it didn't. It turns the quiet of a long agent run into an Australian
call-centre for your build, and it runs **100% on your own machine**.

## The problem it solves

When you hand a multi-minute task to an autonomous coding agent, you lose the ambient cues
a desktop gives you for free: *is it still working? did it finish? did it get stuck? did it
just do something I should look at?* The terminal goes quiet, you context-switch, and you
come back to a wall of text and have to reconstruct what happened.

That's a **situational-awareness** gap. The familiar fix is **ambient, peripheral feedback** —
information you absorb without looking: a spinner, a "ding", a status light. NHJ takes that
idea and borrows a metaphor everyone already understands: **the phone call on hold.**

## What you actually experience

- The agent starts working → you go **on hold**: music plays.
- The agent finishes → the music stops and a **person picks up** and tells you how it went.

The presence or absence of music is a **zero-attention "still working / done" signal**, and
**who answers — and their tone — tells you the severity** before you read a single word:

| Who answers | When | The vibe |
|---|---|---|
| 👩 **Jan** — reception | routine work, "all done" | cheerful, occasionally scatty |
| 🧑‍💼 **Bazza** — middle management | a warning needs a look | apathetic → anxious, your call |
| 📣 **Karren** — the manager | it broke / something urgent | *NOT. HAPPY. JAN.* |

It fans the same event out across **every channel you have** — the cloned voice on your
speakers, a buzz on a haptic mouse, a tile on a [pixel display](integration.md), a LaMetric,
even a physical bell — or nothing but your speakers if that's all you've got.

→ Meet them properly: **[Characters & dials](characters.md)** · see it move: **[Watch the demo](watch-the-demo.md)**

## You shape it by just asking

Every character has live dials — **boganism**, **competence**, **chaos** — that you change in
plain language ("make Jan more bogan", "crank Karren to eleven"), no restart. One-word
**[modes](modes.md)** reconfigure the whole vibe at once — voice, displays, music and haptics
together: `rave`, `call-centre`, `quiet`, `special-forces`, `went-full-bogan`.

## What's under the comedy

The bogans are the delivery vehicle; the engine is a real, on-device pipeline:

- a **custom fine-tuned LLM** (`ocker-bogan-nano`) writes a fresh in-character line every time,
- **zero-shot voice cloning** (Qwen3-TTS) speaks it in each character's voice,
- lifecycle **hooks → a durable queue → a worker** turn the agent's status into events,
- a single **audio mixer** ducks the hold music under the voice, and
- a **multi-channel dispatch layer** fans out to whatever hardware is present, degrading
  gracefully when a device (or a model) is absent.

Everything inference runs **locally** — the cloned-voice references never leave your machine.

→ The full design and the tradeoffs behind it: **[Architecture](ARCHITECTURE.md)**

## How to run it

- **Minimal** speaks instantly from a bundled voice bank — no model downloads. Good for CI and
  RAM-tight machines.
- **Default / full** is the live experience — cloned voices, the dynamic LLM, and ~4 hours of
  hold music — on an **Apple-Silicon Mac**, a one-off ~5 GB of local models, loaded on demand.

Install and uninstall run **entirely in your user account** (no `sudo`) and are fully reversible.

→ **[Requirements & tiers](minimum-specs.md)** · **[Install profiles](install-profiles.md)** · **[Quick start](quickstart.md)**

## The honest version

NHJ is **not** a pragmatic everyday tool for most people, and it doesn't pretend to be. The
full experience wants a particular Mac, several gigabytes of models, and ideally some hardware;
the comedy is subjective; and it's a large engineering surface for a feedback feature you could
approximate with a terminal bell.

That lopsidedness is rather the point. NHJ is where a set of otherwise-separate applied-AI
disciplines — custom LLM distillation, fine-tuned/zero-shot TTS, low-latency local inference,
a concurrency-safe queue, real-time audio sequencing, graceful multi-channel degradation — got
taken end-to-end, on one machine, under real constraints, far enough to surface the actual
tradeoffs. It's a small enough system to hold in your head, and real enough to be honest.
