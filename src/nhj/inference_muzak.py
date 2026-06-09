"""Inference muzak — plays while *any* Claude session is burning tokens, paused when all idle.

Concurrency model (multiple sessions on one machine share one speaker): instead of a
single paused flag, the state holds a BUSY SET of session ids. Each hook carries Claude
Code's `session_id`:

    UserPromptSubmit  → mark_busy(session_id)   (this session started burning tokens)
    Stop              → mark_idle(session_id)   (this session's turn ended)
    StopFailure       → mark_idle(session_id)   (turn ended with an API error)
    SessionEnd        → mark_idle(session_id)   (session gone)

The detached controller plays while the busy set is non-empty (or continuous mode),
pauses (SIGSTOP — exact-resume) when it empties. Stale entries (a crashed session that
never fired Stop/SessionEnd) are evicted after _BUSY_TTL, so orphaned music is impossible.

Gated by the `muzak` flag (`nhj muzak on`, off by default). Tracks cycle (shuffled) from
$NHJ_MUZAK_DIR (colon-separated) + the bundled `audio/music/` dir.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from nhj import resources

# data_dir() = repo root in a checkout, else the platform data dir (macOS Application
# Support, XDG ~/.local/share elsewhere). `_REPO_ROOT / "audio"` is the music/sfx/voice
# tree in both layouts (duck_player reads it via M._REPO_ROOT).
_REPO_ROOT = resources.data_dir()
load_dotenv(resources.env_file())  # pick up NHJ_MUZAK_DIR / NHJ_MUZAK_VOLUME everywhere

_AUDIO_EXT = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".aiff", ".caf"}
_BUSY_TTL = 1800.0    # a busy entry older than this = a crashed session → evict
_IDLE_STOP_S = 600    # controller self-exits after this long with nothing burning tokens
_MANUAL = "__manual__"
_DUCK_GAIN = 0.22     # music level while ducking under speech (streaming player only)
_DUCK_MAX = 12.0      # safety: a duck never sticks longer than this many seconds


def _state_path() -> Path:
    p = os.getenv("NHJ_MUZAK_STATE")
    if p:
        return Path(p).expanduser()
    current = resources.state_file("music.json")
    legacy = resources.legacy_state_file("music.json")
    return legacy if legacy.exists() and not current.exists() else current


def _read() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {}


def _write(d: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(p)


def _update(mutate) -> dict:
    """Locked read-modify-write of the muzak state so concurrent hooks (mark_busy /
    mark_idle from different sessions) can't clobber each other's busy-set edits."""
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".json.lock")
    lock.touch()
    with open(lock, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            d = _read()
            mutate(d)
            _write(d)
            return d
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _patch(**kw) -> None:
    _update(lambda d: d.update(kw))


def _alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0); return True
    except (ValueError, TypeError, OSError):
        return False


# ---- track discovery --------------------------------------------------------
def _music_dirs() -> list[Path]:
    dirs: list[Path] = []
    for chunk in os.getenv("NHJ_MUZAK_DIR", "").split(":"):
        if chunk.strip():
            dirs.append(Path(chunk).expanduser())
    dirs.append(_REPO_ROOT / "audio" / "music")  # bundled fallback
    return dirs


def tracks() -> list[Path]:
    found: list[Path] = []
    for d in _music_dirs():
        if d.is_dir():
            found += [p for p in d.iterdir() if p.suffix.lower() in _AUDIO_EXT]
    return sorted(found)


# ---- busy-set / play decision ----------------------------------------------
def _any_busy() -> bool:
    """True if any session is mid-turn. Evicts stale (crashed) entries as a side effect."""
    d = _read()
    busy = d.get("busy") or {}
    cutoff = time.time() - _BUSY_TTL
    fresh = {s: ts for s, ts in busy.items() if ts >= cutoff}
    if len(fresh) != len(busy):
        d["busy"] = fresh; _write(d)
    return bool(fresh)


def _continuous() -> bool:
    try:
        from nhj.state import get_flag
        return get_flag("muzak_continuous")
    except Exception:
        return False


def _should_play() -> bool:
    return _continuous() or _any_busy()


# ---- control surface (CLI / hooks call these) -------------------------------
def is_running() -> bool:
    return _alive(_read().get("controller_pid"))


def start_controller() -> bool:
    """Launch the detached mixer daemon if not running. True if (now) running. Starts even
    with no music tracks — the mixer still plays voice/fx through the one stream."""
    if is_running():
        return True
    subprocess.Popen([sys.executable, "-m", "nhj.inference_muzak"],
                     start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def mark_busy(session_id: str = "") -> bool:
    """A session started burning tokens (UserPromptSubmit). Ensures the muzak is playing."""
    def _m(d):
        d.setdefault("busy", {})[session_id or "default"] = time.time()
        d.pop("stop", None)
    _update(_m)
    return start_controller()


def mark_idle(session_id: str = "") -> None:
    """A session's turn ended (Stop / SessionEnd). The controller pauses once all idle."""
    def _m(d):
        if d.get("busy"):
            d["busy"].pop(session_id or "default", None)
    _update(_m)


def start() -> bool:
    """Manual play (CLI/testing) — a synthetic never-expiring session until `stop`."""
    d = _read()
    d.setdefault("busy", {})[_MANUAL] = time.time() + 10 * 365 * 86400  # far future
    d.pop("stop", None)
    _write(d)
    return start_controller()


def enable() -> bool:
    """Re-enable muzak (CLI `on`/`continuous`, mode-apply) WITHOUT forcing a play session.
    Clears any leftover `stop` flag — otherwise an `off → on` toggle leaves `stop=True` set
    and the next controller exits the instant it spawns (silencing music AND voice, which
    share the mixer). Starts the controller now only if something should already be playing
    (continuous, or a session already busy); on-hold otherwise waits for the next mark_busy."""
    _update(lambda d: d.pop("stop", None))
    return start_controller() if _should_play() else False


def stop() -> None:
    """Clear all sessions + stop the controller (CLI off/stop)."""
    d = _read()
    cpid = d.get("child_pid")
    _write({"busy": {}, "stop": True, "controller_pid": d.get("controller_pid")})
    if _alive(cpid):
        try: os.kill(int(cpid), signal.SIGCONT)  # un-pause so it can be reaped
        except OSError: pass


def status() -> dict:
    d = _read()
    busy = d.get("busy") or {}
    cutoff = time.time() - _BUSY_TTL
    sessions = [s for s, ts in busy.items() if ts >= cutoff and s != _MANUAL]
    running = _alive(d.get("controller_pid"))
    return {
        "running": running,
        "playing": running and _should_play(),
        "sessions": len(sessions),
        "tracks": len(tracks()),
    }


# Intro ("hold please"), transfer beep, and return ("you still there?") clips are played
# directly on the mixer's buses — see duck_player.run_duck_controller (beeps/intro) and
# adapters.audio (return clip). All audio goes through the one streaming mixer; nothing
# here spawns afplay.


# ---- ducking (the streaming mixer honours these cross-process) ---------------
def duck() -> None:
    """Lower the music under speech/beeps. Honoured by the av+sounddevice mixer. (Mostly
    vestigial now that voice/fx route through the mixer and trigger an automatic sidechain
    duck; kept for explicit/cross-process ducking.)"""
    _patch(ducked=True, duck_until=time.time() + _DUCK_MAX)


def unduck() -> None:
    _patch(ducked=False, duck_until=0.0)


def duck_gain() -> float:
    """Current music gain multiplier: _DUCK_GAIN while a (non-expired) duck is held, else 1.0."""
    d = _read()
    if d.get("ducked") and time.time() < float(d.get("duck_until") or 0.0):
        try:
            return float(os.getenv("NHJ_MUZAK_DUCK_GAIN", str(_DUCK_GAIN)))
        except ValueError:
            return _DUCK_GAIN
    return 1.0


# ---- single-stream mixer routing (cross-process) ----------------------------
def submit_audio(path: str, bus: str = "voice", gain: float = 1.0,
                 rate: float = 1.0, effect: str = "", ephemeral: bool = False) -> bool:
    """Hand a clip to the streaming mixer (voice/fx/ambient bus) via the event spool.
    Starts the mixer daemon if it isn't already running, so ALL audio goes through the one
    player — there is no afplay fallback. Non-blocking; returns False only on write error."""
    start_controller()                       # ensure the mixer owns the output
    try:
        d = resources.audio_events_dir()
        d.mkdir(parents=True, exist_ok=True)
        ev = {"path": str(path), "bus": bus, "gain": gain, "rate": rate,
              "effect": effect, "ephemeral": ephemeral}
        (d / f"{int(time.time()*1000)}-{os.getpid()}.json").write_text(json.dumps(ev))
        # prune stray temp wavs so the spool dir can't grow unbounded
        tmp = resources.audio_tmp_dir()
        if tmp.is_dir():
            old = sorted(tmp.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)[80:]
            for o in old:
                try: o.unlink()
                except OSError: pass
        return True
    except Exception:
        return False


# ---- the controller loop (runs detached: `python -m nhj.inference_muzak`) ----
def run() -> int:
    """All audio runs through the uv-native streaming mixer (av+sounddevice). One engine,
    one timeline — music bed + beeps + voice on shared buses with automatic ducking."""
    from nhj.procname import set_title
    set_title("NHJ muzak")
    from nhj.duck_player import run_duck_controller
    return run_duck_controller()


if __name__ == "__main__":
    sys.exit(run())
