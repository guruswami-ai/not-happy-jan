"""Measured cost of the two paths that make a sound: the hold-music mixer and the voice ladder.

`SPEC.md` §9 promises *bank/chime ≤ 300 ms* and *live voice ≤ 2 s warm*, and the mixer's realtime
callback has a hard deadline of one block (`duck_player.BLOCK` frames, ~21 ms at `SR`). Neither number
was ever measured: the only evidence of an audio fault was a hand-grep of `worker.log`, which found
upstream TTS 500s and said nothing about how long a line took to speak. This module is the store and the
arithmetic for those figures; `nhj stats` renders them.

Two producers, one surface. The mixer process snapshots its own counters periodically (it owns a
realtime thread, so nothing writes on its behalf). Every process that speaks appends one line to the
voice log — a rolling window, not history.

Three limits are deliberate, and stating them is part of the module:

* The voice window measures `AudioAdapter.fire` — ladder decision to clip handed to the mixer. It is
  not hook-to-ear: queue dispatch and the mixer's own buffering sit outside it, and the §9 budget is
  written as "time to first sound after Stop". Read the rung numbers as the ladder's share of that
  budget, which is the share the ladder can control.
* Percentiles are over the retained window only, so the sample count travels with every percentile.
* Every read path tolerates a missing, partial or corrupt file. Instrumentation must never be the
  reason a status command fails or a vibe does not fire — but it does say so via `resources.debug`.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MIXER_STATS = "mixer-stats.json"
VOICE_LOG = "voice-stats.jsonl"
VOICE_LOG_OLD = "voice-stats.jsonl.1"
BASELINE = "stats-baseline.json"

#: One rotation generation is kept, so a window can span a rotate without losing the samples in it.
VOICE_MAX_BYTES = 1 << 20
#: How many of the newest samples feed a percentile. At ~1 record per vibe this is a long window.
VOICE_WINDOW = 5000
#: A mixer snapshot older than this (or owned by a dead pid) is stale: the controller exited without a
#: final flush, so its counters are a last-known state, not a live one.
MIXER_STALE_S = 10.0

#: Per-rung §9 budgets (ms, p95) and the mixer's callback deadline as a fraction of one output block.
#: The mixer writes its own block period, so this module never has to import the audio stack.
BUDGETS = {
    "bank_ms": 300.0,        # SPEC.md §9: bank/chime rung
    "chime_ms": 300.0,       # SPEC.md §9: same ceiling, measured separately because it is a different path
    "synth_ms": 2000.0,      # SPEC.md §9: live voice, warm
    "cache_ms": 300.0,       # not in §9; a cache hit that misses the bank ceiling is a bug, so gate it like one
    "callback_block_frac": 0.25,
}

#: Rungs the hit rate counts as "served without synthesizing". `none` is excluded from the denominator:
#: nothing was spoken, so it says nothing about the cache.
BANKED_RUNGS = ("bank", "cache", "chime")
SYNTH_RUNGS = ("synth", "fallback")


def _state_path(name: str) -> Path:
    from nhj import resources
    return resources.state_file(name)


# ---------------------------------------------------------------------------
# arithmetic
# ---------------------------------------------------------------------------
def percentile(values, q: float):
    """Nearest-rank percentile (q in 0..100) of a sequence of numbers, or None when empty.

    Nearest rank rather than interpolation: these are latency budgets, and reporting a value that was
    actually observed is more useful on a handful of samples than a fraction that exists between two.
    """
    vals = sorted(values)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, int(round(q / 100.0 * len(vals))) - 1))
    return vals[k]


def _ms(seconds: float) -> float:
    return round(seconds * 1000.0, 1)


# ---------------------------------------------------------------------------
# voice ladder: one line per spoken vibe, appended by whichever process spoke
# ---------------------------------------------------------------------------
def record_voice(outcome: str, engine: str = "", ladder_s: float = 0.0, render_s=None,
                 path: str = "", error: str = "") -> None:
    """Append one ladder traversal. Never raises: a failed measurement must not lose the voice line.

    `outcome` is the rung that actually produced the clip — bank, cache, synth, fallback (a synth that
    failed and fell back to the bundled bank), chime, or none (nothing was spoken). `render_s` is the
    synthesis round trip alone, so a rung's total and its engine's cost stay separable.
    """
    rec: dict = {"t": round(time.time(), 3), "outcome": outcome,
                 "engine": engine or "-", "ladder_ms": _ms(ladder_s)}
    if render_s is not None:
        rec["render_ms"] = _ms(render_s)
    if path:
        try:
            rec["bytes"] = os.path.getsize(path)
        except OSError:
            pass
    if error:
        rec["error"] = str(error)[:200]
    try:
        p = _state_path(VOICE_LOG)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > VOICE_MAX_BYTES:
            os.replace(p, _state_path(VOICE_LOG_OLD))
        # One small O_APPEND write: two processes speaking in the same millisecond stay whole.
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except Exception as e:
        _debug(f"voice stats write failed: {e}")


def _debug(msg: str) -> None:
    try:
        from nhj import resources
        resources.debug(msg)
    except Exception:
        pass


def read_voice(limit: int = VOICE_WINDOW) -> list[dict]:
    """The newest *limit* samples, oldest first. Missing or unreadable files read as fewer samples."""
    rows: list[dict] = []
    for name in (VOICE_LOG_OLD, VOICE_LOG):
        try:
            raw = _state_path(name).read_bytes()
        except OSError:
            continue
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue                      # a torn tail line, not a reason to drop the window
            if isinstance(rec, dict) and rec.get("outcome"):
                rows.append(rec)
    return rows[-limit:]


def _rung_row(records: list[dict]) -> dict:
    ladders = [float(r.get("ladder_ms") or 0.0) for r in records]
    row = {"n": len(records),
           "p50_ms": percentile(ladders, 50), "p95_ms": percentile(ladders, 95),
           "max_ms": max(ladders) if ladders else None}
    sizes = [float(r["bytes"]) for r in records if isinstance(r.get("bytes"), (int, float))]
    if sizes:
        row["bytes_p50"] = int(percentile(sizes, 50) or 0)
    return row


def voice_summary(records: list[dict] | None = None) -> dict:
    """Group the window by rung and by engine, with the error counts that today only exist in logs."""
    rows = read_voice() if records is None else records
    out: dict = {"samples": len(rows), "rungs": {}, "engines": {}, "errors": [], "hit_rate": None}
    if not rows:
        return out
    stamps = [float(r["t"]) for r in rows if isinstance(r.get("t"), (int, float))]
    if stamps:
        out["since"], out["until"] = min(stamps), max(stamps)

    rungs: dict[str, list] = {}
    for r in rows:
        rungs.setdefault(str(r["outcome"]), []).append(r)
    for name, group in sorted(rungs.items()):
        out["rungs"][name] = _rung_row(group)

    engines: dict[str, list] = {}
    for r in rows:
        if r.get("engine") and r["engine"] != "-" and r["outcome"] in SYNTH_RUNGS:
            engines.setdefault(str(r["engine"]), []).append(r)
    for name, group in sorted(engines.items()):
        row = _rung_row(group)
        renders = [float(r["render_ms"]) for r in group if isinstance(r.get("render_ms"), (int, float))]
        row["renders"] = len(renders)
        row["render_p50_ms"] = percentile(renders, 50)
        row["render_p95_ms"] = percentile(renders, 95)
        row["errors"] = sum(1 for r in group if r.get("error"))
        out["engines"][name] = row

    grouped: dict[tuple[str, str], int] = {}
    for r in rows:
        if r.get("error"):
            key = (str(r.get("engine") or "-"), str(r["error"])[:120])
            grouped[key] = grouped.get(key, 0) + 1
    out["errors"] = [{"engine": e, "error": m, "n": n}
                     for (e, m), n in sorted(grouped.items(), key=lambda kv: -kv[1])]

    banked = sum(out["rungs"][k]["n"] for k in BANKED_RUNGS if k in out["rungs"])
    spoken = banked + sum(out["rungs"][k]["n"] for k in SYNTH_RUNGS if k in out["rungs"])
    if spoken:
        out["hit_rate"] = {"answered_from_disk": banked, "spoken": spoken,
                           "rate": round(banked / spoken, 4)}
    return out


# ---------------------------------------------------------------------------
# mixer: the realtime process snapshots itself; everyone else only reads
# ---------------------------------------------------------------------------
def write_mixer(snapshot: dict) -> None:
    """Publish the mixer's counters atomically — a reader must never see half an update."""
    snap = dict(snapshot)
    snap["written_at"] = round(time.time(), 3)
    snap["pid"] = os.getpid()
    try:
        p = _state_path(MIXER_STATS)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.{os.getpid()}.{os.urandom(3).hex()}.tmp")
        tmp.write_text(json.dumps(snap, separators=(",", ":")))
        os.replace(tmp, p)
    except Exception as e:
        _debug(f"mixer stats write failed: {e}")


def read_mixer() -> dict | None:
    try:
        snap = json.loads(_state_path(MIXER_STATS).read_text())
    except Exception:
        return None
    return snap if isinstance(snap, dict) else None


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                 # someone else's pid, but a live one
        return True
    except OSError:
        return False
    return True


def mixer_summary(snap: dict | None = None, now: float | None = None) -> dict | None:
    """The mixer snapshot plus how much it can be trusted right now."""
    snap = read_mixer() if snap is None else snap
    if not snap:
        return None
    now = time.time() if now is None else now
    written = float(snap.get("written_at") or 0.0)
    alive = pid_alive(snap.get("pid"))
    out = dict(snap)
    out["pid_alive"] = alive
    out["age_s"] = round(max(0.0, now - written), 1) if written else None
    out["stale"] = bool(not alive or (out["age_s"] or 0.0) > MIXER_STALE_S)
    block_us = float(snap.get("block_us") or 0.0)
    p95 = snap.get("callback_p95_us")
    out["deadline_miss_frac"] = (round(float(p95) / block_us, 4)
                                 if block_us and isinstance(p95, (int, float)) else None)
    return out


# ---------------------------------------------------------------------------
# budgets and the regression gate
# ---------------------------------------------------------------------------
def budget_failures(mixer: dict | None, voice: dict) -> list[str]:
    """Which §9 / callback budgets the measured window misses. Empty means nothing measured is over."""
    fails: list[str] = []
    rungs = voice.get("rungs", {})

    def over(rung: str, budget_key: str, label: str) -> None:
        row = rungs.get(rung)
        if not row:
            return
        p95, budget = row.get("p95_ms"), BUDGETS[budget_key]
        if isinstance(p95, (int, float)) and p95 > budget:
            fails.append(f"{label} p95 {p95:.0f} ms over its {budget:.0f} ms budget "
                         f"(n={row['n']})")

    over("bank", "bank_ms", "bank rung")
    over("chime", "chime_ms", "chime")
    over("cache", "cache_ms", "cache hit")
    over("synth", "synth_ms", "live voice")
    if mixer:
        misses = mixer.get("deadline_misses")
        if isinstance(misses, int) and misses:
            fails.append(f"mixer missed its audio deadline {misses} time(s) "
                         f"({mixer.get('blocks', 0)} blocks)")
        block_us = float(mixer.get("block_us") or 0.0)
        p95 = mixer.get("callback_p95_us")
        limit = block_us * BUDGETS["callback_block_frac"]
        if block_us and isinstance(p95, (int, float)) and p95 > limit:
            fails.append(f"mixer callback p95 {p95:.0f} us over {limit:.0f} us "
                         f"({BUDGETS['callback_block_frac']:.0%} of a {block_us:.0f} us block)")
    return fails


def _gated_numbers(mixer: dict | None, voice: dict) -> dict:
    """The handful of figures the regression gate compares, and nothing else.

    Deliberately sparse: a baseline that stores uptime and sample counts would fail for reasons that
    have nothing to do with cost.
    """
    g: dict = {}
    for rung in ("bank", "cache", "chime", "synth"):
        row = voice.get("rungs", {}).get(rung)
        if row and row.get("p95_ms") is not None:
            g[f"{rung}_p95_ms"] = round(float(row["p95_ms"]), 1)
    for engine, row in voice.get("engines", {}).items():
        if row.get("render_p95_ms") is not None:
            g[f"render[{engine}]_p95_ms"] = round(float(row["render_p95_ms"]), 1)
    if mixer:
        for key in ("callback_p50_us", "callback_p95_us"):
            if isinstance(mixer.get(key), (int, float)):
                g[f"mixer_{key}"] = round(float(mixer[key]), 1)
    return g


REGRESSION_RATIO = 1.10      # REDESIGN-2 phase 1: fail past a 10 % measured regression


def save_baseline(mixer: dict | None, voice: dict) -> dict:
    g = _gated_numbers(mixer, voice)
    payload = {"saved_at": round(time.time(), 3), "numbers": g}
    try:
        p = _state_path(BASELINE)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        _debug(f"baseline write failed: {e}")
    return payload


def read_baseline() -> dict | None:
    try:
        data = json.loads(_state_path(BASELINE).read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("numbers"), dict) else None


def regression_failures(mixer: dict | None, voice: dict, baseline: dict | None = None,
                        ratio: float = REGRESSION_RATIO) -> tuple[list[str], bool]:
    """(failures, had_baseline). A number may always get *faster*; only a >ratio slowdown fails.

    The gate arms on a measured baseline rather than on a spec figure nobody has timed, so the first run
    on a new host reports instead of failing for writing down the truth.
    """
    baseline = read_baseline() if baseline is None else baseline
    if not baseline:
        return [], False
    now = _gated_numbers(mixer, voice)
    fails = []
    for key, was in baseline["numbers"].items():
        is_now = now.get(key)
        if is_now is None or not isinstance(was, (int, float)) or was <= 0:
            continue
        if is_now > was * ratio:
            fails.append(f"{key} {is_now:.1f} vs baseline {was:.1f} "
                         f"(+{(is_now / was - 1) * 100:.0f} %, gate {ratio:.0%})")
    return fails, True


def summarise(with_voice: bool = True) -> dict:
    """Everything `nhj stats` and `--check` need, in one read."""
    mixer = mixer_summary()
    voice = voice_summary() if with_voice else {"samples": 0, "rungs": {}, "engines": {},
                                                "errors": [], "hit_rate": None}
    fails = budget_failures(mixer, voice)
    regress, had_baseline = regression_failures(mixer, voice)
    return {"mixer": mixer, "voice": voice, "budget_failures": fails,
            "regression_failures": regress, "had_baseline": had_baseline,
            "gated": _gated_numbers(mixer, voice),
            "budgets": BUDGETS, "regression_ratio": REGRESSION_RATIO}


def reset() -> list[str]:
    """Drop the retained windows. Returns what was actually removed, so it can be reported."""
    removed = []
    for name in (VOICE_LOG, VOICE_LOG_OLD, MIXER_STATS, BASELINE):
        try:
            p = _state_path(name)
            if p.exists():
                p.unlink()
                removed.append(name)
        except OSError as e:
            _debug(f"stats reset skipped {name}: {e}")
    return removed


def _fmt_ms(v) -> str:
    return "—" if v is None else (f"{v:.0f} ms" if v >= 10 else f"{v:.1f} ms")


def _fmt_us(v) -> str:
    return "—" if v is None else (f"{v / 1000:.2f} ms" if v >= 1000 else f"{v:.0f} µs")


def _fmt_age(seconds) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.1f} h ago"


def render(summ: dict) -> list:
    """Rich renderables for `nhj stats`. Kept separate from the command so tests can assert on it."""
    from rich.table import Table

    blocks = []
    mixer, voice = summ.get("mixer"), summ.get("voice", {})

    mt = Table(title="Mixer — realtime path", show_header=True)
    for col in ("metric", "value", "note"):
        mt.add_column(col)
    if not mixer:
        mt.add_row("snapshot", "[dim]none[/dim]",
                   "no mixer has run since `nhj stats --reset`, or it has never started")
    else:
        live = "[green]running[/green]" if mixer.get("pid_alive") else "[red]dead[/red]"
        if mixer.get("stale"):
            live = f"{live} [yellow]stale[/yellow]"
        mt.add_row("state", live, f"pid {mixer.get('pid')} · {_fmt_age(mixer.get('age_s'))}")
        mt.add_row("blocks rendered", f"{mixer.get('blocks', 0)}",
                   f"block {_fmt_us(mixer.get('block_us'))} ({mixer.get('block_frames', '?')} frames)")
        mt.add_row("callback p50 / p95",
                   f"{_fmt_us(mixer.get('callback_p50_us'))} / {_fmt_us(mixer.get('callback_p95_us'))}",
                   f"max {_fmt_us(mixer.get('callback_max_us'))} · window "
                   f"{mixer.get('window_blocks', 0)} blocks")
        dms = mixer.get("deadline_misses", 0)
        mt.add_row("deadline misses",
                   "[green]0[/green]" if not dms else f"[red]{dms}[/red]",
                   "callbacks that outlived the block they were filling")
        und = mixer.get("underruns", 0)
        mt.add_row("bed underruns", "[green]0[/green]" if not und else f"[red]{und}[/red]",
                   f"{mixer.get('short_frames', 0)} silent frames — the bed asked for audio and got none")
        if mixer.get("pa_flags"):
            mt.add_row("PortAudio flags", str(mixer["pa_flags"]), "device-side xrun reports")
        if mixer.get("stream_live") is False:
            mt.add_row("output stream", "[red]down[/red]", "the keeper is in backoff (StreamKeeper)")
    blocks.append(mt)

    vt = Table(title="Voice ladder — per rung", show_header=True)
    for col in ("rung", "n", "p50", "p95", "max", "clip"):
        vt.add_column(col)
    rungs = voice.get("rungs", {})
    if not rungs:
        vt.add_row("[dim]no samples[/dim]", "—", "—", "—", "—",
                   "nothing has spoken since the last reset")
    order = ("bank", "cache", "chime", "synth", "fallback", "none")
    for name in [r for r in order if r in rungs] + [r for r in rungs if r not in order]:
        row = rungs[name]
        vt.add_row(name, str(row["n"]), _fmt_ms(row.get("p50_ms")), _fmt_ms(row.get("p95_ms")),
                   _fmt_ms(row.get("max_ms")),
                   f"{row['bytes_p50'] // 1024} KiB" if row.get("bytes_p50") else "—")
    blocks.append(vt)

    et = Table(title="Voice — per engine (synthesis rung)", show_header=True)
    for col in ("engine", "renders", "render p50", "render p95", "rung p95", "errors"):
        et.add_column(col)
    engines = voice.get("engines", {})
    if not engines:
        et.add_row("[dim]no synthesis in the window[/dim]", "—", "—", "—", "—", "—")
    for name, row in engines.items():
        errs = row.get("errors", 0)
        et.add_row(name, str(row.get("renders", 0)), _fmt_ms(row.get("render_p50_ms")),
                   _fmt_ms(row.get("render_p95_ms")), _fmt_ms(row.get("p95_ms")),
                   "[green]0[/green]" if not errs else f"[red]{errs}[/red]")
    blocks.append(et)

    hit = voice.get("hit_rate")
    st = Table(title="Voice — what it cost, and how often it was free", show_header=True)
    st.add_column("metric")
    st.add_column("value")
    st.add_column("note")
    st.add_row("samples", str(voice.get("samples", 0)),
               f"window of {VOICE_WINDOW} retained, rotated at {VOICE_MAX_BYTES >> 10} KiB")
    if hit:
        st.add_row("answered from disk", f"{hit['rate']:.0%}",
                   f"{hit['answered_from_disk']} of {hit['spoken']} spoken lines needed no engine")
    else:
        st.add_row("answered from disk", "[dim]—[/dim]", "no spoken lines in the window yet")
    for err in voice.get("errors", [])[:5]:
        st.add_row(f"errors · {err['engine']}", f"[red]{err['n']}[/red]", err["error"])
    if not voice.get("errors"):
        st.add_row("errors", "[green]0[/green]",
                   "these used to exist only as lines in worker.log, findable by hand")
    blocks.append(st)

    fails = list(summ.get("budget_failures") or []) + list(summ.get("regression_failures") or [])
    gt = Table(title="Budgets", show_header=True)
    for col in ("check", "result"):
        gt.add_column(col)
    if fails:
        for f in fails:
            gt.add_row("[red]FAIL[/red]", f)
    else:
        gt.add_row("[green]ok[/green]", "every measured number is inside budget")
    if mixer and mixer.get("stale"):
        # Not a failure: the controller is on demand and leaves when the session goes quiet, so its
        # last snapshot is history rather than a live reading. Saying so beats implying it is current.
        gt.add_row("[yellow]note[/yellow]",
                   f"mixer counters are last-known (pid {mixer.get('pid')}, "
                   f"{_fmt_age(mixer.get('age_s'))}) — the controller is on demand, so this is age, not fault")
    if not summ.get("had_baseline"):
        gt.add_row("[yellow]note[/yellow]",
                   "regression gate is unarmed — no baseline on this host yet. "
                   "`nhj stats --save-baseline` once you like the current numbers.")
    gt.add_row("[dim]measured window[/dim]",
               "the ladder from decision to hand-off; queue dispatch and mixer buffering are outside it")
    blocks.append(gt)
    return blocks
