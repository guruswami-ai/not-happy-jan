"""Filesystem locations that work in BOTH a source checkout and a wheel install.

The codebase used to derive everything from ``Path(__file__).parents[2]`` — the
repo root in a `src/` checkout, but garbage once installed into `site-packages`.
This module is the single place that knows where things live:

- :func:`bundled` — immutable package data shipped *inside* the wheel (config,
  hooks, character ``.yaml`` defs). In a checkout these live at the repo root; in
  a wheel they're force-included under ``nhj/_bundled/`` (see pyproject).
- :func:`data_dir` — user-writable root for *downloaded* media (voice ref clips,
  audio). In a checkout it stays the repo root so dev + ``nhj setup-media`` are
  unchanged unless overridden. Installed defaults follow macOS Application Support
  or XDG data conventions.
- :func:`config_dir` — user config (macOS Application Support or XDG config).
- :func:`state_dir` — mutable runtime state (macOS Application Support or XDG state).
- :func:`cache_dir` — re-creatable cached/generated data.
- :func:`tts_model_cache_dir` — Hugging Face cache root for Qwen3-TTS weights.
- :func:`llm_model_dir` — local GGUF directory for ocker-bogan-nano.
- :func:`log_dir` — user-visible diagnostics.
- :func:`env_file` — ``<repo>/.env`` in a checkout, else ``config_dir(create=False)/.env``.

Override roots with ``NHJ_DATA_DIR`` / ``NHJ_CONFIG_DIR`` / ``NHJ_STATE_DIR`` /
``NHJ_CACHE_DIR`` / ``NHJ_LOG_DIR``.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import platform
from pathlib import Path

APP_NAME = "not-happy-jan"
LEGACY_APP_NAME = "nhj"
CLAUDE_SKILL_NAME = "not-happy-jan"
BUNDLED_SKILLS_DIR = "claude-skills"

_PKG = Path(__file__).resolve().parent          # .../nhj  (src/nhj in a checkout, site-packages/nhj in a wheel)
_CHECKOUT_ROOT = _PKG.parents[1]                 # repo root when running from a src/ checkout


def _is_checkout() -> bool:
    """True when running from a source tree (editable install or `python src/...`)."""
    return (_CHECKOUT_ROOT / "pyproject.toml").exists()


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _mkdir(path: Path, create: bool) -> Path:
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _override_path(env_name: str) -> Path | None:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else None


def user_tag() -> str:
    """A per-user identifier for partitioning shared temp paths (e.g. /tmp logs).

    Portable: ``os.getuid()`` is Unix-only, so fall back to the username on platforms
    without it — importing a module that builds a per-user path must never crash.
    """
    try:
        return str(os.getuid())
    except AttributeError:                       # e.g. Windows
        return os.environ.get("USERNAME") or os.environ.get("USER") or "default"


def app_support_dir(create: bool = True) -> Path:
    override = _override_path("NHJ_APP_SUPPORT_DIR")
    if override:
        return _mkdir(override, create)
    if _is_macos():
        return _mkdir(Path.home() / "Library" / "Application Support" / APP_NAME, create)
    return config_dir(create=create)


def legacy_config_dir() -> Path:
    return Path.home() / ".config" / LEGACY_APP_NAME


def bundled(rel: str) -> Path:
    """Resolve a read-only bundled resource, e.g. ``bundled('config/default.yaml')``."""
    if _is_checkout():
        p = _CHECKOUT_ROOT / rel
        if p.exists():
            return p
    return _PKG / "_bundled" / rel               # wheel layout (caller handles a missing file)


def data_dir(create: bool = True) -> Path:
    """User-writable root for downloaded media."""
    override = _override_path("NHJ_DATA_DIR")
    if override:
        d = override
    elif _is_checkout():
        return _CHECKOUT_ROOT                     # dev: media stays in the repo (gitignored)
    elif _is_macos():
        d = app_support_dir(create=False)
    else:
        d = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_NAME
    return _mkdir(d, create)


def config_dir(create: bool = True) -> Path:
    """User config root; create it only when a caller intends to write."""
    override = _override_path("NHJ_CONFIG_DIR")
    if override:
        base = override
    elif _is_macos():
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / APP_NAME
    return _mkdir(base, create)


def state_dir(create: bool = True) -> Path:
    override = _override_path("NHJ_STATE_DIR")
    if override:
        base = override
    elif _is_macos():
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / APP_NAME
    return _mkdir(base, create)


def cache_dir(create: bool = True) -> Path:
    override = _override_path("NHJ_CACHE_DIR")
    if override:
        base = override
    elif _is_macos():
        base = Path.home() / "Library" / "Caches" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / APP_NAME
    return _mkdir(base, create)


def tts_model_cache_dir(create: bool = True) -> Path:
    """Hugging Face cache root used by the Qwen3-TTS server."""
    return _mkdir(cache_dir(create=False) / "tts-models", create)


def llm_model_dir(create: bool = True) -> Path:
    """Directory for the ocker-bogan-nano GGUF downloaded by ``nhj install-model``."""
    return _mkdir(cache_dir(create=False) / "ocker-bogan-nano", create)


def log_dir(create: bool = True) -> Path:
    override = _override_path("NHJ_LOG_DIR")
    if override:
        base = override
    elif _is_macos():
        base = Path.home() / "Library" / "Logs" / APP_NAME
    else:
        base = state_dir(create=False) / "logs"
    return _mkdir(base, create)


def state_file(name: str) -> Path:
    return state_dir(create=False) / name


def legacy_state_file(name: str) -> Path:
    return legacy_config_dir() / name


def log_file(name: str) -> str:
    return str(log_dir(create=False) / name)


# hook.log, worker.log and the model-server logs. Three older files, then drop.
_LOG_MAX_BYTES = 1_000_000
_LOG_BACKUPS = 3


def rotate_log(path: Path, max_bytes: int | None = None, backups: int | None = None) -> None:
    """If *path* is at least max_bytes, shift it to path.1 .. path.{backups}.

    path.{backups} is removed. A missing file, or any OSError, is ignored so
    logging can never break the caller. The caller should hold the log lock
    when another process might be writing the same file.
    """
    if max_bytes is None:
        max_bytes = _LOG_MAX_BYTES
    if backups is None:
        backups = _LOG_BACKUPS
    try:
        if backups < 1 or not path.is_file() or path.stat().st_size < max_bytes:
            return
        path.with_name(f"{path.name}.{backups}").unlink(missing_ok=True)
        for i in range(backups - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            if src.exists():
                os.replace(src, path.with_name(f"{path.name}.{i + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except OSError:
        return


@contextlib.contextmanager
def _log_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f"{path.name}.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def append_log(path: str | Path, line: str) -> None:
    """Append one line, rotating first when the file is already at the cap."""
    path = Path(path)
    text = line if line.endswith("\n") else line + "\n"
    try:
        with _log_lock(path):
            rotate_log(path)
            with open(path, "a") as handle:
                handle.write(text)
    except OSError:
        pass


def rotate_attached_log(name: str) -> None:
    """Rotate ``{name}-server.log`` when stderr is that file and it is over the cap.

    The supervisor hands the server process the open log as stdout and stderr.
    Renaming the path leaves that fd on the old inode, so this reopens both
    streams onto a fresh file. A tty, or any other stderr, is left alone.
    An external binary (llama.cpp) keeps the fd it was started with; its log
    rotates the next time the supervisor opens it.
    """
    path = Path(log_file(f"{name}-server.log"))
    try:
        err = os.fstat(2)
        st = path.stat()
    except OSError:
        return
    if (err.st_dev, err.st_ino) != (st.st_dev, st.st_ino) or err.st_size < _LOG_MAX_BYTES:
        return
    try:
        with _log_lock(path):
            # Recheck inside the lock. Another writer may have rotated already.
            try:
                err = os.fstat(2)
                st = path.stat()
            except OSError:
                return
            if (err.st_dev, err.st_ino) != (st.st_dev, st.st_ino) or err.st_size < _LOG_MAX_BYTES:
                return
            rotate_log(path)
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o644)
            try:
                os.dup2(fd, 1)
                os.dup2(fd, 2)
            finally:
                os.close(fd)
    except OSError:
        return


def debug(msg: str) -> None:
    """Emit a diagnostic line to stderr when NHJ_DEBUG is set; no-op otherwise.

    Lets best-effort swallow sites surface *why* they silently did nothing,
    without ever crashing the caller in normal operation.
    """
    import os
    import sys
    if os.environ.get("NHJ_DEBUG"):
        print(f"[nhj:debug] {msg}", file=sys.stderr)


def audio_events_dir(create: bool = True) -> Path:
    return _mkdir(state_dir(create=False) / "audio-events", create)


def audio_tmp_dir(create: bool = True) -> Path:
    return _mkdir(state_dir(create=False) / "audio-tmp", create)


# ---- concrete resources ---------------------------------------------------
def config_file() -> Path:
    return bundled("config/default.yaml")


def character_defs_dir() -> Path:
    """Where the bundled ``<name>/character.yaml`` persona defs live."""
    return bundled("voices")


def hooks_file() -> Path:
    return bundled("hooks/hooks.json")


def claude_skill_file() -> Path:
    """The bundled Claude Code skill definition used by `nhj install-skill`."""
    if _is_checkout():
        return _CHECKOUT_ROOT / "skills" / CLAUDE_SKILL_NAME / "SKILL.md"
    return bundled(f"{BUNDLED_SKILLS_DIR}/{CLAUDE_SKILL_NAME}/SKILL.md")


def claude_skill_install_path(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / ".claude" / "skills" / CLAUDE_SKILL_NAME / "SKILL.md"


def voices_dir() -> Path:
    """Downloaded voice reference clips (ref.wav / ref.txt)."""
    return data_dir() / "voices"


def audio_dir() -> Path:
    """Downloaded audio (music / sfx / ambient / voice)."""
    return data_dir() / "audio"


def env_file() -> Path:
    return (_CHECKOUT_ROOT / ".env") if _is_checkout() else (config_dir(create=False) / ".env")
