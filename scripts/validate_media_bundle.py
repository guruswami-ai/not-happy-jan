#!/usr/bin/env python3
"""Validate media-bundle contents against the approved manifest (issue #52).

Two guarantees, enforced before anything is packaged into a public bundle:

1. Every file is listed in ``scripts/media-manifest.txt`` (an allowlist of
   fnmatch globs). Anything not matched is rejected — a bundle cannot smuggle
   in an asset that has no documented provenance.
2. macOS metadata (``.DS_Store`` and AppleDouble ``._*``) is rejected outright,
   regardless of the manifest.

Usable as a library (``validate(...)``) and as a CLI:

    python scripts/validate_media_bundle.py FILE [FILE ...]

exits non-zero and prints the offending entries when validation fails.
"""
from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

_MANIFEST = Path(__file__).resolve().parent / "media-manifest.txt"


def load_manifest(path: Path = _MANIFEST) -> list[str]:
    """Return the approved glob patterns from a manifest file."""
    patterns: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def is_macos_metadata(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return base == ".DS_Store" or base.startswith("._")


def validate(paths: list[str], patterns: list[str]) -> list[str]:
    """Return a list of human-readable rejection reasons (empty == valid)."""
    errors: list[str] = []
    for raw in paths:
        name = raw.lstrip("./")
        if is_macos_metadata(name):
            errors.append(f"macOS metadata not allowed in bundle: {raw}")
            continue
        if not any(fnmatch.fnmatch(name, pat) for pat in patterns):
            errors.append(f"file not in media manifest (no documented provenance): {raw}")
    return errors


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: validate_media_bundle.py FILE [FILE ...]", file=sys.stderr)
        return 2
    errors = validate(argv, load_manifest())
    if errors:
        print("media bundle validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"media bundle OK — {len(argv)} approved files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
