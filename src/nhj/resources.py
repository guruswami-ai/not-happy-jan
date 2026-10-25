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
import stat
from pathlib import Path

APP_NAME = "not-happy-jan"
LEGACY_APP_NAME = "nhj"
CLAUDE_SKILL_NAME = "not-happy-jan"
BUNDLED_SKILLS_DIR = "claude-skills"

_PKG = Path(__file__).resolve().parent          # .../nhj  (src/nhj in a checkout, site-packages/nhj in a wheel)
_CHECKOUT_ROOT = _PKG.parents[1]                 # repo root when running from a src/ checkout
_ENV_LOADED = False


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
        # A full disk or a permissions error must not break the caller.
        return


def _stderr_raw_path() -> str:
    """Path currently attached to stderr, or "" when it cannot be named.

    Linux exposes it through ``/proc/self/fd/2``. macOS does not, and
    ``readlink("/dev/fd/2")`` fails there; ``F_GETPATH`` is the call that
    returns the path a LaunchAgent opened.
    """
    proc = "/proc/self/fd/2"
    if os.path.exists(proc):
        try:
            raw = os.readlink(proc)
        except OSError:
            raw = ""
        if raw:
            return raw
    getpath = getattr(fcntl, "F_GETPATH", None)
    if getpath is not None:
        try:
            raw = fcntl.fcntl(2, getpath, bytes(1024))
        except (OSError, OverflowError, TypeError):
            raw = b""
        if isinstance(raw, bytes):
            text = raw.split(b"\0", 1)[0].decode(errors="surrogateescape")
        else:
            text = str(raw).split("\0", 1)[0]
        if text:
            return text
    try:
        return os.readlink("/dev/fd/2")
    except OSError:
        return ""


def _attached_log(name: str) -> Path | None:
    """Regular file on stderr, when it is this server's log under ``log_dir``.

    The on-demand supervisor opens ``{name}-server.log``. A LaunchAgent opens
    ``{name}.log`` before the process starts. A pipe, a socket, a tty,
    ``/dev/null``, or any other file is left alone.
    """
    try:
        if not stat.S_ISREG(os.fstat(2).st_mode):
            return None
    except OSError:
        return None
    raw = _stderr_raw_path()
    if not raw or raw.startswith(("pipe:", "socket:", "anon_inode:")):
        return None
    if raw.endswith(" (deleted)") or not Path(raw).is_absolute():
        return None
    try:
        path = Path(raw).resolve()
        path.relative_to(log_dir(create=False).resolve())
    except (OSError, ValueError):
        return None
    if path.name not in {f"{name}.log", f"{name}-server.log"} or not path.is_file():
        return None
    return path


def rotate_attached_log(name: str) -> None:
    """Reopen stdout and stderr when the attached log is already over the cap.

    Renaming the path leaves the existing descriptor on the old inode, so this
    rotates that file and points both streams at the new one. ``name`` selects
    ``{name}.log`` (LaunchAgent) or ``{name}-server.log`` (supervisor).

    llama.cpp holds its own descriptor. Calling this in the parent before the
    child starts retargets the child. A later rename does not.
    """
    path = _attached_log(name)
    if path is None:
        return
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
            # Owner-only. NHJ_DEBUG logs can contain the spoken text.
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.dup2(fd, 1)
                os.dup2(fd, 2)
            finally:
                os.close(fd)
    except OSError:
        return


def reopen_stdio_log(path: str | Path) -> None:
    """Point stdout/stderr at *path* when this process still holds an old inode.

    The worker inherits ``worker.log`` from the spawner. ``append_log`` in the
    parent (or this process) may rename that file; the inherited descriptor
    stays on the old inode. A tty, pipe or socket is left alone.

    Also rotates and reopens when the named file is already over the cap.
    """
    path = Path(path)
    try:
        if not stat.S_ISREG(os.fstat(2).st_mode):
            return
    except OSError:
        return
    try:
        err = os.fstat(2)
        st = path.stat()
        if (err.st_dev, err.st_ino) == (st.st_dev, st.st_ino) and st.st_size < _LOG_MAX_BYTES:
            return
    except OSError:
        pass
    try:
        with _log_lock(path):
            rotate_log(path)
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
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


def load_env() -> bool:
    """Load NHJ's ``.env`` once for this process.

    Entry points call this explicitly before reading environment-backed
    settings. Importing NHJ library modules does not call it. Variables
    already present in the process environment win (``override=False``).
    Returns True only when this call loaded an existing file.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return False
    from dotenv import load_dotenv

    loaded = bool(load_dotenv(env_file(), override=False))
    _ENV_LOADED = True
    return loaded
