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


def test_feed_applies_each_accent_once():
    class M:
        _MANUAL = "__manual__"
        state = {"music_accent": "err", "music_accent_ts": 100.0}
        _read = staticmethod(lambda: M.state)
        _fresh_busy = staticmethod(lambda d: {"a": 1})

    d = Director()
    seen = runtime.feed(M, d, 0.0, 5.0, last_accent_ts=50.0)
    assert seen == 100.0 and d.at_bar(0).label == "breakdown"
    assert runtime.feed(M, d, 0.0, 6.0, last_accent_ts=seen) == 100.0
    assert d.at_bar(4).label == "busy:1"


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


def test_music_cli_explains_a_missing_audio_extra(monkeypatch):
    from typer.testing import CliRunner

    from nhj import cli
    monkeypatch.setitem(sys.modules, "numpy", None)               # `import numpy` now raises ImportError
    for args in (["music", "status"], ["music", "build-pack"], ["music", "accent", "err"], ["music", "demo"]):
        r = CliRunner().invoke(cli.cli, args)
        assert r.exit_code == 1, args
        assert "audio extra" in r.output and "Traceback" not in r.output, args
