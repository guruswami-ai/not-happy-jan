"""Test oblivious_chance from garble — a pure, deterministic probability function.

oblivious_chance returns the probability that Jan ignores a message entirely.
It's a pure mathematical function with no side effects: given chaos, return a
float between 0.0 and 0.5.
"""
from __future__ import annotations

import pytest

from nhj.garble import oblivious_chance


def test_oblivious_chance_is_zero_below_chaos_8():
    """At chaos 0-7 the chance is always 0.0."""
    for c in range(0, 8):
        assert oblivious_chance(c) == 0.0, f"chaos={c}"


def test_oblivious_chance_at_chaos_8():
    """chaos=8 → (8-7)/3 * 0.5 = 1/3 * 0.5 = 1/6 ≈ 0.16666..."""
    assert oblivious_chance(8) == pytest.approx(1.0 / 6.0)

def test_oblivious_chance_at_chaos_9():
    """chaos=9 → (9-7)/3 * 0.5 = 2/3 * 0.5 = 2/6 = 1/3 ≈ 0.33333..."""
    assert oblivious_chance(9) == pytest.approx(1.0 / 3.0)

def test_oblivious_chance_at_chaos_10():
    """chaos=10 → (10-7)/3 * 0.5 = 3/3 * 0.5 = 0.5"""
    assert oblivious_chance(10) == 0.5


def test_oblivious_chance_is_bounded_in_design_range():
    """Result is in [0.0, 0.5] for the design range 0-10."""
    for c in range(0, 11):
        v = oblivious_chance(c)
        assert 0.0 <= v <= 0.5, f"chaos={c} → {v}"


def test_oblivious_chance_exceeds_half_past_ten():
    """Past the design range (chaos > 10) the probability exceeds 0.5.
    The formula doesn't clamp; it's up to the caller to stay in [0, 10]."""
    assert oblivious_chance(11) == pytest.approx(2.0 / 3.0)
    assert oblivious_chance(12) == pytest.approx(5.0 / 6.0)
    assert oblivious_chance(20) == pytest.approx(13.0 / 3.0 * 0.5)  # 2.1666...


def test_oblivious_chance_is_monotonic():
    """Probability never decreases as chaos increases."""
    prev = -1.0
    for c in range(0, 21):
        cur = oblivious_chance(c)
        assert cur >= prev, f"chaos={c} dropped from {prev} to {cur}"
        prev = cur
