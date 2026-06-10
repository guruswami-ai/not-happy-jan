# Quick start

*Already installed? Here's how to fire your first notification and tune the experience.
New to NHJ? Start with [Requirements & tiers](minimum-specs.md) and
[Install profiles](install-profiles.md).*

## Your first notification

```bash
nhj test ok                              # fire a "done" across every configured channel
nhj test err --message "something broke" # an error — routes to Karren
```

If you hear a voice (or feel a buzz / see a tile), you're wired up. `nhj test` only
confirms the event was *queued* — if nothing renders, the channel is the issue, not the
queue; see [Configuration](configuration.md) and `nhj devices`.

## See what's configured

```bash
nhj voices     # the cast — bundled bank availability + live-TTS references per voice
nhj devices    # the notification channels NHJ found and can reach
nhj status     # current mode, dials, mute/muzak, censor
```

## Tune it live — no restart

Every setting takes effect on the next vibe. You can run these directly, **or just ask
Claude in plain language** ("make Jan more bogan", "switch to call-centre mode").

```bash
nhj set jan.ockerism 11      # any character dial — jan.competence, bazza.stress, karren.karren…
nhj mode rave                # one-word vibe switch — rave | call-centre | quiet | special-forces | went-full-bogan
nhj muzak on                 # hold music while the agent works
nhj mute                     # silence everything;  nhj unmute to restore
```

→ Full dial reference: **[Characters & dials](characters.md)** · the mode macros: **[Modes](modes.md)**

## Faster playback (optional)

```bash
nhj build-bank --voice jan   # pre-render a voice's bank so lines play instantly
```

## Uninstall

One command stops the TTS/LLM/MCP services and removes everything NHJ installed
(runtime, config, downloaded models/media, the `nhj` launcher, and NHJ's Claude
hooks/MCP/markers/skill). Your Claude install is left intact. No `sudo`.

```bash
nhj uninstall                # add --yes to skip the confirmation
```
