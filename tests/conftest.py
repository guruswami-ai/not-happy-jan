"""Suite-wide isolation: the tests must never read or write the developer's real NHJ install.

Before this file existed the suite shared the machine's real state/log/cache/config directories and
inherited its environment (a live ``.env``, ``voice_override`` in ``state.json``, a Vestaboard URL in
``config.yaml``). Consequences: a test that passed in CI failed on the maintainer's host (it read the
live ``voice_override``), tests wrote lines into the real ``worker.log``/``hook.log``, and tests that
start a worker acted on the real queue.

Two layers:

1. At **import time** (here, before any test module imports ``nhj``) the four mutable directories are
   pointed at a throwaway session directory, ``HOME`` and the XDG directories move there too (the legacy
   config dir and ``~/.env`` come from the real home), and the variables NHJ reads while importing are
   removed (``config.CFG`` is built on import, so ``NHJ_CONFIG_FILE`` must be gone before that). Several
   modules compute module-level constants from these (``queue_manager._LOG``, ``hook._SEEN``,
   ``audio._CLIPS_ROOT``, ...), so a per-test fixture alone would be too late. Read-only package data
   (character definitions, the bundled clip bank, media) is left alone.
2. Before **each test** the contents of those directories are wiped and the host-specific environment
   variables below are removed, so every test starts from the same empty install. A test that needs a
   value sets it itself with ``monkeypatch``.
"""
from __future__ import annotations

import atexit
import os
import shutil
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

# A fake home: Path.home() feeds the legacy config dir (~/.config/nhj), ~/.env (the Vestaboard token
# fallback) and, on macOS, the default ~/Library paths.
_HOME = _ROOT / "home"
_HOME.mkdir()
os.environ["HOME"] = str(_HOME)
for _xdg, _sub in (("XDG_STATE_HOME", ".local/state"), ("XDG_CONFIG_HOME", ".config"),
                   ("XDG_CACHE_HOME", ".cache"), ("XDG_DATA_HOME", ".local/share")):
    (_HOME / _sub).mkdir(parents=True)
    os.environ[_xdg] = str(_HOME / _sub)

# Read while nhj is IMPORTED (config.CFG, audio._VOICE_TARGET_DBFS): remove them here, not per test.
for _early in ("NHJ_CONFIG_FILE", "NHJ_VOICE_TARGET_DBFS"):
    os.environ.pop(_early, None)

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

_HOST_VARS = frozenset(_HOST_ENV) | {"NHJ_CONFIG_FILE", "NHJ_VOICE_TARGET_DBFS"}
_real_load_dotenv = dotenv.load_dotenv


def _scrubbing_load_dotenv(*args, **kwargs):
    already_set = {var for var in _HOST_VARS if var in os.environ}
    try:
        return _real_load_dotenv(*args, **kwargs)
    finally:
        for var in _HOST_VARS - already_set:
            os.environ.pop(var, None)


dotenv.load_dotenv = _scrubbing_load_dotenv


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
    resources._ENV_LOADED = False


@pytest.fixture(autouse=True)
def _hermetic_install(monkeypatch):
    for directory in _DIRS.values():
        _empty(directory)
    for var in _HOST_ENV:
        monkeypatch.delenv(var, raising=False)
    _reset_process_bootstrap_state()
    yield
