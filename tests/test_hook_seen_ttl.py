"""hook-seen ledger TTL (#75): stale session entries are pruned on write."""
from __future__ import annotations

import json
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
