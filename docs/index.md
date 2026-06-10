# Not-Happy-Jan

<p align="center">
  <img src="assets/nhj-icon.png" alt="The three-tier Not-Happy-Jan cast: Karren the manager raging up top, Bazza in stressed middle-management, Jan on reception with a headset, ringed by feedback devices" width="280">
</p>

**Multi-sensory feedback for AI coding agents.** When your coding agent finishes a
task, someone picks up the phone and tells you — cheerfully if it went well,
*furiously* if it didn't. It's an Australian call-centre for your build: hold music
while the agent works, a cast of cloned voices when it's done, and a pulse on your
mouse or a flash on a pixel display to match — all running **100% on your own machine**.

> *"Not happy, Jan — the issue is…"*

!!! tip "New here? Three things to know"
    - **It speaks instantly on a minimal install** — the bundled voice bank needs no downloads.
    - **The full live experience** (cloned voices + a fresh in-character line every time + hold music) wants an **Apple-Silicon Mac** and a one-off ~5 GB of local models.
    - Everything inference runs **on-device**; nothing about your code or prompts leaves the machine.

## What you'll experience

- 🎷 **Hold music** plays while the agent is busy, and pauses the instant it's done — a zero-attention "still working / finished" signal.
- 👩 **Jan** (reception) handles the routine "all done". 🧑‍💼 **Bazza** (middle management) escalates warnings. 📣 **Karren** (the manager) takes over when it all breaks — she is *NOT. HAPPY. JAN.*
- 🎚️ **Tune each character** — boganism, competence, chaos — just by asking Claude in plain language.
- 📟 **Fan out across devices** you already have: a haptic mouse, $60 pixel clocks, a LaMetric, even a physical bell — or nothing but your speakers.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/guruswami-ai/not-happy-jan/main/install.sh | bash
```

One command, no `sudo`. The default profile installs the **full local experience** with
on-demand model loading (no resident RAM at idle). The MCP server binds to `127.0.0.1`;
LAN access is an explicit opt-in.

→ **[Requirements & tiers](minimum-specs.md)** · **[Install profiles](install-profiles.md)** · **[Quick start](quickstart.md)**

## Find your path

<div class="grid cards" markdown>

- **:material-play: Just trying it**
  Start with [Requirements & tiers](minimum-specs.md), then [Quick start](quickstart.md).

- **:material-tune: Living in it**
  Meet the [characters & dials](characters.md), switch [modes](modes.md), tune the [audio](AUDIO-STANDARD.md).

- **:material-connection: Wiring it up**
  Connect [agents, hooks & devices](integration.md) — Claude Code, other MCP agents, pixel displays, haptics.

- **:material-cog: Customizing**
  The [dynamic LLM voices](dynamic-voices.md) and full [configuration](configuration.md).

- **:material-source-branch: Building on it**
  The [architecture](ARCHITECTURE.md), the [developer guide](DEVELOPER-GUIDE.md), and [contributing](https://github.com/guruswami-ai/not-happy-jan/blob/main/CONTRIBUTING.md).

</div>

## Trust

Inference runs **locally** — the cloned-voice references never leave your machine.
Local services bind to `127.0.0.1` by default; reaching them over the LAN is a
deliberate opt-in. Install and uninstall run **entirely in your user account** (no
`sudo`) and are fully reversible.

Code is [MIT](https://github.com/guruswami-ai/not-happy-jan/blob/main/LICENSE).
Media and voice assets are licensed **separately** with mixed provenance — see
[media provenance & licenses](media-provenance.md).
