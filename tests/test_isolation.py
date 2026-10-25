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


def test_lazy_path_constants_point_at_the_throwaway_dirs():
    # These compatibility attributes resolve at access time from the session env conftest sets.
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


def test_lazy_config_attributes_materialise_on_access():
    from nhj import config
    assert config.CFG
    assert "CFG" in vars(config)


def test_lazy_config_attributes_are_reset_between_tests():
    # Runs after the materialisation test above (file order). The merged
    # config cache may stay loaded, but its lazy module globals must not.
    from nhj import config
    assert "CFG" not in vars(config)


def test_importing_nhj_modules_loads_neither_config_nor_dotenv(tmp_path):
    """Import guard (#61): with a malformed selected config and a hostile .env
    already selected, importing the public/entry modules must load neither."""
    import subprocess
    import sys

    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("routing: [unclosed\n")
    evil_env = tmp_path / ".env"
    evil_env.write_text("NHJ_GUARD_LEAK=leaked\nNHJ_CONFIG_FILE=/host/config.yaml\n")
    modules = ",".join([
        "nhj.config", "nhj.resources", "nhj.state", "nhj.event", "nhj.queue_manager",
        "nhj.worker", "nhj.hook", "nhj.prompt_hook", "nhj.notify_hook",
        "nhj.inference_muzak", "nhj.duck_player", "nhj.cli", "nhj.mcp_server",
        "nhj.tts_server", "nhj.llm_server", "nhj.serverctl", "nhj.adapters.audio",
        "nhj.adapters.claky", "nhj.characters", "nhj.scenes",
    ])
    code = (
        "import importlib, os, sys; from pathlib import Path; "
        "from nhj import resources; resources.env_file = lambda: Path(sys.argv[1]); "
        "[importlib.import_module(name) for name in sys.argv[2].split(',')]; "
        "from nhj import config; "
        "print(config._CFG is None, 'CFG' in vars(config), resources._ENV_LOADED); "
        "print(os.getenv('NHJ_GUARD_LEAK', '<unset>'), "
        "os.getenv('NHJ_CONFIG_FILE') == sys.argv[3], "
        "os.getenv('NHJ_VOICE_TARGET_DBFS'))"
    )
    env = {
        **os.environ,
        "NHJ_CONFIG_FILE": str(bad_config),
        "NHJ_VOICE_TARGET_DBFS": "not-a-float",
    }
    env.pop("NHJ_GUARD_LEAK", None)
    out = subprocess.run(
        [sys.executable, "-c", code, str(evil_env), modules, str(bad_config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == [
        "True False False",
        "<unset> True not-a-float",
    ]


def test_dotenv_cannot_reintroduce_host_variables(tmp_path, monkeypatch):
    import dotenv
    env = tmp_path / ".env"
    env.write_text("NHJ_CONFIG_FILE=/host/config.yaml\nTTS_ENGINE=audiocpp\nHARMLESS_TEST_VAR=1\n")
    monkeypatch.setenv("NHJ_OUTPUT_DEVICE", "set-by-the-test")        # set BEFORE the load: must survive
    env2 = tmp_path / ".env2"
    env2.write_text("NHJ_OUTPUT_DEVICE=from-the-file\n")
    dotenv.load_dotenv(env)
    dotenv.load_dotenv(env2)
    try:
        assert "NHJ_CONFIG_FILE" not in os.environ and "TTS_ENGINE" not in os.environ
        assert os.environ["NHJ_OUTPUT_DEVICE"] == "set-by-the-test"
        assert os.environ.get("HARMLESS_TEST_VAR") == "1"              # ordinary dotenv behaviour kept
    finally:
        os.environ.pop("HARMLESS_TEST_VAR", None)


def test_worker_import_with_a_host_dotenv_does_not_leak_config(tmp_path):
    """Regression for the old reported path: importing nhj.worker must not load the checkout .env."""
    import subprocess
    import sys

    evil = tmp_path / "evil.yaml"
    evil.write_text("claky:\n  url: http://host-config-leaked.invalid\n")
    dot = tmp_path / ".env"
    dot.write_text(f"NHJ_CONFIG_FILE={evil}\n")
    conftest = Path(__file__).with_name("conftest.py")
    code = (
        "import runpy, sys; from pathlib import Path; runpy.run_path(sys.argv[1]); "
        "from nhj import resources; resources.env_file = lambda: Path(sys.argv[2]); "
        "import nhj.worker; from nhj import config; print(config.section('claky').get('url') or '')"
    )
    out = subprocess.run([sys.executable, "-c", code, str(conftest), str(dot)], capture_output=True,
                         text=True, timeout=60, env=dict(os.environ))
    assert out.returncode == 0, out.stderr
    assert "host-config-leaked" not in out.stdout
