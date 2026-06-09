"""`nhj install-markers` writes a managed marker block into CLAUDE.md (idempotent,
preserves the user's own content) so Claude emits the markers the Stop hook speaks."""
from __future__ import annotations

from nhj import cli
from nhj.cli import _MARKERS_START, _MARKERS_END


def _count(text: str, needle: str) -> int:
    return text.count(needle)


def test_install_markers_creates_block(tmp_path):
    md = tmp_path / "CLAUDE.md"
    cli.install_markers(claude_md=md)
    text = md.read_text()
    assert _MARKERS_START in text and _MARKERS_END in text
    assert "[Jan:ok|" in text and "[Karren:err|" in text     # the instruction landed


def test_install_markers_is_idempotent_and_preserves_user_content(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("# My rules\n\nDo the thing.\n")
    cli.install_markers(claude_md=md)
    cli.install_markers(claude_md=md)                         # twice
    text = md.read_text()
    assert _count(text, _MARKERS_START) == 1                 # exactly one block
    assert _count(text, _MARKERS_END) == 1
    assert "# My rules" in text and "Do the thing." in text  # user content intact
    assert md.with_suffix(".md.pre-nhj.bak").read_text() == "# My rules\n\nDo the thing.\n"


def test_remove_markers_strips_block_and_keeps_user_content(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text("# Mine\n")
    cli.install_markers(claude_md=md)
    cli.remove_markers(claude_md=md)
    text = md.read_text()
    assert _MARKERS_START not in text and _MARKERS_END not in text
    assert "# Mine" in text
