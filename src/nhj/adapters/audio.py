"""Audio adapter — TTS synthesis + playback.

Self-contained and macOS-only: the voice comes from NHJ's own local Qwen3-TTS server
(the cloned character voices). ALL playback goes through the single streaming mixer
(nhj.duck_player) — there is no afplay path. The mixer ducks the music bed under the
voice and sequences clips on the voice bus, so beep → "you still there?" → result come
out in order on one timeline.

Dispatch order (3 tiers):
  Tier 1  Bank clip  — random pre-rendered WAV from clips/<voice>/<intent>/
  Tier 2  Cache hit  — sha256-keyed previously-synthesised message WAV
  Tier 3  Live synth — local Qwen3-TTS (localhost:9992); result cached

TTS_ENGINE in .env:
  qwen     → local NHJ Qwen3-TTS server (default, Apple Silicon)
  audiocpp → an audio.cpp server (Breeze-TTS zero-shot cloning, CUDA/Linux) — see
             synth_audiocpp and the ``audiocpp:`` config block
  none     → suppress TTS
"""
from __future__ import annotations

import base64
import hashlib
import os
import random
import struct
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import requests

from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character

from nhj import resources

_VOICES_ROOT = resources.voices_dir()            # downloaded ref clips
_AUDIO_ROOT  = resources.audio_dir()             # downloaded music/sfx/voice/ambient
_CLIPS_ROOT  = resources.cache_dir(create=False) / "clips"
_TMP_ROOT    = resources.audio_tmp_dir(create=False)
_MAX_CACHE   = 300


# ---------------------------------------------------------------------------
# Playback helpers — everything goes through the streaming mixer
# ---------------------------------------------------------------------------
def _phone_fx_on() -> bool:
    return os.getenv("NHJ_PHONE_FX", "").strip().lower() in ("1", "true", "yes", "on")


def _emit_voice(path: str, rate: float = 1.0, gain: float = 1.0, ephemeral: bool = False) -> None:
    """Hand a voice clip to the mixer's voice bus (async; auto-ducks + sequences)."""
    from nhj import inference_muzak
    effect = "phone" if _phone_fx_on() else ""
    inference_muzak.submit_audio(path, bus="voice", gain=gain, rate=rate,
                                 effect=effect, ephemeral=ephemeral)


def _effective_speed(user_speed: float, band_speed: float) -> float:
    """Resolve the ONE speed applied at playback. An explicit marker/MCP `speed` (≠ 1.0)
    overrides the character band's speed; 1.0 defers to the band's natural pace."""
    return user_speed if user_speed and user_speed != 1.0 else band_speed


def _write_temp_wav(data: bytes) -> str:
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    # pid+random too: two synths finishing in the same millisecond must not overwrite
    p = _TMP_ROOT / f"{int(time.time() * 1000):013d}-{os.getpid()}-{uuid.uuid4().hex[:8]}.wav"
    p.write_bytes(data)
    return str(p)


# Live TTS comes out at whatever level the model produced; the pre-rendered banks are
# loudness-normalised to ~-19 LUFS. Level live output to the same target (full-clip RMS ≈
# LUFS for this voice) so live and banked lines sit consistently in the mix. Timbre-safe
# (pure gain) with a peak ceiling; returns the original bytes on any error so levelling can
# never break playback.
_VOICE_TARGET_DBFS = float(os.getenv("NHJ_VOICE_TARGET_DBFS", "-19.5"))
_VOICE_PEAK_CEIL_DBFS = -1.5


def _normalize_voice_wav(wav: bytes) -> bytes:
    try:
        import io

        import numpy as np
        import soundfile as sf

        data, rate = sf.read(io.BytesIO(wav), dtype="float32", always_2d=False)
        mono = data.mean(axis=1) if getattr(data, "ndim", 1) > 1 else data
        if mono.size == 0:
            return wav
        rms = float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))
        if rms < 10 ** (-50 / 20):                     # near-silent → leave it
            return wav
        gain = 10 ** ((_VOICE_TARGET_DBFS - 20 * np.log10(rms)) / 20)
        out = data * gain
        peak = float(np.max(np.abs(out))) if out.size else 0.0
        ceil = 10 ** (_VOICE_PEAK_CEIL_DBFS / 20)
        if peak > ceil:                                # preserve headroom; never clip
            out = out * (ceil / peak)
        buf = io.BytesIO()
        sf.write(buf, out, rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()
    except Exception:
        return wav


# ---------------------------------------------------------------------------
# Tier helpers — resolve a PATH (playback is done centrally by _emit_voice)
# ---------------------------------------------------------------------------
def pick_bank_clip(voice: str, intent: str) -> Optional[str]:
    # Cache first (the user's own `nhj build-bank`), then the bank shipped inside the
    # package (resources.bundled('clips')) — so a bare-marker install speaks in-character
    # with no model and no download. First location with clips wins.
    for root in (_CLIPS_ROOT, resources.bundled("clips")):
        d = root / voice / intent
        if d.is_dir():
            candidates = sorted(d.glob("*.wav"))
            if candidates:
                return str(random.choice(candidates))
    return None


def bank_clip_count(voice: str) -> int:
    """Distinct pre-rendered bank clips for *voice* across the user cache and the bundled
    package bank (the same two roots `pick_bank_clip` resolves). >0 means the voice speaks
    with no model and no live reference — the minimal-install contract. Deduped by
    (intent, filename) so a user-built clip shadowing a bundled one isn't double-counted."""
    names: set[tuple[str, str]] = set()
    for root in (_CLIPS_ROOT, resources.bundled("clips")):
        base = root / voice
        if base.is_dir():
            for wav in base.glob("*/*.wav"):
                names.add((wav.parent.name, wav.name))
    return len(names)


def _cache_path(voice: str, text: str, variant: str = "") -> Path:
    # variant (intensity band) keeps the same novel line cached separately per dial level
    digest = hashlib.sha256(f"{voice}::{variant}::{text}".encode()).hexdigest()[:32]
    return _CLIPS_ROOT / "cache" / f"{digest}.wav"


def _riff_size_ok(p: Path) -> bool:
    """True when the file's RIFF header declares exactly its actual size.

    A clip truncated before the atomic-write fix (#56) can sit in the cache
    indefinitely; size >= 100 alone would serve it forever (#59)."""
    try:
        with p.open("rb") as f:
            head = f.read(8)
        if len(head) < 8 or head[:4] != b"RIFF":
            return False
        return struct.unpack("<I", head[4:8])[0] == p.stat().st_size - 8
    except OSError:
        return False


def cache_hit(voice: str, text: str, variant: str = "") -> Optional[str]:
    p = _cache_path(voice, text, variant)
    try:
        if not p.is_file() or p.stat().st_size < 100:
            return None
    except OSError:
        return None
    if _riff_size_ok(p):
        return str(p)
    try:                       # truncated/foreign entry: drop it, treat as a miss
        p.unlink()
    except OSError:
        pass
    return None


def pick_return_clip() -> Optional[str]:
    """The 'you still there?' reconnect clip, as a path (for the mixer)."""
    if os.getenv("NHJ_MUZAK_RETURN", "1").strip().lower() in ("0", "off", "false", "no"):
        return None
    d = _AUDIO_ROOT / "voice" / "returns"
    clips = sorted(d.glob("*.wav")) if d.is_dir() else []
    return str(random.choice(clips)) if clips else None


# ---------------------------------------------------------------------------
# Tier 3 — live synthesis (local Qwen3-TTS only)
# ---------------------------------------------------------------------------
def synth_qwen(text: str, base_url: str, voice: str = "jan",
               model_tier: str = "fast", instruct: str = "", speed: float = 1.0,
               temperature: Optional[float] = None, timeout: float = 30.0) -> Optional[bytes]:
    """POST to the NHJ Qwen TTS server, return raw WAV bytes or None.

    instruct/speed/temperature shape the delivery per character intensity band
    (see nhj.characters.Delivery). Servers that don't understand them ignore them.
    """
    payload = {"text": text, "voice": voice, "model_tier": model_tier}
    if instruct:
        payload["instruct"] = instruct
    if speed and speed != 1.0:
        payload["speed"] = speed
    if temperature is not None:
        payload["temperature"] = temperature
    from nhj.lan_auth import HEADER, secret
    headers = {HEADER: secret()} if secret() else None
    try:
        r = requests.post(f"{base_url.rstrip('/')}/tts/generate", json=payload,
                          headers=headers, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "ok" or not body.get("audio_b64"):
            return None
        return base64.b64decode(body["audio_b64"])
    except Exception as e:
        print(f"[nhj] Qwen TTS error: {e}", file=sys.stderr)
        return None


def _audiocpp_cfg() -> dict:
    from nhj import config
    return config.section("audiocpp")


def audiocpp_voice(character_voice: str, cfg: Optional[dict] = None) -> str:
    """NHJ character voice id -> enrolled audio.cpp voice name (``audiocpp.voices`` map)."""
    cfg = _audiocpp_cfg() if cfg is None else cfg
    return (cfg.get("voices") or {}).get(character_voice, character_voice)


def synth_audiocpp(text: str, base_url: str, voice: str, voices_dir: str,
                   timeout: float = 60.0) -> Optional[bytes]:
    """Zero-shot synthesis on an audio.cpp server (Breeze-TTS), return WAV bytes or None.

    The voice is a reference clip enrolled in the server's ``--voice-dir``; its transcript
    (``<voice>.txt`` beside the clip) is sent per request as ``reference_text`` because the
    server-wide default is a single global value. Do NOT send ``instruction``: it fights the
    clone. A streaming OOM comes back as HTTP 200 with an EMPTY body, so a body that is not
    a real WAV is treated as a failure (the caller falls back to the bundled clip bank).
    """
    ref_txt = Path(voices_dir).expanduser() / f"{voice}.txt"
    try:
        reference_text = ref_txt.read_text().strip()
    except OSError:
        print(f"[nhj] audiocpp: no transcript {ref_txt} for voice {voice!r}", file=sys.stderr)
        return None
    body = {"model": "breeze", "input": text, "voice": voice, "response_format": "wav",
            "options": {"guidance_scale": "1.0", "stream_frames_per_event": "2",
                        "stream_lookahead_margin": "0", "reference_text": reference_text}}
    try:
        r = requests.post(f"{base_url.rstrip('/')}/v1/audio/speech", json=body, timeout=timeout)
        r.raise_for_status()
        wav = r.content
        if len(wav) < 1024 or wav[:4] != b"RIFF":
            print(f"[nhj] audiocpp: empty/invalid audio ({len(wav)} B) — server out of VRAM?",
                  file=sys.stderr)
            return None
        return wav
    except Exception as e:
        print(f"[nhj] audiocpp TTS error: {e}", file=sys.stderr)
        return None


def _save_and_cache(voice: str, text: str, wav: bytes, variant: str = "") -> None:
    """Write the cache WAV via a temp file and replace.

    A crash mid-write must not leave a truncated file at the cache path.
    ``cache_hit`` treats any file of 100 bytes or more as playable, so a partial
    WAV there would be spoken on the next turn.
    """
    cache = _cache_path(voice, text, variant)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(f".{cache.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        tmp.write_bytes(wav)
        os.replace(tmp, cache)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    try:  # sweep orphan temp files a hard kill mid-write leaked (#59), then LRU prune
        cutoff = time.time() - 3600
        for orphan in cache.parent.glob(".*.tmp"):
            try:
                if orphan.stat().st_mtime < cutoff:
                    orphan.unlink()
            except OSError:
                pass
        entries = sorted(cache.parent.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in entries[_MAX_CACHE:]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------
class AudioAdapter(NotificationAdapter):
    channel = "voice"   # gated per project (nhj.gate); silent adapters are not

    def available(self) -> bool:
        # The bundled bare-marker bank means the adapter can speak even on a minimal
        # install. TTS_ENGINE=none only disables LIVE synthesis, not the bank.
        return True

    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, **kwargs) -> bool:
        tts_enabled = os.getenv("TTS_ENGINE", "qwen").lower() != "none"

        # Another session is still working (see nhj.orchestration): a routine completion
        # is only a soft chime so the music bed and the session you are waiting on are not
        # talked over. The silent channels already fired.
        if kwargs.get("voice_mode") == "chime":
            from nhj import orchestration
            return orchestration.play_chime()

        # Coming off hold: the mixer already beeped the instant the music froze. Jan
        # reconnects ("you still there?") before the result. Both go on the mixer's voice
        # bus, which plays them in order and auto-ducks the music — so the return clip and
        # the result never collide and the music drops under both.
        try:
            from nhj import inference_muzak
            from nhj.state import get_flag
            if get_flag("muzak", True) and not inference_muzak._any_busy():   # only if the music really paused
                ret = pick_return_clip()
                if ret:
                    _emit_voice(ret, gain=0.9)
        except Exception as e:
            from nhj import resources
            resources.debug(f"return-clip dispatch failed: {e}")

        d = character.delivery(intent, message)
        voice, tts_text, variant = d.voice, d.text, d.band
        # Mention completions that were only chimed while other sessions were busy. Only when a
        # message will actually be spoken (a bare marker plays a bundled clip with no digest in
        # it); the count is cleared below only once it has really been delivered.
        digest_n, digest_delivered = 0, False
        if message and tts_text and tts_enabled and intent in ("ok", "celebrate", "step"):
            from nhj import orchestration
            digest_n = orchestration.peek_digest()
            if digest_n:
                tts_text = f"{tts_text} {orchestration.digest_sentence(digest_n)}"

        # Mode can override the voice globally (e.g. special-forces -> a whisper
        # voice/sample). Empty/unset = use the character's own voice.
        try:
            from nhj.state import get_setting
            ov = (get_setting("voice_override", "") or "").strip()
            if ov:
                voice, variant = ov, f"{ov}:{variant}"
        except Exception:
            pass

        # Tier 1: bank clip (bare markers only) · Tier 2: message cache (per band)
        path = pick_bank_clip(voice, intent) if not message else None
        if path is None and tts_text:
            path = cache_hit(voice, tts_text, variant)
            digest_delivered = bool(digest_n and path)        # a cached clip of the same sentence

        # Speed is applied ONCE, at playback (below) — never at synthesis. The deployed
        # Qwen backend ignores a synth-speed kwarg, and applying it at both synth and
        # playback would double it; bank/cache clips only ever see playback. So all three
        # paths get speed identically.  An explicit user speed (from the marker/MCP) wins
        # over the character band's speed.
        try:
            user_speed = float(kwargs.get("speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            user_speed = 1.0

        ephemeral = False
        if path is None and tts_enabled:                      # Tier 3: live synth (unsped)
            if os.getenv("TTS_ENGINE", "qwen").lower() == "audiocpp":
                acfg = _audiocpp_cfg()
                wav = synth_audiocpp(
                    tts_text or intent,
                    os.getenv("NHJ_AUDIOCPP_URL") or acfg.get("url", "http://127.0.0.1:8092"),
                    audiocpp_voice(voice, acfg),
                    os.getenv("NHJ_AUDIOCPP_VOICES") or acfg.get("voices_dir", "~/s2s/voices"))
            else:
                base_url   = os.getenv("NHJ_TTS_URL", "http://localhost:9992")
                model_tier = os.getenv("NHJ_TTS_MODEL_TIER", d.model_tier)
                try:                                          # on-demand: warm the TTS server up first
                    from nhj import servers
                    servers.ensure("tts")
                except Exception:
                    pass
                wav = synth_qwen(tts_text or intent, base_url, voice=voice,
                                 model_tier=model_tier, instruct=d.instruct,
                                 temperature=d.temperature)
            if wav:
                wav = _normalize_voice_wav(wav)           # level live TTS to match the banks
                if tts_text:
                    _save_and_cache(voice, tts_text, wav, variant)
                path = _write_temp_wav(wav)
                ephemeral = True
                digest_delivered = bool(digest_n)

        if path is None:
            # No bank/cache hit and no live voice (TTS_ENGINE=none, or synth failed/
            # unreachable) → fall back to the bundled bare-marker bank so the character
            # still speaks its generic in-character line. Keeps a minimal install — and
            # any message vibe without a model — audible.
            path = pick_bank_clip(voice, intent)
            if path is None:
                return False

        if digest_n and digest_delivered:
            from nhj import orchestration
            orchestration.clear_digest(digest_n)

        # The single point speed is applied — by resampling at playback. Resampling shifts
        # pitch (faster = higher); a pitch-preserving time-stretch would need a DSP dep we
        # deliberately avoid (see docs/AUDIO-STANDARD.md).
        _emit_voice(path, rate=_effective_speed(user_speed, d.speed), ephemeral=ephemeral)
        return True
