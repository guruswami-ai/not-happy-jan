"""State-file concurrency (#63): locked read-modify-write must not drop writers."""
from __future__ import annotations

import threading

from nhj import state


def test_concurrent_set_dial_no_lost_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("NHJ_STATE_FILE", str(tmp_path / "state.json"))
    chars = [f"c{i}" for i in range(50)]
    threads = [threading.Thread(target=state.set_dial, args=(c, "level", 7))
               for c in chars]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dials = state.load()["dials"]
    assert set(dials) == set(chars)                       # no writer dropped
    assert all(dials[c]["level"] == 7 for c in chars)
