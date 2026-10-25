"""Session mixer guard (#80 / #81): detect leaks without killing a live mixer."""
from __future__ import annotations

import os
import signal

import conftest as cf


def test_reap_fails_on_detected_owned_pid_even_after_kill(monkeypatch):
    """Muse P2: asserting on post-kill survivors would pass if SIGKILL worked."""
    fake = 2**31 - 7
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(cf, "_PREEXISTING_MIXERS", set())
    monkeypatch.setattr(cf, "_mixer_pids", lambda: {fake})
    monkeypatch.setattr(cf, "_is_suite_owned", lambda pid: True)
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))
    owned, unverified = cf._reap_suite_mixers()
    assert owned == {fake}
    assert unverified == set()
    assert killed == [(fake, signal.SIGKILL)]


def test_reap_does_not_kill_unverified_new_mixer(monkeypatch):
    """Muse P2: a mixer that only looks similar (live one started mid-suite) stays up."""
    fake = 2**31 - 11
    killed: list[int] = []
    monkeypatch.setattr(cf, "_PREEXISTING_MIXERS", set())
    monkeypatch.setattr(cf, "_mixer_pids", lambda: {fake})
    monkeypatch.setattr(cf, "_is_suite_owned", lambda pid: False)
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    owned, unverified = cf._reap_suite_mixers()
    assert owned == set()
    assert unverified == {fake}
    assert killed == []


def test_preexisting_mixer_is_neither_owned_nor_unverified(monkeypatch):
    live = 2**31 - 13
    monkeypatch.setattr(cf, "_PREEXISTING_MIXERS", {live})
    monkeypatch.setattr(cf, "_mixer_pids", lambda: {live})
    monkeypatch.setattr(cf, "_is_suite_owned", lambda pid: True)
    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(AssertionError("killed")))
    owned, unverified = cf._reap_suite_mixers()
    assert owned == set()
    assert unverified == set()


def test_pytest_process_env_proves_suite_ownership():
    assert cf._is_suite_owned(os.getpid())


def test_pid_1_is_not_suite_owned():
    assert not cf._is_suite_owned(1)
