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


def settings() -> dict:
    from nhj import config
    import os
    cfg = dict(config.CFG.get("music") or {})
    env = os.getenv("NHJ_MUSIC_MODE", "").strip().lower()
    if env:
        cfg["mode"] = env
    return cfg


def default_pack_dir() -> Path:
    return resources.data_dir() / "music_packs" / "default"


def pack_dir(cfg: dict) -> Path:
    return Path(cfg["pack"]).expanduser() if cfg.get("pack") else default_pack_dir()


def build_default_pack_async(dest: Path) -> None:
    def _go():
        try:
            from nhj.music import synth
            synth.build_default_pack(dest)
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
        if not cfg.get("pack"):
            build_default_pack_async(dest)               # ready from the next session
        resources.debug(f"music: no usable pack at {dest}; playing recorded tracks")
        return None
    from nhj.music.continuation import Composer
    from nhj.music.source import StemSource
    director = Director(long_after_s=float(cfg.get("long_after_s", 30)), long_ramp_s=float(cfg.get("long_ramp_s", 30)))
    composer = Composer(pack, cfg.get("continuation") or {})
    return (lambda buf, paused: StemSource(pack, director, buf, paused, composer)), director


def busy_count(M) -> int:
    """Sessions mid-turn; a manual `muzak start` / rave session counts as a full arrangement."""
    ids = M._fresh_busy(M._read())
    real = sum(1 for s in ids if s != M._MANUAL)
    return max(real, MANUAL_BUSY) if M._MANUAL in ids else real


def note_accent(M, intent: str) -> None:
    """Explicit accent request (`nhj music accent`); cross-process via the muzak state, not mode-gated.
    Hooks use `inference_muzak.note_accent`, which is gated and numpy-free."""
    M._patch(music_accent=intent, music_accent_ts=time.time())


def feed(M, director: Director, active_since: float | None, now: float, last_accent_ts: float) -> float:
    """One controller tick: push busy count, elapsed time and any new accent. Returns the newest
    accent timestamp seen (pass it back next tick)."""
    director.update(busy_count(M), None if active_since is None else now - active_since)
    st = M._read()
    ts = float(st.get("music_accent_ts") or 0.0)
    if ts > last_accent_ts:
        if time.time() - ts <= M.ACCENT_TTL_S:
            director.accent(str(st.get("music_accent") or ""))
        return ts
    return last_accent_ts
