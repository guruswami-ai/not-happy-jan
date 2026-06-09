"""NHJ's long-lived services name themselves (NHJ TTS / MCP / LLM / worker) instead of
showing as a generic 'Python' / 'llama-server' in Activity Monitor."""
from __future__ import annotations

import builtins

import pytest


def test_set_title_is_safe_without_setproctitle(monkeypatch):
    import nhj.procname as procname
    real_import = builtins.__import__

    def boom(name, *a, **k):
        if name == "setproctitle":
            raise ImportError("simulated")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    procname.set_title("NHJ X")            # must not raise


def test_set_title_sets_when_available():
    sp = pytest.importorskip("setproctitle")
    from nhj.procname import set_title
    set_title("NHJ test-proc")
    assert "NHJ test-proc" in sp.getproctitle()


def test_ocker_plist_runs_through_named_supervisor(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from nhj import cli
    monkeypatch.setattr(cli.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    dest = cli._install_ocker_launchagent("/models/ocker.gguf", 9991)
    plist = dest.read_text()
    assert "nhj.llm_server" in plist            # launched via the named supervisor
    assert "llama-server" in plist              # which still runs the real binary
    assert "/models/ocker.gguf" in plist


def test_llm_supervisor_spawns_child_and_mirrors_exit(monkeypatch):
    import nhj.llm_server as llm
    seen = {}

    class FakeChild:
        def wait(self):
            return 0
        def send_signal(self, s):
            seen["sig"] = s

    def fake_popen(cmd, **k):
        seen["cmd"] = cmd
        return FakeChild()

    monkeypatch.setattr(llm.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(llm.signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(llm.sys, "argv", ["nhj.llm_server", "/bin/llama-server", "-m", "x.gguf", "--port", "9991"])
    with pytest.raises(SystemExit) as e:
        llm.main()
    assert seen["cmd"] == ["/bin/llama-server", "-m", "x.gguf", "--port", "9991"]
    assert e.value.code == 0
