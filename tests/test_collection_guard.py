"""Collection guard (issue #5).

Fails if pytest collects anything outside ``tests/`` — e.g. a recursive
fallback that grabs utility scripts elsewhere in the tree (some import heavy
optional deps). Runs the collector in a subprocess so it observes the real,
configured behaviour, and asserts a clean, deterministic exit.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_collection_is_deterministic_and_confined_to_tests():
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    out = proc.stdout + proc.stderr
    # Collection must succeed (exit 0) — not exit 5 "no tests" or a torch ImportError.
    assert proc.returncode == 0, f"collection failed (exit {proc.returncode}):\n{out}"
    # Every collected node id lives under tests/.
    stray = [ln.strip() for ln in out.splitlines()
             if "::" in ln and not ln.strip().startswith("tests/")]
    assert not stray, f"collected items outside tests/: {stray}"
