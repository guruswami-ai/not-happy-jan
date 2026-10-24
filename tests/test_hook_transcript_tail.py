"""Stop-hook transcript read is tail-bounded (#45).

The Stop hook polls this while waiting for the final assistant message to
flush, so the read cost must not grow with the whole transcript. The window
must still return the correct last assistant text when the final message is
in the tail, and must expand when a single transcript line is larger than
the window."""
from __future__ import annotations

import json

from nhj import hook


def _line(role: str, text: str) -> str:
    return json.dumps({"message": {"role": role, "content": [{"type": "text", "text": text}]}})


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n")
    return path


def test_tail_window_finds_last_assistant_past_large_history(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "_TAIL_BYTES", 4096)
    filler = _line("user", "x" * 200)
    lines = [_line("assistant", "old answer")] + [filler] * 200 + [_line("assistant", "final answer [vibes:ok]")]
    t = _write(tmp_path / "t.jsonl", lines)
    assert t.stat().st_size > 4096
    assert hook._read_last_assistant_text(t) == "final answer [vibes:ok]"


def test_single_line_larger_than_window_still_read(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "_TAIL_BYTES", 4096)
    big = "final " + "y" * 20000
    lines = [_line("user", "hi"), _line("assistant", big)]
    t = _write(tmp_path / "t.jsonl", lines)
    assert hook._read_last_assistant_text(t) == big


def test_no_assistant_in_window_falls_back_to_earlier(tmp_path, monkeypatch):
    """Only assistant message is far before the tail window: expansion must find it."""
    monkeypatch.setattr(hook, "_TAIL_BYTES", 4096)
    filler = _line("user", "x" * 200)
    lines = [_line("assistant", "only answer")] + [filler] * 200
    t = _write(tmp_path / "t.jsonl", lines)
    assert hook._read_last_assistant_text(t) == "only answer"


def test_missing_empty_and_malformed(tmp_path):
    assert hook._read_last_assistant_text(tmp_path / "nope.jsonl") == ""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert hook._read_last_assistant_text(empty) == ""
    bad = _write(tmp_path / "bad.jsonl", ["not json", _line("user", "hi"), "{broken"])
    assert hook._read_last_assistant_text(bad) == ""


def test_string_content_and_last_wins(tmp_path):
    plain = json.dumps({"message": {"role": "assistant", "content": "plain string"}})
    lines = [_line("assistant", "first"), _line("user", "q"), plain]
    t = _write(tmp_path / "t.jsonl", lines)
    assert hook._read_last_assistant_text(t) == "plain string"
