"""Soft-duck streaming MIXER — uv-native (av + sounddevice + numpy), macOS.

One persistent sounddevice OutputStream owns ALL of NHJ's audio so everything shares
one timeline instead of racing independent `afplay` processes. It sums named buses:

  • music   — the hold-music bed (looping playlist), ducks under speech/fx
  • fx      — transfer beeps / hold tones (concurrent one-shots)
  • voice   — Jan/Bazza/Karren TTS + return clips (SEQUENTIAL queue → correct ordering)
  • ambient — low concurrent beds (scene vignettes and mode loops) ducked under voice

Ducking is automatic (sidechain): whenever a voice/fx clip is sounding, the music bed
drops to NHJ_MUZAK_DUCK_GAIN. Other processes (the worker/adapter) feed the voice bus by
dropping play-events into a spool dir — the mixer daemon ingests them. This is the ONLY
audio path; nothing in NHJ spawns afplay.

Needs av, sounddevice, numpy (all uv-managed).
"""
from __future__ import annotations

import collections
import json
import os
import random
import threading
import time
from pathlib import Path

import numpy as np

from nhj import resources

SR = 48000          # output samplerate
CH = 2              # stereo
BLOCK = 1024        # ~21ms per callback
_BUF_MAX = 48       # bounded music decode buffer (~1s) → frozen position when full/paused
_EVENTS = resources.audio_events_dir(create=False)   # play-event spool (cross-process)


# ---- decode helpers ---------------------------------------------------------
def _decode_file(path: str) -> np.ndarray:
    """Whole-file decode → (N, 2) float32 at 48k. Empty array on failure."""
    import av
    from av.audio.resampler import AudioResampler
    try:
        container = av.open(str(path))
        stream = container.streams.audio[0]
        rs = AudioResampler(format="flt", layout="stereo", rate=SR)
        chunks = []
        for frame in container.decode(stream):
            for r in rs.resample(frame):
                chunks.append(r.to_ndarray().reshape(-1, CH))
        container.close()
        return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros((0, CH), np.float32)
    except Exception:
        return np.zeros((0, CH), np.float32)


def _resample_rate(data: np.ndarray, rate: float) -> np.ndarray:
    """Speed/pitch shift like `afplay -r` (resample by 1/rate). rate>1 = faster+higher."""
    if rate <= 0 or abs(rate - 1.0) < 0.01 or len(data) == 0:
        return data
    n = int(len(data) / rate)
    if n <= 0:
        return data
    src = np.arange(len(data))
    idx = np.linspace(0, len(data) - 1, n)
    out = np.empty((n, CH), np.float32)
    for c in range(CH):
        out[:, c] = np.interp(idx, src, data[:, c])
    return out


def _phone(data: np.ndarray) -> np.ndarray:
    """Telephone colour: bandpass ~300–3400 Hz + soft drive. FFT-based (fast, whole-clip)."""
    n = len(data)
    if n == 0:
        return data
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    mask = ((freqs >= 300) & (freqs <= 3400)).astype(np.float32)
    out = np.empty_like(data)
    for c in range(CH):
        out[:, c] = np.fft.irfft(np.fft.rfft(data[:, c]) * mask, n)
    return (np.tanh(out * 2.2) * 0.7).astype(np.float32)


def _make_tel_filter():
    """Build a stateful telephone bandpass (300–3400 Hz) for the streaming music bed.
    Returns (sos, [per-channel zi]) or (None, None) if scipy is unavailable.
    Stateful across blocks → no edge glitches (unlike a per-block FFT)."""
    try:
        from scipy.signal import butter, sosfilt_zi
    except Exception:
        return None, None
    sos = butter(4, [300, 3400], btype="band", fs=SR, output="sos")
    zi = [sosfilt_zi(sos).copy() for _ in range(CH)]
    return sos, zi


def _tel_apply(sos, zi, block: np.ndarray, makeup: float = 1.8) -> np.ndarray:
    """Apply the telephone bandpass to one (frames, CH) music block, carrying zi."""
    from scipy.signal import sosfilt
    out = np.empty_like(block)
    for c in range(CH):
        out[:, c], zi[c] = sosfilt(sos, block[:, c], zi=zi[c])
    return np.tanh(out * makeup) * 0.8          # makeup + soft drive → tinny "on-hold" colour


class _Decoder(threading.Thread):
    """Decodes a shuffled playlist to 48k stereo float32, feeding a bounded buffer.
    Blocks when the buffer is full (or paused) — that is what freezes playback position."""

    def __init__(self, playlist, buf: collections.deque, paused: threading.Event):
        super().__init__(daemon=True)
        self.playlist = list(playlist)
        self.buf = buf
        self.paused = paused
        self.stop = threading.Event()

    def _push(self, samples: np.ndarray) -> None:
        i, n = 0, len(samples)
        while i < n and not self.stop.is_set():
            while (len(self.buf) >= _BUF_MAX or self.paused.is_set()) and not self.stop.is_set():
                time.sleep(0.01)
            chunk = samples[i:i + BLOCK]
            i += len(chunk)
            self.buf.append(np.ascontiguousarray(chunk, dtype=np.float32))

    def run(self) -> None:
        import av
        from av.audio.resampler import AudioResampler
        idx = 0
        while not self.stop.is_set():
            if not self.playlist:
                return
            path = self.playlist[idx % len(self.playlist)]
            idx += 1
            if idx % len(self.playlist) == 0:
                random.shuffle(self.playlist)
            try:
                container = av.open(str(path))
                stream = container.streams.audio[0]
                resampler = AudioResampler(format="flt", layout="stereo", rate=SR)
                for frame in container.decode(stream):
                    if self.stop.is_set():
                        break
                    for rs in resampler.resample(frame):
                        self._push(rs.to_ndarray().reshape(-1, CH))
                container.close()
            except Exception:
                time.sleep(0.05)
                continue


class _Clip:
    __slots__ = ("data", "pos", "gain")

    def __init__(self, data: np.ndarray, gain: float):
        self.data = data
        self.pos = 0
        self.gain = gain


def _default_output_device() -> str:
    """Ground-truth current macOS default OUTPUT device name (independent of PortAudio's
    cached default). Prefers SwitchAudioSource (fast); falls back to system_profiler."""
    import shutil
    import subprocess
    if shutil.which("SwitchAudioSource"):
        try:
            return subprocess.run(["SwitchAudioSource", "-c"], capture_output=True,
                                  text=True, timeout=2).stdout.strip()
        except Exception:
            return ""
    try:
        import json as _json
        out = subprocess.run(["system_profiler", "SPAudioDataType", "-json"],
                             capture_output=True, text=True, timeout=6).stdout
        for grp in _json.loads(out).get("SPAudioDataType", []):
            for dev in grp.get("_items", []):
                if dev.get("coreaudio_default_audio_output_device") == "spaudio_yes":
                    return dev.get("_name", "")
    except Exception:
        return ""
    return ""


class Mixer:
    """The single-stream mixer. music bed + concurrent fx + sequential voice queue."""

    def __init__(self, playlist):
        self.buf: collections.deque = collections.deque()
        self._paused_evt = threading.Event()
        self._paused_evt.set()                               # music starts silent
        self._decoder = _Decoder(playlist, self.buf, self._paused_evt)
        try:
            self.master = float(os.getenv("NHJ_MUZAK_VOLUME", "0.5"))
        except ValueError:
            self.master = 0.5
        try:
            self._duck_floor = float(os.getenv("NHJ_MUZAK_DUCK_GAIN", "0.22"))
        except ValueError:
            self._duck_floor = 0.22
        self._music_gain = 1.0
        self.duck_external = False                           # set by control loop (cross-proc flag)
        self._fx: list[_Clip] = []                           # concurrent one-shots
        self._ambient: list[_Clip] = []                      # concurrent beds (low; duck under voice)
        self._ambient_loop: _Clip | None = None              # looping mode bed (call-centre etc.)
        self._voiceq: collections.deque = collections.deque()  # pending voice clips
        self._voice_cur: _Clip | None = None                 # sequential head
        try:
            self._ambient_duck = float(os.getenv("NHJ_AMBIENT_DUCK", "0.35"))
        except ValueError:
            self._ambient_duck = 0.35
        self.music_floor = 1.0                               # long-session duck target (controller-driven)
        self.voice_fx = ""                                   # global voice-bus effect ("phone" = call-centre)
        self.music_phone = False                             # tinny hold music (call-centre); controller-driven
        self._tel_sos, self._tel_zi = _make_tel_filter()     # stateful telephone bandpass for the music bed
        self._comp = os.getenv("NHJ_COMP", "0").strip().lower() not in ("0", "off", "false", "no")
        try:
            self._comp_drive = float(os.getenv("NHJ_COMP_DRIVE", "1.6"))
        except ValueError:
            self._comp_drive = 1.6
        self._lock = threading.Lock()
        self._stream = None

    # ---- realtime callback (no I/O) -----------------------------------------
    def _callback(self, outdata, frames, time_info, status):
        out = np.zeros((frames, CH), dtype=np.float32)

        # music bed (skipped while paused → exact-resume)
        if not self._paused_evt.is_set():
            filled = 0
            while filled < frames and self.buf:
                chunk = self.buf[0]
                take = min(len(chunk), frames - filled)
                out[filled:filled + take] = chunk[:take]
                if take == len(chunk):
                    self.buf.popleft()
                else:
                    self.buf[0] = chunk[take:]
                filled += take
            # tinny "on-hold" colour: phone-band the music bed (stateful → no glitches)
            if self.music_phone and self._tel_sos is not None and filled:
                out = _tel_apply(self._tel_sos, self._tel_zi, out)

        with self._lock:
            speaking = bool(self._fx) or self._voice_cur is not None or bool(self._voiceq)
        # sidechain duck while anything sounds; otherwise ride the long-session floor (the
        # controller eases music_floor down over a long inference, back to 1.0 when idle)
        target = self._duck_floor if (speaking or self.duck_external) else self.music_floor
        ramp = np.linspace(self._music_gain, target, frames, dtype=np.float32)[:, None]
        out *= ramp * self.master
        self._music_gain = target

        # mix one-shots ON TOP (full level, not music-ducked)
        with self._lock:
            voice_active = self._voice_cur is not None or bool(self._voiceq)
            # fx — concurrent, full level
            keep = []
            for c in self._fx:
                n = min(frames, len(c.data) - c.pos)
                if n > 0:
                    out[:n] += c.data[c.pos:c.pos + n] * c.gain
                    c.pos += n
                if c.pos < len(c.data):
                    keep.append(c)
            self._fx = keep
            # ambient — concurrent low beds, ducked further under the voice
            amb_g = self._ambient_duck if voice_active else 1.0
            keep_a = []
            for c in self._ambient:
                n = min(frames, len(c.data) - c.pos)
                if n > 0:
                    out[:n] += c.data[c.pos:c.pos + n] * c.gain * amb_g
                    c.pos += n
                if c.pos < len(c.data):
                    keep_a.append(c)
            self._ambient = keep_a
            # ambient loop — persistent mode bed, wrapped across callbacks
            loop = self._ambient_loop
            if loop is not None and len(loop.data):
                idx = (np.arange(frames) + loop.pos) % len(loop.data)
                out[:frames] += loop.data[idx] * loop.gain * amb_g
                loop.pos = (loop.pos + frames) % len(loop.data)
            # voice — strictly sequential, one clip at a time in submission order
            if self._voice_cur is None and self._voiceq:
                self._voice_cur = self._voiceq.popleft()
            if self._voice_cur is not None:
                c = self._voice_cur
                n = min(frames, len(c.data) - c.pos)
                if n > 0:
                    out[:n] += c.data[c.pos:c.pos + n] * c.gain
                    c.pos += n
                if c.pos >= len(c.data):
                    self._voice_cur = None

        if self._comp:                                       # soft-knee bus glue (tanh saturate + makeup)
            d = self._comp_drive
            out = np.tanh(out * d) * (1.0 / np.tanh(d))
        np.clip(out, -1.0, 1.0, out)                         # master safety limiter
        outdata[:] = out

    # ---- control surface -----------------------------------------------------
    def start(self) -> None:
        import sounddevice as sd
        self._decoder.start()
        self._stream = sd.OutputStream(samplerate=SR, channels=CH, dtype="float32",
                                       blocksize=BLOCK, callback=self._callback)
        self._stream.start()

    def reopen(self) -> None:
        """Tear down + reopen the output stream so it binds to the CURRENT default device.
        PortAudio caches the default at open, so we re-init it to pick up the new one
        (e.g. when the user plugs in headphones)."""
        import sounddevice as sd
        try:
            if self._stream:
                self._stream.stop(); self._stream.close()
        except Exception:
            pass
        try:
            sd._terminate(); sd._initialize()
        except Exception:
            pass
        self._stream = sd.OutputStream(samplerate=SR, channels=CH, dtype="float32",
                                       blocksize=BLOCK, callback=self._callback)
        self._stream.start()

    def pause(self) -> None:
        self._paused_evt.set()

    def resume(self) -> None:
        self._paused_evt.clear()

    @property
    def paused(self) -> bool:
        return self._paused_evt.is_set()

    def play(self, path: str, bus: str = "voice", gain: float = 1.0,
             rate: float = 1.0, effect: str = "", pre: float = 0.0) -> None:
        data = _decode_file(path)
        if data.size == 0:
            return
        if rate != 1.0:
            data = _resample_rate(data, rate)
        eff = effect or (self.voice_fx if bus not in ("fx", "ambient") else "")
        if eff == "phone":                                   # per-clip, or global voice-bus (call-centre)
            data = _phone(data)
        if pre > 0:                                          # lead-in silence → sequence on a bus
            pad = np.zeros((int(pre * SR), CH), dtype=np.float32)
            data = np.concatenate([pad, data])
        clip = _Clip(data, gain)
        with self._lock:
            if bus == "fx":
                self._fx.append(clip)
            elif bus == "ambient":
                self._ambient.append(clip)                   # concurrent bed, ducks under voice
            else:
                self._voiceq.append(clip)                    # voice → sequential queue

    def set_ambient_loop(self, path: str | None, gain: float = 1.0) -> None:
        """Set or clear the looping mode bed mixed through the ambient bus."""
        clip = None
        if path:
            data = _decode_file(path)
            if data.size:
                clip = _Clip(data, gain)
        with self._lock:
            self._ambient_loop = clip

    def play_sequence(self, items: list, bus: str = "voice") -> None:
        """Play several clips as ONE gapless one-shot — concatenated buffer, per-part gain
        baked in, zero samples between them. `items` is a list of (path, gain). On the voice
        bus this also butts straight up against whatever voice plays next (the vibe line)."""
        parts = []
        for path, g in items:
            d = _decode_file(path)
            if d.size:
                parts.append(d * np.float32(g))
        if not parts:
            return
        buf = np.concatenate(parts)
        if bus not in ("fx", "ambient") and self.voice_fx == "phone":
            buf = _phone(buf)                                # call-centre: phone the whole voice chain
        clip = _Clip(buf, 1.0)
        with self._lock:
            if bus == "fx":
                self._fx.append(clip)
            elif bus == "ambient":
                self._ambient.append(clip)
            else:
                self._voiceq.append(clip)

    def pump_events(self) -> None:
        """Ingest cross-process play-events (worker/adapter → mixer)."""
        try:
            files = sorted(_EVENTS.glob("*.json"))
        except Exception:
            return
        for f in files:
            try:
                ev = json.loads(f.read_text())
            except Exception:
                ev = None
            try:
                f.unlink()
            except OSError:
                pass
            if not ev:
                continue
            self.play(ev.get("path", ""), bus=ev.get("bus", "voice"),
                      gain=float(ev.get("gain", 1.0)), rate=float(ev.get("rate", 1.0)),
                      effect=ev.get("effect", ""), pre=float(ev.get("pre", 0.0)))
            if ev.get("ephemeral"):
                try:
                    Path(ev["path"]).unlink()
                except OSError:
                    pass

    def stop(self) -> None:
        self._decoder.stop.set()
        if self._stream is not None:
            try:
                self._stream.stop(); self._stream.close()
            except Exception:
                pass


class StreamKeeper:
    """Keeps the mixer's output stream open across transient PortAudio failures and
    device switches — with **bounded backoff**, so a failed (re)open never kills the
    controller and never busy-loops.

    ``start()`` the first time (it also starts the decoder); ``reopen()`` thereafter to
    rebind to the current default device. A failure schedules a capped exponential retry;
    success resets the backoff. Failures stay observable (one log line each) without a
    tight error loop.
    """

    def __init__(self, mixer: "Mixer", cap: float = 30.0):
        self.mixer = mixer
        self.cap = cap
        self.attempts = 0
        self.live = False
        self.next_try = 0.0
        self._started = False

    def ensure(self, now: float) -> bool:
        """(Re)open the stream if it's down and the backoff has elapsed. Returns liveness."""
        import sys
        if self.live or now < self.next_try:
            return self.live
        try:
            if not self._started:
                self._started = True
                self.mixer.start()
            else:
                self.mixer.reopen()
            if self.attempts:
                print(f"[nhj] audio stream recovered after {self.attempts} attempt(s)",
                      file=sys.stderr)
            self.attempts = 0
            self.live = True
        except Exception as e:
            self.attempts += 1
            backoff = min(self.cap, 0.5 * 2 ** min(self.attempts, 6))
            self.next_try = now + backoff
            self.live = False
            print(f"[nhj] audio stream open failed (attempt {self.attempts}, "
                  f"retry in {backoff:.1f}s): {e}", file=sys.stderr)
        return self.live

    def mark_down(self) -> None:
        """Force a reopen on the next ensure() — e.g. the default output device switched."""
        self.live = False
        self.next_try = 0.0


def run_duck_controller() -> int:
    """Controller loop driving the mixer (selected by NHJ_MUZAK_PLAYER=duck). Same lifecycle
    as inference_muzak.run, but beeps/intro play on the mixer's buses and voice events from
    other processes are pumped in every tick — so it's all one timeline with auto-ducking."""
    from nhj import inference_muzak as M

    pool = M.tracks()                # may be empty → mixer still plays voice/fx one-shots
    random.shuffle(pool)
    mixer = Mixer(pool)
    _EVENTS.mkdir(parents=True, exist_ok=True)

    fx_dir = M._REPO_ROOT / "audio" / "sfx"
    on_tone = fx_dir / "call_on_hold.wav"                    # *click* going ON hold
    off_tone = fx_dir / "call_off_hold.wav"                  # *click* coming OFF hold (own tone)
    transfer = fx_dir / "transfer.wav"                       # legacy beep (fallback if no tone)
    handset_dir = fx_dir / "handset"                         # pickup/putdown clunks (shared pool)
    failed_xfer = fx_dir / "failed_transfer.wav"             # Jan fumbles the transfer (rare gag)
    intro_dir = M._REPO_ROOT / "audio" / "voice" / "intros"

    def _on(var: str, default: str = "1") -> bool:
        return os.getenv(var, default).strip().lower() not in ("0", "off", "false", "no")

    def _f(var: str, default: str) -> float:
        try:
            return float(os.getenv(var, default))
        except ValueError:
            return float(default)

    def _mode() -> str:
        """normal | agent-vibes (no hold music) | call-centre (phone-line sim). NHJ_MODE env
        wins, else the `audio_mode` setting (nhj mode …), else normal."""
        m = os.getenv("NHJ_MODE")
        if not m:
            try:
                from nhj.state import get_setting
                m = get_setting("audio_mode", "normal")
            except Exception:
                m = "normal"
        m = str(m or "normal").strip().lower().replace("_", "-")
        if m in ("agent-vibes", "agentvibes", "vibes"):
            return "agent-vibes"
        if m in ("call-centre", "callcentre", "call-center", "callcenter"):
            return "call-centre"
        return "normal"

    def _ambient() -> str:
        try:
            from nhj.state import get_setting
            bed = get_setting("ambient_mode", "")
        except Exception:
            bed = ""
        return str(bed or "").strip().lower().replace("_", "-")

    def _rave() -> bool:
        """Rave party: continuous grab-bag at full volume (skip the long-session
        floor). Still ducks under the TTS line via the voice sidechain, so alerts
        stay audible."""
        try:
            from nhj.state import get_flag
            return get_flag("muzak_rave", False)
        except Exception:
            return False

    def _handset_clip() -> str | None:
        """A random clunk from the shared pool — serves as both pickup and putdown."""
        if not _on("NHJ_HANDSET"):
            return None
        clips = sorted(handset_dir.glob("*.wav")) if handset_dir.is_dir() else []
        return str(random.choice(clips)) if clips else None

    def _onhold():
        """Going ON hold: handset putdown → hold tone, one gapless clip, then music resumes."""
        seq: list = []
        hs = _handset_clip()
        if hs and _on("NHJ_PUTDOWN"):                        # set the receiver down…
            seq.append((hs, float(os.getenv("NHJ_HANDSET_VOLUME", "0.9"))))
        tone = on_tone if on_tone.exists() else transfer
        if _on("NHJ_MUZAK_TRANSFER") and tone.exists():      # …*click*, on hold
            seq.append((str(tone), float(os.getenv("NHJ_MUZAK_TRANSFER_VOLUME", "0.85"))))
        if seq:
            mixer.play_sequence(seq, bus="fx")

    def _offhold():
        """Coming off hold — the music has just frozen and a character may be about to speak.
        ALWAYS: call_off_hold click → handset pickup clunk, played as ONE gapless clip butted
        straight onto the silence. It goes on the SEQUENTIAL voice bus so the vibe line (when
        its TTS lands) follows on the same queue with no gap — or it's just silence if no vibe
        fires. A rare botch (failed_transfer) replaces the pair."""
        if (_on("NHJ_FAILED_TRANSFER", "1") and failed_xfer.exists()
                and random.random() < float(os.getenv("NHJ_FAILED_TRANSFER_PROB", "0.05"))):
            mixer.play(str(failed_xfer), bus="voice",
                       gain=float(os.getenv("NHJ_FAILED_TRANSFER_VOLUME", "0.9")))
            return
        seq: list = []
        tone = off_tone if off_tone.exists() else (on_tone if on_tone.exists() else transfer)
        if _on("NHJ_MUZAK_TRANSFER") and tone.exists():      # *click*, off hold…
            seq.append((str(tone), float(os.getenv("NHJ_MUZAK_TRANSFER_VOLUME", "0.85"))))
        hs = _handset_clip()
        if hs:                                               # …handset pickup
            seq.append((hs, float(os.getenv("NHJ_HANDSET_VOLUME", "0.9"))))
        if seq:
            mixer.play_sequence(seq, bus="voice")            # gapless; vibe voice queues right after

    def _intro():
        if os.getenv("NHJ_MUZAK_INTRO", "1").lower() in ("0", "off", "false", "no"):
            return
        clips = sorted(intro_dir.glob("*.wav")) if intro_dir.is_dir() else []
        if clips:
            mixer.play(str(random.choice(clips)), bus="voice",
                       gain=float(os.getenv("NHJ_MUZAK_INTRO_VOLUME", "0.9")))

    def _openmic() -> bool:
        """Jan forgets to hit hold and you overhear her slagging you off (to Bazza / the room)
        before the line-click cuts her short. Rare; fourth-wall charm. True if one played."""
        try:
            from nhj import scenes
            om = scenes.maybe_pick_open_mic()
            if not om:
                return False
            clip = scenes.openmic_clip(om["key"])
            if not clip:
                return False
            mixer.play(clip, bus="voice", gain=0.97)             # overheard, no 'hold please' framing
            return True
        except Exception:
            return False

    def _scene() -> bool:
        """Occasionally Jan goes off on a little vignette (with an ambient bed) instead of
        the plain 'hold please'. Rare; pure charm. Returns True if a scene played."""
        try:
            from nhj import scenes
            sc = scenes.maybe_pick()
            if not sc:
                return False
            line = scenes.scene_clip(sc["key"])
            if not line:
                return False
            amb = scenes.ambient_clip(sc.get("ambient", ""))
            if amb:
                mixer.play(amb, bus="ambient", gain=0.55)   # bed under the line + music
            mixer.play(line, bus="voice", gain=0.95)
            return True
        except Exception:
            return False

    M._patch(controller_pid=os.getpid(), child_pid=None, stop=False)
    on_hold = False                                          # in an active inference (music playing)?
    active_since = None
    idle_since = time.monotonic()
    rave_adapter = None                                      # lazy AWTRIX adapter for rave display cycling
    last_party = 0.0
    active_ambient = None                                    # looped mode bed currently feeding the ambient bus

    # follow the system default OUTPUT device — reopen the stream when the user switches
    # output (PortAudio binds the stream at open; it won't migrate on its own).
    _devbox = [_default_output_device()]
    _dev_changed = threading.Event()
    if _on("NHJ_FOLLOW_DEVICE", "1"):
        def _watch_device():
            while True:
                time.sleep(_f("NHJ_FOLLOW_DEVICE_POLL", "4"))
                d = _default_output_device()
                if d and d != _devbox[0]:
                    _devbox[0] = d
                    _dev_changed.set()
        threading.Thread(target=_watch_device, daemon=True).start()
    keeper = StreamKeeper(mixer)
    try:
        while True:
            now = time.monotonic()
            if _dev_changed.is_set():                        # output device switched → follow it
                _dev_changed.clear()
                keeper.mark_down()
            # (Re)open the stream with bounded backoff — covers the initial open AND a failed
            # device-reopen, retried until it succeeds even if the device name doesn't change
            # again. Never kills the controller; queued voice keeps buffering meanwhile.
            keeper.ensure(now)
            mixer.pump_events()                              # ingest voice from other processes
            mixer.duck_external = M.duck_gain() < 1.0

            # rave: actively cycle the display party tile (this loop is the heartbeat)
            if _rave() and now - last_party >= _f("NHJ_RAVE_DISPLAY_INTERVAL", "8"):
                last_party = now
                try:
                    from nhj.adapters.ulanzi import UlanziAdapter
                    if rave_adapter is None:
                        rave_adapter = UlanziAdapter()
                    rave_adapter.refresh_rave()
                except Exception:
                    pass

            mode = _mode()                                   # live: normal | agent-vibes | call-centre
            music_on = mode != "agent-vibes"
            mixer.voice_fx = "phone" if mode == "call-centre" else ""
            mixer.music_phone = mode == "call-centre"        # tinny hold music in call-centre
            ambient = _ambient()
            if ambient != active_ambient:
                active_ambient = ambient
                bed = resources.audio_dir() / "ambient" / f"{ambient}.wav" if ambient else None
                mixer.set_ambient_loop(str(bed) if bed and bed.exists() else None,
                                       gain=_f("NHJ_MODE_AMBIENT_VOLUME", "0.5"))
            if not music_on and not mixer.paused:            # switched to agent-vibes mid-music
                mixer.pause()

            st = M._read()
            if st.get("stop") or st.get("controller_pid") != os.getpid():
                break

            if not M._should_play():                         # all sessions idle
                if on_hold:                                  # → just came OFF hold
                    on_hold = False
                    active_since = None
                    mixer.music_floor = 1.0
                    if not mixer.paused:
                        mixer.pause()
                    _offhold()                               # *click* off hold + handset pickup → vibe
                idle_since = idle_since or now
                if now - idle_since > M._IDLE_STOP_S:
                    break
                time.sleep(0.12)
                continue

            idle_since = None
            if not on_hold:                                  # → just went ON hold
                on_hold = True
                active_since = now
                if music_on:                                 # agent-vibes stays silent until the verdict
                    if not _scene():                         # rare easter-egg vignette, else…
                        if not _openmic():                   # rare hot-mic aside, else…
                            _intro()                         # "hold please"
                    _onhold()                                # …putdown clunk + *click* going on hold
                    if mixer.paused:
                        mixer.resume()

            # long-session ducking: ease the muzak floor down over a long think, snap back on idle.
            # Rave stays at full volume (but the voice sidechain still ducks it under the alert).
            if _rave():
                mixer.music_floor = 1.0
            elif music_on and _on("NHJ_LONGHOLD") and active_since is not None:
                el = now - active_since
                after, ramp = _f("NHJ_LONGHOLD_AFTER", "5"), max(0.1, _f("NHJ_LONGHOLD_RAMP", "4"))
                if el <= after:
                    mixer.music_floor = 1.0
                else:
                    frac = min(1.0, (el - after) / ramp)
                    mixer.music_floor = 1.0 - frac * (1.0 - _f("NHJ_LONGHOLD_DUCK", "0.15"))
            time.sleep(0.05)
    finally:
        mixer.stop()
        d = M._read()
        if d.get("controller_pid") == os.getpid():
            d.pop("controller_pid", None); d.pop("child_pid", None); d.pop("stop", None)
            M._write(d)
    return 0
