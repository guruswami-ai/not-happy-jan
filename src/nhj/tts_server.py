#!/usr/bin/env python3
"""Not-Happy-Jan local TTS server — a *warm* Qwen3-TTS daemon.

Loads a Qwen3-TTS model ONCE and keeps it resident, then serves:

    POST /tts/generate  {text, voice, model_tier}  ->  {status, audio_b64, sample_rate}
    GET  /health        ->  {status, tier, sample_rate, voices_with_ref}

The response shape matches ``nhj.adapters.audio.synth_qwen`` (the client).

Why a daemon: the first synthesis after load is slow (MLX graph compile); a warm
model runs faster-than-realtime. Loading per-call is the pathological slow path,
so this process stays up (see ``nhj install-service`` / the LaunchAgent).

Optimal path: ``model.generate(text, ref_audio, ref_text)`` using the voice's
reference clip + transcript from ``voices/<voice>/{ref.wav,ref.txt}``. Supplying
``ref_text`` avoids loading a whisper STT model (~1.5 GB) to transcribe the clip.

Apple Silicon only (MLX) — NHJ v1 is macOS. Elsewhere, run with ``TTS_ENGINE=none``.

Run:  python -m nhj.tts_server --port 9992 --load fast
  or: nhj start-server
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Optional

from nhj import resources

_VOICES = resources.voices_dir()        # downloaded ref.wav / ref.txt for voice cloning

# Model tier -> mlx-community model id. One model is loaded per daemon (the tier
# given at start-up); per-request model_tier is advisory (we serve the loaded one
# to keep exactly one warm model resident). Override with NHJ_TTS_MODEL.
TIER_MODELS = {
    "fast":   "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit",
    "best":   "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit",
    "better": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-4bit",
}

_lock = threading.Lock()      # MLX generate is serialized — one utterance at a time
# Single dedicated worker: MLX's Metal GPU stream is thread-local, so the model is
# loaded AND run on this one thread. Awaiting work on it (run_in_executor) keeps the
# async event loop free, so /health stays responsive during synthesis.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nhj-mlx")
_model = None
_sample_rate = 24000
_loaded_tier = "fast"
_loaded_model_id = ""


def _configure_hf_cache() -> None:
    """Keep Qwen3-TTS downloads inside NHJ's cache by default.

    mlx-audio resolves Hugging Face repo IDs through huggingface_hub. Those cache
    variables are read when huggingface_hub imports, so set the default before we
    import the mlx-audio loader. Respect explicit user/global Hugging Face cache
    settings and local-path NHJ_TTS_MODEL overrides.
    """
    if (
        os.getenv("HF_HOME")
        or os.getenv("HF_HUB_CACHE")
        or os.getenv("HUGGINGFACE_HUB_CACHE")
    ):
        return
    os.environ["HF_HOME"] = str(resources.tts_model_cache_dir())


def _migrate_legacy_models() -> int:
    """One-time: move Qwen3-TTS model dirs from the default HF hub cache into NHJ's
    managed cache, so existing installs don't re-download ~2 GB. Only runs when NHJ
    owns the cache (``HF_HOME`` == the managed dir, i.e. no user/global HF override).
    Best-effort and idempotent — a fast no-op once a model is in the managed cache."""
    managed_root = resources.tts_model_cache_dir()
    if os.getenv("HF_HOME") != str(managed_root):
        return 0                                   # user/global HF cache — leave it alone
    managed_hub = managed_root / "hub"
    if managed_hub.is_dir() and any(managed_hub.glob("models--*Qwen3-TTS*")):
        return 0                                   # already migrated/downloaded
    legacy_hub = Path.home() / ".cache" / "huggingface" / "hub"
    if not legacy_hub.is_dir():
        return 0
    import shutil
    moved = 0
    for src in legacy_hub.glob("models--*Qwen3-TTS*"):
        dst = managed_hub / src.name
        if dst.exists():
            continue
        try:
            managed_hub.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved += 1
        except OSError:
            pass
    return moved


# Module-level so FastAPI recognises it as the request BODY (a closure-local
# BaseModel gets mis-read as a query param). pydantic is always available.
from pydantic import BaseModel


class GenReq(BaseModel):
    text: str
    voice: str = "jan"
    model_tier: str = "fast"
    instruct: str = ""
    speed: float = 1.0
    temperature: Optional[float] = None


def _log(msg: str) -> None:
    print(f"[nhj-tts] {msg}", file=sys.stderr, flush=True)


def _voice_roots():
    """Voice-library roots, searched in order. NHJ_VOICES_DIR (colon-separated)
    takes precedence; the bundled voices/ is always appended last. Lets one NHJ
    server serve its own cast AND an external set (e.g. /opt/tts/characters)."""
    roots = []
    env = os.getenv("NHJ_VOICES_DIR", "")
    for p in env.split(":"):
        if p.strip():
            roots.append(Path(p.strip()))
    roots.append(_VOICES)
    return roots


def _resolve_voice(voice: str):
    """Return (ref_audio_path, ref_text) for a voice, or (None, '') if no ref clip.

    Supports both layouts: <root>/<voice>/ref.wav  and  <root>/<voice>/reference/ref.wav
    (the latter is the /opt/tts characters layout)."""
    for root in _voice_roots():
        for ref_wav in (root / voice / "ref.wav", root / voice / "reference" / "ref.wav"):
            if ref_wav.is_file():
                ref_txt = ref_wav.parent / "ref.txt"
                text = ref_txt.read_text().strip() if ref_txt.is_file() else ""
                return str(ref_wav), text
    return None, ""


def _to_wav_bytes(audio, sr: int) -> bytes:
    import numpy as np
    import soundfile as sf
    a = np.asarray(audio, dtype=np.float32).reshape(-1)
    buf = io.BytesIO()
    sf.write(buf, a, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _synth(text: str, voice: str, instruct: str = "", speed: float = 1.0,
           temperature=None):
    """Synthesize one utterance. Returns (wav_bytes, None) or (None, reason).

    instruct/speed/temperature shape delivery per the character's intensity band.
    """
    import numpy as np

    ref_audio, ref_text = _resolve_voice(voice)
    if ref_audio is None:
        return None, (f"no reference clip for voice {voice!r} — add "
                      f"voices/{voice}/ref.wav and ref.txt")

    kw = {}
    if instruct:
        kw["instruct"] = instruct
    if speed and speed != 1.0:
        kw["speed"] = float(speed)
    if temperature is not None:
        kw["temperature"] = float(temperature)

    with _lock:
        out = _model.generate(text=text, ref_audio=ref_audio, ref_text=ref_text,
                              verbose=False, **kw)
        segs = []
        try:  # generate() yields segments with an .audio array
            for seg in out:
                segs.append(np.asarray(getattr(seg, "audio", seg),
                                       dtype=np.float32).reshape(-1))
        except TypeError:  # or returns a single result
            _log(f"generate() returned a non-iterable ({type(out).__name__}); "
                 f"treating as one result (check mlx-audio version if audio is wrong)")
            segs = [np.asarray(getattr(out, "audio", out), dtype=np.float32).reshape(-1)]

    if not segs:
        return None, "model returned no audio"
    return _to_wav_bytes(np.concatenate(segs), _sample_rate), None


def _load(tier: str) -> None:
    # Load on the executor thread so the model + its thread-local Metal GPU stream
    # live where _synth will run them (see _executor).
    _executor.submit(_load_impl, tier).result()


def _load_impl(tier: str) -> None:
    global _model, _sample_rate, _loaded_tier, _loaded_model_id
    model_id = os.getenv("NHJ_TTS_MODEL") or TIER_MODELS.get(tier, TIER_MODELS["fast"])
    _configure_hf_cache()
    _migrate_legacy_models()                       # relocate an existing model; no-op once managed
    from mlx_audio.tts.utils import load_model
    _log(f"loading {model_id} (tier={tier}) ...")
    _model = load_model(model_id)
    _sample_rate = int(getattr(_model, "sample_rate", 24000) or 24000)
    _loaded_tier = tier
    _loaded_model_id = model_id
    _log(f"ready — sample_rate={_sample_rate}")


def build_app():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="nhj-tts", version="1.0")

    @app.get("/health")
    def health():
        found = set()
        for root in _voice_roots():
            for pat in ("*/ref.wav", "*/reference/ref.wav"):
                for p in root.glob(pat):
                    found.add(p.parent.name if p.parent.name != "reference"
                              else p.parent.parent.name)
        voices = sorted(found)
        return {
            "status": "ok",
            "tier": _loaded_tier,
            "model": _loaded_model_id,
            "sample_rate": _sample_rate,
            "voices_with_ref": voices,
        }

    @app.post("/tts/generate")
    async def generate(req: GenReq):
        # Synthesis runs on the dedicated single worker thread (_executor) — the same
        # thread the model was loaded on, so MLX's thread-local Metal GPU stream is
        # valid. Awaiting it keeps the event loop free, so /health stays responsive
        # while a (5-15s) generation runs. The 1-wide executor serialises requests.
        if not req.text.strip():
            return JSONResponse({"status": "error", "reason": "empty text"})
        if req.model_tier and req.model_tier != _loaded_tier:
            _log(f"note: request tier {req.model_tier!r} != loaded {_loaded_tier!r}; "
                 f"serving loaded model")
        try:
            wav, reason = await asyncio.get_running_loop().run_in_executor(
                _executor,
                partial(_synth, req.text, req.voice, instruct=req.instruct,
                        speed=req.speed, temperature=req.temperature))
        except Exception as e:  # never 500 — let the client fall back gracefully
            _log(f"synth error: {type(e).__name__}: {e}")
            return JSONResponse({"status": "error", "reason": f"{type(e).__name__}: {e}"})
        if wav is None:
            return JSONResponse({"status": "error", "reason": reason})
        return {
            "status": "ok",
            "audio_b64": base64.b64encode(wav).decode("ascii"),
            "sample_rate": _sample_rate,
        }

    return app


def main() -> None:
    from nhj.procname import set_title
    set_title("NHJ TTS")
    ap = argparse.ArgumentParser(description="Not-Happy-Jan warm Qwen3-TTS server")
    ap.add_argument("--host", default=os.getenv("NHJ_TTS_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("NHJ_TTS_PORT", "9992")))
    ap.add_argument("--load", default="fast", help="model tier to load: fast | best")
    args = ap.parse_args()

    _load(args.load)  # load BEFORE serving so the daemon is warm on first request

    import uvicorn
    _log(f"serving on http://{args.host}:{args.port}")
    uvicorn.run(build_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
