"""Phase-1 instrumentation of the realtime path: underruns, short frames, callback wall time.

No audio device and no PortAudio. The mixer's callback is driven directly with a buffer shaped exactly
like the one sounddevice hands it, so these run on both CI legs, where there is no output device at all
— which is the whole point of counting our own wall clock instead of trusting the device to report xruns.

`status` gets the same treatment as the buffer: a real CallbackFlags, or None. Passing a convenient int
there is what let a `TypeError` ship that would have stopped the stream on its first xrun — see
test_a_real_status_object_never_breaks_the_callback.
"""
from __future__ import annotations

import numpy as np
import sounddevice                      # dev extra carries it; CI's linux legs install PortAudio for it

from nhj import duck_player
from nhj import stats as S
from nhj.duck_player import BLOCK, CH, Mixer


def _out(frames: int = BLOCK) -> np.ndarray:
    return np.zeros((frames, CH), dtype=np.float32)


def _feed(mixer: Mixer, blocks: int, value: float = 0.01) -> None:
    """Put decoded audio on the bed buffer the way the decoder thread does: BLOCK-length chunks."""
    for _ in range(blocks):
        mixer.buf.append(np.full((BLOCK, CH), value, dtype=np.float32))


def _clip(frames: int = BLOCK * 4, value: float = 0.05) -> duck_player._Clip:
    return duck_player._Clip(np.full((frames, CH), value, dtype=np.float32), 0.9)


# ---- counting ---------------------------------------------------------------
def test_every_callback_is_counted_and_timed():
    m = Mixer([])
    m.pause()                                       # silent bed: no starvation to report
    m._callback(_out(), BLOCK, None, None)
    st = m.stats()
    assert st["blocks"] == 1 and st["window_blocks"] == 1
    assert st["callback_p50_us"] is not None and st["callback_max_us"] >= st["callback_p95_us"]
    assert st["deadline_misses"] == 0 and st["underruns"] == 0
    assert st["block_us"] == round(duck_player._BLOCK_US, 1) and st["block_frames"] == BLOCK


def test_a_bed_that_asked_for_audio_and_got_none_is_an_underrun():
    """The failure this whole phase exists to make visible: the music stopped and nobody knew."""
    m = Mixer([])
    m.resume()                                      # meant to be playing; the decoder never ran
    m._callback(_out(), BLOCK, None, None)
    st = m.stats()
    assert st["underruns"] == 1 and st["short_frames"] == BLOCK


def test_a_supplied_bed_underruns_neither_once_nearly():
    m = Mixer([])
    m.resume()
    _feed(m, 8)
    for _ in range(8):
        m._callback(_out(), BLOCK, None, None)
    st = m.stats()
    assert st["underruns"] == 0 and st["short_frames"] == 0 and st["blocks"] == 8


def test_a_partially_supplied_bed_reports_the_frames_it_missed():
    m = Mixer([])
    m.resume()
    m.buf.append(np.zeros((BLOCK // 4, CH), dtype=np.float32))
    m._callback(_out(), BLOCK, None, None)
    st = m.stats()
    assert st["underruns"] == 1 and st["short_frames"] == BLOCK - BLOCK // 4


def test_a_paused_bed_is_never_an_underrun():
    m = Mixer([])                                   # the bed starts paused: music starts silent
    for _ in range(5):
        m._callback(_out(), BLOCK, None, None)
    assert m.stats()["underruns"] == 0 and m.stats()["blocks"] == 5


def test_a_callback_that_outlived_its_block_is_counted_as_a_miss():
    m = Mixer([])
    m.pause()
    late = duck_player.time.perf_counter_ns() - int(2 * duck_player._BLOCK_US * 1000)
    m._tally(late, BLOCK, BLOCK, None)
    assert m.stats()["deadline_misses"] == 1


def test_a_fast_callback_is_not_a_miss():
    m = Mixer([])
    m.pause()
    m._tally(duck_player.time.perf_counter_ns(), BLOCK, BLOCK, None)
    assert m.stats()["deadline_misses"] == 0


def _flags(**named: bool):
    """A real sounddevice.CallbackFlags. Constructed the only way the class allows: it takes a
    PortAudio bitmask and exposes the individual flags as properties (settable except priming_output)."""
    f = sounddevice.CallbackFlags()
    for name, value in named.items():
        setattr(f, name, value)
    return f


def test_device_side_flags_are_remembered_by_name():
    m = Mixer([])
    m.pause()
    m._callback(_out(), BLOCK, None, _flags(output_underflow=True))
    m._callback(_out(), BLOCK, None, _flags(output_underflow=True))     # the same flag twice
    m._callback(_out(), BLOCK, None, _flags(output_overflow=True))
    m._callback(_out(), BLOCK, None, None)                              # a clean block reports None
    assert m.stats()["pa_flags"] == ["output_overflow", "output_underflow"]


def test_a_real_status_object_never_breaks_the_callback():
    """The regression that a made-up status value hid for a whole review cycle.

    `status` is never an int: sounddevice passes None on a clean block and a CallbackFlags otherwise,
    and CallbackFlags defines neither __int__ nor __index__. The first version of the tally did
    `self._pa_flags |= int(status)`, which raises TypeError *inside the callback* — sounddevice catches
    exceptions from a callback, stops the stream and never calls us again. So the first output underflow
    of the evening would have been the last sound the hold music ever made, while StreamKeeper — whose
    `live` flag that exception also skipped — kept reporting a healthy stream and never restarted it.

    The old form of this test passed the integer 4 and passed happily, which is the lesson: a stub
    shaped like whatever the code expects proves nothing about the device's shape.
    """
    m = Mixer([])
    m.pause()
    for status in (sounddevice.CallbackFlags(),                  # truthy check: no flags at all
                   _flags(output_underflow=True),
                   _flags(output_overflow=True, input_underflow=True),
                   None):
        m._callback(_out(), BLOCK, None, status)
    st = m.stats()
    assert st["blocks"] == 4                                # the callback survived every shape
    assert st["pa_flags"] == ["input_underflow", "output_overflow", "output_underflow"]


def test_flags_survive_across_callbacks_and_the_snapshot_carries_them():
    m = Mixer([])
    m.pause()
    m._callback(_out(), BLOCK, None, _flags(output_underflow=True))
    duck_player.publish_mixer_stats(m)
    assert S.read_mixer()["pa_flags"] == ["output_underflow"]


def test_the_ring_is_bounded_so_a_long_session_cannot_grow_the_heap():
    m = Mixer([])
    m.pause()
    for _ in range(duck_player._STATS_RING + 500):
        m._callback(_out(), BLOCK, None, None)
    st = m.stats()
    assert st["blocks"] == duck_player._STATS_RING + 500
    assert st["window_blocks"] == duck_player._STATS_RING   # percentiles are a window, and say so


# ---- the gate that phase 1 was for -----------------------------------------
def test_a_full_mix_fits_well_inside_the_block_deadline():
    """The CI-runnable form of the mixer budget: bed + voice + fx + ambient + compressor, measured on a
    real callback, against the same budget `nhj stats --check` applies on a real host.

    This is an absolute deadline rather than a stored ratio because the invariant IS a deadline: the
    callback must finish inside the block it is filling. Runner noise of ±30 % cannot move a number that
    has roughly an order of magnitude of headroom, whereas a 10 % ratio gate on microseconds would fail
    on whichever job landed on the wrong host.
    """
    m = Mixer([])
    m.resume()
    m._comp = True                                  # the bus compressor is part of the cost
    m.music_phone = m._tel_sos is not None          # call-centre tinny bed, when scipy is installed
    _feed(m, 64)
    m.play_sequence([], bus="fx")                   # no-op, but exercises the bus path
    m._fx.append(_clip())
    m._voiceq.append(_clip())
    m._ambient_loop = _clip()
    limit = duck_player._BLOCK_US * S.BUDGETS["callback_block_frac"]
    for _ in range(400):
        _feed(m, 1)
        if m._voice_cur is None and not m._voiceq:
            m._voiceq.append(_clip())               # keep a voice clip on the bus for most blocks
        if not m._fx:
            m._fx.append(_clip())
        if len(m.buf) < 8:                          # a starved bed is the thing under test, not the noise
            _feed(m, 8)
        m._callback(_out(), BLOCK, None, None)
    st = m.stats()
    # The figures docs/AUDIO-STANDARD.md §9c quotes come from this line under `pytest -s`.
    print(f"\n[callback] p50 {st['callback_p50_us']} us   p95 {st['callback_p95_us']} us   "
          f"max {st['callback_max_us']} us   of a {duck_player._BLOCK_US:.1f} us block   "
          f"({st['blocks']} blocks, {st['window_blocks']} in the window)")
    assert st["underruns"] == 0, f"the bed starved {st['underruns']} times ({st['short_frames']} frames)"
    assert st["deadline_misses"] == 0
    assert st["callback_p95_us"] < limit, (
        f"callback p95 {st['callback_p95_us']} us vs {limit:.0f} us "
        f"({S.BUDGETS['callback_block_frac']:.0%} of the {duck_player._BLOCK_US:.0f} us block)")


def test_the_gate_actually_bites_on_a_callback_that_cannot_keep_up():
    """A budget nobody can fail is a comment, not a gate."""
    m = Mixer([])
    m.pause()
    late = duck_player.time.perf_counter_ns() - int(40 * duck_player._BLOCK_US * 1000)
    for _ in range(20):
        m._tally(late, BLOCK, BLOCK, None)
    st = m.stats()
    assert S.budget_failures(S.mixer_summary(dict(st, pid=1234, written_at=duck_player.time.time())),
                             {"rungs": {}})


# ---- publishing ------------------------------------------------------------
def test_the_snapshot_round_trips_through_the_state_dir():
    m = Mixer([])
    m.pause()
    _feed(m, 2)
    m._callback(_out(), BLOCK, None, None)
    S.write_mixer(m.stats())
    snap = S.mixer_summary()
    assert snap["blocks"] == 1 and snap["pid_alive"] is True and snap["underruns"] == 0
    assert snap["stale"] is False and snap["sample_rate"] == duck_player.SR


def test_percentiles_are_computed_in_the_mixer_not_by_the_reader():
    """The realtime process owns the samples; what crosses the process boundary is already summarised,
    so `nhj stats` never has to read a per-block array written by a thread it cannot synchronize with."""
    m = Mixer([])
    m.pause()
    for _ in range(10):
        m._callback(_out(), BLOCK, None, None)
    assert set(m.stats()) == {"block_us", "block_frames", "sample_rate", "uptime_s", "blocks",
                              "window_blocks", "callback_p50_us", "callback_p95_us", "callback_max_us",
                              "deadline_misses", "underruns", "short_frames", "pa_flags", "paused"}


def test_the_controller_publishes_what_nhj_stats_reads():
    """The controller loop's part of the bargain: the counters only exist to a reader if the loop hands
    them over, including whether the output stream is actually live."""
    m = Mixer([])
    m.pause()
    _feed(m, 1)
    m._callback(_out(), BLOCK, None, None)

    class Keeper:
        live = True
        attempts = 2

    snap = duck_player.publish_mixer_stats(m, Keeper())
    assert snap["blocks"] == 1 and snap["stream_live"] is True and snap["stream_attempts"] == 2
    read = S.mixer_summary()
    assert read["blocks"] == 1 and read["stream_live"] is True and read["stale"] is False


def test_publishing_without_a_keeper_records_no_stream_claim():
    """A mixer can be measured before anything owns its stream — and must not imply it was live."""
    m = Mixer([])
    snap = duck_player.publish_mixer_stats(m)
    assert snap["blocks"] == 0
    assert "stream_live" not in S.read_mixer()


def test_a_status_file_that_will_not_write_does_not_stop_the_music(monkeypatch):
    """Publishing is a courtesy to the reader, not a dependency of playback."""
    def boom(*a, **k):
        raise OSError("read-only filesystem")
    monkeypatch.setattr(S, "write_mixer", boom)
    m = Mixer([])
    assert duck_player.publish_mixer_stats(m) == {}      # swallowed, not raised into the controller loop
