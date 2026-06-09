"""Packaging / resource-resolution tests (issue #2).

Proves runtime resources resolve in a source checkout, that user-writable dirs
are separate from immutable package data, and (slow) that a built wheel ships
every required resource and no downloaded media.
"""
from __future__ import annotations

import json
import importlib.util
import platform
import subprocess
import sys
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
import typer

from nhj import characters, cli, config, resources

_REPO = Path(__file__).resolve().parents[1]


def test_bundled_resources_resolve():
    assert resources.config_file().exists()
    assert resources.claude_skill_file().exists()
    assert resources.hooks_file().exists()
    defs = resources.character_defs_dir()
    for name in ("jan", "bazza", "karren"):
        assert (defs / name / "character.yaml").exists()
        assert characters.Character.load(name).name


def test_config_is_not_empty():
    # The old loader silently fell back to {} when the path was wrong — guard that.
    assert config.CFG, "config loaded empty (silent fallback)"
    assert config.section("intents") is not None


def test_user_dirs_writable_and_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("NHJ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NHJ_CONFIG_DIR", str(tmp_path / "config"))
    d, c = resources.data_dir(), resources.config_dir()
    assert d.is_dir() and c.is_dir()
    (d / "probe").write_text("x")            # writable
    (c / "probe").write_text("x")
    # user data is NOT the package directory
    assert resources._PKG not in d.parents and resources._PKG != d


def test_env_file_discovery_does_not_create_config_dir(tmp_path, monkeypatch):
    config_root = tmp_path / "missing"
    monkeypatch.setenv("NHJ_CONFIG_DIR", str(config_root))
    monkeypatch.setattr(resources, "_is_checkout", lambda: False)
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("optional .env discovery must not create directories")
        ),
    )

    assert resources.env_file() == config_root / ".env"
    assert not config_root.exists()


def test_macos_default_user_dirs_follow_library_conventions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(resources, "_is_checkout", lambda: False)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("NHJ_CONFIG_DIR", "NHJ_DATA_DIR", "NHJ_STATE_DIR", "NHJ_CACHE_DIR", "NHJ_LOG_DIR"):
        monkeypatch.delenv(name, raising=False)

    app_support = home / "Library" / "Application Support" / "not-happy-jan"
    assert resources.config_dir(create=False) == app_support
    assert resources.data_dir(create=False) == app_support
    assert resources.state_dir(create=False) == app_support
    assert resources.env_file() == app_support / ".env"
    assert resources.cache_dir(create=False) == home / "Library" / "Caches" / "not-happy-jan"
    assert resources.tts_model_cache_dir(create=False) == home / "Library" / "Caches" / "not-happy-jan" / "tts-models"
    assert resources.llm_model_dir(create=False) == home / "Library" / "Caches" / "not-happy-jan" / "ocker-bogan-nano"
    assert resources.log_dir(create=False) == home / "Library" / "Logs" / "not-happy-jan"


def test_non_macos_default_user_dirs_follow_xdg(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(resources, "_is_checkout", lambda: False)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("NHJ_CONFIG_DIR", "NHJ_DATA_DIR", "NHJ_STATE_DIR", "NHJ_CACHE_DIR", "NHJ_LOG_DIR",
                 "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)

    assert resources.config_dir(create=False) == home / ".config" / "not-happy-jan"
    assert resources.data_dir(create=False) == home / ".local" / "share" / "not-happy-jan"
    assert resources.state_dir(create=False) == home / ".local" / "state" / "not-happy-jan"
    assert resources.cache_dir(create=False) == home / ".cache" / "not-happy-jan"
    assert resources.tts_model_cache_dir(create=False) == home / ".cache" / "not-happy-jan" / "tts-models"
    assert resources.llm_model_dir(create=False) == home / ".cache" / "not-happy-jan" / "ocker-bogan-nano"
    assert resources.log_dir(create=False) == home / ".local" / "state" / "not-happy-jan" / "logs"


def test_user_dir_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_is_checkout", lambda: False)
    monkeypatch.setenv("NHJ_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NHJ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NHJ_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NHJ_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("NHJ_LOG_DIR", str(tmp_path / "logs"))

    assert resources.config_dir(create=False) == tmp_path / "config"
    assert resources.data_dir(create=False) == tmp_path / "data"
    assert resources.state_dir(create=False) == tmp_path / "state"
    assert resources.cache_dir(create=False) == tmp_path / "cache"
    assert resources.tts_model_cache_dir(create=False) == tmp_path / "cache" / "tts-models"
    assert resources.llm_model_dir(create=False) == tmp_path / "cache" / "ocker-bogan-nano"
    assert resources.log_dir(create=False) == tmp_path / "logs"


def test_setup_media_strict_mode_fails_when_bundles_are_unavailable(
    tmp_path, monkeypatch
):
    import shutil
    import urllib.error

    data_root = tmp_path / "data"
    monkeypatch.setattr(resources, "data_dir", lambda: data_root)
    monkeypatch.setattr(resources, "voices_dir", lambda: data_root / "voices")
    monkeypatch.setattr(resources, "audio_dir", lambda: data_root / "audio")
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        "urllib.request.urlretrieve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("offline")
        ),
    )

    with pytest.raises(typer.Exit) as exc_info:
        cli.setup_media(
            base="https://example.invalid/media",
            force=False,
            strict=True,
        )

    assert exc_info.value.exit_code == 1


def test_install_hook_exec_form(tmp_path, monkeypatch):
    """install-hook must emit exec-form entries (command + args), not a shell string."""
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "_PYTHON", Path("/tmp/NHJ Python/bin/python"))

    cli.install_hook(settings)

    hooks = json.loads(settings.read_text())["hooks"]
    for event, entries in hooks.items():
        h = entries[0]["hooks"][0]
        # exec-form: command is the bare python path, args carries the module flags
        assert h["command"] == "/tmp/NHJ Python/bin/python", \
            f"{event}: command must be the bare python path, got {h['command']!r}"
        args = h.get("args") or []
        assert len(args) >= 2, f"{event}: args must have at least 2 elements, got {args!r}"
        assert args[0] == "-m", f"{event}: args[0] must be '-m', got {args[0]!r}"
        assert args[1].startswith("nhj."), f"{event}: args[1] must be nhj.<module>, got {args[1]!r}"


def test_install_hook_descriptions_and_timeouts(tmp_path, monkeypatch):
    """install-hook must emit description and timeout for each event."""
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "_PYTHON", Path("/usr/bin/python3"))

    cli.install_hook(settings)

    hooks = json.loads(settings.read_text())["hooks"]
    assert set(hooks) == {"Stop", "StopFailure", "UserPromptSubmit", "SessionEnd"}
    for event, entries in hooks.items():
        h = entries[0]["hooks"][0]
        assert "description" in h, f"{event}: missing description"
        assert "timeout" in h, f"{event}: missing timeout"
        assert isinstance(h["timeout"], int), f"{event}: timeout must be an int"
    # Stop covers NHJ_HOOK_WAIT (default 3 s) + overhead → must be >= 10 s
    stop_h = hooks["Stop"][0]["hooks"][0]
    assert stop_h["timeout"] >= 10, "Stop timeout too short to cover NHJ_HOOK_WAIT"
    # UserPromptSubmit blocks every prompt — keep it short
    ups_h = hooks["UserPromptSubmit"][0]["hooks"][0]
    assert ups_h["timeout"] <= 30, "UserPromptSubmit timeout unreasonably long"


def test_install_hook_idempotent_module_form(tmp_path, monkeypatch):
    # The exec-form `-m nhj.` module form must be superseded on re-install, not duplicated.
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(cli, "_PYTHON", Path("/tmp/venv/bin/python"))
    settings.write_text(json.dumps({"hooks": {"Stop": [
        {"matcher": "", "hooks": [{"type": "command", "command": "/usr/bin/other-tool"}]}
    ]}}))
    cli.install_hook(settings)
    cli.install_hook(settings)                               # twice → must stay idempotent
    stop_entries = json.loads(settings.read_text())["hooks"]["Stop"]
    nhj_hooks = [h for b in stop_entries for h in b["hooks"]
                 if h.get("command") == "/tmp/venv/bin/python"
                 and "-m" in (h.get("args") or [])]
    other_hooks = [h for b in stop_entries for h in b["hooks"]
                   if "other-tool" in h.get("command", "")]
    assert len(nhj_hooks) == 1, "NHJ Stop hook must appear exactly once"
    assert other_hooks, "unrelated entry must be preserved"


def test_bundled_hooks_template_no_legacy_paths():
    """Bundled hooks/hooks.json must not reference $NHJ_ROOT, $NHJ_VENV, or src/nhj paths."""
    from nhj import resources
    content = resources.hooks_file().read_text()
    for forbidden in ("$NHJ_ROOT", "$NHJ_VENV", "src/nhj", "/nhj/hook.py", "/nhj/prompt_hook.py"):
        assert forbidden not in content, \
            f"bundled hooks.json still references {forbidden!r} — update hooks/hooks.json"


def test_install_skill_copies_bundled_skill_idempotently(tmp_path, monkeypatch):
    src = tmp_path / "source" / "SKILL.md"
    src.parent.mkdir(parents=True)
    src.write_text("---\nname: not-happy-jan\n---\n")
    dest = tmp_path / "claude" / "skills" / "not-happy-jan" / "SKILL.md"
    monkeypatch.setattr(resources, "claude_skill_file", lambda: src)

    cli.install_skill(dest)
    first = dest.read_text()
    cli.install_skill(dest)

    assert dest.read_text() == first == src.read_text()


def test_install_skill_backs_up_existing_different_skill(tmp_path, monkeypatch):
    src = tmp_path / "source" / "SKILL.md"
    src.parent.mkdir(parents=True)
    src.write_text("---\nname: not-happy-jan\n---\n")
    dest = tmp_path / "claude" / "skills" / "not-happy-jan" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("custom local skill\n")
    monkeypatch.setattr(resources, "claude_skill_file", lambda: src)

    cli.install_skill(dest)

    assert dest.read_text() == src.read_text()
    assert dest.with_suffix(".md.pre-nhj.bak").read_text() == "custom local skill\n"


def test_claude_skill_uses_current_commands_and_paths():
    skill = resources.claude_skill_file().read_text()
    assert "nhj status" in skill
    assert "nhj set" in skill
    assert "~/.config/nhj" not in skill
    assert "allowed-tools: Bash" in skill


@pytest.mark.parametrize(
    "member",
    [
        tarfile.TarInfo("../outside.txt"),
        tarfile.TarInfo("voices/jan/ref.wav"),
    ],
)
def test_safe_extract_rejects_paths_and_links_outside_root(tmp_path, member):
    archive = tmp_path / "media.tar"
    if member.name.startswith("voices/"):
        member.type = tarfile.SYMTYPE
        member.linkname = "../../../outside.txt"
        payload = b""
    else:
        payload = b"escape"
        member.size = len(payload)

    with tarfile.open(archive, "w") as tf:
        tf.addfile(member, BytesIO(payload))

    with tarfile.open(archive) as tf, pytest.raises(ValueError):
        cli._safe_extract_tar(tf, tmp_path / "data")


@pytest.mark.slow
def test_wheel_bundles_resources_and_excludes_media(tmp_path):
    """Build a wheel and assert its contents (issue #2's required packaging test)."""
    build_module_spec = importlib.util.find_spec("build")
    if build_module_spec is None:
        pytest.skip("python -m build required to build the wheel")
    cmd = [
        sys.executable,
        "-m",
        "build",
        "--wheel",
        "--no-isolation",
        "--outdir",
        str(tmp_path),
    ]
    r = subprocess.run(cmd, cwd=_REPO, capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    whls = list(tmp_path.glob("*.whl"))
    assert whls, "no wheel produced"
    names = set(zipfile.ZipFile(whls[0]).namelist())

    required = {
        "nhj/resources.py",
        "nhj/_bundled/config/default.yaml",
        "nhj/_bundled/claude-skills/not-happy-jan/SKILL.md",
        "nhj/_bundled/hooks/hooks.json",
        "nhj/_bundled/voices/jan/character.yaml",
        "nhj/_bundled/voices/bazza/character.yaml",
        "nhj/_bundled/voices/karren/character.yaml",
    }
    assert required <= names, f"wheel missing: {required - names}"

    # Downloaded media (ref clips, audio) must NEVER be bundled in the wheel.
    leaked = [n for n in names if n.endswith(("ref.wav", "ref.txt", ".m4a")) or "/audio/" in n]
    assert not leaked, f"media leaked into the wheel: {leaked}"


def test_set_env_vars_creates_parent_dir(tmp_path):
    # #20: writing .env into a not-yet-created user config dir must not fail.
    env = tmp_path / "deep" / "nested" / ".env"
    cli._set_env_vars(env, {"NHJ_X": "1"})
    assert env.exists() and "NHJ_X=1" in env.read_text()


def test_safe_extract_tar_extracts_normal_file(tmp_path):
    # #22: the data-filter extraction path still extracts regular files correctly.
    src = tmp_path / "bundle.tar"
    with tarfile.open(src, "w") as tf:
        info = tarfile.TarInfo("audio/music/x.txt")
        body = b"hi"
        info.size = len(body)
        info.mode = 0o777                       # exotic mode the data filter should sanitise
        tf.addfile(info, BytesIO(body))
    dest = tmp_path / "out"
    with tarfile.open(src) as tf:
        cli._safe_extract_tar(tf, dest)
    assert (dest / "audio" / "music" / "x.txt").read_bytes() == b"hi"


def test_install_model_skips_launchagent_off_darwin(tmp_path, monkeypatch):
    # #21: --service is a macOS LaunchAgent; it must not run on non-macOS hosts.
    pytest.importorskip("huggingface_hub")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/llama-server")
    monkeypatch.setattr("huggingface_hub.hf_hub_download", lambda **k: str(tmp_path / "m.gguf"))
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("NHJ_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "_set_env_vars", lambda *a, **k: None)
    calls = {"n": 0}
    monkeypatch.setattr(cli, "_install_ocker_launchagent",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), tmp_path)[1])
    cli.install_model(repo="x/y", gguf="m.gguf", port=9991, service=True)
    assert calls["n"] == 0                       # LaunchAgent NOT installed on Linux


def test_scipy_runtime_dep_declared():
    """scipy is imported at runtime by duck_player (call-centre bandpass) — must be declared (#64)."""
    import sys
    if sys.version_info >= (3, 11):
        import tomllib as toml
    else:
        import tomli as toml
    with open(_REPO / "pyproject.toml", "rb") as fh:
        proj = toml.load(fh)["project"]
    declared = " ".join(proj["dependencies"]) + " ".join(
        d for v in proj.get("optional-dependencies", {}).values() for d in v
    )
    assert "scipy" in declared
