"""Media bundle provenance validation (issue #52).

The bundle builder must include only manifest-approved assets and must reject
macOS metadata and unapproved file types. We exercise the validator directly so
the guarantee is tested without the (large, gitignored) media tree present.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR = ROOT / "scripts" / "validate_media_bundle.py"


def _load():
    spec = importlib.util.spec_from_file_location("validate_media_bundle", _VALIDATOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_manifest_and_validator_ship():
    assert _VALIDATOR.is_file()
    assert (ROOT / "scripts" / "media-manifest.txt").is_file()
    assert (ROOT / "scripts" / "media-license.txt").is_file()
    assert (ROOT / "docs" / "media-provenance.md").is_file()


def test_approved_assets_pass():
    mod = _load()
    patterns = mod.load_manifest()
    approved = [
        "voices/jan/ref.wav",
        "voices/jan/ref.txt",
        "voices/karren/ref.wav",
        "audio/music/muzak-9a4e4aea.m4a",
        "audio/sfx/handset/pickup.wav",   # nested dir
        "audio/voice/intros/jan-01.wav",
        "MEDIA-LICENSE.txt",
    ]
    assert mod.validate(approved, patterns) == []


def test_validation_fails_when_asset_absent_from_manifest():
    """An included asset with no documented provenance must be rejected."""
    mod = _load()
    patterns = mod.load_manifest()
    errors = mod.validate(["voices/unknown-celebrity/ref.wav"], patterns)
    assert errors
    assert "manifest" in errors[0].lower()


def test_validation_rejects_macos_metadata():
    mod = _load()
    patterns = mod.load_manifest()
    errors = mod.validate(
        ["voices/jan/._ref.wav", "audio/.DS_Store", "audio/music/._track.m4a"],
        patterns,
    )
    assert len(errors) == 3
    assert all("metadata" in e.lower() for e in errors)


def test_validation_rejects_unapproved_file_types():
    """A non-media file type inside an approved tree must be rejected."""
    mod = _load()
    patterns = mod.load_manifest()
    errors = mod.validate(
        ["audio/music/secret.key", "audio/notes.txt", "voices/jan/ref.exe"],
        patterns,
    )
    assert len(errors) == 3
    assert all("manifest" in e.lower() for e in errors)
