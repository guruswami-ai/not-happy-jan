"""TTS speed + audio-device recovery tests (issue #4).

Speed is applied exactly once (at playback), with user-override precedence over the
character band; the audio controller survives transient stream-open failures and
device switches with bounded backoff.
"""
from __future__ import annotations

import types

from nhj.adapters import audio
from nhj.duck_player import StreamKeeper


# ---- speed: applied once, with precedence ---------------------------------

def test_effective_speed_precedence():
    assert audio._effective_speed(1.0, 1.2) == 1.2     # no user override → band speed
    assert audio._effective_speed(1.5, 1.2) == 1.5     # explicit user speed wins
    assert audio._effective_speed(1.0, 1.0) == 1.0


def test_speed_applied_once_at_playback_not_synthesis(monkeypatch):
    captured, synth_kw = {}, {}
    monkeypatch.setattr(audio, "pick_return_clip", lambda: None)
    monkeypatch.setattr(audio, "pick_bank_clip", lambda *a, **k: None)
    monkeypatch.setattr(audio, "cache_hit", lambda *a, **k: None)        # force live synth
    monkeypatch.setattr(audio, "synth_qwen", lambda text, url, **kw: (synth_kw.update(kw), b"WAV")[1])
    monkeypatch.setattr(audio, "_save_and_cache", lambda *a, **k: None)
    monkeypatch.setattr(audio, "_write_temp_wav", lambda d: "/tmp/x.wav")
    monkeypatch.setattr(audio, "_emit_voice",
                        lambda path, rate=1.0, gain=1.0, ephemeral=False: captured.update(rate=rate))
    monkeypatch.setenv("TTS_ENGINE", "qwen")

    deliv = types.SimpleNamespace(voice="jan", text="hi", band="mid", speed=1.2,
                                  model_tier="fast", instruct="", temperature=0.8)
    char = types.SimpleNamespace(delivery=lambda intent, message: deliv)

    # user speed 1.0 → band's 1.2 applied once at playback; synth never sees speed
    audio.AudioAdapter.fire(None, "ok", "hi", char, vibe_level=5, speed=1.0)
    assert captured["rate"] == 1.2
    assert "speed" not in synth_kw, "speed must not be applied at synthesis"

    # explicit user speed overrides the band
    audio.AudioAdapter.fire(None, "ok", "hi", char, vibe_level=5, speed=1.5)
    assert captured["rate"] == 1.5


# ---- device recovery: bounded-backoff stream keeper -----------------------

class _Mixer:
    def __init__(self, fail_starts=0, fail_reopens=0):
        self.starts = self.reopens = 0
        self.fail_starts, self.fail_reopens = fail_starts, fail_reopens

    def start(self):
        self.starts += 1
        if self.starts <= self.fail_starts:
            raise RuntimeError("PortAudio busy")

    def reopen(self):
        self.reopens += 1
        if self.reopens <= self.fail_reopens:
            raise RuntimeError("device gone")


def test_initial_open_retries_then_recovers():
    m = _Mixer(fail_starts=1, fail_reopens=1)
    k = StreamKeeper(m, cap=30)
    now = 0.0
    assert k.ensure(now) is False and k.next_try > now    # 1st start fails → backoff
    assert k.ensure(now) is False                          # within backoff → no extra attempt
    assert m.starts == 1
    now = k.next_try
    assert k.ensure(now) is False                          # 2nd fails (reopen)
    assert m.reopens == 1
    now = k.next_try
    assert k.ensure(now) is True                           # 3rd succeeds (reopen)
    assert k.live and k.attempts == 0 and m.starts == 1 and m.reopens == 2


def test_device_change_reopens_and_retries_failed_reopen():
    m = _Mixer(fail_reopens=2)
    k = StreamKeeper(m, cap=30)
    assert k.ensure(0.0) is True                           # initial start ok (decoder + stream)
    k.mark_down()                                          # output device switched
    now = 0.0
    assert k.ensure(now) is False                          # reopen #1 fails
    now = k.next_try
    assert k.ensure(now) is False                          # reopen #2 retried (name didn't change)
    now = k.next_try
    assert k.ensure(now) is True                           # reopen #3 ok
    assert m.reopens == 3


def test_backoff_is_bounded_no_tight_loop():
    class AlwaysFail:
        def start(self): raise RuntimeError("x")
        def reopen(self): raise RuntimeError("x")
    k = StreamKeeper(AlwaysFail(), cap=5.0)
    now = 0.0
    for _ in range(12):
        before = now
        k.ensure(now)
        assert 0 < (k.next_try - before) <= 5.0 + 1e-9     # always waits, never beyond the cap
        now = k.next_try
    assert not k.live
