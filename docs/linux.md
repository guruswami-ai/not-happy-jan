# Running NHJ on Linux (and other non-Mac hosts)

NHJ started on Apple Silicon, but nothing in its core is Mac-only. This page covers what is
cross-platform, how to get live voices on a Linux/NVIDIA box, and the traps worth knowing.

## What runs where

| Component | macOS (Apple Silicon) | Linux |
|---|---|---|
| Hooks, queue, worker, MCP ingest, `nhj` CLI | yes | yes |
| Mixer (PyAV + sounddevice), hold music, bundled voice bank | yes | yes (PipeWire/PulseAudio/ALSA) |
| Live voice | Qwen3-TTS on MLX (`TTS_ENGINE=qwen`) | **audio.cpp / Breeze-TTS** zero-shot cloning on CUDA (`TTS_ENGINE=audiocpp`) |
| Dynamic lines (ocker-bogan-nano) | llama.cpp (Homebrew) | llama.cpp native, or the Docker image (CPU or GPU) |
| Persistent services | LaunchAgents | `systemd --user` |
| Displays, Vestaboard, haptic mouse | yes | yes (network devices; haptic needs HID access) |

`TTS_ENGINE=none` runs everything except live synthesis; the bundled clip bank still speaks.

## Quick start

```bash
# 1. Install (hooks, markers, media, the nhj CLI). Point it at an audio.cpp server if you have one:
NHJ_AUDIOCPP_URL=http://127.0.0.1:8092 bash install.sh
# 2. Enrol the NHJ voices into the server's voice directory (see below), then:
nhj test ok -m "hello from linux"
```

Without `NHJ_AUDIOCPP_URL` the installer sets `TTS_ENGINE=none` and the bundled bank speaks.

## Voice: audio.cpp / Breeze-TTS

`TTS_ENGINE=audiocpp` sends each line to an [audio.cpp](https://github.com/0xshug0/audio.cpp)
server (`POST /v1/audio/speech`, model `breeze`) which clones a voice from a reference clip.
Settings (config `audiocpp:` block, env overrides in brackets):

```yaml
audiocpp:
  url: http://127.0.0.1:8092        # [NHJ_AUDIOCPP_URL]
  voices_dir: ~/s2s/voices          # [NHJ_AUDIOCPP_VOICES] where <name>.txt transcripts live
  voices: { jan-whispering: jan_whispering }   # character voice -> enrolled name (default: same name)
```

**Enrol the real NHJ voices** by copying each character's reference clip and transcript (from
`nhj setup-media`) into the server's `--voice-dir`:

```bash
for v in jan bazza karren; do
  cp voices/$v/ref.wav  $VOICE_DIR/$v.wav      # 24 kHz mono
  cp voices/$v/ref.txt  $VOICE_DIR/$v.txt      # the transcript of ref.wav
done
```

The running server discovers new voices without a restart. Notes from measurement:

- Each request sends **that voice's own transcript** as `reference_text`; the server-wide default
  is a single global value and is wrong for every other voice.
- Never send the `instruction` option with a cloned voice: it fights the clone.
- If the GPU runs out of memory the server answers **HTTP 200 with an empty body**. NHJ treats a
  body that is not a real WAV as a failure and falls back to the clip bank, but check
  `docker logs <container> | grep "out of memory"` if a voice goes silent.
- Warm latency is ~1.5-2 s per line (first call per voice ~1 s slower); repeated messages are cached.

## Dynamic lines: ocker-bogan-nano

```bash
nhj install-model --external-server        # download the 986 MB GGUF, write NHJ_DYNAMIC_* to .env
docker compose -f deploy/linux/nhj-llm.compose.yml up -d     # 127.0.0.1:9991
nhj dynamic-test
```

`install.sh` does the download step automatically when there is no native `llama-server`. With a
native llama.cpp, plain `nhj install-model` works as on macOS. On a 32-core CPU the model takes
0.3-0.4 s for a short line and up to ~3 s for a long one; the GPU variant (see the compose file)
is 0-0.3 s for ~1.6 GB of VRAM.

## Choosing the audio output

The mixer opens one PortAudio stream. By default that is the system default, which on a
multi-output PipeWire box may not be where your speakers are.

```bash
nhj audio-devices                              # lists outputs, marks the one in use
echo 'NHJ_OUTPUT_DEVICE=analog-stereo' >> .env # index or name part; or playback.output_device
```

Match a PipeWire sink name (`analog-stereo`, `hdmi-stereo`, `reachy`) rather than a bare word like
`analog`, which also matches the raw ALSA `hw:` device. To prove sound reaches a sink without
listening, record its monitor while a clip plays:

```bash
pw-record -P stream.capture.sink=true --target <sink-name> out.wav
```

## Persistent MCP ingest (systemd)

```bash
nhj install-mcp-service            # nhj-mcp.service, streamable-http on 127.0.0.1:8765
systemctl --user status nhj-mcp.service
loginctl enable-linger $USER       # start at boot without a login session
nhj uninstall                      # removes the unit too
```

Agents on other machines (or with no hooks) post events to `http://HOST:8765/mcp` using the
`nhj_vibe` tool; pass `source` so displays can say which session spoke. Binding beyond loopback
is an explicit `--listen` choice: put it behind a firewall or an authenticated proxy first.

## Vestaboard

Set `claky.url` (a Bifrost/MCP gateway URL) or `claky.host` (the board's Local API) in your user
`config.yaml` (`nhj` prints the path with `python -c "from nhj import resources; print(resources.config_dir())"`)
and export the token named by `claky.token_env`. The newest message wins; a detached sender honours
the board's ~15 s update limit so it never blocks the voice.

## Which session is talking, and who may speak

- Every event carries a **source** (the project folder, or `host:project` for remote nodes). Displays
  show it; `nhj_vibe` and `nhj vibe -s` set it for agents without hooks.
- **Voice is opt-in per project** if you want it to be: `nhj voice on|off|clear|status`
  (`--here`, `--project NAME`, `--global`); `voice.default` in config sets the baseline. Silent
  channels (board, displays, haptics) always fire.
- With several agents running at once, see **[Multi-agent orchestration](orchestration.md)**.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Nothing audible | wrong output device: `nhj audio-devices`; record the sink monitor to prove sound is flowing |
| Mixer errors invisible | the mixer daemon runs with output sent to `/dev/null`; run `python -m nhj.inference_muzak` in a terminal |
| A voice is silent | server out of VRAM (HTTP 200, empty body); check the server log |
| Hold music never stops | an older build defaulted the Stop hook's `muzak` flag off; update, or `nhj muzak stop` |
| `default: off` in YAML ignored | YAML 1.1 reads a bare `on`/`off` as booleans; NHJ accepts both, but quote values you compare yourself |
| `pkill -f nhj.xyz` kills your shell | the pattern matches the shell's own command line; use `pkill -f "[n]hj.xyz"` |
