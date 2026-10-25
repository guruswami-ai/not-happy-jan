"""Lazy path/env resolution (#61 slice 3).

The named modules used to resolve state/log/cache/data paths (and the voice
normalisation target) while being imported. They now resolve at point of use,
while preserving the private module attributes existing tests/consumers use.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from nhj import duck_player, hook, inference_muzak, queue_manager, resources
from nhj.adapters import audio


@pytest.mark.parametrize("module,attrs", [
    ("nhj.hook", "_LOG,_SEEN"),
    ("nhj.queue_manager", "_LOG"),
    ("nhj.duck_player", "_EVENTS"),
    ("nhj.inference_muzak", "_REPO_ROOT"),
    ("nhj.adapters.audio", "_VOICES_ROOT,_AUDIO_ROOT,_CLIPS_ROOT,_TMP_ROOT,_VOICE_TARGET_DBFS"),
])
def test_importing_does_not_resolve_paths_or_parse_voice_target(module, attrs):
    code = (
        "import importlib, sys; from nhj import resources; "
        "boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError('resolved at import')); "
        "[setattr(resources, name, boom) for name in "
        "('log_file', 'state_file', 'audio_events_dir', 'data_dir', 'voices_dir', "
        "'audio_dir', 'cache_dir', 'audio_tmp_dir')]; "
        "module = importlib.import_module(sys.argv[1]); "
        "print([name in vars(module) for name in sys.argv[2].split(',')])"
    )
    child_env = {**os.environ, "NHJ_VOICE_TARGET_DBFS": "not-a-float"}
    out = subprocess.run(
        [sys.executable, "-c", code, module, attrs],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == str([False] * len(attrs.split(",")))


def test_hook_paths_follow_resources_at_point_of_use(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    monkeypatch.setattr(resources, "state_file", lambda name: first / name)
    monkeypatch.setattr(resources, "log_file", lambda name: str(first / name))
    assert hook._seen_path() == first / "hook-seen.json"
    assert hook._SEEN == first / "hook-seen.json"
    assert "_SEEN" not in vars(hook)                 # access does not pin it

    monkeypatch.setattr(resources, "state_file", lambda name: second / name)
    monkeypatch.setattr(resources, "log_file", lambda name: str(second / name))
    assert hook._seen_path() == second / "hook-seen.json"
    assert hook._log_path() == str(second / "hook.log")


def test_hook_seen_override_seam_still_wins(tmp_path, monkeypatch):
    seen = tmp_path / "seen.json"
    monkeypatch.setattr(hook, "_SEEN", seen)
    monkeypatch.setattr(resources, "state_file", lambda name: tmp_path / "elsewhere" / name)
    hook._seen_set("session-1", "sig-1")
    assert seen.is_file()
    assert hook._seen_get("session-1") == "sig-1"


def test_queue_and_duck_paths_follow_resources(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "log_file", lambda name: str(tmp_path / name))
    monkeypatch.setattr(
        resources, "audio_events_dir", lambda create=True: tmp_path / "events")
    assert queue_manager._log_path() == str(tmp_path / "worker.log")
    assert queue_manager._LOG == str(tmp_path / "worker.log")
    assert duck_player._events_dir() == tmp_path / "events"
    assert duck_player._EVENTS == tmp_path / "events"


def test_inference_and_audio_roots_follow_resources(tmp_path, monkeypatch):
    monkeypatch.delenv("NHJ_MUZAK_DIR", raising=False)
    monkeypatch.setattr(resources, "data_dir", lambda create=True: tmp_path / "data")
    monkeypatch.setattr(resources, "voices_dir", lambda: tmp_path / "voices")
    monkeypatch.setattr(resources, "audio_dir", lambda: tmp_path / "audio")
    monkeypatch.setattr(resources, "cache_dir", lambda create=True: tmp_path / "cache")
    monkeypatch.setattr(
        resources, "audio_tmp_dir", lambda create=True: tmp_path / "audio-tmp")

    assert inference_muzak._repo_root() == tmp_path / "data"
    assert inference_muzak._music_dirs()[-1] == tmp_path / "data" / "audio" / "music"
    assert audio._voices_root() == tmp_path / "voices"
    assert audio._audio_root() == tmp_path / "audio"
    assert audio._clips_root() == tmp_path / "cache" / "clips"
    assert audio._tmp_root() == tmp_path / "audio-tmp"
    assert audio._CLIPS_ROOT == tmp_path / "cache" / "clips"


def test_audio_root_override_seams_still_win(tmp_path, monkeypatch):
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path / "clips")
    monkeypatch.setattr(audio, "_TMP_ROOT", tmp_path / "tmp")
    monkeypatch.setattr(resources, "cache_dir", lambda create=True: tmp_path / "elsewhere")
    monkeypatch.setattr(
        resources, "audio_tmp_dir", lambda create=True: tmp_path / "elsewhere-tmp")

    assert audio._cache_path("jan", "hello").is_relative_to(tmp_path / "clips")
    written = audio._write_temp_wav(b"RIFF")
    assert written.startswith(str(tmp_path / "tmp"))


def test_voice_target_is_read_at_point_of_use(monkeypatch):
    monkeypatch.setenv("NHJ_VOICE_TARGET_DBFS", "-12.5")
    assert audio._voice_target_dbfs() == -12.5
    assert audio._VOICE_TARGET_DBFS == -12.5
    assert "_VOICE_TARGET_DBFS" not in vars(audio)

    monkeypatch.setenv("NHJ_VOICE_TARGET_DBFS", "-9")
    assert audio._voice_target_dbfs() == -9.0

    monkeypatch.setattr(audio, "_VOICE_TARGET_DBFS", -3.0)
    assert audio._voice_target_dbfs() == -3.0
