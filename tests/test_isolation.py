"""The suite must stay hermetic (see tests/conftest.py): no test may see or touch the real install."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from nhj import hook, queue_manager, resources, state


def _real_state_dir() -> Path:
    # Where a developer's real state lives when no NHJ_STATE_DIR override is set.
    return Path.home() / ".local" / "state" / "not-happy-jan"


def test_mutable_dirs_are_throwaway_not_the_real_install():
    root = Path(os.environ["NHJ_STATE_DIR"]).parent
    assert root.name.startswith("nhj-tests-")
    for fn in (resources.state_dir, resources.log_dir, resources.cache_dir, resources.config_dir):
        assert str(fn(create=False)).startswith(str(root)), fn.__name__
    assert not str(resources.state_dir(create=False)).startswith(str(_real_state_dir()))


def test_import_time_constants_point_at_the_throwaway_dirs():
    # These are computed when the module is imported, which is why conftest sets the env at import time.
    root = str(Path(os.environ["NHJ_STATE_DIR"]).parent)
    assert str(hook._SEEN).startswith(root)
    assert str(queue_manager._LOG).startswith(root)


@pytest.mark.parametrize("var", ["TTS_ENGINE", "NHJ_OUTPUT_DEVICE", "CLAKY_URL", "NHJ_STATE_FILE",
                                 "NHJ_ORCHESTRATION", "BIFROST_KV_KEY", "NHJ_REMOTE_URL"])
def test_host_environment_is_removed(var):
    assert var not in os.environ


def test_state_written_here_is_visible_in_this_test():
    state.set_flag("mute", True)
    state.update(lambda s: s.setdefault("settings", {}).update({"voice_override": "leaky"}))
    assert state.load()["settings"]["voice_override"] == "leaky"


def test_state_from_the_previous_test_is_gone():
    # runs right after the test above (file order); the autouse fixture must have wiped it
    assert state.load() == {}
