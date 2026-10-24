from __future__ import annotations

import time

import pytest

from nhj import queue_manager, worker
from nhj.queue_manager import QueueManager


class _NoHeartbeat:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def test_dispatch_failure_nacks_instead_of_acknowledging(monkeypatch, tmp_path):
    token = tmp_path / "task.proc.123"

    class FakeQueue:
        def __init__(self):
            self.claims = [(token, {"intent": "ok", "vibe_level": "invalid"})]
            self.acked = []
            self.nacked = []

        def claim_next(self):
            return self.claims.pop(0) if self.claims else None

        def ack(self, claimed):
            self.acked.append(claimed)

        def nack(self, claimed, reason):
            self.nacked.append((claimed, reason))

        def release_worker_slot(self):
            pass

        def holding_claim(self, token):
            return _NoHeartbeat()

    queue = FakeQueue()
    monkeypatch.setattr(worker, "QueueManager", lambda: queue)
    monkeypatch.setattr(worker, "load_adapters", lambda order: [])
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    # Don't depend on the ambient ~/.config/nhj mute flag — a muted env would skip
    # dispatch and ack instead of nacking, making this test environment-sensitive.
    monkeypatch.setattr(worker, "get_flag", lambda *a, **k: False)

    worker.main()

    assert queue.acked == []
    assert queue.nacked and queue.nacked[0][0] == token


@pytest.mark.parametrize("speed", ["nan", "inf", "2.5", "0.25", "bad"])
def test_invalid_queued_speed_nacks_without_dispatch(monkeypatch, tmp_path, speed):
    token = tmp_path / "task.proc.123"

    class FakeQueue:
        def __init__(self):
            self.claims = [(token, {"intent": "ok", "vibe_level": "5", "speed": speed})]
            self.acked = []
            self.nacked = []

        def claim_next(self):
            return self.claims.pop(0) if self.claims else None

        def ack(self, claimed):
            self.acked.append(claimed)

        def nack(self, claimed, reason):
            self.nacked.append((claimed, reason))

        def release_worker_slot(self):
            pass

        def holding_claim(self, token):
            return _NoHeartbeat()

    class FakeAdapter:
        calls = []

        def fire(self, **kwargs):
            self.calls.append(kwargs)

    queue = FakeQueue()
    adapter = FakeAdapter()
    monkeypatch.setattr(worker, "QueueManager", lambda: queue)
    monkeypatch.setattr(worker, "load_adapters", lambda order: [adapter])
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(worker, "get_flag", lambda *a, **k: False)

    worker.main()

    assert queue.acked == []
    assert queue.nacked and queue.nacked[0][0] == token
    assert adapter.calls == []


def test_valid_queued_speed_dispatches(monkeypatch):
    calls = []

    class FakeAdapter:
        def fire(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(worker, "resolve_character", lambda intent: object())

    worker._dispatch({"intent": "ok", "vibe_level": "5", "speed": "1.5"}, [FakeAdapter()])

    assert calls[0]["speed"] == 1.5


def test_slow_adapter_is_not_reclaimed_mid_dispatch(monkeypatch, tmp_path):
    """A dispatch longer than the lease must not be claimed again (#48 B5)."""
    monkeypatch.setattr(queue_manager, "_CLAIM_LEASE_SECONDS", 0.4)
    monkeypatch.setattr(queue_manager, "_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(worker, "resolve_character", lambda intent: object())
    monkeypatch.setattr("nhj.gate.voice_allowed", lambda source="": False)

    qm = QueueManager(queue_dir=str(tmp_path / "queue"))
    qm.add("ok", message="slow")
    claimed = qm.claim_next()
    assert claimed is not None
    token, task = claimed
    stolen = {}

    class Slow:
        channel = "visual"

        def fire(self, **kwargs):
            time.sleep(0.9)
            stolen["next"] = qm.claim_next()

    with qm.holding_claim(token):
        worker._dispatch(task, [Slow()])

    assert stolen["next"] is None
    assert token.exists()
    qm.ack(token)
