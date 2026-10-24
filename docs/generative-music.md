# Generative hold music (experimental, opt-in)

The hold-music bed can follow what your agents are doing instead of shuffling recorded tracks. It is **off by default**; recorded tracks stay the default and the fallback.

```yaml
# ~/.config/not-happy-jan/config.yaml   (or NHJ_MUSIC_MODE=generative)
music:
  mode: generative
```

`nhj music status` shows what the controller will use; `nhj music demo -o demo.wav` renders a scripted timeline offline so you can hear it without a running session.

## How it works

1. **A stem pack** (`manifest.json` plus equal-length 48 kHz WAV loops: kick, hats, clap, bass, pad, arp) at a fixed tempo. The default pack is rendered by NHJ's own numpy synth (138 BPM, A minor, 16 bars, a few seconds of CPU, built automatically on first use or with `nhj music build-pack`). Point `music.pack` at your own pack directory to use other material.
2. **The director** (`nhj.music.director`, deterministic, no model in the real-time path) turns NHJ state into a plan. It is asked once per bar, so the arrangement only changes on the beat grid, with a half-beat fade.

   | State | Music |
   |---|---|
   | nobody busy | silence (the controller pauses the bed, as for recorded music) |
   | 1 session busy | kick + bass |
   | 2 | + hats, clap |
   | 3 | + arp |
   | 4 or more (or `nhj mode rave`) | + pad: the full arrangement |
   | a long turn (`long_after_s`, default 30 s) | a rising high-pass sweep on everything but the kick |
   | `err` accent | a 4-bar breakdown (pad and arp only) |
   | `ok` accent | one bar of release (kick, bass, pad) |

3. **The stem source** (`nhj.music.source`) renders the plan into the mixer's music buffer. The mixer's ducking under speech, master volume, pause/resume and output-device handling are unchanged.

Accents are requested with `nhj music accent err|ok` (they ride the muzak state file). **Not yet wired:** the Stop hook does not know the verdict when it marks a session idle, so real `err`/`ok` events do not trigger accents on their own; `warn` and `attn` are accepted and ignored.

## Continuation (optional, needs a GPU model)

Instead of looping the same 16 bars, the composer can ask an audio model to carry the current groove on. When the plan has been stable for `stable_phrases` phrases, it renders the current stem mix for `keep_bars`, has the model continue it to `total_bars`, level-matches the new bars to the stems and splices them in at the next phrase line (a 20 ms cross-fade). One job runs at a time; each result is cached with a provenance file (model, licence, prompt, seed); failures back off for five minutes and never affect the bed.

It is off until you configure a command. The command is an argv list (no shell); `{workdir}`, `{prompt}`, `{seed}`, `{duration}` and `{mask_start}` are substituted per argument, and the command must leave `{workdir}/out.wav` (the full continued clip) next to the `in.wav` it was given. The tested setup is Stable Audio 3 Medium (f16) through audio.cpp inpainting:

```yaml
music:
  mode: generative
  continuation:
    enabled: true
    model_id: stable-audio-3-medium-f16 (audio.cpp)
    licence: Stability AI Community License
    min_free_vram_mib: 8000          # skip a job when the GPU is busy (see below)
    command: [docker, run, --rm, --gpus, all,
              -v, "/path/to/models:/app/models:ro", -v, "{workdir}:/work",
              --entrypoint, /app/audiocpp_cli, "ghcr.io/0xshug0/audio.cpp:full-cuda13",
              --task, gen, --family, stable_audio,
              --model, /app/models/Stable-Audio-3-Medium-GGUF/stable-audio-3-medium-f16.gguf,
              --backend, cuda, --text, "{prompt}", --seed, "{seed}", --audio, /work/in.wav,
              --duration-seconds, "{duration}", --request-option, audio_input_kind=inpaint_audio,
              --request-option, "inpaint_mask_start_sec={mask_start}",
              --request-option, "inpaint_mask_end_sec={duration}", --out, /work/out.wav]
```

Measured on an RTX 3090: about 7 s and 4-6 GB of extra VRAM per 8-bar continuation.

### Things to know

- **GPU sharing.** If the same GPU serves your TTS, a continuation job that runs it out of memory can make the speech server return HTTP 200 with an empty body (silent alerts). Set `min_free_vram_mib` to at least the job's footprint plus headroom, or run the model on another card.
- **Tempo drift.** Continued audio drifts about 1-2% from the pack's tempo over eight bars, so the join back to the stems can sit slightly off the grid. A tempo-corrected stretch is a follow-up.
- **Licences.** Stable Audio 3 weights are under the Stability AI Community License (check its revenue terms). MusicGen weights are CC-BY-NC and must not be used for anything NHJ ships. Generated audio may have no copyright protection in some jurisdictions; the provenance file exists so you can show what produced it. NHJ never bundles generated audio.
- **Not tested here:** playback on a real output device during a live agent session. The offline demo and the unit tests exercise the renderer, director, composer and splice; the controller wiring is a small, guarded addition to `run_duck_controller`.
