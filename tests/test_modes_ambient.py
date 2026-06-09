from __future__ import annotations

import numpy as np

from nhj import duck_player, modes


def test_apply_mode_publishes_ambient_bed(monkeypatch):
    calls = []
    monkeypatch.setattr(modes, "set_setting", lambda key, value: calls.append((key, value)))
    monkeypatch.setattr(modes, "set_flag", lambda *a, **k: None)
    monkeypatch.setattr(modes, "update", lambda fn: fn({"dials": {}}))
    monkeypatch.setattr(modes, "resolve_dial", lambda dial, value: (dial, value, value))
    monkeypatch.setattr(modes, "_apply_muzak", lambda *a, **k: None)

    modes.apply_mode("call-centre")
    assert ("ambient_mode", "callcentre") in calls
    assert ("ambient_gated", False) in calls          # call-centre room tone is continuous

    calls.clear()
    modes.apply_mode("special-forces")
    assert ("ambient_mode", "special-forces-radio") in calls
    assert ("ambient_gated", True) in calls           # radio bed only sounds under the voice


def test_set_ambient_loop_voice_gated_flag(monkeypatch):
    monkeypatch.setattr(duck_player, "_decode_file", lambda path: np.zeros((4, 2), dtype=np.float32))
    mixer = duck_player.Mixer([])
    mixer.set_ambient_loop("radio.wav", gain=0.5, voice_gated=True)
    assert mixer._ambient_gated is True
    mixer.set_ambient_loop("room.wav", gain=0.5)      # default: continuous (not gated)
    assert mixer._ambient_gated is False


def test_ambient_loop_wraps_and_clears(monkeypatch):
    loop = np.array(
        [[0.1, 0.4], [0.2, 0.5], [0.3, 0.6]],
        dtype=np.float32,
    )
    monkeypatch.setattr(duck_player, "_decode_file", lambda path: loop.copy())

    mixer = duck_player.Mixer([])
    mixer.set_ambient_loop("callcentre.wav", gain=1.0)

    out = np.zeros((6, duck_player.CH), dtype=np.float32)
    mixer._callback(out, 6, None, None)

    assert np.allclose(out[:, 0], [0.1, 0.2, 0.3, 0.1, 0.2, 0.3])
    assert np.allclose(out[:, 1], [0.4, 0.5, 0.6, 0.4, 0.5, 0.6])

    mixer.set_ambient_loop(None)
    cleared = np.zeros((3, duck_player.CH), dtype=np.float32)
    mixer._callback(cleared, 3, None, None)
    assert np.allclose(cleared, 0.0)
