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


def test_enable_clears_stop_flag_without_forcing_play(monkeypatch):
    """`enable()` must clear a leftover `stop` flag (the off→on wedge that silenced the
    mixer) and NOT force a play session when nothing should be playing (on-hold idle)."""
    import nhj.inference_muzak as M
    state = {"busy": {}, "stop": True}

    def fake_update(fn):
        fn(state)

    started = []
    monkeypatch.setattr(M, "_update", fake_update)
    monkeypatch.setattr(M, "_should_play", lambda: False)        # on-hold, no busy
    monkeypatch.setattr(M, "start_controller", lambda: started.append(True) or True)

    M.enable()
    assert "stop" not in state          # leftover stop flag cleared
    assert started == []                # didn't spawn a controller (nothing to play yet)


def test_enable_starts_controller_when_should_play(monkeypatch):
    import nhj.inference_muzak as M
    monkeypatch.setattr(M, "_update", lambda fn: fn({"stop": True}))
    monkeypatch.setattr(M, "_should_play", lambda: True)         # continuous / busy
    started = []
    monkeypatch.setattr(M, "start_controller", lambda: started.append(True) or True)
    M.enable()
    assert started == [True]            # spawns the bed when it should be playing
