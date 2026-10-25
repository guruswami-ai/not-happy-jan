"""Issue #50 R2/R3: one music.json read per 250 ms tick, and no macOS device poll elsewhere."""
from __future__ import annotations

import time

from nhj import duck_player, inference_muzak


def test_refresh_mixer_state_reads_on_a_quarter_second_tick():
    class M:
        n = 0

        @staticmethod
        def _read():
            M.n += 1
            return {"busy": {}}

    last, fresh = duck_player.refresh_mixer_state(M, 10.0, 0.0)
    assert fresh == {"busy": {}} and last == 10.0 and M.n == 1
    last, fresh = duck_player.refresh_mixer_state(M, 10.2, last)
    assert fresh is None and last == 10.0 and M.n == 1
    last, fresh = duck_player.refresh_mixer_state(M, 10.25, last)
    assert fresh == {"busy": {}} and M.n == 2


def test_duck_gain_from_uses_the_snapshot():
    assert inference_muzak.duck_gain_from({}) == 1.0
    held = {"ducked": True, "duck_until": time.time() + 30}
    assert inference_muzak.duck_gain_from(held) == inference_muzak._DUCK_GAIN
    expired = {"ducked": True, "duck_until": time.time() - 1}
    assert inference_muzak.duck_gain_from(expired) == 1.0


def test_feed_uses_the_snapshot_and_does_not_reread():
    from nhj.music.director import Director
    from nhj.music import runtime

    class M:
        _MANUAL = "__manual__"

        @staticmethod
        def _read():
            raise AssertionError("music.json was read again")

        @staticmethod
        def _fresh_busy(d):
            return d.get("busy") or {}

    ts = time.time()
    st = {"busy": {"a": ts}, "music_accent": "err", "music_accent_ts": ts}
    director = Director()
    assert runtime.feed(M, director, 0.0, 5.0, 0.0, state=st) == ts
    assert director.at_bar(0).label == "breakdown"


def test_should_play_uses_the_snapshot(monkeypatch):
    monkeypatch.setattr(inference_muzak, "_continuous", lambda: False)
    assert inference_muzak._should_play({}) is False
    assert inference_muzak._should_play({"busy": {"s": time.time()}}) is True


def test_follow_output_device_never_starts_off_macos(monkeypatch):
    monkeypatch.setattr(duck_player.sys, "platform", "linux")
    monkeypatch.setenv("NHJ_FOLLOW_DEVICE", "1")
    assert duck_player.follow_output_device() is False


def test_follow_output_device_defaults_off_when_a_device_is_pinned(monkeypatch):
    monkeypatch.setattr(duck_player.sys, "platform", "darwin")
    monkeypatch.delenv("NHJ_FOLLOW_DEVICE", raising=False)
    monkeypatch.setenv("NHJ_OUTPUT_DEVICE", "hdmi")
    assert duck_player.follow_output_device() is False


def test_follow_output_device_explicit_value_wins_over_the_pin(monkeypatch):
    monkeypatch.setattr(duck_player.sys, "platform", "darwin")
    monkeypatch.setenv("NHJ_OUTPUT_DEVICE", "hdmi")
    monkeypatch.setenv("NHJ_FOLLOW_DEVICE", "1")
    assert duck_player.follow_output_device() is True
    monkeypatch.setenv("NHJ_FOLLOW_DEVICE", "0")
    assert duck_player.follow_output_device() is False


def test_follow_output_device_defaults_on_for_an_unpinned_mac(monkeypatch):
    monkeypatch.setattr(duck_player.sys, "platform", "darwin")
    monkeypatch.delenv("NHJ_FOLLOW_DEVICE", raising=False)
    monkeypatch.delenv("NHJ_OUTPUT_DEVICE", raising=False)
    monkeypatch.setattr("nhj.config.section", lambda name: {})
    assert duck_player.follow_output_device() is True
