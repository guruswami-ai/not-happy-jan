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
  os       → CPU system voice, no model: espeak-ng/espeak (Linux) or say (macOS)
  piper    → CPU Piper CLI with a local .onnx voice (NHJ_PIPER_MODEL)
  none     → suppress TTS
"""
from __future__ import annotations

import base64
import hashlib
import os
import random
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import requests

from nhj.adapters.base import NotificationAdapter

if TYPE_CHECKING:
    from nhj.characters import Character

from nhj import env_registry, resources

_LAZY_ATTRS = ("_VOICES_ROOT", "_AUDIO_ROOT", "_CLIPS_ROOT", "_TMP_ROOT", "_VOICE_TARGET_DBFS")

# Compatibility declarations only; module __getattr__ resolves these on access.
_VOICES_ROOT: Path
_AUDIO_ROOT: Path
_CLIPS_ROOT: Path
_TMP_ROOT: Path
_VOICE_TARGET_DBFS: float

_MAX_CACHE = 300


def _voices_root() -> Path:
    override = globals().get("_VOICES_ROOT")
    return override if override is not None else resources.voices_dir()


def _audio_root() -> Path:
    override = globals().get("_AUDIO_ROOT")
    return override if override is not None else resources.audio_dir()


def _clips_root() -> Path:
    override = globals().get("_CLIPS_ROOT")
    return override if override is not None else resources.cache_dir(create=False) / "clips"


def _tmp_root() -> Path:
    override = globals().get("_TMP_ROOT")
    return override if override is not None else resources.audio_tmp_dir(create=False)


def _voice_target_dbfs() -> float:
    override = globals().get("_VOICE_TARGET_DBFS")
    if override is not None:
        return float(override)
    entry = env_registry.lookup("NHJ_VOICE_TARGET_DBFS")
    default = entry.default if entry and entry.default else "-19.5"
    return float(os.getenv("NHJ_VOICE_TARGET_DBFS", default))


def __getattr__(name: str) -> object:
    if name == "_VOICES_ROOT":
        return _voices_root()
    if name == "_AUDIO_ROOT":
        return _audio_root()
    if name == "_CLIPS_ROOT":
        return _clips_root()
    if name == "_TMP_ROOT":
        return _tmp_root()
    if name == "_VOICE_TARGET_DBFS":
        return _voice_target_dbfs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))


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
    tmp_root = _tmp_root()
    tmp_root.mkdir(parents=True, exist_ok=True)
    # pid+random too: two synths finishing in the same millisecond must not overwrite
    p = tmp_root / f"{int(time.time() * 1000):013d}-{os.getpid()}-{uuid.uuid4().hex[:8]}.wav"
    p.write_bytes(data)
    return str(p)


# Live TTS comes out at whatever level the model produced; the pre-rendered banks are
# loudness-normalised to ~-19 LUFS. Level live output to the same target (full-clip RMS ≈
# LUFS for this voice) so live and banked lines sit consistently in the mix. Timbre-safe
# (pure gain) with a peak ceiling; returns the original bytes on any error so levelling can
# never break playback.
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
        gain = 10 ** ((_voice_target_dbfs() - 20 * np.log10(rms)) / 20)
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
    for root in (_clips_root(), resources.bundled("clips")):
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
    for root in (_clips_root(), resources.bundled("clips")):
        base = root / voice
        if base.is_dir():
            for wav in base.glob("*/*.wav"):
                names.add((wav.parent.name, wav.name))
    return len(names)


def _cache_backend_id(model_tier: str = "") -> str:
    """What will synthesize a cache miss right now.

    The string uses the same URL, voice directory and tier the synth call uses.
    A config URL alone would replay another server's clip, and the intensity band
    is not the model tier.
    """
    engine = os.getenv("TTS_ENGINE", "qwen").lower()
    if engine == "audiocpp":
        acfg = _audiocpp_cfg()
        url = os.getenv("NHJ_AUDIOCPP_URL") or acfg.get("url") or "http://127.0.0.1:8092"
        voices = os.getenv("NHJ_AUDIOCPP_VOICES") or acfg.get("voices_dir") or "~/s2s/voices"
        return f"audiocpp:{url}:{voices}"
    if engine == "os":
        return f"os:{sys.platform}:{(os.getenv('NHJ_OS_TTS_VOICE') or '').strip()}"
    if engine == "piper":
        return (f"piper:{os.getenv('NHJ_PIPER_MODEL', '')}:"
                f"{(os.getenv('NHJ_PIPER_SPEAKER') or '').strip()}")
    if engine == "none":
        return "none"
    url = os.getenv("NHJ_TTS_URL") or "http://localhost:9992"
    tier = (os.getenv("NHJ_TTS_MODEL_TIER") or model_tier or "").strip()
    return f"qwen:{url}:{tier}"


def _cache_path(voice: str, text: str, variant: str = "", model_tier: str = "") -> Path:
    # variant (intensity band) + backend identity keep the same novel line cached
    # separately per dial level and per synthesis backend/settings.
    digest = hashlib.sha256(
        f"{voice}::{variant}::{_cache_backend_id(model_tier)}::{text}".encode()).hexdigest()[:32]
    return _clips_root() / "cache" / f"{digest}.wav"


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


def cache_hit(voice: str, text: str, variant: str = "", model_tier: str = "") -> Optional[str]:
    p = _cache_path(voice, text, variant, model_tier)
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
    d = _audio_root() / "voice" / "returns"
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


# ---------------------------------------------------------------------------
# Tier 3 (CPU) — system voice / Piper CLI: speaks arbitrary text with no model
# server and no GPU, so a minimal install is not limited to banked lines (#49).
# Delivery shaping these engines cannot express (instruct, temperature) is
# dropped; speed still applies once at playback, like every other tier.
# ---------------------------------------------------------------------------
_ESPEAK_PITCH = {"jan": 50, "bazza": 35, "karren": 65, "jan-whispering": 50}


def _os_command(text: str, voice: str, platform: Optional[str] = None) -> tuple[Optional[list], str]:
    """Argv + output suffix for the OS voice, or (None, suffix) if no binary exists.

    The output path is the literal ``{out}`` placeholder; the caller substitutes it.
    NHJ_OS_TTS_VOICE overrides the voice for both engines; on espeak the cast is
    otherwise told apart by pitch (espeak -p is 0-99, 50 = neutral)."""
    platform = sys.platform if platform is None else platform
    wanted = (os.getenv("NHJ_OS_TTS_VOICE") or "").strip()
    if platform == "darwin":
        exe = shutil.which("say")
        if not exe:
            return None, ".aiff"
        cmd = [exe, "-o", "{out}"]
        if wanted:
            cmd += ["-v", wanted]
        return cmd + [text], ".aiff"
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        return None, ".wav"
    cmd = [exe, "-w", "{out}", "-p", str(_ESPEAK_PITCH.get(voice, 50))]
    if wanted:
        cmd += ["-v", wanted]
    return cmd + [text], ".wav"


def _run_synth_cli(cmd: list, out: Path, stdin_text: str = "", timeout: float = 30.0,
                   label: str = "cpu-tts") -> Optional[bytes]:
    try:
        subprocess.run(cmd, input=stdin_text.encode() if stdin_text else None,
                       capture_output=True, timeout=timeout, check=True)
        data = out.read_bytes()
    except Exception as e:
        print(f"[nhj] {label} error: {e}", file=sys.stderr)
        return None
    finally:
        out.unlink(missing_ok=True)
    if len(data) < 44 or data[:4] not in (b"RIFF", b"FORM"):   # WAV or AIFF (say)
        print(f"[nhj] {label}: empty/invalid audio ({len(data)} B)", file=sys.stderr)
        return None
    return data


def _synth_out_path(suffix: str) -> Path:
    tmp_root = _tmp_root()
    tmp_root.mkdir(parents=True, exist_ok=True)
    return tmp_root / f"synth-{os.getpid()}-{uuid.uuid4().hex[:8]}{suffix}"


def synth_os(text: str, voice: str = "jan", timeout: float = 30.0) -> Optional[bytes]:
    """System-voice synthesis (espeak-ng/espeak on Linux, say on macOS)."""
    if not text.strip():
        return None
    cmd, suffix = _os_command(text, voice)
    if cmd is None:
        print("[nhj] os TTS: no espeak-ng/espeak/say binary found", file=sys.stderr)
        return None
    out = _synth_out_path(suffix)
    return _run_synth_cli([str(out) if a == "{out}" else a for a in cmd], out,
                          timeout=timeout, label="os TTS")


def synth_piper(text: str, model: str, timeout: float = 60.0) -> Optional[bytes]:
    """Piper CLI synthesis: piper --model <voice.onnx> --output_file <wav>, text on stdin."""
    if not text.strip() or not model:
        if not model:
            print("[nhj] piper: NHJ_PIPER_MODEL is not set", file=sys.stderr)
        return None
    model_path = Path(model).expanduser()
    if not model_path.is_file():
        print(f"[nhj] piper: no model at {model_path}", file=sys.stderr)
        return None
    exe = shutil.which("piper")
    if not exe:
        print("[nhj] piper: piper binary not found on PATH", file=sys.stderr)
        return None
    out = _synth_out_path(".wav")
    cmd = [exe, "--model", str(model_path), "--output_file", str(out)]
    speaker = (os.getenv("NHJ_PIPER_SPEAKER") or "").strip()
    if speaker:
        cmd += ["--speaker", speaker]
    return _run_synth_cli(cmd, out, stdin_text=text, timeout=timeout, label="piper")


def _save_and_cache(voice: str, text: str, wav: bytes, variant: str = "",
                    model_tier: str = "") -> None:
    """Write the cache WAV via a temp file and replace.

    A crash mid-write must not leave a truncated file at the cache path.
    ``cache_hit`` treats any file of 100 bytes or more as playable, so a partial
    WAV there would be spoken on the next turn.
    """
    cache = _cache_path(voice, text, variant, model_tier)
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
def _apply_voice_override(voice: str, variant: str) -> tuple[str, str]:
    """Mode can override the voice globally (e.g. special-forces -> a whisper voice/sample).
    Empty/unset = use the character's own voice."""
    try:
        from nhj.state import get_setting
        ov = (get_setting("voice_override", "") or "").strip()
        if ov:
            return ov, f"{ov}:{variant}"
    except Exception:
        pass
    return voice, variant


def literal_clip_cached(character: "Character", intent: str, message: str) -> bool:
    """True when the exact text of a literal `say` already has a cached clip, i.e. it can be played
    with no speech engine at all. Uses the same key as `AudioAdapter.fire`."""
    try:
        d = character.delivery(intent, message, literal=True)
        voice, variant = _apply_voice_override(d.voice, d.band)
        model_tier = os.getenv("NHJ_TTS_MODEL_TIER", d.model_tier)
        return bool(d.text) and cache_hit(voice, d.text, variant, model_tier) is not None
    except Exception:
        return False


class AudioAdapter(NotificationAdapter):
    channel = "voice"   # gated per project (nhj.gate); silent adapters are not

    def available(self) -> bool:
        # The bundled bare-marker bank means the adapter can speak even on a minimal
        # install. TTS_ENGINE=none only disables LIVE synthesis, not the bank.
        return True

    def fire(self, intent: str, message: str, character: "Character",
             vibe_level: int = 5, **kwargs) -> bool:
        tts_enabled = os.getenv("TTS_ENGINE", "qwen").lower() != "none"
        # `nhj say`: speak exactly this text, nothing before it and nothing instead of it. With no
        # message there is nothing to be literal about, so a bare marker keeps its normal behaviour.
        literal = bool(kwargs.get("literal")) and bool(message and str(message).strip())

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
            # not for a literal `say`: the words before it would be ones the caller did not give
            if not literal and get_flag("muzak", True) and not inference_muzak._any_busy():   # only if the music really paused
                ret = pick_return_clip()
                if ret:
                    _emit_voice(ret, gain=0.9)
        except Exception as e:
            from nhj import resources
            resources.debug(f"return-clip dispatch failed: {e}")

        d = character.delivery(intent, message, literal=True) if literal else character.delivery(intent, message)
        voice, tts_text, variant = d.voice, d.text, d.band
        # Mention completions that were only chimed while other sessions were busy. Only when a
        # message will actually be spoken (a bare marker plays a bundled clip with no digest in
        # it); the count is cleared below only once it has really been delivered.
        digest_n, digest_delivered = 0, False
        if message and tts_text and tts_enabled and not literal and intent in ("ok", "celebrate", "step"):
            from nhj import orchestration
            digest_n = orchestration.peek_digest()
            if digest_n:
                tts_text = f"{tts_text} {orchestration.digest_sentence(digest_n)}"

        voice, variant = _apply_voice_override(voice, variant)

        # Tier 1: bank clip (bare markers only) · Tier 2: message cache (per band)
        path = pick_bank_clip(voice, intent) if not message else None
        model_tier = os.getenv("NHJ_TTS_MODEL_TIER", d.model_tier)
        if path is None and tts_text:
            path = cache_hit(voice, tts_text, variant, model_tier)
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
            engine = os.getenv("TTS_ENGINE", "qwen").lower()
            if engine == "os":
                wav = synth_os(tts_text or intent, voice=voice)
            elif engine == "piper":
                wav = synth_piper(tts_text or intent, os.getenv("NHJ_PIPER_MODEL", ""))
            elif engine == "audiocpp":
                acfg = _audiocpp_cfg()
                wav = synth_audiocpp(
                    tts_text or intent,
                    os.getenv("NHJ_AUDIOCPP_URL") or acfg.get("url", "http://127.0.0.1:8092"),
                    audiocpp_voice(voice, acfg),
                    os.getenv("NHJ_AUDIOCPP_VOICES") or acfg.get("voices_dir", "~/s2s/voices"))
            else:
                base_url   = os.getenv("NHJ_TTS_URL", "http://localhost:9992")
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
                    _save_and_cache(voice, tts_text, wav, variant, model_tier)
                path = _write_temp_wav(wav)
                ephemeral = True
                digest_delivered = bool(digest_n)

        if path is None:
            # No bank/cache hit and no live voice (TTS_ENGINE=none, or synth failed/
            # unreachable) → fall back to the bundled bare-marker bank so the character
            # still speaks its generic in-character line. Keeps a minimal install — and
            # any message vibe without a model — audible.
            if literal:
                # A literal `say` has no stand-in: a generic clip would be different words. Report
                # "not spoken" (the CLI has already printed the text to stderr when it knew).
                return False
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
