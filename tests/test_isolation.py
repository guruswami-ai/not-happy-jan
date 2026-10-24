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


def test_home_and_xdg_are_throwaway():
    root = str(Path(os.environ["NHJ_STATE_DIR"]).parent)
    assert str(Path.home()).startswith(root)
    assert str(resources.legacy_config_dir()).startswith(root)
    for var in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        assert os.environ[var].startswith(root), var


def test_no_explicit_config_override_survives():
    from nhj import config
    assert "NHJ_CONFIG_FILE" not in os.environ
    assert not any(str(p) == os.environ.get("NHJ_CONFIG_FILE", "<none>") for p in config._override_paths())


def test_import_time_env_is_cleared_before_nhj_is_imported(tmp_path):
    """config.CFG is built when nhj.config is imported, so clearing NHJ_CONFIG_FILE per test would be too
    late. Load conftest in a fresh interpreter that has the variable set, then import nhj."""
    import subprocess
    import sys

    evil = tmp_path / "evil.yaml"
    evil.write_text("claky:\n  url: http://host-config-leaked.invalid\n")
    conftest = Path(__file__).with_name("conftest.py")
    code = (
        "import runpy, sys; runpy.run_path(sys.argv[1]); "
        "from nhj import config; print(config.section('claky').get('url') or '')"
    )
    env = {**os.environ, "NHJ_CONFIG_FILE": str(evil), "NHJ_VOICE_TARGET_DBFS": "-1"}
    out = subprocess.run([sys.executable, "-c", code, str(conftest)], env=env, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "host-config-leaked" not in out.stdout
