"""The phase-1 measurement store: what the two audible paths cost, and what that means (§9 budgets).

These tests are the reason the store is shaped the way it is: the numbers have to survive an empty tree,
a torn log line, a dead mixer and a host that has never had a baseline, without ever becoming the reason
a status command fails or a vibe goes missing.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from nhj import stats as S


def _path(name: str):
    return S._state_path(name)


# ---- percentile -------------------------------------------------------------
def test_percentile_is_nearest_rank_and_safe_when_empty():
    assert S.percentile([], 95) is None
    assert S.percentile([7], 95) == 7
    vals = list(range(1, 101))
    assert S.percentile(vals, 50) == 50
    assert S.percentile(vals, 95) == 95
    assert S.percentile(vals, 100) == 100
    assert S.percentile([5, 1, 3], 0) == 1              # clamped both ends


def test_percentile_reports_a_value_that_actually_happened():
    """Nearest rank on five samples must be one of them — an interpolated 1.5 s on three vibes reads
    like a measurement of something nobody experienced."""
    vals = [10.0, 20.0, 30.0, 40.0, 500.0]
    assert S.percentile(vals, 95) in vals


def test_the_slowest_sample_survives_a_small_window():
    """The rank rounds up, which is the difference between a gate and a decoration.

    Ten 100 ms renders plus one 3000 ms render is n=11, so p95 is rank 11 — the outlier. The rank this
    used to compute (round, not ceil) gave rank 10, reported 100 ms, and `--check` passed a host running
    half again over its 2 s budget while the one sample that broke the budget sat in the window.
    """
    vals = [100.0] * 10 + [3000.0]
    assert S.percentile(vals, 95) == 3000.0
    rows = [{"t": float(i), "outcome": "synth", "engine": "qwen", "ladder_ms": v}
            for i, v in enumerate(vals)]
    voice = S.voice_summary(rows)
    assert voice["rungs"]["synth"]["p95_ms"] == 3000.0
    fails = S.budget_failures(None, voice)
    assert any("live voice p95 3000 ms over its 2000 ms budget (n=11)" in f for f in fails)


# ---- the voice window ------------------------------------------------------
def test_record_voice_appends_and_read_returns_oldest_first():
    S.record_voice("bank", ladder_s=0.05, path=__file__)
    S.record_voice("synth", "qwen", ladder_s=1.2, render_s=1.1, path=__file__)
    rows = S.read_voice()
    assert [r["outcome"] for r in rows] == ["bank", "synth"]
    assert rows[0]["engine"] == "-"                      # no engine served the bank rung
    assert "render_ms" not in rows[0]                    # and nothing was synthesized, so none is claimed
    assert rows[1]["engine"] == "qwen" and rows[1]["render_ms"] == 1100.0
    assert rows[0]["bytes"] == os.path.getsize(__file__)


def test_a_torn_tail_line_costs_one_record_not_the_window():
    p = _path(S.VOICE_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"t": 1.0, "outcome": "bank", "engine": "-", "ladder_ms": 1.0}) + "\n"
                 + json.dumps({"t": 2.0, "outcome": "cache", "engine": "-", "ladder_ms": 2.0}) + "\n"
                 + '{"t": 3.0, "outcome": "syn", "lad')
    rows = S.read_voice()
    assert [r["outcome"] for r in rows] == ["bank", "cache"]


def test_a_record_that_is_not_a_dict_is_ignored():
    p = _path(S.VOICE_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('[1, 2]\n{"outcome": "bank", "ladder_ms": 1.0}\n\n')
    assert len(S.read_voice()) == 1


def test_the_log_rotates_instead_of_growing_forever(monkeypatch):
    monkeypatch.setattr(S, "VOICE_MAX_BYTES", 2000)
    for _ in range(200):
        S.record_voice("bank", ladder_s=0.001)
    assert _path(S.VOICE_LOG).stat().st_size <= 2200
    assert _path(S.VOICE_LOG_OLD).exists()
    assert 0 < len(S.read_voice()) < 200                 # a window, not a history
    assert _path(S.VOICE_LOG).stat().st_size + _path(S.VOICE_LOG_OLD).stat().st_size < 5000


def test_a_failing_write_loses_the_sample_not_the_voice(monkeypatch):
    """Instrumentation must never be the reason a line goes unspoken."""
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(S, "_state_path", boom)
    S.record_voice("bank", ladder_s=0.01)                # must not raise
    assert S.read_voice() == []


# ---- summary arithmetic ----------------------------------------------------
_ROWS = [
    {"t": 1.0, "outcome": "bank", "engine": "-", "ladder_ms": 2.0, "bytes": 100},
    {"t": 2.0, "outcome": "cache", "engine": "-", "ladder_ms": 3.0},
    {"t": 3.0, "outcome": "synth", "engine": "audiocpp", "ladder_ms": 900.0, "render_ms": 880.0},
    {"t": 4.0, "outcome": "fallback", "engine": "audiocpp", "ladder_ms": 950.0, "render_ms": 940.0,
     "error": "audiocpp: 500 Server Error"},
    {"t": 5.0, "outcome": "none", "engine": "-", "ladder_ms": 1.0},
]


def test_summary_groups_rungs_engines_and_the_hit_rate():
    s = S.voice_summary(_ROWS)
    assert s["samples"] == 5 and (s["since"], s["until"]) == (1.0, 5.0)
    assert s["rungs"]["bank"]["n"] == 1 and s["rungs"]["bank"]["bytes_p50"] == 100
    assert s["rungs"]["none"]["n"] == 1                  # silent vibes are counted, not hidden
    eng = s["engines"]["audiocpp"]
    assert eng["renders"] == 2 and eng["errors"] == 1
    # nearest rank on an even sample: p50 is the lower observation, p95 the higher. A mean of 910 ms
    # would describe a render that never happened.
    assert eng["render_p50_ms"] == 880.0 and eng["render_p95_ms"] == 940.0
    # the hit rate is the number that says whether the ≤300-clip cache earns its disk
    assert s["hit_rate"] == {"answered_from_disk": 2, "spoken": 4, "rate": 0.5}


def test_errors_are_grouped_so_one_log_grep_becomes_a_counter():
    rows = _ROWS + [{"t": 6.0, "outcome": "fallback", "engine": "audiocpp", "ladder_ms": 900.0,
                     "error": "audiocpp: 500 Server Error"}]
    errs = {(e["engine"], e["error"]): e["n"] for e in S.voice_summary(rows)["errors"]}
    assert errs[("audiocpp", "audiocpp: 500 Server Error")] == 2


def test_an_engine_that_failed_every_call_still_has_a_row():
    """The window the engine table exists for: nothing got spoken, and the ladder said "none".

    `meter["engine"]` is only ever set where synthesis was attempted, so a failed attempt still names the
    engine — but its rung stays "none", and filtering the engine table on the synthesis rungs dropped it.
    An engine timing out on every call showed no row, hiding both its error count and its slow failures,
    which is the opposite of what ADR-10 promises the audiocpp 500s buy.
    """
    rows = [{"t": 1.0, "outcome": "none", "engine": "audiocpp", "ladder_ms": 2100.0,
             "render_ms": 2050.0, "error": "audiocpp: 500 Server Error"},
            {"t": 2.0, "outcome": "none", "engine": "audiocpp", "ladder_ms": 2200.0,
             "render_ms": 2150.0, "error": "audiocpp: 500 Server Error"}]
    s = S.voice_summary(rows)
    eng = s["engines"]["audiocpp"]
    assert eng["n"] == 2 and eng["renders"] == 2 and eng["errors"] == 2
    assert eng["render_p95_ms"] == 2150.0
    assert s["rungs"]["none"]["n"] == 2                    # the rung table still counts it as unspoken
    assert s["hit_rate"] is None                           # and nothing was spoken, so no rate to quote


def test_an_empty_window_summarises_without_dividing_by_zero():
    s = S.voice_summary([])
    assert s == {"samples": 0, "rungs": {}, "engines": {}, "errors": [], "hit_rate": None}


# ---- budgets ---------------------------------------------------------------
def test_a_missed_rung_budget_is_named_with_its_sample_count():
    rows = [{"t": float(i), "outcome": "synth", "engine": "qwen", "ladder_ms": 2600.0,
             "render_ms": 2500.0} for i in range(20)]
    fails = S.budget_failures(None, S.voice_summary(rows))
    assert len(fails) == 1
    assert "live voice p95 2600 ms over its 2000 ms budget (n=20)" in fails[0]


def test_a_bank_rung_within_budget_does_not_fail():
    rows = [{"t": float(i), "outcome": "bank", "engine": "-", "ladder_ms": 4.0} for i in range(20)]
    assert S.budget_failures(None, S.voice_summary(rows)) == []


def test_the_mixer_fails_on_deadline_misses_and_on_a_slow_callback():
    snap = S.mixer_summary({"written_at": time.time(), "pid": os.getpid(), "block_us": 21333.3,
                            "callback_p95_us": 9000.0, "deadline_misses": 3, "blocks": 1000})
    fails = S.budget_failures(snap, {"rungs": {}})
    assert any("missed its audio deadline 3 time(s)" in f for f in fails)
    assert any("callback p95 9000 us over 5333 us" in f for f in fails)


def test_a_clean_mixer_snapshot_passes():
    snap = S.mixer_summary({"written_at": time.time(), "pid": os.getpid(), "block_us": 21333.3,
                            "callback_p50_us": 180.0, "callback_p95_us": 400.0,
                            "deadline_misses": 0, "blocks": 1000, "underruns": 0})
    assert S.budget_failures(snap, {"rungs": {}}) == []


def test_nothing_measured_is_not_reported_as_within_budget():
    """The gate can only speak about numbers that exist; silence is stated elsewhere."""
    assert S.budget_failures(None, {"rungs": {}}) == []


# ---- the regression gate ---------------------------------------------------
def _synth_rows(ladder_ms, render_ms, n=5):
    return [{"t": float(i), "outcome": "synth", "engine": "qwen", "ladder_ms": ladder_ms,
             "render_ms": render_ms} for i in range(n)]


def test_gated_numbers_cover_both_audible_paths():
    g = S._gated_numbers({"callback_p50_us": 120.0, "callback_p95_us": 400.0, "uptime_s": 99.0},
                         S.voice_summary(_synth_rows(1000.0, 900.0)))
    assert g["synth_p95_ms"] == 1000.0 and g["render[qwen]_p95_ms"] == 900.0
    assert g["mixer_callback_p95_us"] == 400.0
    assert "uptime_s" not in g and "mixer_uptime_s" not in g      # cost, not calendar


def test_the_gate_arms_on_a_baseline_and_only_fails_on_slowness():
    voice = S.voice_summary(_synth_rows(1000.0, 900.0))
    payload = S.save_baseline(None, voice)
    assert payload["numbers"]["synth_p95_ms"] == 1000.0

    faster, had = S.regression_failures(None, S.voice_summary(_synth_rows(100.0, 90.0)))
    assert had and faster == []

    slower, had = S.regression_failures(None, S.voice_summary(_synth_rows(1400.0, 1300.0)))
    assert had and any("synth_p95_ms" in f and "+40 %" in f for f in slower)


def test_a_ten_percent_move_is_still_inside_a_ten_percent_gate():
    base = S.voice_summary(_synth_rows(1000.0, 900.0))
    S.save_baseline(None, base)
    exactly, had = S.regression_failures(None, S.voice_summary(_synth_rows(1100.0, 990.0)))
    assert had and exactly == []


def test_without_a_baseline_the_gate_is_unarmed_rather_than_guessing():
    fails, had = S.regression_failures(None, S.voice_summary(_synth_rows(9000.0, 8000.0)))
    assert (fails, had) == ([], False)


def test_save_baseline_with_nothing_measured_records_nothing():
    assert S.save_baseline(None, S.voice_summary([]))["numbers"] == {}


# ---- mixer snapshot --------------------------------------------------------
def test_a_snapshot_round_trips_and_knows_its_author_is_alive():
    S.write_mixer({"blocks": 42, "callback_p95_us": 300.0, "block_us": 21333.3})
    snap = S.mixer_summary()
    assert snap["blocks"] == 42 and snap["pid_alive"] is True and snap["stale"] is False
    # written_at is rounded to milliseconds, so compare with slack instead of claiming an ordering the
    # rounding can invert by a fraction of a millisecond
    assert abs(snap["written_at"] - time.time()) < 1.0
    assert snap["age_s"] == 0.0                          # a fresh snapshot never reads as old


def test_a_snapshot_from_a_dead_controller_is_marked_stale(monkeypatch):
    def dead_pid(pid, sig):
        raise ProcessLookupError()
    monkeypatch.setattr(os, "kill", dead_pid)
    snap = S.mixer_summary({"pid": 4242, "written_at": time.time(), "blocks": 1})
    assert snap["pid_alive"] is False and snap["stale"] is True


def test_a_snapshot_that_stops_being_refreshed_goes_stale():
    snap = S.mixer_summary({"pid": os.getpid(), "written_at": time.time() - 600, "blocks": 1},
                           now=time.time())
    assert snap["stale"] is True and snap["age_s"] > 500


def test_missing_and_corrupt_snapshots_read_as_no_mixer():
    assert S.read_mixer() is None
    p = _path(S.MIXER_STATS)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json")
    assert S.read_mixer() is None and S.mixer_summary() is None


def test_a_deadline_fraction_is_reported_against_the_blocks_own_period():
    snap = S.mixer_summary({"pid": os.getpid(), "written_at": time.time(), "block_us": 20000.0,
                            "callback_p95_us": 500.0})
    assert snap["deadline_miss_frac"] == 0.025


# ---- whole-surface behaviour ----------------------------------------------
def test_stats_survive_a_tree_where_nothing_has_ever_run():
    summ = S.summarise()
    assert summ["mixer"] is None and summ["voice"]["samples"] == 0
    assert summ["budget_failures"] == [] and summ["had_baseline"] is False
    blocks = S.render(summ)
    assert blocks and all(hasattr(b, "title") for b in blocks)


def test_reset_removes_every_retained_file():
    S.record_voice("bank", ladder_s=0.01)
    S.write_mixer({"blocks": 1})
    S.save_baseline(None, S.voice_summary())
    removed = S.reset()
    assert set(removed) >= {S.VOICE_LOG, S.MIXER_STATS, S.BASELINE}
    assert S.read_voice() == [] and S.read_mixer() is None and S.read_baseline() is None
    assert S.reset() == []                               # idempotent


# ---- the CLI surface -------------------------------------------------------
def _invoke(*args):
    from typer.testing import CliRunner

    from nhj import cli
    return CliRunner().invoke(cli.cli, ["stats", *args])


def test_cli_stats_json_reports_an_empty_window_with_exit_zero():
    r = _invoke("--json")
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["voice"]["samples"] == 0 and data["mixer"] is None


def test_cli_stats_prints_tables_without_a_traceback():
    S.record_voice("bank", ladder_s=0.004)
    r = _invoke()
    assert r.exit_code == 0 and "Traceback" not in r.output
    assert "bank" in r.stdout and "Budgets" in r.stdout


def test_cli_check_exits_one_when_a_budget_is_missed():
    S.record_voice("synth", "audiocpp", ladder_s=3.0, render_s=2.9)
    r = _invoke("--check")
    assert r.exit_code == 1 and "BUDGET" in r.stdout and "live voice" in r.stdout


def test_cli_json_check_keeps_stdout_parseable_when_the_check_fails():
    """--json promises one document on stdout, and the moment a consumer needs to parse it is exactly the
    moment the check failed. Verdict lines go to stderr; the exit status still carries the failure."""
    S.record_voice("synth", "audiocpp", ladder_s=3.0, render_s=2.9)
    r = _invoke("--json", "--check")
    assert r.exit_code == 1
    data = json.loads(r.stdout)                   # a BUDGET line on stdout would raise here
    assert any("live voice" in f for f in data["budget_failures"])
    assert "BUDGET" in r.stderr and "live voice" in r.stderr


def test_cli_save_baseline_reports_a_write_it_could_not_make(tmp_path, monkeypatch):
    """A "baseline saved" over a failed write is the worst outcome this command can produce: the operator
    believes the regression gate is armed, and --check then passes because there is no baseline to
    compare against. A read-only state dir or a full disk has to say so and fail."""
    S.record_voice("synth", "qwen", ladder_s=0.9, render_s=0.8)      # there IS something worth saving
    blocker = tmp_path / "blocker"
    blocker.write_text("in the way of the state directory")
    monkeypatch.setattr(S, "_state_path", lambda name: blocker / "nested" / name)
    r = _invoke("--save-baseline")
    assert r.exit_code == 1 and "NOT saved" in r.output


def test_save_baseline_raises_instead_of_returning_a_payload_it_never_wrote(tmp_path, monkeypatch):
    """Same fault at the library edge, so a caller other than the CLI cannot inherit the lie."""
    blocker = tmp_path / "blocker"
    blocker.write_text("in the way")
    monkeypatch.setattr(S, "_state_path", lambda name: blocker / "nested" / name)
    voice = S.voice_summary([{"t": 1.0, "outcome": "bank", "engine": "-", "ladder_ms": 5.0}])
    with pytest.raises(OSError):
        S.save_baseline(None, voice)


def test_cli_check_exits_zero_within_budget():
    S.record_voice("bank", ladder_s=0.002)
    r = _invoke("--check")
    assert r.exit_code == 0, r.stdout


def test_cli_saves_a_baseline_then_reset_clears_it():
    S.record_voice("bank", ladder_s=0.002)
    r = _invoke("--save-baseline")
    assert r.exit_code == 0 and "baseline saved" in r.stdout
    assert S.read_baseline()["numbers"]["bank_p95_ms"] == 2.0
    assert _invoke("--reset").exit_code == 0 and S.read_baseline() is None


def test_cli_cannot_save_a_baseline_from_nothing():
    r = _invoke("--save-baseline")
    assert r.exit_code == 1 and "nothing to record" in r.stdout


def test_an_old_mixer_snapshot_is_labelled_history_rather_than_current(monkeypatch):
    """The controller is on demand and exits when the session goes quiet, so an old snapshot is the
    normal case. It has to read as age rather than as a live reading — and must not fail --check."""
    def dead_pid(pid, sig):
        raise ProcessLookupError()
    monkeypatch.setattr(os, "kill", dead_pid)
    S.write_mixer({"blocks": 10, "block_us": 21333.3, "callback_p50_us": 200.0,
                   "callback_p95_us": 300.0, "deadline_misses": 0})
    p = _path(S.MIXER_STATS)
    stale = json.loads(p.read_text())
    stale["written_at"] = time.time() - 4000
    p.write_text(json.dumps(stale))

    summ = S.summarise()
    assert summ["mixer"]["stale"] is True and summ["budget_failures"] == []
    assert _table_text(summ).count("last-known") == 1


def test_a_live_mixer_snapshot_claims_no_caveat():
    S.write_mixer({"blocks": 10, "block_us": 21333.3, "callback_p95_us": 300.0,
                   "deadline_misses": 0})
    summ = S.summarise()
    assert summ["mixer"]["stale"] is False
    assert "last-known" not in _table_text(summ)


def _table_text(summ: dict) -> str:
    """What `nhj stats` actually puts on screen, as text to assert against."""
    import io

    from rich.console import Console
    buf = io.StringIO()
    console = Console(file=buf, width=200)
    for block in S.render(summ):
        console.print(block)
    return buf.getvalue()


def test_every_command_shaped_function_in_the_cli_is_registered():
    """A refactor that swallows a `@cli.command` decorator takes a command out of the product silently:
    adding `nhj stats` to cli.py nearly removed `nhj dynamic-test` that way, and nothing noticed — the
    only visible symptom is one line missing from `nhj --help`.

    So the check is deliberately not "does the registry contain what the source declares" (a swallowed
    decorator vanishes from both sides, and that passes). A function whose every default is a
    `typer.Option`/`typer.Argument` is a command by construction, whatever decorators survive above it, so
    those are compared against what typer actually registered.
    """
    import inspect

    import typer
    from typer.models import ArgumentInfo, OptionInfo

    from nhj import cli as cli_module
    # Every Typer app in the module, not just the root one: `nhj music …` lives on a sub-app, and
    # comparing against the root alone would report its commands as orphans.
    registered = {c.callback for app in vars(cli_module).values() if isinstance(app, typer.Typer)
                  for c in app.registered_commands if c.callback}
    orphans = []
    for value in vars(cli_module).values():
        if not inspect.isfunction(value) or value.__module__ != cli_module.__name__:
            continue
        params = [p for p in inspect.signature(value).parameters.values()
                  if p.default is not inspect.Parameter.empty
                  and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
        if params and all(isinstance(p.default, (OptionInfo, ArgumentInfo)) for p in params):
            if value not in registered:
                orphans.append(value.__name__)
    assert not orphans, f"command-shaped but not registered: {sorted(orphans)}"
    names = {c.name or c.callback.__name__.replace("_", "-") for app in vars(cli_module).values()
             if isinstance(app, typer.Typer) for c in app.registered_commands if c.callback}
    assert {"stats", "dynamic-test", "status"} <= names


def test_the_budgets_are_the_ones_spec_section_9_promises():
    """These numbers are `SPEC.md` §9's, not preferences, so the gate cannot quietly drift away from the
    promise it enforces. If §9 moves, this test moves in the same change."""
    spec = (Path(__file__).resolve().parents[1] / "SPEC.md").read_text()
    line = next(line for line in spec.splitlines() if "Time to first sound after Stop" in line)
    assert "bank/chime ≤ 300 ms" in line and "live voice ≤ 2 s warm" in line
    assert S.BUDGETS["bank_ms"] == 300.0 and S.BUDGETS["chime_ms"] == 300.0
    assert S.BUDGETS["synth_ms"] == 2000.0
    assert S.REGRESSION_RATIO == pytest.approx(1.10)
