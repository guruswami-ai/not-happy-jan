"""`nhj run` (#16 phase 1): wrap a command, pass its status through, announce how it ended."""
from __future__ import annotations

import sys

import pytest
from typer.testing import CliRunner

from nhj import cli, runwrap


# ---- label / message building -------------------------------------------------------------------
def test_label_is_the_basename_only_never_the_arguments():
    assert runwrap.default_label(["/usr/bin/make", "-j4", "TOKEN=hunter2"]) == "make"
    assert runwrap.default_label([]) == "command"


def test_clean_label_strips_control_characters_and_caps_length():
    assert runwrap.clean_label("back\nup\x07 job\t1") == "back up job 1"
    assert runwrap.clean_label("") == "command"
    assert len(runwrap.clean_label("x" * 500)) == 40


@pytest.mark.parametrize("seconds,spoken", [
    (0, "0 seconds"), (1, "1 second"), (12, "12 seconds"), (59.6, "1 minute"),
    (60, "1 minute"), (185, "3 minutes"), (3600, "1 hour"), (3900, "1 hour 5 minutes"), (7260, "2 hours 1 minute"),
])
def test_spoken_duration(seconds, spoken):
    assert runwrap.spoken_duration(seconds) == spoken


@pytest.mark.parametrize("rc,status", [(0, 0), (3, 3), (-2, 130), (-15, 143)])
def test_exit_status_maps_signal_deaths_like_a_shell(rc, status):
    assert runwrap.exit_status(rc) == status


# ---- what gets announced -------------------------------------------------------------------------
def test_decide_matrix():
    d = runwrap.decide
    assert d(0, 2, "make") is None                                     # quick success: silent
    assert d(0, 12, "make") == runwrap.Outcome("ok", "make finished in 12 seconds")
    assert d(0, 200, "make", only_failures=True) is None
    assert d(0, 0.1, "make", min_seconds=0) == runwrap.Outcome("ok", "make finished in 0 seconds")
    assert d(3, 0.1, "make") == runwrap.Outcome("err", "make failed with status 3")      # failures always speak
    assert d(3, 0.1, "make", only_failures=True).intent == "err"
    assert d(-2, 30, "make") == runwrap.Outcome("warn", "make was interrupted")
    assert d(-2, 1, "make") is None                                    # you pressed Ctrl-C on a quick one
    assert d(130, 30, "make").intent == "warn" and d(143, 30, "make").intent == "warn"   # script exits 130/143
    assert d(130, 1, "make") is None


# ---- the command itself --------------------------------------------------------------------------
@pytest.fixture
def events(monkeypatch):
    """Record queued events instead of queuing them (and never start a worker)."""
    seen = []

    class FakeQM:
        def add(self, **kw):
            seen.append(kw)

        def start_worker_if_needed(self):
            pass

    monkeypatch.setattr("nhj.queue_manager.QueueManager", FakeQM)
    return seen


def _run(*args):
    return CliRunner().invoke(cli.cli, ["run", *args])


def test_exit_status_and_output_pass_through_and_failure_is_announced(events, capfd):
    r = _run("--", sys.executable, "-c", "import sys; print('to-out'); print('to-err', file=sys.stderr); sys.exit(3)")
    assert r.exit_code == 3
    out = capfd.readouterr()
    assert "to-out" in out.out and "to-err" in out.err                 # stdio is the command's own
    assert [(e["intent"], e["message"]) for e in events] == [
        ("err", f"{sys.executable.rsplit('/', 1)[-1]} failed with status 3")]


def test_a_quick_success_is_silent_and_a_slow_one_speaks(events):
    assert _run("--", sys.executable, "-c", "pass").exit_code == 0
    assert events == []
    assert _run("--min-seconds", "0", "--label", "nightly backup", "--", sys.executable, "-c", "pass").exit_code == 0
    assert len(events) == 1 and events[0]["intent"] == "ok"
    assert events[0]["message"].startswith("nightly backup finished in ")


def test_arguments_never_reach_the_message(events):
    secret = "sk-very-secret-value"
    r = _run("--", sys.executable, "-c", "import sys; sys.exit(1)", secret)
    assert r.exit_code == 1
    assert events and secret not in events[0]["message"]


def test_options_after_the_command_belong_to_the_command(events):
    """`nhj run make -j4 --label x` must hand -j4 and --label to make, not parse them."""
    r = _run(sys.executable, "-c", "import sys; sys.exit(0 if sys.argv[1:] == ['-j4', '--label', 'x'] else 9)",
             "-j4", "--label", "x")
    assert r.exit_code == 0


def test_a_missing_command_reports_127_and_is_announced(events):
    r = _run("--", "/definitely/not/a/nosuchtool")
    assert r.exit_code == 127
    assert "command not found" in r.output
    assert events and events[0]["intent"] == "err"
    assert events[0]["message"] == "nosuchtool failed with status 127"


def test_a_failing_announce_never_changes_the_commands_status(monkeypatch):
    class BrokenQM:
        def add(self, **kw):
            raise RuntimeError("queue on fire")

        def start_worker_if_needed(self):
            raise RuntimeError("no worker")

    monkeypatch.setattr("nhj.queue_manager.QueueManager", BrokenQM)
    assert _run("--", sys.executable, "-c", "import sys; sys.exit(7)").exit_code == 7


def test_ctrl_c_makes_us_keep_waiting_for_the_child(monkeypatch):
    calls = {"n": 0}

    class FakeProc:
        def wait(self):
            calls["n"] += 1
            if calls["n"] < 3:
                raise KeyboardInterrupt
            return 4

        def send_signal(self, sig):
            pass

    monkeypatch.setattr(runwrap.subprocess, "Popen", lambda argv: FakeProc())
    rc, seconds = runwrap.run_command(["whatever"])
    assert rc == 4 and calls["n"] == 3 and seconds >= 0


@pytest.mark.parametrize("exc,status,words", [
    (OSError(8, "Exec format error"), 126, "Exec format error"),
    (PermissionError(13, "Permission denied"), 126, "Permission denied"),
    (OSError(7, "Argument list too long"), 126, "Argument list too long"),
    (NotADirectoryError(20, "Not a directory"), 127, "command not found"),
    (ValueError("embedded null byte"), 126, "ValueError"),
])
def test_every_launch_failure_is_a_shell_like_status_not_a_traceback(monkeypatch, capsys, exc, status, words):
    """Codex review on #85: only FileNotFoundError/PermissionError were handled; other launch errors escaped."""
    def boom(argv):
        raise exc
    monkeypatch.setattr(runwrap.subprocess, "Popen", boom)
    rc, seconds = runwrap.run_command(["/opt/tool", "--password=hunter2"])
    err = capsys.readouterr().err
    assert rc == status and words in err
    assert "hunter2" not in err                                   # arguments are never printed


def test_a_real_file_with_an_invalid_executable_format_gives_126(events, tmp_path):
    script = tmp_path / "noshebang"
    script.write_text("echo hi\n")
    script.chmod(0o755)
    r = _run("--", str(script))
    assert r.exit_code == 126
    assert "cannot execute" in r.output and "Exec format error" in r.output
    assert events and events[0]["intent"] == "err" and "status 126" in events[0]["message"]
