"""Regression tests for queue durability + worker lifecycle (issue #1).

Covers: collision-resistant IDs, claim/ack durability, poison quarantine,
crash-before-ack recovery with bounded retries, atomic worker election, and
safe PID ownership. No torch / GPU / network — pure filesystem + mocks.
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
import time

import pytest

from nhj import queue_manager
from nhj.event import VibeValidationError
from nhj.queue_manager import QueueManager


@pytest.fixture
def qm(tmp_path):
    return QueueManager(queue_dir=str(tmp_path / "queue"))


@pytest.fixture
def dead_pid():
    """A PID that is dead (and reaped, so not a zombie).

    "Dead" is not guaranteed by having waited on the child: the OS may hand the number to a new process
    before the test uses it (the suite spawns many processes, and recovery trusts PID liveness first, so
    a recycled PID makes ``claim_next`` skip the item and return ``None`` -- a random failure that passes
    when the test runs alone). Verify the PID is really gone with the same check the code under test uses,
    retry a few times, and skip with a clear reason if the machine keeps recycling PIDs.
    """
    for _ in range(5):
        p = subprocess.Popen([sys.executable, "-c", ""])
        p.wait()
        if not queue_manager._pid_alive(p.pid):
            return p.pid
    pytest.skip("could not obtain a dead PID: the OS keeps recycling them")


def _vibes(qm):
    return list(qm.queue_dir.glob("*.vibe"))


# ---- collision resistance -------------------------------------------------

def test_add_unique_under_same_millisecond(qm, monkeypatch):
    monkeypatch.setattr(queue_manager.time, "time", lambda: 1_700_000_000.0)  # frozen clock
    names = {qm.add("ok", message=f"n{i}") for i in range(100)}
    assert len(names) == 100                      # no overwrites despite identical ms
    assert len(_vibes(qm)) == 100


def test_concurrent_producers_no_loss(qm):
    per_thread, n_threads = 50, 8

    def producer():
        for i in range(per_thread):
            qm.add("ok", message=f"m{i}")

    threads = [threading.Thread(target=producer) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_vibes(qm)) == per_thread * n_threads


def test_failed_atomic_write_removes_temp_file(qm, monkeypatch):
    monkeypatch.setattr(queue_manager.os, "fsync",
                        lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        qm.add("ok", message="not-written")
    assert list(qm.queue_dir.glob("*.tmp-*")) == []
    assert _vibes(qm) == []


# ---- claim / ack durability ----------------------------------------------

def test_claim_then_ack_roundtrip(qm):
    qm.add("err", message="boom", character="karren")
    token, data = qm.claim_next()
    assert data["intent"] == "err" and data["message"] == "boom" and data["character"] == "karren"
    assert token.name.endswith(f".proc.{os.getpid()}")     # claimed, not deleted
    assert _vibes(qm) == []                                # no longer pending
    qm.ack(token)
    assert not token.exists()                              # acked = gone
    assert qm.claim_next() is None


def test_claim_refreshes_lease_timestamp(qm):
    qm.add("ok", message="waited")
    [vibe] = _vibes(qm)
    old = time.time() - queue_manager._CLAIM_LEASE_SECONDS - 1
    os.utime(vibe, (old, old))

    token, _ = qm.claim_next()

    assert time.time() - token.stat().st_mtime < 5
    qm.ack(token)


def test_claim_empty_returns_none(qm):
    assert qm.claim_next() is None


# ---- poison handling ------------------------------------------------------

def test_poison_quarantined_and_does_not_block(qm):
    # An unparseable item that sorts FIRST must not block the valid newer item.
    (qm.queue_dir / "0000000000001-x-000000-dead.vibe").write_text("not valid base64 ===")
    qm.add("ok", message="good")

    token, data = qm.claim_next()
    assert data["message"] == "good"                       # valid item returned despite poison
    assert list(qm.queue_dir.glob("*.poison"))             # poison quarantined
    qm.ack(token)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode("ascii")


def test_add_rejects_a_bad_event_without_writing(qm):
    with pytest.raises(VibeValidationError):
        qm.add("nope", message="no")
    with pytest.raises(VibeValidationError):
        qm.add("ok", vibe_level=0)
    assert _vibes(qm) == []


def test_add_stores_canonical_fields(qm):
    qm.add("OK", message="hi", vibe_level="7.0", emotion="Alert", character="Karren", speed="1.5")
    _, data = qm.claim_next()
    assert data["intent"] == "ok"
    assert data["vibe_level"] == "7"
    assert data["emotion"] == "alert"
    assert data["character"] == "karren"
    assert data["speed"] == "1.5"
    assert int(data["vibe_level"]) == 7


def test_claim_quarantines_an_invalid_event_without_retry(qm):
    (qm.queue_dir / "0000000000001-x-000000-bad.vibe").write_text(
        f"intent={_b64('ok')}\nvibe_level=99\nemotion=neutral\nspeed=1.0\n"
    )
    qm.add("ok", message="good")

    token, data = qm.claim_next()

    assert data["message"] == "good"
    assert list(qm.queue_dir.glob("*.poison"))
    assert list(qm.queue_dir.glob("*.r*.vibe")) == []
    qm.ack(token)
    assert qm.claim_next() is None


def test_claim_canonicalises_a_numeric_vibe_level(qm):
    (qm.queue_dir / "0000000000001-x-000000-num.vibe").write_text(
        f"intent={_b64('OK')}\nvibe_level=5.0\nemotion=neutral\nspeed=1\n"
    )
    _, data = qm.claim_next()
    assert data["intent"] == "ok"
    assert data["vibe_level"] == "5"
    assert int(data["vibe_level"]) == 5


def test_missing_intent_is_poison(qm):
    (qm.queue_dir / "0000000000001-x-000000-dead.vibe").write_text("vibe_level=5\nemotion=neutral")
    assert qm.claim_next() is None                         # only the structurally-invalid item present
    assert list(qm.queue_dir.glob("*.poison"))


# ---- crash recovery -------------------------------------------------------

def test_crash_before_ack_is_recoverable(qm, dead_pid):
    qm.add("ok", message="survive")
    # Simulate a worker that claimed the item then died (proc file owned by a dead pid).
    [vibe] = _vibes(qm)
    proc = qm.queue_dir / f"{vibe.name[:-len('.vibe')]}.proc.{dead_pid}"
    os.replace(vibe, proc)

    token, data = qm.claim_next()                          # recovers + re-claims
    assert data["message"] == "survive"
    assert ".r1." in token.name                            # retry counter bumped
    qm.ack(token)


def test_live_workers_claim_not_recovered(qm):
    qm.add("ok", message="mine")
    [vibe] = _vibes(qm)
    proc = qm.queue_dir / f"{vibe.name[:-len('.vibe')]}.proc.{os.getpid()}"   # alive owner
    os.replace(vibe, proc)
    assert qm.claim_next() is None                         # not stolen from the live worker
    assert proc.exists()


def test_expired_claim_is_recovered_even_if_pid_is_alive(qm):
    qm.add("ok", message="lease-expired")
    [vibe] = _vibes(qm)
    proc = qm.queue_dir / f"{vibe.name[:-len('.vibe')]}.proc.{os.getpid()}"
    os.replace(vibe, proc)
    expired = time.time() - queue_manager._CLAIM_LEASE_SECONDS - 1
    os.utime(proc, (expired, expired))

    token, data = qm.claim_next()

    assert data["message"] == "lease-expired"
    assert ".r1." in token.name
    qm.ack(token)


def test_nack_requeues_with_bounded_retry(qm):
    qm.add("ok", message="retry-me")
    token, _ = qm.claim_next()

    qm.nack(token, "dispatch failed")

    [pending] = _vibes(qm)
    assert ".r1." in pending.name


def test_retry_bounded_then_quarantine(qm, dead_pid):
    qm.add("ok", message="poison-at-process")
    [vibe] = _vibes(qm)
    # Pretend it already failed MAX_RETRIES times under a now-dead worker.
    proc = qm.queue_dir / f"{vibe.name[:-len('.vibe')]}.r{queue_manager._MAX_RETRIES}.proc.{dead_pid}"
    os.replace(vibe, proc)
    assert qm.claim_next() is None                         # not re-queued again
    assert list(qm.queue_dir.glob("*.poison"))


# ---- worker election ------------------------------------------------------

@pytest.fixture
def spawns(monkeypatch):
    """Mock just the worker spawn (returns a live pid), so real subprocess use
    elsewhere — _is_zombie, the dead_pid fixture — is untouched."""
    calls = {"n": 0}

    def fake_spawn(self):
        calls["n"] += 1
        return os.getpid()                                 # a guaranteed-alive pid
    monkeypatch.setattr(QueueManager, "_spawn_worker", fake_spawn)
    return calls


def test_election_spawns_exactly_one(qm, spawns):
    qm.start_worker_if_needed()
    qm.start_worker_if_needed()                 # second sees the live pid
    assert spawns["n"] == 1
    assert qm.worker_pid_file.read_text().strip() == str(os.getpid())


def test_stale_pid_respawns(qm, spawns, dead_pid):
    qm.worker_pid_file.write_text(str(dead_pid))
    qm.start_worker_if_needed()
    assert spawns["n"] == 1                                # dead pid ⇒ respawn


def test_zombie_pid_respawns(qm, spawns, monkeypatch):
    qm.worker_pid_file.write_text(str(os.getpid()))        # alive…
    monkeypatch.setattr(queue_manager, "_is_zombie", lambda pid: True)   # …but a zombie
    qm.start_worker_if_needed()
    assert spawns["n"] == 1


def test_spawn_falls_back_to_devnull_when_log_unavailable(qm, monkeypatch):
    real_open = open

    def fail_log(path, *args, **kwargs):
        if str(path) == queue_manager._LOG:
            raise OSError("read-only")
        return real_open(path, *args, **kwargs)

    captured = {}

    class Proc:
        pid = 12345

    def fake_popen(args, **kwargs):
        captured.update(kwargs)
        return Proc()

    monkeypatch.setattr("builtins.open", fail_log)
    monkeypatch.setattr(queue_manager.subprocess, "Popen", fake_popen)

    assert qm._spawn_worker() == 12345
    assert captured.get("stdin") is subprocess.DEVNULL
    assert captured["stdout"] is subprocess.DEVNULL
    assert captured["stderr"] is subprocess.DEVNULL


def test_worker_log_uses_resource_log_dir():
    assert queue_manager._LOG == queue_manager.resources.log_file("worker.log")


# ---- PID ownership safety -------------------------------------------------

def test_release_only_removes_own_pid(qm, dead_pid):
    qm.worker_pid_file.write_text(str(dead_pid))           # another worker's record
    qm.release_worker_slot()
    assert qm.worker_pid_file.exists()                     # must NOT remove someone else's

    qm.worker_pid_file.write_text(str(os.getpid()))        # our own record
    qm.release_worker_slot()
    assert not qm.worker_pid_file.exists()


def test_queue_dir_is_private(qm):
    assert (qm.queue_dir.stat().st_mode & 0o777) == 0o700


def test_claim_skips_unclaimable_oserror(qm, monkeypatch):
    # #23: a filesystem error claiming one item must not stop the whole scan.
    qm.add("ok", message="first")
    qm.add("ok", message="second")
    first = sorted(qm.queue_dir.glob("*.vibe"))[0]
    real_replace = os.replace

    def flaky_replace(src, dst):
        if str(src) == str(first):
            raise OSError("EIO simulated")
        return real_replace(src, dst)

    monkeypatch.setattr(queue_manager.os, "replace", flaky_replace)
    claimed = qm.claim_next()
    assert claimed is not None
    token, data = claimed
    assert data["message"] == "second"          # skipped the unclaimable first, claimed the next
    qm.ack(token)


def test_claim_does_not_retry_persistently_unclaimable_file(qm, monkeypatch):
    qm.add("ok", message="first")
    first = sorted(qm.queue_dir.glob("*.vibe"))[0]
    attempts = {"n": 0}

    def always_fail_replace(src, dst):
        if str(src) == str(first):
            attempts["n"] += 1
            raise OSError("EIO simulated")
        raise AssertionError(f"unexpected replacement: {src} -> {dst}")

    monkeypatch.setattr(queue_manager.os, "replace", always_fail_replace)

    assert qm.claim_next() is None
    assert qm.claim_next() is None
    assert attempts["n"] == 1


def test_claim_retries_unclaimable_file_after_backoff(qm, monkeypatch):
    qm.add("ok", message="first")
    first = sorted(qm.queue_dir.glob("*.vibe"))[0]
    now = {"t": 100.0}
    attempts = {"n": 0}
    real_replace = os.replace

    def flaky_once_replace(src, dst):
        if str(src) == str(first) and attempts["n"] == 0:
            attempts["n"] += 1
            raise OSError("EIO simulated")
        attempts["n"] += 1
        return real_replace(src, dst)

    monkeypatch.setattr(queue_manager.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(queue_manager.os, "replace", flaky_once_replace)

    assert qm.claim_next() is None
    assert qm.claim_next() is None
    now["t"] += queue_manager._CLAIM_ERROR_BACKOFF_SECONDS + 0.1
    claimed = qm.claim_next()

    assert claimed is not None
    token, data = claimed
    assert data["message"] == "first"
    assert attempts["n"] == 2
    qm.ack(token)


def test_holding_claim_keeps_a_long_pause_from_being_reclaimed(qm, monkeypatch):
    monkeypatch.setattr(queue_manager, "_CLAIM_LEASE_SECONDS", 0.4)
    monkeypatch.setattr(queue_manager, "_HEARTBEAT_SECONDS", 0.05)
    qm.add("ok", message="held")
    token, _ = qm.claim_next()

    with qm.holding_claim(token):
        time.sleep(0.9)
        assert qm.claim_next() is None
        assert token.exists()
    qm.ack(token)


def test_holding_claim_releases_a_dispatch_past_the_budget(qm, monkeypatch):
    """A hung dispatch stops being refreshed, so the short lease can reclaim it."""
    monkeypatch.setattr(queue_manager, "_CLAIM_LEASE_SECONDS", 0.25)
    monkeypatch.setattr(queue_manager, "_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(queue_manager, "_DISPATCH_BUDGET_SECONDS", 0.4)
    qm.add("ok", message="hung")
    token, _ = qm.claim_next()

    with qm.holding_claim(token):
        time.sleep(0.2)
        assert qm.claim_next() is None
        assert token.exists()
        time.sleep(1.0)
        recovered = qm.claim_next()

    assert recovered is not None
    recovered_token, data = recovered
    assert data["message"] == "hung"
    assert not token.exists()
    qm.ack(recovered_token)


def test_holding_claim_stops_when_the_dispatch_returns(qm, monkeypatch):
    monkeypatch.setattr(queue_manager, "_HEARTBEAT_SECONDS", 0.05)
    qm.add("ok", message="done")
    token, _ = qm.claim_next()
    with qm.holding_claim(token):
        time.sleep(0.1)
    old = time.time() - 30
    os.utime(token, (old, old))
    time.sleep(0.2)
    assert time.time() - token.stat().st_mtime > 5
    qm.ack(token)


def test_expired_lease_recovers_claim_even_if_pid_alive(tmp_path):
    """A .proc whose lease has expired is recovered even when the owner PID is still
    alive — guards against an over-long lease (#73)."""
    from nhj import queue_manager
    from nhj.queue_manager import QueueManager

    qm = QueueManager(queue_dir=str(tmp_path))
    qm.add("ok", "hi")
    claimed = qm.claim_next()
    assert claimed is not None
    token, _ = claimed
    assert token.exists()                       # claimed .proc owned by our live pid

    old = time.time() - (queue_manager._CLAIM_LEASE_SECONDS + 1)
    os.utime(token, (old, old))                 # age the claim past the lease

    qm._recover_stale()
    assert not token.exists()                   # re-queued despite the live pid
    assert list(tmp_path.glob("*.vibe"))
