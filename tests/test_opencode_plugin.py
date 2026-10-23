"""integrations/opencode/nhj.js: feed synthetic opencode events through the plugin with a fake
`nhj` that records its argv + stdin, and check what it would have run. Skipped without node."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "integrations" / "opencode" / "nhj.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _run(tmp_path, events, min_seconds="0"):
    log = tmp_path / "calls.jsonl"
    fake = tmp_path / "fake-nhj"
    fake.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        payload="$(cat)"
        printf '%s\\n' "$(python3 -c 'import json,sys; print(json.dumps({{"args": sys.argv[1:], "stdin": sys.stdin.read()}}))' "$@" <<<"$payload")" >> {log}
    """))
    fake.chmod(0o755)
    script = tmp_path / "drive.mjs"
    script.write_text(textwrap.dedent(f"""\
        import {{ Nhj }} from "{PLUGIN}";
        const h = await Nhj({{ directory: "/work/reactor" }});
        for (const e of {json.dumps(events)}) await h.event({{ event: e }});
        await new Promise(r => setTimeout(r, 4000));
    """))
    env = {**os.environ, "NHJ_BIN": str(fake), "NHJ_OPENCODE_MIN_SECONDS": min_seconds}
    subprocess.run([shutil.which("node"), str(script)], env=env, check=True, timeout=30)
    return [json.loads(ln) for ln in log.read_text().splitlines()] if log.exists() else []


def _cmds(calls):
    return [" ".join(c["args"]) for c in calls]


def test_turn_busy_then_idle_runs_busy_idle_and_ok(tmp_path):
    calls = _run(tmp_path, [
        {"type": "session.status", "properties": {"sessionID": "s1", "status": {"type": "busy"}}},
        {"type": "session.idle", "properties": {"sessionID": "s1"}},
    ])
    # strictly in event order: the vibe must not overtake idle, nor idle overtake busy
    assert _cmds(calls) == ["busy", "idle", "vibe ok -m opencode is done"]
    assert json.loads(calls[0]["stdin"]) == {"session_id": "opencode-s1", "cwd": "/work/reactor"}


def test_short_turns_do_not_announce_but_still_release_the_music(tmp_path):
    calls = _run(tmp_path, [
        {"type": "session.status", "properties": {"sessionID": "s1", "status": {"type": "busy"}}},
        {"type": "session.idle", "properties": {"sessionID": "s1"}},
    ], min_seconds="3600")
    assert sorted(_cmds(calls)) == ["busy", "idle"]


def test_idle_is_debounced_and_needs_a_busy_turn(tmp_path):
    calls = _run(tmp_path, [
        {"type": "session.idle", "properties": {"sessionID": "s1"}},          # never busy: ignored
        {"type": "session.status", "properties": {"sessionID": "s1", "status": "busy"}},
        {"type": "session.idle", "properties": {"sessionID": "s1"}},
        {"type": "session.idle", "properties": {"sessionID": "s1"}},          # repeat: ignored
    ])
    assert _cmds(calls).count("idle") == 1


@pytest.mark.parametrize("etype,msg", [("permission.asked", "opencode needs your permission"),
                                        ("permission.updated", "opencode needs your permission"),
                                        ("question.asked", "opencode has a question")])
def test_blocked_on_you_is_attn(tmp_path, etype, msg):
    assert _cmds(_run(tmp_path, [{"type": etype, "properties": {"sessionID": "s1"}}])) == [f"vibe attn -m {msg}"]


def test_error_releases_the_music_and_announces(tmp_path):
    calls = _run(tmp_path, [{"type": "session.error", "properties": {"sessionID": "s1"}}])
    assert _cmds(calls) == ["idle", "vibe err -m opencode hit an error"]


def test_subagent_sessions_are_ignored(tmp_path):
    calls = _run(tmp_path, [
        {"type": "session.created", "properties": {"info": {"id": "child", "parentID": "s1"}}},
        {"type": "permission.asked", "properties": {"sessionID": "child"}},
        {"type": "session.error", "properties": {"sessionID": "child"}},
    ])
    assert calls == []
