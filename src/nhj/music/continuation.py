"""Continuation sections: let a model carry the current groove on instead of looping the stems.

When the director's plan has been stable for a while, the composer renders the current stem mix
for `keep_bars`, hands it to an audio model that *continues* it to `total_bars` (Stable Audio 3
inpainting through audio.cpp was the one tested, see docs/generative-music.md), and keeps the
new bars as a `Section` the source splices in at a phrase line. Everything is optional and off by
default: no command configured means no composer, and the stems simply keep playing.

Safety: the command is an argv list (never a shell); placeholders are substituted per element;
each job runs in its own temp dir; one job at a time; failures back off. Every generated file
gets a provenance JSON (model, licence, prompt, seed) next to it in the cache.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from nhj import resources
from nhj.music.director import Plan
from nhj.music.pack import SR, Pack
from nhj.music.source import Section

_DEFAULT_PROMPT = ("Instrumental uplifting trance, 138 BPM, A minor, four-on-the-floor kick, rolling offbeat bass, "
                   "arpeggiated synth, no vocals, continuing the same groove")
_FAIL_BACKOFF_S = 300.0


class Composer:
    def __init__(self, pack: Pack, cfg: dict | None = None, runner=None, cache_dir=None):
        c = dict(cfg or {})
        self.pack = pack
        self.keep_bars = max(4, int(c.get("keep_bars", 8)))
        self.total_bars = max(self.keep_bars + 4, int(c.get("total_bars", 16)))
        self.stable_phrases = max(1, int(c.get("stable_phrases", 2)))
        self.cooldown_phrases = max(0, int(c.get("cooldown_phrases", 4)))
        self.max_variants = max(1, int(c.get("max_variants", 4)))
        self.prompt = str(c.get("prompt") or _DEFAULT_PROMPT)
        self.model_id = str(c.get("model_id") or "unknown")
        self.licence = str(c.get("licence") or "unknown")
        self.timeout_s = float(c.get("timeout_s", 120))
        self.min_free_vram_mib = int(c.get("min_free_vram_mib", 0))
        command = c.get("command")
        self._runner = runner or (self._command_runner(list(command), self.timeout_s) if command else None)
        self.enabled = bool(c.get("enabled")) and self._runner is not None
        self._cache = Path(cache_dir) if cache_dir else resources.cache_dir() / "music"
        self._lock = threading.Lock()
        self._key = None                       # frozenset of layers the stability count refers to
        self._stable = 0
        self._phrases_since_take = 10 ** 6
        self._variant: dict[frozenset, int] = {}
        self._ready: dict[frozenset, Section] = {}
        self._busy = False
        self._fail_until = 0.0
        self.last_error = ""

    # ---- SectionProvider ------------------------------------------------------
    def observe(self, plan: Plan, bar: int) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._phrases_since_take += 1
            self._stable = self._stable + 1 if plan.layers == self._key else 1
            self._key = plan.layers
            ok = (plan.label.startswith("busy") and self._stable >= self.stable_phrases
                  and plan.layers not in self._ready and not self._busy and time.monotonic() >= self._fail_until)
            if not ok:
                return
            self._busy = True
            variant = self._variant.get(plan.layers, 0) % self.max_variants
        threading.Thread(target=self._make, args=(plan.layers, variant), daemon=True).start()

    def prime(self, layers: frozenset, variant: int = 0) -> bool:
        """Blocking: make (or load) the section for *layers* now, ignoring stability and cooldown.
        For the offline demo and tests. Returns False if it could not be made (see `last_error`)."""
        if not self.enabled:
            return False
        self._make(layers, variant)
        with self._lock:
            self._phrases_since_take = 10 ** 6
            return layers in self._ready

    def take(self, plan: Plan, bar: int) -> Section | None:
        with self._lock:
            if self._phrases_since_take < self.cooldown_phrases:
                return None
            sec = self._ready.pop(plan.layers, None)
            if sec is not None:
                self._phrases_since_take = 0
                self._variant[plan.layers] = self._variant.get(plan.layers, 0) + 1
            return sec

    # ---- generation -----------------------------------------------------------
    def cache_key(self, layers: frozenset, variant: int) -> str:
        blob = json.dumps({"model": self.model_id, "prompt": self.prompt, "layers": sorted(layers),
                           "keep": self.keep_bars, "total": self.total_bars, "variant": variant,
                           "pack": [self.pack.name, self.pack.bpm, self.pack.bars]}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def _make(self, layers: frozenset, variant: int) -> None:
        try:
            key = self.cache_key(layers, variant)
            wav = self._cache / f"{key}.wav"
            audio = self._load(wav, int(round((self.total_bars - self.keep_bars) * self.pack.spb)))
            if audio is None:
                audio = self._generate(layers, variant, wav)
            new_bars = self.total_bars - self.keep_bars
            with self._lock:
                self._ready[layers] = Section(audio, new_bars, f"continuation:{key[:8]}")
        except Exception as e:                                 # a failed job must never hurt the bed
            self.last_error = f"{type(e).__name__}: {e}"
            with self._lock:
                self._fail_until = time.monotonic() + _FAIL_BACKOFF_S
        finally:
            with self._lock:
                self._busy = False

    def _load(self, wav: Path, expect: int):
        try:
            with wave.open(str(wav), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 2, 2):
                    return None
                raw = w.readframes(w.getnframes())
        except (OSError, wave.Error, EOFError):
            return None
        audio = (np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0).reshape(-1, 2)
        return audio if len(audio) == expect else None          # a short/torn cache file is regenerated

    def _generate(self, layers: frozenset, variant: int, wav_out: Path) -> np.ndarray:
        if self.min_free_vram_mib:
            free = _free_vram_mib()
            if free is None or free < self.min_free_vram_mib:
                raise RuntimeError(f"free GPU memory {free} MiB is below min_free_vram_mib "
                                   f"{self.min_free_vram_mib} (or could not be read); skipping continuation")
        spb = self.pack.spb
        src = self.pack.mix(layers, bars=self.keep_bars)
        peak = float(np.max(np.abs(src))) or 1.0
        src_44 = resample_poly(src / peak * 0.89, 147, 160, axis=0).astype(np.float32)
        seed = 7 + variant
        with tempfile.TemporaryDirectory(prefix="nhj-music-") as tmp:
            tmpd = Path(tmp)
            _write_wav(tmpd / "in.wav", src_44, 44100)
            self._runner(tmpd, self.prompt, seed, self.total_bars * spb / SR, self.keep_bars * spb / SR)
            from nhj.duck_player import _decode_file
            out = _decode_file(str(tmpd / "out.wav"))
        a = int(round(self.keep_bars * spb))
        want = int(round((self.total_bars - self.keep_bars) * spb))
        new = out[a:a + want]
        if len(new) < 0.9 * want:
            raise RuntimeError(f"model returned {len(out) / SR:.1f}s, need {self.total_bars * spb / SR:.1f}s")
        if len(new) < want:
            new = np.concatenate([new, np.zeros((want - len(new), 2), np.float32)])
        rms_new = float(np.sqrt(np.mean(new ** 2))) or 1.0
        rms_ref = float(np.sqrt(np.mean(self.pack.mix(layers) ** 2))) or rms_new
        new = (new * float(np.clip(rms_ref / rms_new, 0.25, 4.0))).astype(np.float32)
        self._cache.mkdir(parents=True, exist_ok=True)
        tmp_wav = wav_out.with_name(wav_out.name + ".tmp")
        _write_wav(tmp_wav, new, SR)
        tmp_wav.replace(wav_out)                                # atomic: a crash never leaves a short file
        wav_out.with_suffix(".json").write_text(json.dumps({
            "model": self.model_id, "licence": self.licence, "prompt": self.prompt, "seed": seed,
            "layers": sorted(layers), "keep_bars": self.keep_bars, "total_bars": self.total_bars,
            "pack": self.pack.name, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "note": "AI-generated continuation; keep model, version and licence for authorship/licence questions"},
            indent=2))
        return new

    @staticmethod
    def _command_runner(template: list, timeout_s: float = 120.0):
        def run(workdir: Path, prompt: str, seed: int, duration: float, mask_start: float) -> None:
            subs = {"workdir": str(workdir), "prompt": prompt, "seed": str(seed),
                    "duration": f"{duration:.3f}", "mask_start": f"{mask_start:.3f}"}
            argv = [str(part).format_map(subs) for part in template]
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s, cwd=workdir)
            if r.returncode != 0 or not (workdir / "out.wav").exists():
                raise RuntimeError(f"continuation command failed rc={r.returncode}: {(r.stderr or r.stdout)[-200:]}")
        return run


def _write_wav(path: Path, data: np.ndarray, rate: int) -> None:
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def _free_vram_mib() -> int | None:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return None
