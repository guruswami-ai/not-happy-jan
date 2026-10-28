"""Glue between the duck controller and the music package: settings, pack discovery, state feed.

Kept out of duck_player so the opt-in stays one `if` there. Returns None whenever the
generative mode is off or no pack is ready, and the controller then plays recorded tracks.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from nhj import resources
from nhj.music.director import MANUAL_BUSY, Director
from nhj.music.pack import PackError, load_pack


DEFAULT_ACCENT_GAIN = 0.5        # stinger level (peak 1.0 x this); about the voice level at the onset, mostly outside speech bands
DEFAULT_ACCENT_TTL_S = 8.0       # used when the muzak module (or a test double) does not define ACCENT_TTL_S


def settings() -> dict:
    from nhj import config
    import os
    cfg = dict(config.get().get("music") or {})
    env = os.getenv("NHJ_MUSIC_MODE", "").strip().lower()
    if env:
        cfg["mode"] = env
    return cfg


def default_pack_dir() -> Path:
    return resources.data_dir() / "music_packs" / "default"


def pack_style(cfg: dict) -> str:
    """The built-in style a config selects: a bare style name in music.pack, else the original trance pack."""
    from nhj.music import styles
    return cfg["pack"] if styles.is_style(cfg.get("pack")) else "trance"


def pack_dir(cfg: dict) -> Path:
    """music.pack is a directory, or a bare built-in style name (lofi-rhodes, ...) kept in the data dir."""
    from nhj.music import styles
    raw = cfg.get("pack")
    if not raw:
        return default_pack_dir()
    if styles.is_style(raw):
        return resources.data_dir() / "music_packs" / styles.pack_subdir(raw)
    return Path(raw).expanduser()


def build_default_pack_async(dest: Path, style: str = "trance") -> None:
    def _go():
        try:
            from nhj.music import styles
            styles.build(dest, style)
        except Exception:
            resources.debug("music: default pack build failed")
    threading.Thread(target=_go, daemon=True).start()


def make_source(cfg: dict | None = None):
    """(source_factory, director) for the Mixer, or None to fall back to recorded tracks."""
    cfg = settings() if cfg is None else cfg
    if str(cfg.get("mode", "recorded")).lower() != "generative":
        return None
    dest = pack_dir(cfg)
    try:
        pack = load_pack(dest)
    except PackError:
        from nhj.music import styles
        if not cfg.get("pack") or styles.is_style(cfg.get("pack")):
            style = pack_style(cfg)                              # ready from the next session
            build_default_pack_async(dest) if style == "trance" else build_default_pack_async(dest, style)
        resources.debug(f"music: no usable pack at {dest}; playing recorded tracks")
        return None
    from nhj.music.continuation import Composer
    from nhj.music.source import StemSource
    director = Director(long_after_s=float(cfg.get("long_after_s", 30)), long_ramp_s=float(cfg.get("long_ramp_s", 30)),
                        arrangement=pack.arrangement)
    composer = Composer(pack, cfg.get("continuation") or {})
    accent_gain = float(cfg.get("accent_gain", DEFAULT_ACCENT_GAIN))
    return (lambda buf, paused: StemSource(pack, director, buf, paused, composer, accent_gain)), director


def busy_count(M, state=None) -> int:
    """Sessions mid-turn; a manual `muzak start` / rave session counts as a full arrangement.

    *state* is a music.json snapshot. The controller passes the one it just read.
    """
    ids = M._fresh_busy(M._read() if state is None else state)
    real = sum(1 for s in ids if s != M._MANUAL)
    return max(real, MANUAL_BUSY) if M._MANUAL in ids else real


def note_accent(M, intent: str) -> None:
    """Explicit accent request (`nhj music accent`); cross-process via the muzak state, not mode-gated.
    Hooks use `inference_muzak.note_accent`, which is gated and numpy-free."""
    M._patch(music_accent=intent, music_accent_ts=time.time())


def feed(M, director: Director, active_since: float | None, now: float, last_accent_ts: float,
         state=None) -> float:
    """One controller tick: push busy count, elapsed time and any new accent. Returns the newest
    accent timestamp seen (pass it back next tick).

    *state* is the music.json snapshot for this tick. Without it the function reads the file.
    """
    st = M._read() if state is None else state
    director.update(busy_count(M, st), None if active_since is None else now - active_since)
    ts = float(st.get("music_accent_ts") or 0.0)
    if ts > last_accent_ts:
        if time.time() - ts <= getattr(M, "ACCENT_TTL_S", DEFAULT_ACCENT_TTL_S):
            director.accent(str(st.get("music_accent") or ""))
        return ts
    return last_accent_ts
