"""Per-project opt-in for the *voice* channel (audio adapter + hold music).

Many agent sessions share one speaker. Silent channels (Vestaboard, AWTRIX, haptics) can
fire for every session, but several sessions all talking and playing hold music at once is
a cacophony. Voice is therefore gated per project, with AgentVibes-style precedence:

    1. a rule for this project (on | off)      -- wins either way, so a project can opt IN
                                                  while voice is globally off
    2. the global rule (on | off)
    3. config ``voice.default`` (on | off; "on" if unset)

``nhj mute`` is separate and stronger: it drops every channel (checked by the worker).
A rule is keyed by the event ``source``: "project" or "host:project" for a remote node.
A rule for "host:project" beats one for just "project".
"""
from __future__ import annotations

from nhj import config, state

VALUES = ("on", "off")


def _variants(source: str) -> list[str]:
    out: list[str] = []
    for k in (source.strip(), source.strip().split(":", 1)[-1]):
        if k and k not in out:
            out.append(k)
    return out


def _default_on() -> bool:
    """config ``voice.default``. YAML 1.1 turns a bare ``on``/``off`` into True/False, so
    accept booleans as well as the strings."""
    v = config.section("voice").get("default", True)
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("off", "false", "no", "0")


def voice_allowed(source: str = "") -> bool:
    gate = state.load().get("voice_gate", {}) or {}
    projects = gate.get("projects", {}) or {}
    for key in _variants(source):
        if projects.get(key) in VALUES:
            return projects[key] == "on"
    if gate.get("global") in VALUES:
        return gate["global"] == "on"
    return _default_on()


def set_rule(value: str | None, project: str | None = None) -> None:
    """Set (``on``/``off``) or clear (``None``) the rule for *project*, or the global rule."""
    if value is not None and value not in VALUES:
        raise ValueError(f"value must be one of {VALUES} or None, got {value!r}")

    def _m(s: dict) -> None:
        gate = s.setdefault("voice_gate", {})
        if project:
            projects = gate.setdefault("projects", {})
            if value is None:
                projects.pop(project, None)
            else:
                projects[project] = value
        elif value is None:
            gate.pop("global", None)
        else:
            gate["global"] = value

    state.update(_m)


def describe() -> dict:
    gate = state.load().get("voice_gate", {}) or {}
    return {"default": "on" if _default_on() else "off",
            "global": gate.get("global"), "projects": dict(gate.get("projects", {}) or {})}
