"""Entry-point .env bootstrap (#61 slice 2).

Library imports must not read .env. Real entry points call
``resources.load_env()`` explicitly; long-lived config consumers then call
``config.get()`` once so a bad config fails at startup instead of on every
later section lookup.
"""
from __future__ import annotations

import os
import subprocess
import sys
from io import StringIO

import pytest

from nhj import resources


def test_load_env_is_once_and_process_env_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("NHJ_BOOTSTRAP_EXISTING=from-file\nNHJ_BOOTSTRAP_NEW=first\n")
    monkeypatch.setattr(resources, "env_file", lambda: env)
    monkeypatch.setattr(resources, "_ENV_LOADED", False)
    monkeypatch.setenv("NHJ_BOOTSTRAP_EXISTING", "from-process")
    monkeypatch.delenv("NHJ_BOOTSTRAP_NEW", raising=False)

    assert resources.load_env() is True
    assert os.environ["NHJ_BOOTSTRAP_EXISTING"] == "from-process"
    assert os.environ["NHJ_BOOTSTRAP_NEW"] == "first"

    env.write_text("NHJ_BOOTSTRAP_NEW=second\n")
    monkeypatch.delenv("NHJ_BOOTSTRAP_NEW", raising=False)
    assert resources.load_env() is False
    assert "NHJ_BOOTSTRAP_NEW" not in os.environ


@pytest.mark.parametrize("module", [
    "nhj.worker",
    "nhj.mcp_server",
    "nhj.inference_muzak",
    "nhj.hook",
    "nhj.prompt_hook",
    "nhj.notify_hook",
    "nhj.tts_server",
    "nhj.llm_server",
    "nhj.serverctl",
    "nhj.cli",
    "nhj.adapters.claky",
])
def test_importing_an_entry_module_does_not_load_dotenv(tmp_path, module):
    env = tmp_path / ".env"
    env.write_text("NHJ_BOOTSTRAP_LEAK=leaked\n")
    code = (
        "import importlib, os, sys; from pathlib import Path; "
        "from nhj import resources; resources.env_file = lambda: Path(sys.argv[1]); "
        "importlib.import_module(sys.argv[2]); "
        "print(os.getenv('NHJ_BOOTSTRAP_LEAK', '<unset>')); print(resources._ENV_LOADED)"
    )
    child_env = dict(os.environ)
    child_env.pop("NHJ_BOOTSTRAP_LEAK", None)
    out = subprocess.run(
        [sys.executable, "-c", code, str(env), module],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["<unset>", "False"]


def test_prompt_hook_main_loads_dotenv(tmp_path, monkeypatch):
    from nhj import prompt_hook

    env = tmp_path / ".env"
    env.write_text("NHJ_PROMPT_PROVE=from-file\n")
    monkeypatch.setattr(resources, "env_file", lambda: env)
    monkeypatch.setattr(resources, "_ENV_LOADED", False)
    monkeypatch.delenv("NHJ_PROMPT_PROVE", raising=False)
    monkeypatch.setattr(sys, "stdin", StringIO("{}"))

    assert prompt_hook.main() == 0
    assert os.environ.get("NHJ_PROMPT_PROVE") == "from-file"


def test_serverctl_main_loads_dotenv_before_checking_the_port(monkeypatch):
    from nhj import serverctl

    events = []
    monkeypatch.setattr(serverctl.resources, "load_env", lambda: events.append("env"))
    monkeypatch.setattr(serverctl.servers, "is_up", lambda name: events.append("up") or True)

    assert serverctl.main(["tts"]) == 0
    assert events == ["env", "up"]


def _raise_config_error():
    from nhj import config

    raise config.ConfigError("bad config")


def test_worker_startup_loads_env_then_fails_fast_on_config(monkeypatch):
    from nhj import config, worker

    events = []
    monkeypatch.setattr(worker.resources, "load_env", lambda: events.append("env"))
    monkeypatch.setattr(worker.config, "get", lambda: events.append("config") or _raise_config_error())
    monkeypatch.setattr(worker, "QueueManager", lambda: events.append("queue"))

    with pytest.raises(config.ConfigError):
        worker.main()
    assert events == ["env", "config"]


def test_mcp_startup_loads_env_then_fails_fast_on_config(monkeypatch):
    from nhj import config, mcp_server

    events = []
    monkeypatch.setattr(mcp_server.resources, "load_env", lambda: events.append("env"))
    monkeypatch.setattr(mcp_server.config, "get", lambda: events.append("config") or _raise_config_error())
    monkeypatch.setattr(mcp_server.mcp, "run", lambda *a, **k: events.append("serve"))

    with pytest.raises(config.ConfigError):
        mcp_server.run()
    assert events == ["env", "config"]


def test_mixer_startup_loads_env_then_fails_fast_on_config(monkeypatch):
    from nhj import config, inference_muzak

    events = []
    monkeypatch.setattr(inference_muzak.resources, "load_env", lambda: events.append("env"))
    monkeypatch.setattr(config, "get", lambda: events.append("config") or _raise_config_error())

    with pytest.raises(config.ConfigError):
        inference_muzak.run()
    assert events == ["env", "config"]
