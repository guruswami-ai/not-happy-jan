"""Suite-wide isolation: the tests must never read or write the developer's real NHJ install.

Before this file existed the suite shared the machine's real state/log/cache/config directories and
inherited its environment (a live ``.env``, ``voice_override`` in ``state.json``, a Vestaboard URL in
``config.yaml``). Consequences: a test that passed in CI failed on the maintainer's host (it read the
live ``voice_override``), tests wrote lines into the real ``worker.log``/``hook.log``, and tests that
start a worker acted on the real queue.

Two layers:

1. At **import time** (here, before any test module imports ``nhj``) the four mutable directories are
   pointed at a throwaway session directory, and ``HOME`` and the XDG directories move there too (the
   legacy config dir and ``~/.env`` come from the real home). This still has to happen before collection:
   a few modules outside the #61 lazy-path set resolve data directories while importing, and subprocesses
   spawned by tests inherit these session directories. Config and .env selection no longer need an
   import-time scrub: importing ``nhj`` modules must not load either (the guard in
   ``test_isolation.py`` proves it), and the per-test fixture below removes host variables before any
   test body runs. Read-only package data (character definitions, the bundled clip bank, media) is
   left alone.
2. Before **each test** the contents of those directories are wiped and the host-specific environment
   variables below are removed, so every test starts from the same empty install. A test that needs a
   value sets it itself with ``monkeypatch``.
3. ``NHJ_TEST_NO_MIXER=1`` is set for the session and ``start_controller`` is stubbed (#80).
   A leftover mixer would poll this session's spool dir and eat play-events. Worker
   subprocesses inherit the env var so they do not spawn either. A session-finish check
   fails if a suite-owned mixer appeared during the run (detected, then cleaned up).
   Mixers that only look similar are reported and left running.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(tempfile.mkdtemp(prefix="nhj-tests-"))
atexit.register(shutil.rmtree, _ROOT, ignore_errors=True)

_DIRS = {
    "NHJ_STATE_DIR": _ROOT / "state",
    "NHJ_LOG_DIR": _ROOT / "logs",
    "NHJ_CACHE_DIR": _ROOT / "cache",
    "NHJ_CONFIG_DIR": _ROOT / "config",
}
for _name, _path in _DIRS.items():
    _path.mkdir()
    os.environ[_name] = str(_path)          # unconditional: never inherit a real directory
os.environ["NHJ_TEST_NO_MIXER"] = "1"       # worker subprocesses inherit this (#80)

# A fake home: Path.home() feeds the legacy config dir (~/.config/nhj), ~/.env (the Vestaboard token
# fallback) and, on macOS, the default ~/Library paths.
_HOME = _ROOT / "home"
_HOME.mkdir()
os.environ["HOME"] = str(_HOME)
for _xdg, _sub in (("XDG_STATE_HOME", ".local/state"), ("XDG_CONFIG_HOME", ".config"),
                   ("XDG_CACHE_HOME", ".cache"), ("XDG_DATA_HOME", ".local/share")):
    (_HOME / _sub).mkdir(parents=True)
    os.environ[_xdg] = str(_HOME / _sub)

# Variables a developer's shell, ``.env`` or service units set that change NHJ's behaviour.
_HOST_ENV = (
    "NHJ_CONFIG_FILE", "NHJ_STATE_FILE", "NHJ_MUZAK_STATE", "NHJ_REMOTE_URL", "NHJ_ORCHESTRATION", "NHJ_OUTPUT_DEVICE",
    "NHJ_AUDIOCPP_URL", "NHJ_AUDIOCPP_VOICES", "NHJ_TTS_URL", "NHJ_TTS_MODEL_TIER", "NHJ_TTS_PORT",
    "NHJ_LLM_PORT", "NHJ_DYNAMIC", "NHJ_DYNAMIC_BASE_URL", "NHJ_DYNAMIC_MODEL", "NHJ_DYNAMIC_API_KEY",
    "NHJ_SERVERS", "NHJ_PHONE_FX", "NHJ_ECHO_CONTEXT", "NHJ_MUZAK_DIR", "NHJ_MUZAK_VOLUME",
    "NHJ_MUZAK_DUCK_GAIN", "NHJ_VOICE_TARGET_DBFS", "TTS_ENGINE",
    "NHJ_SHARED_SECRET", "NHJ_DEBUG",
    "CLAKY_TRANSPORT", "CLAKY_URL", "CLAKY_HOST", "VESTABOARD_LOCAL_KEY", "BIFROST_KV_KEY",
)


# NHJ entry points call ``resources.load_env()``, which in turn calls
# ``dotenv.load_dotenv(resources.env_file())``. In a checkout that file is ``<repo>/.env`` -- the
# developer's real one -- and python-dotenv would put its values straight back after the clean-up
# above. So wrap it for the test session: it
# still loads the file, but the host-specific variables the FILE introduced are removed again. Variables that
# were already set (by a test, with monkeypatch) are left alone, as dotenv itself would not override them.
import dotenv  # noqa: E402

_HOST_VARS = frozenset(_HOST_ENV)
_real_load_dotenv = dotenv.load_dotenv


def _scrubbing_load_dotenv(*args, **kwargs):
    already_set = {var for var in _HOST_VARS if var in os.environ}
    try:
        return _real_load_dotenv(*args, **kwargs)
    finally:
        for var in _HOST_VARS - already_set:
            os.environ.pop(var, None)


dotenv.load_dotenv = _scrubbing_load_dotenv

# Tests patch ``subprocess.Popen`` / ``check_output``. Bind the real Popen so the mixer
# reap (#80) does not call the stub (check_output uses Popen at call time).
_Popen = subprocess.Popen
_PIPE = subprocess.PIPE
_DEVNULL = subprocess.DEVNULL


def _empty(directory: Path) -> None:
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _reset_process_bootstrap_state() -> None:
    """Drop process-lifetime entry-point state that would leak between tests.

    ``CFG``/``INTENTS``/``ADAPTER_ORDER`` are materialised in the config
    module's globals on first access (including by a monkeypatch read), so
    remove those globals without discarding the already-loaded merged
    config. Also let the next entry point perform its one .env load.
    """
    try:
        from nhj import config, resources
    except Exception:
        return
    for name in getattr(config, "_LAZY_ATTRS", ("CFG", "INTENTS", "ADAPTER_ORDER")):
        vars(config).pop(name, None)
    # Lazy path/env attributes are not cached on ordinary access, but
    # monkeypatch restores the pre-patch value into module globals; drop any
    # such pinned value so the next test resolves paths from its own env.
    for module_name, attrs in {
        "nhj.hook": ("_LOG", "_SEEN"),
        "nhj.queue_manager": ("_LOG",),
        "nhj.duck_player": ("_EVENTS",),
        "nhj.inference_muzak": ("_REPO_ROOT",),
        "nhj.adapters.audio": (
            "_VOICES_ROOT", "_AUDIO_ROOT", "_CLIPS_ROOT", "_TMP_ROOT", "_VOICE_TARGET_DBFS"),
    }.items():
        module = sys.modules.get(module_name)
        if module is not None:
            for name in attrs:
                vars(module).pop(name, None)
    resources._ENV_LOADED = False


def _is_mixer_cmd(cmd: str) -> bool:
    cmd = cmd.strip()
    return "-m nhj.inference_muzak" in cmd or cmd == "NHJ muzak"


def _mixer_pids() -> set[int]:
    """Pids of muzak controllers: ``python -m nhj.inference_muzak`` or the setproctitle name."""
    procfs = Path("/proc")
    if procfs.is_dir():
        pids: set[int] = set()
        for entry in procfs.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == os.getpid():
                continue
            try:
                raw = (entry / "cmdline").read_bytes()
                comm = (entry / "comm").read_text().strip()
            except OSError:
                continue
            cmd = raw.replace(b"\0", b" ").decode("utf-8", "replace")
            if _is_mixer_cmd(cmd) or comm == "NHJ muzak":
                pids.add(pid)
        return pids
    try:
        proc = _Popen(
            ["ps", "-ax", "-o", "pid=,command="],
            stdout=_PIPE, stderr=_DEVNULL, text=True,
        )
        out, _ = proc.communicate()
    except OSError:
        return set()
    pids = set()
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, _, cmd = line.partition(" ")
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid != os.getpid() and _is_mixer_cmd(cmd):
            pids.add(pid)
    return pids


_PREEXISTING_MIXERS: set[int] = set()
_SUITE_STATE_DIR = str(_DIRS["NHJ_STATE_DIR"])
_SUITE_STATE_MARKER = f"NHJ_STATE_DIR={_SUITE_STATE_DIR}"


def _proc_environ(pid: int) -> str:
    """Best-effort environment text for *pid* (Linux procfs, else ``ps eww``)."""
    try:
        raw = Path("/proc", str(pid), "environ").read_bytes()
        return raw.replace(b"\0", b"\n").decode("utf-8", "replace")
    except OSError:
        pass
    try:
        proc = _Popen(
            ["ps", "eww", "-p", str(pid)],
            stdout=_PIPE, stderr=_DEVNULL, text=True,
        )
        out, _ = proc.communicate()
    except OSError:
        return ""
    return out or ""


def _parent_pid(pid: int) -> int | None:
    try:
        for line in Path("/proc", str(pid), "status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    try:
        proc = _Popen(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            stdout=_PIPE, stderr=_DEVNULL, text=True,
        )
        out, _ = proc.communicate()
        return int(out.strip())
    except (OSError, ValueError):
        return None


def _is_descendant(pid: int, ancestor: int) -> bool:
    seen: set[int] = set()
    cur: int | None = pid
    for _ in range(32):
        if cur is None or cur in seen:
            return False
        if cur == ancestor:
            return True
        seen.add(cur)
        cur = _parent_pid(cur)
    return False


def _suite_state_controller_pids() -> set[int]:
    path = _DIRS["NHJ_STATE_DIR"] / "music.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return set()
    pids: set[int] = set()
    for key in ("controller_pid", "child_pid"):
        value = data.get(key)
        if isinstance(value, int):
            pids.add(value)
    return pids


def _is_suite_owned(pid: int) -> bool:
    """True only when *pid* is proven to belong to this test session.

    ``start_controller`` uses ``start_new_session=True``, so parent-pid
    walking misses the usual leak. The session's unique ``NHJ_STATE_DIR``
    in the process environment is the reliable signal.
    """
    if _SUITE_STATE_MARKER in _proc_environ(pid):
        return True
    if pid in _suite_state_controller_pids():
        return True
    return _is_descendant(pid, os.getpid())


def _reap_suite_mixers() -> tuple[set[int], set[int]]:
    """Detect new mixers, kill only suite-owned ones.

    Returns ``(owned, unverified)``. *owned* is the set that appeared and
    was proven ours, captured before SIGKILL so a successful kill still
    fails the session check.
    """
    appeared = _mixer_pids() - _PREEXISTING_MIXERS
    owned = {pid for pid in appeared if _is_suite_owned(pid)}
    unverified = appeared - owned
    for pid in owned:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return owned, unverified


@pytest.fixture(scope="session", autouse=True)
def _fail_if_the_suite_left_a_mixer():
    global _PREEXISTING_MIXERS
    _PREEXISTING_MIXERS = _mixer_pids()
    yield
    owned, unverified = _reap_suite_mixers()
    if unverified:
        print(
            f"[nhj tests] mixer pid(s) {sorted(unverified)} appeared during the "
            "suite but are not suite-owned; left running",
            file=sys.stderr,
        )
    assert not owned, (
        f"the suite left mixer process(es) {sorted(owned)}; "
        "start_controller is stubbed — a test spawned one for real and did not stop it (#80)"
    )


@pytest.fixture(autouse=True)
def _hermetic_install(monkeypatch):
    for directory in _DIRS.values():
        _empty(directory)
    for var in _HOST_ENV:
        monkeypatch.delenv(var, raising=False)
    _reset_process_bootstrap_state()
    # Do not spawn a real mixer into the session-wide spool dir (#80).
    from nhj import inference_muzak
    monkeypatch.setattr(inference_muzak, "start_controller", lambda: True)
    yield
