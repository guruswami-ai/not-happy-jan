"""On-demand model-server lifecycle tests (issue #27)."""
from __future__ import annotations

import sys
import time

import pytest

from nhj import serverctl, servers


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # heartbeats / locks land in a temp state dir; logs in a temp log dir.
    monkeypatch.setattr(servers.resources, "_is_checkout", lambda: False)
    monkeypatch.setenv("NHJ_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NHJ_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("NHJ_SERVERS", raising=False)


def test_mode_env_overrides(monkeypatch):
    monkeypatch.setenv("NHJ_SERVERS", "on-demand")
    assert servers.mode() == "on-demand"
    monkeypatch.setenv("NHJ_SERVERS", "persistent")
    assert servers.mode() == "persistent"


def test_port_honors_installer_url(monkeypatch):
    # the on-demand servers must bind whatever ports install-tts/install-model wrote.
    monkeypatch.delenv("NHJ_LLM_PORT", raising=False)
    monkeypatch.setenv("NHJ_DYNAMIC_BASE_URL", "http://127.0.0.1:9123/v1")
    assert servers._port("llm") == 9123                  # parsed from the service URL
    monkeypatch.setenv("NHJ_LLM_PORT", "9001")
    assert servers._port("llm") == 9001                  # explicit env wins
    monkeypatch.delenv("NHJ_LLM_PORT", raising=False)
    monkeypatch.delenv("NHJ_DYNAMIC_BASE_URL", raising=False)
    assert servers._port("llm") == 9991                  # falls back to the default


def test_is_managed_llm():
    assert servers.is_managed_llm("http://127.0.0.1:9991/v1")
    assert servers.is_managed_llm("http://localhost:9991/v1")
    assert not servers.is_managed_llm("http://localhost:11434/v1")    # Ollama
    assert not servers.is_managed_llm("https://api.openai.com/v1")    # cloud


def test_mark_and_last_used():
    servers.mark_used("tts")
    assert abs(servers.last_used("tts") - time.time()) < 5
    assert servers.last_used("never-touched") == 0.0


def test_ensure_persistent_never_spawns(monkeypatch):
    monkeypatch.setenv("NHJ_SERVERS", "persistent")
    monkeypatch.setattr(servers, "is_up", lambda name, host="127.0.0.1": False)
    spawned = []
    monkeypatch.setattr(servers, "_spawn_supervisor", spawned.append)
    assert servers.ensure("tts") is False        # persistent: the LaunchAgent owns it
    assert spawned == []


def test_ensure_on_demand_spawns_and_waits(monkeypatch):
    monkeypatch.setenv("NHJ_SERVERS", "on-demand")
    state = {"up": False}
    monkeypatch.setattr(servers, "is_up", lambda name, host="127.0.0.1": state["up"])
    monkeypatch.setattr(servers, "is_ready", lambda name: state["up"])
    monkeypatch.setattr(servers, "_spawn_supervisor", lambda name: state.__setitem__("up", True))
    assert servers.ensure("tts", wait=2) is True
    assert servers.last_used("tts") > 0          # heartbeat refreshed


def test_ensure_waits_for_health_after_port_opens(monkeypatch):
    """An open llama.cpp socket is not ready until its health endpoint succeeds."""
    assert hasattr(servers, "is_ready"), "servers.ensure needs an application readiness check"
    monkeypatch.setenv("NHJ_SERVERS", "on-demand")
    checks = {"count": 0}

    def ready(name):
        checks["count"] += 1
        return checks["count"] >= 3

    monkeypatch.setattr(servers, "is_ready", ready)
    monkeypatch.setattr(servers, "is_up", lambda name, host="127.0.0.1": True)
    monkeypatch.setattr(servers, "_spawn_supervisor", lambda name: None)
    monkeypatch.setattr(servers.time, "sleep", lambda seconds: None)

    assert servers.ensure("llm", wait=2) is True
    assert checks["count"] >= 3


def test_readiness_check_bypasses_system_proxy(monkeypatch):
    """Loopback health checks must never be sent through HTTP(S)_PROXY."""
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Opener:
        def open(self, url, timeout):
            captured["url"] = url
            captured["timeout"] = timeout
            return Response()

    def build_opener(handler):
        captured["handler"] = handler
        return Opener()

    monkeypatch.setattr(servers, "is_up", lambda name, host="127.0.0.1": True)
    monkeypatch.setattr(servers.urllib.request, "build_opener", build_opener)

    assert servers.is_ready("tts") is True
    assert isinstance(captured["handler"], servers.urllib.request.ProxyHandler)
    assert captured["handler"].proxies == {}
    assert captured["url"] == "http://127.0.0.1:9992/health"


def test_spawn_supervisor_detaches_stdin(monkeypatch):
    captured = {}

    def fake_popen(args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(servers.subprocess, "Popen", fake_popen)

    servers._spawn_supervisor("tts")

    assert captured.get("stdin") is servers.subprocess.DEVNULL


def test_server_command_tts_and_llm(monkeypatch, tmp_path):
    assert "nhj.tts_server" in servers.server_command("tts")

    models = tmp_path / "models"
    models.mkdir()
    monkeypatch.setattr(servers.resources, "llm_model_dir", lambda create=True: models)
    assert servers.server_command("llm") is None                 # no gguf yet
    (models / "ocker.gguf").write_text("x")
    monkeypatch.setattr(servers.shutil, "which", lambda n: sys.executable)   # an existing "binary"
    cmd = servers.server_command("llm")
    assert cmd and cmd[0] == sys.executable and "ocker-bogan-nano" in cmd


def test_serverctl_reaps_after_idle(monkeypatch):
    monkeypatch.setattr(serverctl.servers, "is_up", lambda name: False)
    monkeypatch.setattr(serverctl.servers, "server_command", lambda name: ["sleep", "30"])
    monkeypatch.setattr(serverctl.servers, "idle_seconds", lambda: 30)
    monkeypatch.setattr(serverctl.servers, "last_used", lambda name: 1.0)   # real but stale ts → reap now
    monkeypatch.setattr(serverctl.servers, "mark_used", lambda name: None)
    monkeypatch.setattr(serverctl.time, "sleep", lambda s: None)

    reaped = {"n": 0}

    class FakeProc:
        def __init__(self):
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def terminate(self):
            reaped["n"] += 1
            self.alive = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.alive = False

    monkeypatch.setattr(serverctl.subprocess, "Popen", lambda cmd: FakeProc())
    assert serverctl.main(["tts"]) == 0
    assert reaped["n"] == 1                       # idle heartbeat → server reaped


def test_serverctl_exits_if_port_already_up(monkeypatch):
    monkeypatch.setattr(serverctl.servers, "is_up", lambda name: True)
    started = []
    monkeypatch.setattr(serverctl.servers, "server_command", lambda name: started.append(name) or ["x"])
    assert serverctl.main(["tts"]) == 0
    assert started == []                          # didn't try to start a second server
