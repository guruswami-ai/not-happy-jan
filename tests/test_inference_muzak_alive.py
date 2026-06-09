"""_alive() must treat a corrupted / non-integer pid as not-alive, never raise (#81)."""
from __future__ import annotations

from nhj.inference_muzak import _alive


def test_alive_handles_garbage_pid():
    assert _alive("garbage") is False
    assert _alive("") is False
    assert _alive(None) is False
    assert _alive("12.5") is False
    assert _alive([1, 2]) is False     # TypeError path


def test_alive_false_for_unused_pid():
    assert _alive(2 ** 31 - 1) is False
