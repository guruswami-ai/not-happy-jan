"""Suite-wide isolation: the tests must never read or write the developer's real NHJ install.

Before this file existed the suite shared the machine's real state/log/cache/config directories and
inherited its environment (a live ``.env``, ``voice_override`` in ``state.json``, a Vestaboard URL in
``config.yaml``). Consequences: a test that passed in CI failed on the maintainer's host (it read the
live ``voice_override``), tests wrote lines into the real ``worker.log``/``hook.log``, and tests that
start a worker acted on the real queue.

Two layers:

1. At **import time** (here, before any test module imports ``nhj``) the four mutable directories are
   pointed at a throwaway session directory. Several modules compute module-level constants from them
   (``queue_manager._LOG``, ``hook._SEEN``, ``audio._CLIPS_ROOT``, ...), so a per-test fixture alone would
   be too late. Read-only package data (character definitions, the bundled clip bank, media) is left alone.
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

# Variables a developer's shell, ``.env`` or service units set that change NHJ's behaviour.
_HOST_ENV = (
    "NHJ_STATE_FILE", "NHJ_MUZAK_STATE", "NHJ_REMOTE_URL", "NHJ_ORCHESTRATION", "NHJ_OUTPUT_DEVICE",
    "NHJ_AUDIOCPP_URL", "NHJ_AUDIOCPP_VOICES", "NHJ_TTS_URL", "NHJ_TTS_MODEL_TIER", "NHJ_TTS_PORT",
    "NHJ_LLM_PORT", "NHJ_DYNAMIC", "NHJ_DYNAMIC_BASE_URL", "NHJ_DYNAMIC_MODEL", "NHJ_DYNAMIC_API_KEY",
    "NHJ_SERVERS", "NHJ_PHONE_FX", "NHJ_ECHO_CONTEXT", "NHJ_MUZAK_DIR", "NHJ_MUZAK_VOLUME",
    "NHJ_MUZAK_DUCK_GAIN", "NHJ_VOICE_TARGET_DBFS", "TTS_ENGINE",
    "CLAKY_TRANSPORT", "CLAKY_URL", "CLAKY_HOST", "VESTABOARD_LOCAL_KEY", "BIFROST_KV_KEY",
)


def _empty(directory: Path) -> None:
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _hermetic_install(monkeypatch):
    for directory in _DIRS.values():
        _empty(directory)
    for var in _HOST_ENV:
        monkeypatch.delenv(var, raising=False)
    yield
