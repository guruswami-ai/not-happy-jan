# Running NHJ on Linux + NVIDIA (no Apple Silicon)

Everything below is optional and additive; the macOS path is unchanged.

## Voice: audio.cpp / Breeze-TTS (zero-shot cloning on CUDA)

```bash
export TTS_ENGINE=audiocpp            # in the NHJ .env
```

`config/default.yaml` → `audiocpp:` sets the server URL, the directory holding each voice's
`<name>.wav` + `<name>.txt` transcript, and the character → voice map
(characters use their own name; `jan-whispering → jan_whispering`). Enrol the real NHJ voices by
copying `voices/<name>/ref.wav` and `ref.txt` (from `nhj setup-media`) into the server's voice dir as
`<name>.wav` / `<name>.txt`; no server restart is needed.
Each request sends that voice's own transcript as `reference_text`. An out-of-VRAM request
returns HTTP 200 with an empty body; NHJ treats that as a failure and falls back to the
bundled clip bank.

## Vestaboard: the `claky` adapter

Set `claky.url` (a Bifrost MCP URL) or `claky.host` (Local API) and export the token named
by `claky.token_env`. Newest message wins; a detached sender honours the board's ~15 s limit.

## Which session is talking: `source`

Hooks label each event with the project (`host:project` for remote nodes). Displays show it;
the `nhj_vibe` MCP tool accepts a `source` argument for agents without hooks.

## Voice opt-in (no cacophony)

Silent channels (board, displays, haptics) fire for every session. The voice channel
(TTS + hold music) is gated per project:

```bash
nhj voice status
nhj voice on            # this project   (also: --project NAME, --global)
nhj voice off
nhj voice clear
```

Precedence: project rule > global rule > `voice.default` in config. `nhj mute` silences
everything and is separate. On a busy multi-session host set `voice: {default: off}` in
`config.yaml` and opt individual projects in.

## Persistent MCP ingest (systemd --user)

```bash
nhj install-mcp-service            # nhj-mcp.service, streamable-http on 127.0.0.1:8765
systemctl --user status nhj-mcp.service
loginctl enable-linger $USER       # start at boot without a login session
nhj uninstall                      # removes the unit too
```

Agents on other machines (or without hooks) post events to `http://HOST:8765/mcp`
(`nhj_vibe`). Binding beyond loopback is an explicit `--listen` choice: put it behind a
firewall or an authenticated proxy first.

## Dynamic lines: ocker-bogan-nano (llama.cpp in Docker)

```bash
nhj install-model --external-server      # downloads the 986 MB GGUF, writes NHJ_DYNAMIC_* to .env
docker compose -f deploy/linux/nhj-llm.compose.yml up -d     # 127.0.0.1:9991, CPU
nhj dynamic-test
```

Measured on a Threadripper 2990WX: warm 0.3-0.4 s for a short line, up to ~3 s for a long one
(first call after start ~2 s). The GPU variant is 0-0.3 s for about 1.6 GB of VRAM (see the comments
in the compose file). The CPU default keeps the 3090 free for Breeze and the S2S stack.
