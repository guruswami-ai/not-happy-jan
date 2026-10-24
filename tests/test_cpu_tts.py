"""CPU TTS tier (#49): TTS_ENGINE=os (espeak-ng / say) and =piper (piper CLI).

Real engines are external binaries, so tests install stub executables on PATH
that write a RIFF file the way the real tools do, and record their argv."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from nhj.adapters import audio
from nhj.characters import Character


def _stub(tmp_path, name: str, body: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return bindir


_ESPEAK = (
    'out=""; prev=""\n'
    'for a in "$@"; do echo "$a" >> "$FAKE_TTS_LOG"; [ "$prev" = "-w" ] && out="$a"; prev="$a"; done\n'
    "printf 'RIFFfake-espeak-wav-bytes-0123456789abcdefghijklmnopqrstuvwxyz' > \"$out\"\n"
)

_PIPER = (
    'out=""; prev=""\n'
    'for a in "$@"; do echo "$a" >> "$FAKE_TTS_LOG"; [ "$prev" = "--output_file" ] && out="$a"; prev="$a"; done\n'
    'cat >> "$FAKE_TTS_LOG"\n'
    "printf 'RIFFfake-piper-wav-bytes-0123456789abcdefghijklmnopqrstuvwxyz' > \"$out\"\n"
)


def _on_path(monkeypatch, bindir, log):
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_TTS_LOG", str(log))


def test_synth_os_espeak(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "linux")   # espeak branch, not macOS say
    log = tmp_path / "argv.log"
    _on_path(monkeypatch, _stub(tmp_path, "espeak-ng", _ESPEAK), log)
    monkeypatch.setattr(audio, "_TMP_ROOT", tmp_path / "tmp")
    wav = audio.synth_os("hello there", voice="karren")
    assert wav and wav.startswith(b"RIFF")
    argv = log.read_text().splitlines()
    assert "-w" in argv and "hello there" in argv
    assert "-p" in argv                       # per-character pitch was passed


def test_synth_os_missing_binary_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "linux")
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(audio, "_TMP_ROOT", tmp_path / "tmp")
    assert audio.synth_os("hello", voice="jan") is None


def test_os_command_macos_say_shape(monkeypatch):
    monkeypatch.setattr(audio.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("NHJ_OS_TTS_VOICE", "Karen")
    cmd, suffix = audio._os_command("hi", "jan", platform="darwin")
    assert cmd[0] == "/usr/bin/say" and "-o" in cmd and cmd[-1] == "hi"
    assert cmd[cmd.index("-v") + 1] == "Karen"
    assert suffix == ".aiff"


def test_synth_piper_stub_and_missing_model(tmp_path, monkeypatch):
    log = tmp_path / "argv.log"
    _on_path(monkeypatch, _stub(tmp_path, "piper", _PIPER), log)
    monkeypatch.setattr(audio, "_TMP_ROOT", tmp_path / "tmp")
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"fake")
    wav = audio.synth_piper("gday mate", str(model))
    assert wav and wav.startswith(b"RIFF")
    assert "gday mate" in log.read_text()      # text arrived on stdin
    assert audio.synth_piper("gday", "") is None           # no model configured
    assert audio.synth_piper("gday", str(tmp_path / "nope.onnx")) is None


def test_cache_identity_includes_backend_and_settings(tmp_path, monkeypatch):
    """Switching engine/model/voice must not serve the previous backend's cache
    (Codex P2 on the CPU-tier PR)."""
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path)
    monkeypatch.setenv("TTS_ENGINE", "qwen")
    qwen = audio._cache_path("jan", "same line", "default")
    monkeypatch.setenv("TTS_ENGINE", "os")
    os_default = audio._cache_path("jan", "same line", "default")
    monkeypatch.setenv("NHJ_OS_TTS_VOICE", "en-au")
    os_voiced = audio._cache_path("jan", "same line", "default")
    monkeypatch.setenv("TTS_ENGINE", "piper")
    monkeypatch.setenv("NHJ_PIPER_MODEL", "/v/a.onnx")
    piper_a = audio._cache_path("jan", "same line", "default")
    monkeypatch.setenv("NHJ_PIPER_MODEL", "/v/b.onnx")
    piper_b = audio._cache_path("jan", "same line", "default")
    assert len({qwen, os_default, os_voiced, piper_a, piper_b}) == 5


def test_fire_speaks_custom_text_via_os_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(audio.sys, "platform", "linux")
    log = tmp_path / "argv.log"
    _on_path(monkeypatch, _stub(tmp_path, "espeak-ng", _ESPEAK), log)
    monkeypatch.setenv("TTS_ENGINE", "os")
    monkeypatch.setattr(audio, "_TMP_ROOT", tmp_path / "tmp")
    monkeypatch.setattr(audio, "_CLIPS_ROOT", tmp_path / "no-cache")
    monkeypatch.setattr("nhj.state.get_flag", lambda *a, **k: False)
    emitted = []
    monkeypatch.setattr(audio, "_emit_voice", lambda path, **k: emitted.append(path))
    a = audio.AudioAdapter()
    assert a.fire("ok", "some custom text", Character.load("jan"), vibe_level=5) is True
    assert emitted and b"fake-espeak" in Path(emitted[0]).read_bytes()  # OS engine, not the bank
    assert "-w" in log.read_text()
