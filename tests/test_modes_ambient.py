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
    modes.apply_mode("special-forces")

    assert ("ambient_mode", "callcentre") in calls
    assert ("ambient_mode", "special-forces-radio") in calls


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
