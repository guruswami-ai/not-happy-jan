"""Optional dynamic line generation — the agent's status, rephrased in-character by an LLM.

When `NHJ_DYNAMIC=1`, NHJ asks a chat model to phrase each notification in the
character's voice (bogan/intensity level, swearing on/off) instead of reading a
static phrase bank. Works with ANY OpenAI-compatible `/chat/completions` endpoint:

  - a local model — ocker-bogan-nano (NHJ's own fine-tune), llama.cpp, LM Studio, Ollama, vLLM
  - a cloud API — OpenAI, Together, Groq, OpenRouter, …
  - a future fine-tuned ocker model — just point NHJ_DYNAMIC_MODEL at it; the persona
    prompt below still applies (and can be trimmed once the personality is baked in).

Config (.env):
  NHJ_DYNAMIC=1
  NHJ_DYNAMIC_BASE_URL=http://localhost:9991/v1     # OpenAI-compatible base
  NHJ_DYNAMIC_MODEL=qwen3.6-35b                      # any model the endpoint serves
  NHJ_DYNAMIC_API_KEY=not-needed                     # dummy is fine for local models
  NHJ_DYNAMIC_TEMP=0.9
  NHJ_DYNAMIC_TIMEOUT=8

Stdlib only (urllib) — no extra deps. Any failure returns None so the caller falls
back to the static phrase bank: dynamic is always safe to enable.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

# Reasoning models leak their chain-of-thought into the content. Strip it so we
# only speak the final line: drop <think>/<analysis>/<reasoning> blocks, anything
# before the last closing tag, and any "Final Answer:" preamble; keep the last line.
_THINK_BLOCK = re.compile(r"(?is)<(think|thinking|analysis|reasoning)>.*?</\1>")
_THINK_CLOSE = re.compile(r"(?is)</(think|thinking|analysis|reasoning)>")
_FINAL = re.compile(r"(?is)\bfinal answer\s*:\s*\**\s*")


def _strip_reasoning(text: str) -> str:
    if not text:
        return text
    had = False
    stripped = _THINK_BLOCK.sub("", text)
    if stripped != text:
        had = True
    closes = list(_THINK_CLOSE.finditer(stripped))
    if closes:
        stripped = stripped[closes[-1].end():]; had = True
    m = _FINAL.search(stripped)
    if m:
        stripped = stripped[m.end():]; had = True
    stripped = stripped.strip()
    if had:  # reasoning was present → the spoken line is the last non-empty line
        lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
        if lines:
            stripped = lines[-1]
    return stripped.strip().strip("*").strip().strip('"').strip("'").strip()

_INTENT_DEFAULT = {
    "ok": "the task finished successfully",
    "err": "something went wrong / a task failed",
    "warn": "there's a warning to look at",
    "attn": "your attention or input is needed",
    "celebrate": "a milestone was reached",
    "step": "a step of the work is done",
}


def enabled() -> bool:
    return os.getenv("NHJ_DYNAMIC", "").strip().lower() in ("1", "true", "yes", "on")


def _scenario() -> str:
    """Optional scenario override (e.g. Government Call Centre Mode) — colours the
    persona's tone on top of the character. Set via `nhj callcentre` / state setting."""
    try:
        from nhj.state import get_setting
        return (get_setting("audio_scenario", "") or "").strip()
    except Exception:
        return ""


def _persona(character, level: int, swearing: bool, band_instruct: str, chaos: int = 0) -> str:
    dial = character.dial or "intensity"
    competence = max(1, 10 - int(chaos))
    bits = [
        f"You are {character.name}. {character.personality}".strip(),
        f"Your {dial} level is {level} out of 10 — let it fully shape how you talk.",
        f"Competence: {competence}/10 — at low competence you muddle the message, "
        f"mangle jargon and trail off; at high competence you relay it cleanly.",
    ]
    if band_instruct:
        bits.append(band_instruct)
    sc = _scenario()
    if sc:
        bits.append(f"SCENARIO — this overrides your usual setting and fully colours your tone: {sc}")
    bits.append(
        "You are at full noise — maximum ockerism. Swear freely and naturally like a "
        "foul-mouthed Aussie tradie: strong profanity (fuck, shit, bugger, bloody, bastard, "
        "piss, crap) woven through the line where it fits, not forced. This is uncensored — "
        "don't hold back or soften it."
        if swearing else
        "Do not swear."
    )
    bits.append(
        "Summarise the status below in your own voice as a SHORT spoken line — usually just a "
        "few words or a quick phrase (think 2-8 words), like a mate's quick heads-up, NOT full "
        "sentences. Only run longer when the character really demands it. Australian English. "
        "Plain text only — no markdown, quotes, emoji or stage directions."
    )
    return " ".join(b for b in bits if b)


def generate(character, intent: str, message: str, level: int,
             swearing: bool = False, band_instruct: str = "", chaos: int = 0) -> str | None:
    base = os.getenv("NHJ_DYNAMIC_BASE_URL", "http://localhost:9991/v1").rstrip("/")
    model = os.getenv("NHJ_DYNAMIC_MODEL", "local")
    key = os.getenv("NHJ_DYNAMIC_API_KEY", "not-needed")
    try:                                    # on-demand: warm NHJ's own LLM (skip BYO endpoints)
        from nhj import servers
        if servers.is_managed_llm(base):
            servers.ensure("llm")
    except Exception:
        pass
    try:
        temp = float(os.getenv("NHJ_DYNAMIC_TEMP", "0.85"))
        top_p = float(os.getenv("NHJ_DYNAMIC_TOP_P", "0.95"))
        timeout = float(os.getenv("NHJ_DYNAMIC_TIMEOUT", "8"))
    except ValueError:
        temp, top_p, timeout = 0.85, 0.95, 8.0

    status = message.strip() if message else _INTENT_DEFAULT.get(intent, "an update")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _persona(character, level, swearing, band_instruct, chaos)},
            {"role": "user", "content": f"Status update: {status}"},
        ],
        "temperature": temp,
        "top_p": top_p,          # curbs the repetition loops small quantised models can fall into
        "max_tokens": 60,
    }).encode()

    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        text = data["choices"][0]["message"]["content"] or ""
        return _strip_reasoning(text) or None
    except Exception:
        return None


def _display_persona(character, level: int, swearing: bool) -> str:
    swear = (
        "Swear freely like a foul-mouthed Aussie tradie." if swearing
        else "Do not swear."
    )
    sc = _scenario()
    scenario = f" SCENARIO (overrides your usual tone): {sc}" if sc else ""
    return (
        f"You are {character.name}. {character.personality} ".strip() + " "
        "Write text for a TINY 8x32-pixel LED matrix sign. "
        f"Your intensity is {level}/10 — broad ocker Australian slang. {swear}{scenario} "
        "Output ONLY a punchy 2-3 word status headline, glanceable at arm's length — "
        "NOT a sentence. No punctuation except ! or ... No quotes, markdown, emoji or "
        "stage directions."
    )


def generate_display(character, intent: str, message: str = "",
                     level: int | None = None, swearing: bool | None = None) -> str | None:
    """Like generate(), but for a tiny LED sign: a 2-3 word in-character headline.
    Returns None on any failure so the caller falls back to the marker message /
    a fixed phrase. Uses a shorter timeout than the spoken-line generator."""
    base = os.getenv("NHJ_DYNAMIC_BASE_URL", "http://localhost:9991/v1").rstrip("/")
    model = os.getenv("NHJ_DYNAMIC_MODEL", "local")
    key = os.getenv("NHJ_DYNAMIC_API_KEY", "not-needed")
    try:                                    # on-demand: warm NHJ's own LLM (skip BYO endpoints)
        from nhj import servers
        if servers.is_managed_llm(base):
            servers.ensure("llm")
    except Exception:
        pass
    try:
        temp = float(os.getenv("NHJ_DYNAMIC_TEMP", "0.85"))
        timeout = min(float(os.getenv("NHJ_DYNAMIC_TIMEOUT", "8")), 4.0)
    except ValueError:
        temp, timeout = 0.85, 4.0

    lvl = getattr(character, "level", 4) if level is None else level
    if swearing is None:
        swearing = lvl >= 11
    status = message.strip() if message else _INTENT_DEFAULT.get(intent, "an update")
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _display_persona(character, lvl, swearing)},
            {"role": "user", "content": f"Status: {status}"},
        ],
        "temperature": temp,
        "top_p": 0.9,
        "max_tokens": 16,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        line = _strip_reasoning(data["choices"][0]["message"]["content"] or "")
        if not line:
            return None
        # keep it glanceable: first ~5 words, then trim trailing punctuation and
        # any dangling little word the cut left hanging.
        _DANGLE = {"to", "ya", "the", "a", "and", "is", "are", "my", "your",
                   "of", "for", "at", "on", "with", "mate"}
        words = line.split()[:5]
        while words and words[-1].lower().strip(",.!?:;") in _DANGLE:
            words.pop()
        line = " ".join(words).rstrip(" ,.;:-")
        return line[:28].strip() or None
    except Exception:
        return None
