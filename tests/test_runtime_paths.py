from __future__ import annotations

import json
import os
from pathlib import Path

from nhj import duck_player, hook, inference_muzak, resources, state, tts_server
from nhj.adapters import audio


def test_state_reads_legacy_config_state_before_new_state_dir(tmp_path, monkeypatch):
    new_state = tmp_path / "Library" / "Application Support" / "not-happy-jan"
    legacy = tmp_path / ".config" / "nhj"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text(json.dumps({"flags": {"mute": True}}))
    monkeypatch.delenv("NHJ_STATE_FILE", raising=False)
    monkeypatch.setattr(resources, "state_dir", lambda create=True: new_state)
    monkeypatch.setattr(resources, "legacy_config_dir", lambda: legacy)

    assert state.get_flag("mute") is True


def test_state_writes_new_state_dir_not_legacy(tmp_path, monkeypatch):
    new_state = tmp_path / "Library" / "Application Support" / "not-happy-jan"
    legacy = tmp_path / ".config" / "nhj"
    legacy.mkdir(parents=True)
    (legacy / "state.json").write_text(json.dumps({"flags": {"mute": True}}))
    monkeypatch.delenv("NHJ_STATE_FILE", raising=False)
    monkeypatch.setattr(resources, "state_dir", lambda create=True: new_state)
    monkeypatch.setattr(resources, "legacy_config_dir", lambda: legacy)

    state.set_flag("mute", False)

    assert json.loads((new_state / "state.json").read_text())["flags"]["mute"] is False
    assert json.loads((legacy / "state.json").read_text())["flags"]["mute"] is True


def test_audio_runtime_paths_use_resources():
    assert duck_player._EVENTS == resources.audio_events_dir(create=False)
    assert audio._CLIPS_ROOT == resources.cache_dir(create=False) / "clips"
    assert audio._TMP_ROOT == resources.audio_tmp_dir(create=False)


def test_hook_and_muzak_state_paths_use_resources(tmp_path, monkeypatch):
    monkeypatch.delenv("NHJ_MUZAK_STATE", raising=False)
    monkeypatch.setattr(resources, "legacy_config_dir", lambda: tmp_path / "missing-legacy")
    assert hook._SEEN == resources.state_file("hook-seen.json")
    assert inference_muzak._state_path() == resources.state_file("music.json")


def test_tts_server_defaults_huggingface_cache_to_nhj_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("NHJ_CACHE_DIR", str(tmp_path / "cache"))
    for name in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        monkeypatch.delenv(name, raising=False)

    tts_server._configure_hf_cache()

    assert Path(os.environ["HF_HOME"]) == resources.tts_model_cache_dir()


def test_tts_server_respects_existing_huggingface_cache(tmp_path, monkeypatch):
    custom = tmp_path / "hf"
    monkeypatch.setenv("NHJ_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HF_HOME", str(custom))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE", raising=False)

    tts_server._configure_hf_cache()

    assert os.environ["HF_HOME"] == str(custom)


def test_migrate_legacy_tts_model_moves_into_managed_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_is_checkout", lambda: False)
    monkeypatch.setenv("NHJ_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    managed = resources.tts_model_cache_dir(create=False)
    monkeypatch.setenv("HF_HOME", str(managed))                 # NHJ owns the cache

    legacy = (tmp_path / "home" / ".cache" / "huggingface" / "hub"
              / "models--mlx-community--Qwen3-TTS-12Hz-0.6B-Base-8bit")
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("{}")

    assert tts_server._migrate_legacy_models() == 1
    moved = managed / "hub" / legacy.name
    assert moved.is_dir() and (moved / "config.json").exists()
    assert not legacy.exists()
    assert tts_server._migrate_legacy_models() == 0            # idempotent once managed


def test_migrate_skips_when_user_overrides_hf_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_is_checkout", lambda: False)
    monkeypatch.setenv("NHJ_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "user-hf"))    # user override ≠ managed

    legacy = (tmp_path / "home" / ".cache" / "huggingface" / "hub"
              / "models--mlx-community--Qwen3-TTS-12Hz-0.6B-Base-8bit")
    legacy.mkdir(parents=True)

    assert tts_server._migrate_legacy_models() == 0            # respect the user's cache
    assert legacy.exists()
