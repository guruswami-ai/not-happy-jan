"""Shared config — shipped defaults plus an optional user ``config.yaml`` override.

Precedence (lowest → highest):

1. the bundled ``default.yaml`` (``nhj/_bundled/config`` in a wheel, ``<repo>/config``
   in a checkout)
2. ``config.yaml`` ALONGSIDE ``default.yaml`` — matches the in-file note; writable in a
   source checkout
3. the platform user config dir (macOS Application Support, XDG config elsewhere)
   plus a legacy read fallback from ``~/.config/nhj/config.yaml``
4. ``$NHJ_CONFIG_FILE`` — an explicit path

Merge semantics: nested **mappings merge key-by-key**; **lists and scalars replace**
wholesale. A config file that exists but is malformed (invalid YAML or a non-mapping top
level) raises :class:`ConfigError` — never a silent fallback. A *missing* override file is
normal.

For settings that also read an environment variable (e.g. ``NHJ_DEFAULT_CHARACTER``,
``NHJ_<NAME>_LEVEL``), the env var takes precedence over YAML at the point of use.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from nhj import resources


class ConfigError(Exception):
    """A config file exists but is malformed or not a top-level mapping."""


def _read_mapping(path: Path) -> dict:
    """Parse a YAML mapping. Empty file → {}. Malformed / non-mapping → ConfigError."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as e:
        raise ConfigError(f"{path}: invalid UTF-8 — {e}. Fix or remove the file.") from e
    except OSError as e:
        raise ConfigError(f"{path}: cannot read config — {e}. Fix permissions or remove the file.") from e
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML — {e}. Fix or remove the file.") from e
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: top level must be a mapping (key: value), got "
            f"{type(raw).__name__}. Fix or remove the file.")
    return raw


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge *override* onto *base*. Nested mappings merge key-by-key; lists and scalars
    replace wholesale."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _override_paths() -> list[Path]:
    """User override locations, lowest precedence first."""
    paths = [
        resources.config_file().parent / "config.yaml",   # alongside default.yaml (checkout)
        resources.config_dir(create=False) / "config.yaml",  # platform user config dir
    ]
    legacy = resources.legacy_config_dir() / "config.yaml"
    if legacy not in paths:
        paths.append(legacy)
    explicit = os.environ.get("NHJ_CONFIG_FILE")
    if explicit:
        paths.append(Path(explicit))                       # explicit path wins
    return paths


def load_config() -> dict:
    """Shipped defaults deep-merged with any user overrides (see module docstring)."""
    default = resources.config_file()
    cfg = _read_mapping(default) if default.exists() else {}
    for p in _override_paths():
        if p.exists():
            cfg = _deep_merge(cfg, _read_mapping(p))
    return cfg


_CFG = load_config()

#: Full parsed config, for adapters that need their own section.
CFG: dict = _CFG


def section(name: str) -> dict:
    """Return a top-level config section as a dict (empty if absent)."""
    return CFG.get(name, {}) or {}


_DEFAULT_INTENTS = {
    "ok":        {"haptic": 7,  "icon": "ok",        "sound": "positive4",   "label": "Done"},
    "err":       {"haptic": 4,  "icon": "err",       "sound": "negative1",   "label": "Error"},
    "warn":      {"haptic": 13, "icon": "warn",      "sound": "notification","label": "Warning"},
    "attn":      {"haptic": 2,  "icon": "attn",      "sound": "knock-knock", "label": "Attention"},
    "celebrate": {"haptic": 10, "icon": "celebrate", "sound": "win",         "label": "Celebration"},
    "step":      {"haptic": 6,  "icon": "progress",  "sound": None,          "label": "Progress"},
}

INTENTS: dict[str, dict] = _CFG.get("intents", _DEFAULT_INTENTS)

ADAPTER_ORDER: list[str] = _CFG.get("adapters", ["haptic", "lametric", "ulanzi", "esp32", "audio"])
