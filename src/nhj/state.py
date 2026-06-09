"""Runtime state — live settings you can flip without editing config or restarting.

`nhj set jan.competence 7` / `nhj muzak on` / `nhj mute` write here; the worker and
characters read it on the next vibe (worker is fresh-spawned per cycle). Layered
ON TOP of config/default.yaml: state overrides config overrides defaults.

Path: $NHJ_STATE_FILE or the platform state dir (macOS Application Support, XDG state
elsewhere), with a read fallback from legacy ~/.config/nhj/state.json.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from nhj import resources


def _path() -> Path:
    p = os.getenv("NHJ_STATE_FILE")
    return Path(p).expanduser() if p else resources.state_file("state.json")


def _read_path() -> Path:
    p = _path()
    if p.exists() or os.getenv("NHJ_STATE_FILE"):
        return p
    legacy = resources.legacy_state_file("state.json")
    return legacy if legacy.exists() else p


def load() -> dict:
    try:
        return json.loads(_read_path().read_text())
    except Exception:
        return {}


def save(state: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    unique = f".{os.getpid()}.{os.urandom(4).hex()}.json.tmp"
    tmp = p.with_suffix(unique)
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(p)


def update(mutate) -> dict:
    """Read-modify-write the state file under an exclusive flock so two concurrent
    writers (two Claude sessions' hooks, or `nhj set` racing the muzak controller)
    can't clobber each other. `mutate(state)` edits the loaded dict in place."""
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".json.lock")
    lock.touch()
    with open(lock, "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            s = load()
            mutate(s)
            save(s)
            return s
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---- dials (per character: level / chaos / ...) -----------------------------
def resolve_dial(dial: str, value: int):
    """Map a friendly dial name → internal (field, stored, shown).

    Intensity (ockerism/stress/karren/level) → `level` 1-11.
    competence → inverts `chaos` (competence 10 = chaos 0 = flawless).
    Single source of truth shared by `nhj set` and the mode macros."""
    dial = dial.lower()
    if dial in ("competence", "comp"):
        v = max(1, min(10, value));  return "chaos", 10 - v, v
    if dial in ("ockerism", "ocker", "bogan", "stress", "karren", "intensity", "level"):
        v = max(1, min(11, value));  return "level", v, v
    if dial == "chaos":
        v = max(0, min(10, value));  return "chaos", v, v
    v = max(0, min(11, value));      return dial, v, v


def get_dial(character: str, key: str):
    return (load().get("dials", {}).get(character, {}) or {}).get(key)


def set_dial(character: str, key: str, value: int) -> None:
    def _m(s):
        s.setdefault("dials", {}).setdefault(character, {})[key] = int(value)
    update(_m)


# ---- flags (mute / hold / ...) ----------------------------------------------
def get_flag(key: str, default: bool = False) -> bool:
    try:
        from nhj.config import section
        default = section("flags").get(key, default)
    except Exception:
        pass
    return bool(load().get("flags", {}).get(key, default))


def set_flag(key: str, value: bool) -> None:
    def _m(s):
        s.setdefault("flags", {})[key] = bool(value)
    update(_m)


# ---- settings (string values: censor mode, ...) -----------------------------
def get_setting(key: str, default=None):
    return load().get("settings", {}).get(key, default)


def set_setting(key: str, value) -> None:
    def _m(s):
        s.setdefault("settings", {})[key] = value
    update(_m)
