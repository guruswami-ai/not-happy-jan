"""hook-seen ledger TTL (#75) and the locked cap (#44)."""
from __future__ import annotations

import json
import threading
import time

from nhj import hook


def test_seen_set_prunes_stale_entries(tmp_path, monkeypatch):
    seen = tmp_path / "hook-seen.json"
    monkeypatch.setattr(hook, "_SEEN", seen)
    monkeypatch.setattr(hook, "_SEEN_TTL", 1.0)
    seen.write_text(json.dumps({"old": {"sig": "x", "ts": time.time() - 100}}))
    hook._seen_set("new", "sig1")
    d = json.loads(seen.read_text())
    assert "old" not in d                     # stale session expired
    assert d["new"]["sig"] == "sig1"
    assert hook._seen_get("new") == "sig1"    # round-trips


def test_seen_get_reads_legacy_bare_sig(tmp_path, monkeypatch):
    seen = tmp_path / "hook-seen.json"
    monkeypatch.setattr(hook, "_SEEN", seen)
    seen.write_text(json.dumps({"s": "legacy-sig"}))  # pre-TTL format
    assert hook._seen_get("s") == "legacy-sig"


def test_seen_cap_keeps_the_session_just_touched(tmp_path, monkeypatch):
    """The cap used to keep the last 50 insertion-order keys, so refreshing the
    oldest session dropped it. A lost baseline re-fires the previous turn."""
    seen = tmp_path / "hook-seen.json"
    monkeypatch.setattr(hook, "_SEEN", seen)
    now = time.time()
    seed = {"long": {"sig": "old", "ts": now - 1000}}
    for i in range(50):
        seed[f"s{i}"] = {"sig": "x", "ts": now - 100 + i}
    seen.write_text(json.dumps(seed))
    hook._seen_set("long", "newsig")
    d = json.loads(seen.read_text())
    assert d["long"]["sig"] == "newsig"
    assert len(d) == hook._SEEN_CAP
    assert "s0" not in d                          # oldest untouched timestamp


def test_concurrent_seen_set_keeps_every_session(tmp_path, monkeypatch):
    seen = tmp_path / "hook-seen.json"
    monkeypatch.setattr(hook, "_SEEN", seen)
    n = 40
    threads = [threading.Thread(target=hook._seen_set, args=(f"s{i}", f"sig{i}"))
               for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    d = json.loads(seen.read_text())
    assert len(d) == n
    assert all(d[f"s{i}"]["sig"] == f"sig{i}" for i in range(n))
