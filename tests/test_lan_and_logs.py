"""Issue #50 S1/S2: optional LAN secret, TTS text cap, log rotation and redaction."""
from __future__ import annotations

import base64
import json
import os
import sys
from io import StringIO
from pathlib import Path

import pytest
from pydantic import ValidationError

from nhj import lan_auth, resources, tts_server


def test_shared_secret_matches_only_the_configured_value():
    assert lan_auth.matches("anything", "")
    assert lan_auth.matches("s3cret", "s3cret")
    assert not lan_auth.matches("s3cret", "other")
    assert not lan_auth.matches("", "s3cret")
    assert lan_auth.allows({"X-NHJ-Token": "s3cret"}, "s3cret")
    assert lan_auth.allows({"Authorization": "Bearer s3cret"}, "s3cret")
    assert not lan_auth.allows({"X-NHJ-Token": "nope"}, "s3cret")
    assert lan_auth.allows({}, "")


def test_append_log_rotates_and_drops_the_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_LOG_MAX_BYTES", 20)
    monkeypatch.setattr(resources, "_LOG_BACKUPS", 3)
    path = tmp_path / "hook.log"
    # 5-byte lines. The cap is crossed every fourth append, and the fourth
    # rotation drops the first file off the end of the three backups.
    for i in range(20):
        resources.append_log(path, f"{i:04d}")
    assert "0019" in path.read_text()
    assert "0000" not in path.read_text()
    assert (tmp_path / "hook.log.1").is_file()
    assert (tmp_path / "hook.log.3").is_file()
    assert not (tmp_path / "hook.log.4").exists()
    kept = "".join(p.read_text() for p in sorted(tmp_path.glob("hook.log*"))
                   if not p.name.endswith(".lock"))
    assert "0000" not in kept
    assert "0019" in kept


@pytest.mark.parametrize("log_name", ["tts-server.log", "tts.log", "llm.log"])
def test_rotate_attached_log_reopens_stderr(tmp_path, monkeypatch, log_name):
    monkeypatch.setattr(resources, "_LOG_MAX_BYTES", 50)
    monkeypatch.setattr(resources, "log_dir", lambda create=True: tmp_path)
    path = tmp_path / log_name
    path.write_bytes(b"x" * 80)
    saved_out, saved_err = os.dup(1), os.dup(2)
    fd = os.open(path, os.O_APPEND | os.O_WRONLY)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        server = "llm" if log_name.startswith("llm") else "tts"
        resources.rotate_attached_log(server)
        os.write(2, b"fresh\n")
    finally:
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
    assert (tmp_path / f"{log_name}.1").read_bytes().startswith(b"x")
    assert b"fresh" in path.read_bytes()
    assert not path.read_bytes().startswith(b"x" * 80)
    assert path.stat().st_mode & 0o777 == 0o600


def test_rotate_attached_log_ignores_other_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "_LOG_MAX_BYTES", 50)
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(resources, "log_dir", lambda create=True: logs)
    outside = tmp_path / "tts.log"
    inside = logs / "hook.log"
    for path in (outside, inside):
        path.write_bytes(b"x" * 80)
        saved_out, saved_err = os.dup(1), os.dup(2)
        fd = os.open(path, os.O_APPEND | os.O_WRONLY)
        try:
            os.dup2(fd, 1)
            os.dup2(fd, 2)
            os.close(fd)
            resources.rotate_attached_log("tts")
        finally:
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)
        assert path.read_bytes() == b"x" * 80
        assert not Path(f"{path}.1").exists()


def test_hook_log_records_length_not_text(monkeypatch):
    from nhj import hook

    phrase = "supercalifragilistic-secret-phrase"
    _run_hook(monkeypatch, hook, f"note {phrase} [vibes:ok|{phrase}]")
    log = Path(hook._LOG).read_text()
    assert phrase not in log
    assert "tail=" not in log
    assert f"chars={len(phrase)}" in log
    assert "intent=ok" in log


def test_hook_log_keeps_text_when_debug_is_set(monkeypatch):
    from nhj import hook

    phrase = "supercalifragilistic-secret-phrase"
    monkeypatch.setenv("NHJ_DEBUG", "1")
    _run_hook(monkeypatch, hook, f"note {phrase} [vibes:ok|{phrase}]")
    log = Path(hook._LOG).read_text()
    assert phrase in log
    assert "tail=" in log


def _run_hook(monkeypatch, hook, text: str) -> None:
    monkeypatch.setenv("NHJ_HOOK_WAIT", "0")
    monkeypatch.setenv("NHJ_HOOK_MIN", "0")
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
        "session_id": "s1",
        "transcript_path": "/tmp/transcript.jsonl",
    })))
    monkeypatch.setattr(hook, "_seen_get", lambda sid: "baseline")
    monkeypatch.setattr(hook, "_seen_set", lambda sid, sig: None)
    monkeypatch.setattr(hook, "_read_last_assistant_text", lambda path: text)
    monkeypatch.setattr(hook, "_known_whos", lambda: {"vibes"})
    monkeypatch.setattr(hook, "_fire_local", lambda ev: True)
    assert hook.main() == 0


def test_mcp_network_call_needs_the_secret(monkeypatch):
    from nhj import mcp_server
    from nhj import resources as res

    phrase = "spoken-over-the-lan"
    monkeypatch.setenv("NHJ_SHARED_SECRET", "s3cret")
    monkeypatch.setenv("NHJ_TRANSPORT", "streamable-http")
    monkeypatch.setattr(mcp_server.QueueManager, "start_worker_if_needed", lambda self: None)
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers", lambda **k: {})
    assert mcp_server.nhj_vibe(intent="ok", message=phrase).startswith("[nhj:error]")
    assert phrase not in _queue_text(res)

    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers",
        lambda **k: {"x-nhj-token": "s3cret"})
    assert mcp_server.nhj_vibe(intent="ok", message=phrase).startswith("[nhj:ok]")

    monkeypatch.setenv("NHJ_TRANSPORT", "stdio")
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers", lambda **k: {})
    assert mcp_server.nhj_vibe(intent="ok", message="local").startswith("[nhj:ok]")


def test_mcp_music_accent_needs_the_secret_on_the_network(monkeypatch):
    """nhj_music_accent shares the LAN secret with nhj_vibe. stdio stays local."""
    from nhj import inference_muzak, mcp_server

    monkeypatch.setenv("NHJ_SHARED_SECRET", "s3cret")
    monkeypatch.setenv("NHJ_TRANSPORT", "streamable-http")
    monkeypatch.setenv("NHJ_MUSIC_MODE", "generative")
    noted = []
    monkeypatch.setattr(inference_muzak, "note_accent", lambda intent: noted.append(intent))
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers", lambda **k: {})
    assert mcp_server.nhj_music_accent(intent="err").startswith("[nhj:error]")
    assert noted == []

    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers",
        lambda **k: {"x-nhj-token": "s3cret"})
    assert mcp_server.nhj_music_accent(intent="err").startswith("[nhj:err]")
    assert noted == ["err"]

    monkeypatch.setenv("NHJ_TRANSPORT", "stdio")
    monkeypatch.setattr(
        "fastmcp.server.dependencies.get_http_headers", lambda **k: {})
    assert mcp_server.nhj_music_accent(intent="ok").startswith("[nhj:ok]")
    assert noted == ["err", "ok"]


def _queue_text(res) -> str:
    root = res.state_dir(create=False)
    blobs = []
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                blobs.append(path.read_bytes().decode("utf-8", "replace"))
    return "\n".join(blobs)


def test_tts_rejects_a_missing_secret_and_an_overlong_text(monkeypatch):
    monkeypatch.setenv("NHJ_SHARED_SECRET", "s3cret")
    assert tts_server.request_allowed("/health", {})
    assert not tts_server.request_allowed("/tts/generate", {})
    assert tts_server.request_allowed(
        "/tts/generate", {"Authorization": "Bearer s3cret"})
    with pytest.raises(ValidationError):
        tts_server.GenReq(text="x" * 501)
    tts_server.GenReq(text="x" * 500)
    monkeypatch.delenv("NHJ_SHARED_SECRET")
    assert tts_server.request_allowed("/tts/generate", {})


def test_tts_http_enforces_the_secret_and_the_length_cap(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("NHJ_SHARED_SECRET", "s3cret")
    client = TestClient(tts_server.build_app())
    assert client.get("/health").status_code == 200
    denied = client.post("/tts/generate", json={"text": "hi"})
    assert denied.status_code == 401
    assert denied.json()["reason"] == "unauthorised"
    bearer = client.post(
        "/tts/generate", json={"text": "x" * 501},
        headers={"Authorization": "Bearer s3cret"})
    assert bearer.status_code == 422
    monkeypatch.delenv("NHJ_SHARED_SECRET")
    open_client = TestClient(tts_server.build_app())
    assert open_client.post("/tts/generate", json={"text": "x" * 501}).status_code == 422


def test_tts_loads_the_env_file(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("NHJ_TTS_PROVE=from-file\n")
    monkeypatch.setattr(resources, "env_file", lambda: env)
    monkeypatch.setattr(resources, "_ENV_LOADED", False)
    monkeypatch.delenv("NHJ_TTS_PROVE", raising=False)
    assert tts_server.resources.load_env() is True
    assert os.environ.get("NHJ_TTS_PROVE") == "from-file"


def test_synth_qwen_sends_the_shared_secret(monkeypatch):
    from nhj.adapters import audio

    seen = {}

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "ok", "audio_b64": base64.b64encode(b"RIFF").decode()}

    def post(url, json=None, headers=None, timeout=None):
        seen["headers"] = headers
        return Resp()

    monkeypatch.setattr(audio.requests, "post", post)
    monkeypatch.delenv("NHJ_SHARED_SECRET", raising=False)
    assert audio.synth_qwen("hi", "http://127.0.0.1:9")
    assert not seen["headers"]
    monkeypatch.setenv("NHJ_SHARED_SECRET", "s3cret")
    assert audio.synth_qwen("hi", "http://127.0.0.1:9")
    assert seen["headers"][lan_auth.HEADER] == "s3cret"
