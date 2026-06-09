from __future__ import annotations

import pytest

from nhj import worker


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
