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
normal. Every file is also checked against the schema in ``nhj.config_schema``
(unknown keys, bad types/ranges/enums are errors naming the source file; partial
overrides validate as-is). ``nhj config check`` runs the same load on demand.
Config is frozen at import into :data:`CFG` — long-lived processes need a
restart to see edits; live state (``state.json``) is read per event instead.

For settings that also read an environment variable (e.g. ``NHJ_DEFAULT_CHARACTER``,
``NHJ_<NAME>_LEVEL``), the env var takes precedence over YAML at the point of use.
"""
from __future__ import annotations

import hashlib
import json
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


def validate_config(cfg: dict, source: str = "config") -> dict:
    """Check *cfg* against the schema; return it unchanged or raise ConfigError."""
    from nhj import config_schema
    errors = config_schema.validation_errors(cfg)
    if errors:
        raise ConfigError(f"{source}: invalid config — " + "; ".join(errors)
                          + ". Fix or remove the file.")
    return cfg


def _fingerprint(paths: list[Path]) -> str | None:
    """Digest of the exact config file bytes (small files; read anyway below)."""
    h = hashlib.sha256()
    for p in paths:
        try:
            data = p.read_bytes()
        except OSError:
            return None
        h.update(str(p).encode()); h.update(b"\0"); h.update(data); h.update(b"\0")
    return h.hexdigest()


def _stamp_matches(fingerprint: str) -> bool:
    try:
        return json.loads(resources.state_file("config-validated.json").read_text()).get(
            "fingerprint") == fingerprint
    except Exception:
        return False


def _write_stamp(fingerprint: str) -> None:
    try:
        p = resources.state_file("config-validated.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{os.urandom(4).hex()}.json.tmp")
        tmp.write_text(json.dumps({"fingerprint": fingerprint}))
        tmp.replace(p)
    except OSError:
        pass                                   # stamp is an optimisation, never a failure


def load_config(force_validate: bool = False) -> dict:
    """Shipped defaults deep-merged with any user overrides (see module docstring).

    Schema validation (pydantic) costs ~140 ms of import time, which short-lived
    hook processes would pay on every turn. So a fingerprint of the exact file
    bytes is stamped after a successful validation; an unchanged set of files
    skips validation (and the pydantic import) entirely. Any byte change, a
    missing stamp, or ``force_validate`` (``nhj config check``) revalidates."""
    default = resources.config_file()
    files = ([default] if default.exists() else []) + [p for p in _override_paths() if p.exists()]
    fingerprint = _fingerprint(files)
    cached = bool(fingerprint) and not force_validate and _stamp_matches(fingerprint)
    cfg: dict = {}
    for p in files:
        raw = _read_mapping(p)
        cfg = _deep_merge(cfg, raw if cached else validate_config(raw, str(p)))
    if not cached:
        validate_config(cfg, "merged config")
        if fingerprint:
            _write_stamp(fingerprint)
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
