"""Generative hold music (#63): pack format, director, stem source, continuation composer, config."""
from __future__ import annotations

import json
import sys
import threading
import wave

import numpy as np
import pytest

from nhj import config_schema
from nhj.music import pack as packmod
from nhj.music import runtime, synth
from nhj.music.continuation import Composer
from nhj.music.director import MANUAL_BUSY, Director
from nhj.music.pack import PackError, load_pack, save_pack
from nhj.music.source import Section, StemSource, render_timeline

BPM, BARS = 120.0, 4                       # 2 s per bar, 96000 samples per bar: round numbers


def _const_pack(**levels):
    n = packmod.loop_samples(BPM, BARS)
    layers = {name: np.full((n, 2), v, np.float32) for name, v in levels.items()}
    return packmod.Pack("test", BPM, BARS, layers)


def _wav(path, data, rate=48000):
    pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ---- pack ---------------------------------------------------------------------------------
def test_pack_roundtrip(tmp_path):
    p = _const_pack(kick=0.25, bass=-0.5)
    save_pack(tmp_path / "p", "test", BPM, BARS, p.layers, {"licence": "MIT"})
    q = load_pack(tmp_path / "p")
    assert (q.name, q.bpm, q.bars, q.n) == ("test", BPM, BARS, p.n)
    assert q.provenance == {"licence": "MIT"}
    assert np.allclose(q.layers["bass"], p.layers["bass"], atol=1e-4)


@pytest.mark.parametrize("fname", ["../evil.wav", "sub/kick.wav", "/etc/passwd.wav", "kick.mp3", ".hidden.wav"])
def test_pack_rejects_non_plain_layer_files(tmp_path, fname):
    p = _const_pack(kick=0.1)
    root = save_pack(tmp_path / "p", "t", BPM, BARS, p.layers)
    m = json.loads((root / "manifest.json").read_text())
    m["layers"]["kick"] = fname
    (root / "manifest.json").write_text(json.dumps(m))
    with pytest.raises(PackError, match="plain .wav name"):
        load_pack(root)


def test_pack_rejects_wrong_length_and_rate(tmp_path):
    root = save_pack(tmp_path / "p", "t", BPM, BARS, _const_pack(kick=0.1).layers)
    _wav(root / "kick.wav", np.zeros((1000, 2), np.float32))
    with pytest.raises(PackError, match="samples, expected"):
        load_pack(root)
    _wav(root / "kick.wav", np.zeros((packmod.loop_samples(BPM, BARS), 2), np.float32), rate=44100)
    with pytest.raises(PackError, match="48 kHz"):
        load_pack(root)
    with pytest.raises(PackError):
        load_pack(tmp_path / "missing")


def test_synth_is_exact_deterministic_and_loops():
    a = synth.render_stems(seed=3, bars=4)
    b = synth.render_stems(seed=3, bars=4)
    n = packmod.loop_samples(synth.BPM, 4)
    assert set(a) == set(synth.LAYERS)
    for name in a:
        assert a[name].shape == (n, 2) and a[name].dtype == np.float32
        assert np.array_equal(a[name], b[name])
        assert float(np.max(np.abs(a[name]))) > 0.05
    assert float(np.max(np.abs(a["pad"][:200]))) > 0           # the loop's tail wraps into bar 1


# ---- director -----------------------------------------------------------------------------
def test_director_ladder_and_idle():
    d = Director()
    expect = {0: set(), 1: {"kick", "bass"}, 2: {"kick", "bass", "hats", "clap"},
              3: {"kick", "bass", "hats", "clap", "arp"}, 4: {"kick", "bass", "hats", "clap", "arp", "pad"}}
    for busy, layers in expect.items():
        d.update(busy, 0)
        assert d.at_bar(0).layers == frozenset(layers)
    d.update(9, 0)
    assert d.at_bar(0).layers == expect[4]


def test_director_err_breakdown_then_back_and_ok_does_not_cut_it():
    d = Director(breakdown_bars=4)
    d.update(4, 0)
    d.at_bar(0)
    d.accent("err")
    plans = [d.at_bar(b) for b in range(1, 7)]
    assert [p.label for p in plans] == ["breakdown"] * 4 + ["busy:4"] * 2
    assert plans[0].layers == frozenset({"pad", "arp"})
    d.accent("err")
    d.at_bar(7)                                              # breakdown starts at bar 7 (4 bars)
    d.accent("ok")
    assert d.at_bar(8).label == "breakdown"                  # ok never shortens a breakdown
    d.at_bar(9); d.at_bar(10); d.at_bar(11)
    d.accent("ok")
    assert d.at_bar(12).label == "release"
    assert d.at_bar(13).label == "busy:4"
    d.accent("warn")                                         # accepted, ignored
    assert d.at_bar(14).label == "busy:4"


def test_director_tension_ramp():
    d = Director(long_after_s=10, long_ramp_s=20)
    d.update(2, 5)
    assert d.at_bar(0).tension == 0.0
    d.update(2, 20)
    assert d.at_bar(1).tension == pytest.approx(0.5)
    d.update(2, 500)
    assert d.at_bar(2).tension == 1.0


# ---- stem source --------------------------------------------------------------------------
def _blocks(src, seconds):
    gen, out, got = src.iter_blocks(), [], 0
    while got < seconds * 48000:
        b = next(gen)
        out.append(b)
        got += len(b)
    return np.concatenate(out)


def test_layers_change_only_on_bar_lines():
    pack = _const_pack(kick=0.5, bass=0.25)
    d = Director()
    d.update(1, 0)
    src = StemSource(pack, d, [], threading.Event())
    gen, out = src.iter_blocks(), []
    spb = int(pack.spb)
    while sum(len(b) for b in out) <= spb:                   # all of bar 0, and bar 1 has begun (plan fixed)
        out.append(next(gen))
    d.update(0, 0)                                           # state changes mid-way through bar 1 ...
    while sum(len(b) for b in out) < spb + spb // 2:
        out.append(next(gen))
    d.update(1, 0)                                           # ... and flips back before bar 2 starts
    while sum(len(b) for b in out) < 3 * spb:
        out.append(next(gen))
    audio = np.concatenate(out)
    fade = int(0.5 * 60.0 / BPM * 48000)
    assert np.allclose(audio[fade:spb], 0.75, atol=1e-6)     # bar 0: kick + bass (after the fade-in from silence)
    # bar 1 plan was fixed at its bar line (busy=1) so the mid-bar change is not heard at all
    assert np.allclose(audio[spb:2 * spb], 0.75, atol=1e-6)
    assert np.allclose(audio[2 * spb:3 * spb], 0.75, atol=1e-6)
    d.update(0, 0)                                           # idle takes effect at the NEXT bar line only
    audio2 = np.concatenate([audio, *[next(gen) for _ in range(spb // 1024 + 2)]])
    assert np.allclose(audio2[3 * spb - 10:3 * spb], 0.75, atol=1e-6)
    assert np.allclose(audio2[3 * spb + int(0.5 * spb):3 * spb + int(0.9 * spb)], 0.0, atol=1e-6)
    assert [lbl for _, lbl in src.plan_log][:3] == ["busy:1", "busy:1", "busy:1"]


def test_blocks_never_straddle_a_bar_line_and_fades_are_smooth():
    pack = _const_pack(kick=0.5, bass=0.25, pad=0.1)
    d = Director()
    d.update(1, 0)
    src = StemSource(pack, d, [], threading.Event())
    gen, pos, spb = src.iter_blocks(), 0, int(pack.spb)
    for _ in range(spb * 2 // 1024 + 4):
        b = next(gen)
        assert pos // spb == (pos + len(b) - 1) // spb
        pos += len(b)
    d.update(4, 0)
    audio = np.concatenate([next(gen) for _ in range(spb // 1024 + 2)])
    assert np.max(np.abs(np.diff(audio[:, 0]))) < 0.01       # a half-beat fade, not a step


def test_tension_high_passes_everything_but_the_kick():
    pack = _const_pack(kick=0.5, bass=0.25)
    calm, tense = Director(), Director(long_after_s=0, long_ramp_s=1)
    calm.update(1, 0)
    tense.update(1, 1000)
    a = _blocks(StemSource(pack, calm, [], threading.Event()), 3)
    b = _blocks(StemSource(pack, tense, [], threading.Event()), 3)
    tail = slice(-48000, None)
    assert np.allclose(a[tail], 0.75, atol=1e-6)
    assert np.allclose(b[tail], 0.5, atol=0.02)               # DC bass swept away, kick untouched


class _OneShot:
    def __init__(self, section, at_bar):
        self.section, self.at_bar, self.observed = section, at_bar, []

    def observe(self, plan, bar):
        self.observed.append(bar)

    def take(self, plan, bar):
        return self.section if bar == self.at_bar else None


def test_section_replaces_the_stems_for_its_bars_only():
    pack = _const_pack(kick=0.5)
    spb = int(pack.spb)
    sec = Section(np.full((spb, 2), 0.9, np.float32), 1)
    d = Director()
    d.update(1, 0)
    prov = _OneShot(sec, at_bar=4)
    src = StemSource(pack, d, [], threading.Event(), prov)
    audio = _blocks(src, 12)
    assert np.allclose(audio[4 * spb + 2000:5 * spb - 2000], 0.9, atol=1e-6)
    assert np.allclose(audio[30000:4 * spb], 0.5, atol=1e-6)
    assert np.allclose(audio[5 * spb + 2000:8 * spb], 0.5, atol=1e-6)
    assert prov.observed[:2] == [0, 4]                          # phrase lines only


# ---- continuation composer ----------------------------------------------------------------
def _fake_runner(calls):
    def run(workdir, prompt, seed, duration, mask_start):
        calls.append((prompt, seed, round(duration, 2), round(mask_start, 2)))
        n = int(duration * 48000)
        _wav(workdir / "out.wav", np.full((n, 2), 0.3, np.float32))
    return run


def _composer(tmp_path, runner, **cfg):
    pack = _const_pack(kick=0.4, bass=0.1)
    conf = {"enabled": True, "keep_bars": 4, "total_bars": 8, "model_id": "fake-model", "licence": "test", **cfg}
    return pack, Composer(pack, conf, runner=runner, cache_dir=tmp_path / "cache")


def test_composer_prime_builds_level_matched_section_with_provenance(tmp_path):
    calls = []
    pack, c = _composer(tmp_path, _fake_runner(calls))
    layers = frozenset({"kick", "bass"})
    assert c.prime(layers)
    assert calls == [(c.prompt, 7, 16.0, 8.0)]                  # 8 bars @120 = 16 s total, mask from 4 bars
    sec = c.take(type("P", (), {"layers": layers})(), 4)
    assert sec.bars == 4 and len(sec.audio) == int(round(4 * pack.spb))
    ref = float(np.sqrt(np.mean(pack.mix(layers) ** 2)))
    assert float(np.sqrt(np.mean(sec.audio ** 2))) == pytest.approx(ref, rel=0.03)
    meta = json.loads(next((tmp_path / "cache").glob("*.json")).read_text())
    assert meta["model"] == "fake-model" and meta["licence"] == "test" and meta["seed"] == 7


def test_composer_cache_hit_skips_the_model_and_variants_rotate(tmp_path):
    calls = []
    layers = frozenset({"kick"})
    _, c1 = _composer(tmp_path, _fake_runner(calls))
    assert c1.prime(layers)
    _, c2 = _composer(tmp_path, _fake_runner(calls))
    assert c2.prime(layers) and len(calls) == 1                  # second composer read the cache
    plan = type("P", (), {"layers": layers})()
    assert c2.take(plan, 4) is not None
    assert c2.cache_key(layers, 0) != c2.cache_key(layers, 1)    # the next section is a new variant


def test_composer_failure_is_contained(tmp_path):
    def boom(*a):
        raise RuntimeError("gpu on fire")
    _, c = _composer(tmp_path, boom)
    assert c.prime(frozenset({"kick"})) is False
    assert "gpu on fire" in c.last_error
    c.observe(type("P", (), {"layers": frozenset({"kick"}), "label": "busy:1"})(), 4)   # backoff: no new thread


def test_composer_disabled_without_command_or_flag(tmp_path):
    pack = _const_pack(kick=0.1)
    assert not Composer(pack, {"enabled": True}, cache_dir=tmp_path).enabled          # no command
    assert not Composer(pack, {"command": ["x"]}, cache_dir=tmp_path).enabled         # not enabled
    assert Composer(pack, {"enabled": False}, cache_dir=tmp_path).prime(frozenset({"kick"})) is False


def test_command_runner_substitutes_per_argument_without_a_shell(tmp_path):
    tricky = 'loud; touch /tmp/pwned "quoted" $(id)'
    script = ("import sys,wave,pathlib;w=pathlib.Path(sys.argv[1]);(w/'prompt.txt').write_text(sys.argv[2]);"
              "f=wave.open(str(w/'out.wav'),'wb');f.setnchannels(2);f.setsampwidth(2);f.setframerate(48000);"
              "f.writeframes(b'\\0'*4*48000);f.close()")
    run = Composer._command_runner([sys.executable, "-c", script, "{workdir}", "{prompt}"])
    run(tmp_path, tricky, 7, 1.0, 0.5)
    assert (tmp_path / "prompt.txt").read_text() == tricky
    assert not __import__("pathlib").Path("/tmp/pwned").exists()


def test_composer_observe_requests_after_stable_phrases(tmp_path):
    calls = []
    _, c = _composer(tmp_path, _fake_runner(calls), stable_phrases=2)
    plan = type("P", (), {"layers": frozenset({"kick", "bass"}), "label": "busy:1"})()
    c.observe(plan, 0)
    assert not calls
    c.observe(plan, 4)
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon and t.name.startswith("Thread"):
            t.join(timeout=10)
    assert len(calls) == 1


# ---- runtime / config ---------------------------------------------------------------------
def test_make_source_falls_back_unless_generative_with_a_pack(tmp_path, monkeypatch):
    assert runtime.make_source({"mode": "recorded"}) is None
    built = []
    monkeypatch.setattr(runtime, "build_default_pack_async", lambda dest: built.append(dest))
    cfg = {"mode": "generative", "pack": str(tmp_path / "nope")}
    assert runtime.make_source(cfg) is None and built == []       # user-supplied pack: never auto-built
    monkeypatch.setattr(runtime, "default_pack_dir", lambda: tmp_path / "default")
    assert runtime.make_source({"mode": "generative"}) is None and built == [tmp_path / "default"]
    save_pack(tmp_path / "ok", "t", BPM, BARS, _const_pack(kick=0.1).layers)
    factory, director = runtime.make_source({"mode": "generative", "pack": str(tmp_path / "ok")})
    assert isinstance(director, Director)
    assert isinstance(factory([], threading.Event()), StemSource)


def test_busy_count_treats_a_manual_session_as_full():
    class M:
        _MANUAL = "__manual__"
        _read = staticmethod(lambda: {})
        _fresh_busy = staticmethod(lambda d: {"a": 1, "b": 1})

    assert runtime.busy_count(M) == 2
    M._fresh_busy = staticmethod(lambda d: {"a": 1, "__manual__": 1})
    assert runtime.busy_count(M) == MANUAL_BUSY


def test_feed_applies_each_accent_once_and_drops_stale_ones():
    import time

    class M:
        _MANUAL = "__manual__"
        ACCENT_TTL_S = 8.0
        state = {}
        _read = staticmethod(lambda: M.state)
        _fresh_busy = staticmethod(lambda d: {"a": 1})

    now = time.time()
    M.state = {"music_accent": "err", "music_accent_ts": now}
    d = Director()
    seen = runtime.feed(M, d, 0.0, 5.0, last_accent_ts=0.0)           # a controller's initial cursor
    assert seen == now and d.at_bar(0).label == "breakdown"
    assert runtime.feed(M, d, 0.0, 6.0, last_accent_ts=seen) == now
    assert d.at_bar(4).label == "busy:1"                          # not applied twice
    M.state = {"music_accent": "err", "music_accent_ts": now - 60}   # written long ago (e.g. at the last turn end)
    d2 = Director()
    assert runtime.feed(M, d2, 0.0, 6.0, last_accent_ts=0.0) == now - 60
    assert d2.at_bar(0).label == "busy:1"                         # seen, but stale: ignored


def test_music_config_schema():
    assert config_schema.validation_errors({"music": {"mode": "generative", "continuation": {"enabled": False}}}) == []
    assert config_schema.validation_errors({"music": {"mode": "loud"}})
    assert config_schema.validation_errors({"music": {"continuation": {"keep_bar": 8}}})      # typo is caught
    assert config_schema.validation_errors({"music": {"continuation": {"keep_bars": 2}}})


def test_render_timeline_follows_the_script_on_bar_lines():
    pack = _const_pack(kick=0.5, bass=0.25, hats=0.1, clap=0.05)
    script = [(4.0, 1, 0.0, None), (4.0, 2, 0.0, None), (4.0, 2, 0.0, "err")]
    audio, log = render_timeline(pack, Director(breakdown_bars=1), script)
    assert abs(len(audio) / 48000 - 12.0) < 0.05
    labels = [lbl for _, lbl in log]
    assert labels[0] == "busy:1" and "busy:2" in labels and "breakdown" in labels
    bars = {b: lbl for b, lbl in log}
    assert all(bars[b] == "busy:1" for b in (0, 1))                  # 2 s bars: seconds 0-4
    assert all(bars[b] == "busy:2" for b in (2, 3))


# ---- review fixes (grok, #67) ---------------------------------------------------------------
@pytest.mark.parametrize("reading", [0, 100, None])
def test_vram_guard_skips_when_full_or_unreadable(tmp_path, monkeypatch, reading):
    from nhj.music import continuation
    calls = []
    _, c = _composer(tmp_path, _fake_runner(calls), min_free_vram_mib=500)
    monkeypatch.setattr(continuation, "_free_vram_mib", lambda: reading)
    assert c.prime(frozenset({"kick"})) is False and calls == []
    assert "min_free_vram_mib" in c.last_error
    monkeypatch.setattr(continuation, "_free_vram_mib", lambda: 9000)
    _, ok = _composer(tmp_path / "other", _fake_runner(calls), min_free_vram_mib=500)
    assert ok.prime(frozenset({"kick"})) and len(calls) == 1


def test_timeout_s_reaches_the_subprocess(tmp_path, monkeypatch):
    from nhj.music import continuation
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        raise RuntimeError("stop here")
    monkeypatch.setattr(continuation.subprocess, "run", fake_run)
    _, c = _composer(tmp_path, None, command=["x", "{workdir}"], timeout_s=17)
    assert c.enabled and c.prime(frozenset({"kick"})) is False
    assert seen["timeout"] == 17


def test_short_cache_file_is_regenerated_and_writes_are_atomic(tmp_path):
    calls = []
    _, c = _composer(tmp_path, _fake_runner(calls))
    layers = frozenset({"kick"})
    wav = tmp_path / "cache" / f"{c.cache_key(layers, 0)}.wav"
    wav.parent.mkdir(parents=True)
    _wav(wav, np.zeros((1000, 2), np.float32))                  # torn/short file at the cache path
    assert c.prime(layers) and len(calls) == 1                  # not accepted as a hit
    assert wav.stat().st_size > 1000 * 4 and not list(wav.parent.glob("*.tmp"))
    _, c2 = _composer(tmp_path, _fake_runner(calls))
    assert c2.prime(layers) and len(calls) == 1                 # the repaired file is a hit now


def test_source_survives_an_exception_and_keeps_producing():
    pack = _const_pack(kick=0.5)

    class Flaky(Director):
        boom = True

        def at_bar(self, bar):
            if Flaky.boom and bar == 1:
                Flaky.boom = False
                raise RuntimeError("one bad bar")
            return super().at_bar(bar)

    d = Flaky()
    d.update(1, 0)
    buf, stop = [], threading.Event()
    src = StemSource(pack, d, buf, threading.Event())
    src.stop = stop
    pushed = []

    def push(block):
        pushed.append(len(block))
        if sum(pushed) > 3 * int(pack.spb):
            stop.set()
    src._push = push
    src.run()                                                   # returns because we set stop, not because it died
    assert sum(pushed) > 3 * int(pack.spb) and not Flaky.boom


# ---- hook-driven accents ----------------------------------------------------------------------
def test_director_drops_accents_that_waited_too_long():
    t = [100.0]
    d = Director(accent_ttl_s=8.0, clock=lambda: t[0])
    d.update(2, 0)
    d.accent("err")
    t[0] += 30                                               # the bed was paused; the verdict is old news
    assert d.at_bar(0).label == "busy:2"
    d.accent("err")
    t[0] += 3
    assert d.at_bar(1).label == "breakdown"


def _state():
    from nhj import inference_muzak as M
    return M._read()


@pytest.mark.parametrize("intent,accent", [("err", "err"), ("ok", "ok"), ("celebrate", "ok")])
def test_note_accent_maps_verdicts_when_generative(monkeypatch, intent, accent):
    from nhj import inference_muzak as M
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    M.note_accent(intent)
    assert _state()["music_accent"] == accent and _state()["music_accent_ts"] > 0


@pytest.mark.parametrize("env,intent", [("generative", "warn"), ("generative", "attn"), ("generative", "step"),
                                        ("recorded", "err"), ("", "err")])
def test_note_accent_is_a_noop_for_other_intents_and_recorded_mode(monkeypatch, env, intent):
    from nhj import inference_muzak as M
    monkeypatch.setenv("NHJ_MUSIC_MODE", env)
    M.note_accent(intent)
    assert "music_accent" not in _state()


def test_worker_dispatch_turns_a_verdict_into_an_accent(monkeypatch):
    """The worker runs on the host that owns the controller, so local hooks, remote hooks and
    `nhj vibe` all take this one path (Codex review on #75)."""
    from nhj import worker
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    worker._dispatch({"intent": "err", "message": "build failed", "source": "proj"}, [])
    assert _state()["music_accent"] == "err"
    worker._dispatch({"intent": "celebrate", "message": "shipped", "source": "proj"}, [])
    assert _state()["music_accent"] == "ok"


def test_worker_dispatch_respects_the_voice_gate_mode_and_intent(monkeypatch):
    from nhj import gate, worker
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    worker._dispatch({"intent": "warn", "source": "proj"}, [])                 # warn: no accent
    assert "music_accent" not in _state()
    monkeypatch.setattr(gate, "voice_allowed", lambda source: False)           # project has not opted in
    worker._dispatch({"intent": "err", "source": "proj"}, [])
    assert "music_accent" not in _state()
    monkeypatch.undo()
    monkeypatch.setenv("NHJ_MUSIC_MODE", "recorded")
    worker._dispatch({"intent": "err", "source": "proj"}, [])
    assert "music_accent" not in _state()


def test_the_stop_hook_itself_no_longer_writes_accents(monkeypatch):
    """Accents are noted by the worker; a hook that also wrote them would double up and, with
    NHJ_REMOTE_URL, write to the wrong host."""
    import inspect

    from nhj import hook
    assert "note_accent" not in inspect.getsource(hook)


def _stop_failure(monkeypatch, cwd="/tmp/proj"):
    from io import StringIO

    from nhj import inference_muzak, prompt_hook
    monkeypatch.setattr(inference_muzak, "mark_idle", lambda sid: None)
    payload = {"hook_event_name": "StopFailure", "session_id": "s1", "cwd": cwd}
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps(payload)))
    assert prompt_hook.main() == 0


def test_stop_failure_is_an_err_accent_when_voice_is_allowed(monkeypatch):
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    _stop_failure(monkeypatch)
    assert _state()["music_accent"] == "err"


def test_stop_failure_respects_the_per_project_voice_gate(monkeypatch):
    """Grok review on #75: `nhj voice off` must keep a project from moving the bed, StopFailure included."""
    from nhj import gate
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    monkeypatch.setattr(gate, "voice_allowed", lambda source: False)
    _stop_failure(monkeypatch)
    assert "music_accent" not in _state()


def test_stop_failure_writes_nothing_locally_when_a_remote_daemon_owns_the_bed(monkeypatch):
    """With NHJ_REMOTE_URL the bed is on the daemon: music-only tool, no local write, no err vibe."""
    from nhj import hook
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    monkeypatch.setenv("NHJ_REMOTE_URL", "http://daemon.invalid/mcp")
    sent = []
    vibes = []
    monkeypatch.setattr(hook, "_fire_remote_accent", lambda url, intent: sent.append((url, intent)))
    monkeypatch.setattr(hook, "_fire_remote", lambda url, ev: vibes.append(ev) or True)
    _stop_failure(monkeypatch)
    assert "music_accent" not in _state()
    assert sent == [("http://daemon.invalid/mcp", "err")]
    assert vibes == []


def test_stop_failure_remote_respects_the_per_project_voice_gate(monkeypatch):
    """A voice-off project must not call the remote music tool."""
    from nhj import gate, hook
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    monkeypatch.setenv("NHJ_REMOTE_URL", "http://daemon.invalid/mcp")
    monkeypatch.setattr(gate, "voice_allowed", lambda source: False)
    sent = []
    monkeypatch.setattr(hook, "_fire_remote_accent", lambda url, intent: sent.append((url, intent)))
    _stop_failure(monkeypatch)
    assert "music_accent" not in _state() and sent == []


def test_stop_failure_remote_failure_is_silent(monkeypatch, capsys):
    """A failed remote accent must not fail the hook, write locally, or print."""
    pytest.importorskip("fastmcp")
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    monkeypatch.setenv("NHJ_REMOTE_URL", "http://daemon.invalid/mcp")

    class BoomClient:
        def __init__(self, url, auth=None):
            raise RuntimeError("nope")

    monkeypatch.setattr("fastmcp.Client", BoomClient)
    _stop_failure(monkeypatch)
    assert "music_accent" not in _state()
    assert capsys.readouterr().err == ""


def test_controller_starts_its_accent_cursor_at_zero():
    """Codex review on #75: an accent written while the controller is still starting must not be
    mistaken for a pre-existing one. The cursor starts at 0.0; feed()'s TTL does the staleness work."""
    import inspect

    from nhj import duck_player
    src = inspect.getsource(duck_player.run_duck_controller)
    assert "last_accent = 0.0" in src and "last_accent = time.time()" not in src


def test_music_cli_explains_a_missing_audio_extra(monkeypatch):
    from typer.testing import CliRunner

    from nhj import cli
    monkeypatch.setitem(sys.modules, "numpy", None)               # `import numpy` now raises ImportError
    for args in (["music", "status"], ["music", "build-pack"], ["music", "accent", "err"], ["music", "demo"]):
        r = CliRunner().invoke(cli.cli, args)
        assert r.exit_code == 1, args
        assert "audio extra" in r.output and "Traceback" not in r.output, args


def test_feed_works_with_a_muzak_double_that_has_no_accent_ttl():
    """tests/test_mixer_poll.py (#78) drives feed() with a minimal fake muzak module; the TTL must have a default."""
    import time

    class M:
        _MANUAL = "__manual__"
        _read = staticmethod(lambda: {})
        _fresh_busy = staticmethod(lambda d: {"a": 1})

    d = Director()
    now = time.time()
    st = {"music_accent": "err", "music_accent_ts": now}
    assert runtime.feed(M, d, 0.0, 1.0, 0.0, state=st) == now
    assert d.at_bar(0).label == "breakdown"


def test_music_status_says_whether_hold_music_is_on(monkeypatch):
    """Generative music only plays when the hold-music switch is on; `nhj music status` must say so."""
    from typer.testing import CliRunner

    from nhj import cli
    from nhj.state import set_flag
    set_flag("muzak", False)
    off = CliRunner().invoke(cli.cli, ["music", "status"])
    assert off.exit_code == 0 and "hold music: OFF" in off.output and "nhj muzak on" in off.output
    set_flag("muzak", True)
    on = CliRunner().invoke(cli.cli, ["music", "status"])
    assert on.exit_code == 0 and "hold music: on" in on.output


# ---- accent stingers (louder err/ok accents) ----------------------------------------------
def test_stingers_are_deterministic_normalised_and_sound_on_the_hit():
    for kind in synth.STINGERS:
        a = synth.render_stinger(kind)
        assert a.dtype == np.float32 and a.ndim == 2 and a.shape[1] == 2
        assert np.isfinite(a).all()
        assert float(np.abs(a).max()) == pytest.approx(1.0, abs=1e-4)          # the player sets the level
        assert np.array_equal(a, synth.render_stinger(kind))
        head = float(np.sqrt(np.mean(a[:12000] ** 2)))
        tail = float(np.sqrt(np.mean(a[-4800:] ** 2)))
        assert head > 0.05 and tail < head / 10                                # a hit that decays, no click at the end
        assert 1.0 < len(a) / packmod.SR < 2.0
    with pytest.raises(ValueError):
        synth.render_stinger("warn")


def test_plan_flags_the_accent_bar_exactly_once():
    d = Director(breakdown_bars=4)
    d.update(1, 0)
    assert d.at_bar(0).accent == ""
    d.accent("err")
    flagged = [d.at_bar(b).accent for b in range(1, 7)]
    assert flagged == ["err", "", "", "", "", ""]                              # first bar of the breakdown only
    d.accent("ok")
    assert [d.at_bar(b).accent for b in (7, 8)] == ["ok", ""]


def test_accents_that_change_nothing_do_not_flag():
    d = Director(breakdown_bars=4, accent_ttl_s=8.0, clock=iter([0.0, 0.0, 100.0, 100.0]).__next__)
    d.update(4, 0)
    d.accent("err")                                      # asked at t=0
    assert d.at_bar(0).accent == "err"
    d.accent("ok")                                       # inside the breakdown: ignored, so no stinger either
    assert d.at_bar(1).accent == ""
    d2 = Director(accent_ttl_s=8.0, clock=iter([0.0, 100.0]).__next__)
    d2.accent("err")                                     # stale by the time a bar line comes round
    assert d2.at_bar(0).accent == ""
    d3 = Director()
    d3.accent("warn")
    assert d3.at_bar(0).accent == ""


def test_release_is_the_full_arrangement_so_it_lifts_a_sparse_bed():
    d = Director()
    d.update(1, 0)
    assert d.at_bar(0).layers == frozenset({"kick", "bass"})
    d.accent("ok")
    rel = d.at_bar(1)
    assert rel.label == "release" and rel.layers == frozenset({"kick", "bass", "hats", "clap", "arp", "pad"})
    assert d.at_bar(2).layers == frozenset({"kick", "bass"})


def test_source_calls_back_once_per_accent_with_the_stinger_and_gain():
    pk = _const_pack(kick=0.2, bass=0.2, pad=0.2)
    d = Director(breakdown_bars=1)
    d.update(1, 0)
    src = StemSource(pk, d, [], threading.Event(), accent_gain=0.4)
    hits = []
    src.on_accent = lambda data, gain: hits.append((len(data), gain))
    gen = src.iter_blocks()
    for _ in range(3):
        next(gen)
    assert hits == []
    d.accent("err")
    for _ in range(int(2 * pk.spb // 1024) + 4):                                # run past the next bar line
        next(gen)
    assert hits == [(len(synth.render_stinger("err")), 0.4)]


def test_gain_zero_or_no_player_means_no_stinger_and_a_failing_player_never_breaks_the_bed():
    pk = _const_pack(kick=0.2)
    for gain, wire in ((0.0, True), (0.5, False)):
        d = Director()
        d.update(1, 0)
        src = StemSource(pk, d, [], threading.Event(), accent_gain=gain)
        calls = []
        if wire:
            src.on_accent = lambda *a: calls.append(a)
        d.accent("err")
        gen = src.iter_blocks()
        for _ in range(int(2 * pk.spb // 1024) + 4):
            next(gen)
        assert calls == []
    d = Director()
    d.update(1, 0)
    src = StemSource(pk, d, [], threading.Event(), accent_gain=0.5)

    def boom(*_a):
        raise RuntimeError("no audio device")
    src.on_accent = boom
    d.accent("err")
    gen = src.iter_blocks()
    blocks = [next(gen) for _ in range(int(2 * pk.spb // 1024) + 4)]
    assert all(np.isfinite(b).all() for b in blocks)                            # still playing


def test_render_timeline_lays_the_stinger_on_the_accent_bar_line():
    pk = _const_pack(kick=0.1, bass=0.1)
    bar = int(pk.spb)
    script = [(2 * bar / packmod.SR, 1, 0.0, None), (0.01, 1, 0.0, "err"), (3 * bar / packmod.SR, 1, 0.0, None)]
    quiet, _ = render_timeline(pk, Director(clock=lambda: 0.0), script, accent_gain=0.0)
    loud, log = render_timeline(pk, Director(clock=lambda: 0.0), script, accent_gain=0.6)
    diff = np.abs(loud - quiet).max(axis=1)
    start = int(np.argmax(diff > 1e-6))
    bar_of_breakdown = next(i for i, (b, lab) in enumerate(log) if lab == "breakdown")
    assert start == int(round(log[bar_of_breakdown][0] * pk.spb))               # exactly on that bar's first sample
    assert float(diff.max()) == pytest.approx(0.6, abs=0.05)                    # peak 1.0 x gain
    assert np.array_equal(loud[:start], quiet[:start])                          # nothing before the hit


def _mixer_with_music(chunks):
    """A Mixer whose bed is playing: constant music queued ahead of the callback."""
    from nhj import duck_player
    mixer = duck_player.Mixer([])
    mixer._paused_evt.clear()
    for _ in range(chunks):
        mixer.buf.append(np.full((2048, 2), 0.5, np.float32))
    return mixer


def _run_blocks(mixer, n, frames=1024):
    gains = []
    for _ in range(n):
        mixer._callback(np.zeros((frames, 2), np.float32), frames, None, None)
        gains.append(round(float(mixer._music_gain), 3))
    return gains


def test_mixer_queues_the_accent_for_the_end_of_the_buffered_music_not_on_the_bus():
    mixer = _mixer_with_music(3)                            # 6144 frames still queued ahead of playback
    try:
        mixer._play_accent(np.ones((100, 2), np.float32), 0.5)
        assert mixer._fx == []                              # not sounding, so it must not count as "speaking"
        ((lead, clip),) = mixer._accent_q
        assert lead == 6144 and clip.gain == 0.5 and len(clip.data) == 100
    finally:
        mixer.stop() if hasattr(mixer, "stop") else None


def test_a_waiting_accent_does_not_duck_the_bed_until_it_actually_sounds():
    mixer = _mixer_with_music(3)
    try:
        mixer._play_accent(np.ones((4096, 2), np.float32), 0.5)
        mixer.buf.append(np.full((4096, 2), 0.5, np.float32))      # the next bar, as the generator pushes it
        gains = _run_blocks(mixer, 8)
        assert gains[:6] == [1.0] * 6                       # the whole buffered lead: bed untouched (was ducked early)
        assert gains[6] == pytest.approx(mixer._duck_floor)  # ducks when the hit starts
        assert not mixer._accent_q and len(mixer._fx) == 1
    finally:
        mixer.stop() if hasattr(mixer, "stop") else None


def _silent_bed(mixer, frames):
    mixer._paused_evt.clear()
    mixer.buf.clear()
    mixer.buf.append(np.zeros((frames, 2), np.float32))     # a playing bed that adds nothing to the output


def test_an_accent_starting_mid_block_starts_on_the_exact_sample():
    from nhj import duck_player
    mixer = duck_player.Mixer([])
    try:
        _silent_bed(mixer, 4096)
        mixer._accent_q.append([1500, duck_player._Clip(np.ones((10, 2), np.float32), 1.0)])
        first, second = np.zeros((1024, 2), np.float32), np.zeros((1024, 2), np.float32)
        mixer._callback(first, 1024, None, None)
        mixer._callback(second, 1024, None, None)           # 1500 - 1024 = 476 frames into this block
        assert not first.any()
        assert not second[:476].any() and second[476].all() and second[485].all() and not second[486:].any()
    finally:
        mixer.stop() if hasattr(mixer, "stop") else None


def test_a_paused_bed_freezes_the_accent_countdown_and_it_lands_on_the_bar_after_resume():
    from nhj import duck_player
    mixer = duck_player.Mixer([])
    try:
        mixer.buf.append(np.zeros((6144, 2), np.float32))
        mixer._play_accent(np.ones((10, 2), np.float32), 1.0)         # paused: the bed is not draining
        _run_blocks(mixer, 8)
        assert mixer._accent_q[0][0] == 6144 and mixer._fx == []      # frozen, nothing heard over frozen music
        assert len(mixer.buf) == 1 and len(mixer.buf[0]) == 6144
        mixer._paused_evt.clear()                                     # resume: 6144 frames still to go
        mixer.buf.append(np.zeros((4096, 2), np.float32))            # the next bar, as the generator pushes it
        _run_blocks(mixer, 5)
        assert mixer._accent_q and mixer._accent_q[0][0] == 6144 - 5 * 1024
        _run_blocks(mixer, 1)
        assert mixer._accent_q[0][0] == 0 and mixer._fx == []         # exactly on the bar line, not before
        probe = np.zeros((1024, 2), np.float32)
        mixer._callback(probe, 1024, None, None)
        assert probe[0].all() and not mixer._accent_q
    finally:
        mixer.stop() if hasattr(mixer, "stop") else None


def test_a_starved_bed_does_not_burn_the_countdown():
    from nhj import duck_player
    mixer = duck_player.Mixer([])
    try:
        _silent_bed(mixer, 500)                                       # only 500 frames, then an underrun
        mixer._accent_q.append([1000, duck_player._Clip(np.ones((10, 2), np.float32), 1.0)])
        _run_blocks(mixer, 5)
        assert mixer._accent_q[0][0] == 500 and mixer._fx == []       # 500 consumed, then frozen
    finally:
        mixer.stop() if hasattr(mixer, "stop") else None


def test_an_accent_waiting_on_a_long_paused_bed_is_dropped_not_fired_next_turn():
    from nhj import duck_player
    mixer = duck_player.Mixer([])
    try:
        mixer._accent_q.append([4096, duck_player._Clip(np.ones((10, 2), np.float32), 1.0)])
        _run_blocks(mixer, int(7.0 * duck_player.SR / 1024))
        assert mixer._accent_q                                        # 7 s: still waiting
        _run_blocks(mixer, int(2.0 * duck_player.SR / 1024))
        assert mixer._accent_q == [] and mixer._fx == []              # past 8 s: gone
    finally:
        mixer.stop() if hasattr(mixer, "stop") else None


def test_mixer_hooks_a_generative_source_but_leaves_recorded_alone():
    from nhj import duck_player
    pk = _const_pack(kick=0.2)
    holder = {}

    def factory(buf, paused):
        holder["src"] = StemSource(pk, Director(), buf, paused, accent_gain=0.5)
        return holder["src"]
    mixer = duck_player.Mixer([], source=factory)
    try:
        assert holder["src"].on_accent == mixer._play_accent
    finally:
        mixer.stop() if hasattr(mixer, "stop") else None
    plain = duck_player.Mixer([])
    assert not hasattr(plain._decoder, "on_accent")


def test_accent_gain_config_is_validated_and_defaulted():
    assert config_schema.Music(accent_gain=0.5).accent_gain == 0.5
    assert config_schema.Music(accent_gain=0).accent_gain == 0
    for bad in (-0.1, 2.5):
        with pytest.raises(Exception):
            config_schema.Music(accent_gain=bad)
    from nhj import config
    assert config.get()["music"]["accent_gain"] == runtime.DEFAULT_ACCENT_GAIN == 0.5
