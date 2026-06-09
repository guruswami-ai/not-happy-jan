# Dynamic voices — the `ocker-bogan-nano` brain

*← back to the [Not-Happy-Jan README](../README.md)*

By default each character speaks from a **static phrase bank** — fast, offline, deterministic. Flip on **dynamic mode** and a local LLM rephrases every notification *in character* instead — fresh slang every time, scaled by each character's bogan/intensity and competence dials. *"Build passed"* becomes *"yeah nice one, all green"* one time and *"beauty, she's a ripper"* the next.

NHJ ships with its own purpose-built model for this: **ocker-bogan-nano**.

## Why install it — the full ocker-bogan experience (everything local)

- **Never repeats** — every notification is freshly generated and scaled live to each character's bogan/competence dials, not pulled from a finite bank.
- **Nails the extremes** — the personas, the dials and the swearing are *fine-tuned in*, so the top of the dial is genuinely unhinged (Jan swears like a tradie at ockerism 11) where a static bank — or a safety-tuned general model — flattens out to a polite "oh *bother*".
- **100% local & private** — no API key, no network, no per-notification cost; your build status never leaves the machine.
- **Tiny & fast** — ~940 MB, sub-second on Apple Silicon, sits resident beside the TTS voice.

## What ocker-bogan-nano is

A **~1.5B [Qwen2.5-abliterated](https://huggingface.co/huihui-ai/Qwen2.5-1.5B-Instruct-abliterated) fine-tune**, distilled from a much larger abliterated teacher on thousands of in-character examples — every persona (Jan, Bazza, Karren) across the full range of dials, steeped in an Australian-slang glossary and a broad base of ocker source material. Quantised to **Q4_K_M GGUF it's ~940 MB** and generates a line in **well under a second** on Apple Silicon — small and cheap enough to keep resident alongside the TTS voice. It trains on NHJ's *exact* inference prompt, so the personality is baked in rather than prompted.

It's a **separate model from the TTS voice** — TTS turns text into Jan's *sound*; ocker-bogan-nano decides *what she says*.

**How it was trained (and why it's uncensored):** broad Australian is inseparable from swearing — at the top of the dial it *is* the grammar — so a safety-tuned model just can't produce it (ask for full ocker and you get a polite "oh *bother*"). ocker-bogan-nano is therefore distilled from an **abliterated** (refusal-removed) teacher, grounded in authentic source material — Australian Reddit threads and the gloriously unfiltered register of one-star "drunken complaints to Macca's" reviews. The uncensored register lives in the weights; NHJ then **bleeps the strong words at runtime** so the default experience is still safe-to-share. The full pipeline, the abliteration rationale and the data provenance are documented on the **[ocker-bogan-nano model card](https://huggingface.co/guruswami-ai/ocker-bogan-nano)**.

## Install it

```bash
nhj install-model
```

That downloads ocker-bogan-nano from Hugging Face, points `.env` at a local OpenAI-compatible server, and installs a LaunchAgent so the endpoint (`http://127.0.0.1:9991/v1`) is always running. Then check it:

```bash
nhj dynamic-test          # generates one in-character line and shows the latency
```

Runs cross-platform via llama.cpp — Linux/Windows/Mac, CPU or GPU.

<details><summary>Manual / advanced</summary>

```bash
# download the Q4_K_M GGUF (~940 MB) yourself …
hf download guruswami-ai/ocker-bogan-nano ocker-bogan-nano-Q4_K_M.gguf \
  --local-dir "$HOME/Library/Caches/not-happy-jan/ocker-bogan-nano"
# … serve it with llama.cpp (or use the LaunchAgent in ../integrations/launchd/) …
llama-server -m "$HOME/Library/Caches/not-happy-jan/ocker-bogan-nano/ocker-bogan-nano-Q4_K_M.gguf" \
  --alias ocker-bogan-nano --host 127.0.0.1 --port 9991 -c 2048
```
```bash
# … and wire .env to it
NHJ_DYNAMIC=1
NHJ_DYNAMIC_BASE_URL=http://127.0.0.1:9991/v1
NHJ_DYNAMIC_MODEL=ocker-bogan-nano
```
Want to retrain or tune the personalities? The distillation pipeline is described on the [ocker-bogan-nano model card](https://huggingface.co/guruswami-ai/ocker-bogan-nano).
</details>

## Bring your own model instead

ocker-bogan-nano is just the default. Dynamic mode speaks plain OpenAI `/chat/completions`, so you can point it at anything — a bigger local model (Ollama, LM Studio, vLLM, lightning-mlx) or a cloud API (OpenAI, Groq, OpenRouter):

```bash
# .env — e.g. Ollama
NHJ_DYNAMIC=1
NHJ_DYNAMIC_BASE_URL=http://localhost:11434/v1
NHJ_DYNAMIC_MODEL=qwen2.5:7b-instruct
```

The character's persona, dial and swearing setting become the system prompt, so even a small general instruct model does a decent job — ocker-bogan-nano just does it better and cheaper because the personas are fine-tuned in. If the endpoint is ever unreachable, NHJ **silently falls back to the static banks**, so dynamic is always safe to enable.

## Swearing & the bleep

At **ockerism 11** Jan swears like a true bogan. The strong words are **bleeped by default** — replaced with a spoken *"quack"* — so it's safe to share, and funnier (*"get absolutely quacked, ya legend"*). The same filter cleans up anything a dynamic model generates.

```bash
nhj set jan.ockerism 11   # or: nhj swearing on  (or just ask Claude)
nhj censor off            # raw, uncensored swears
nhj censor beep           # quack (default) | beep | honk | off
```
