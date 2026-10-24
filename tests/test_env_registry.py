"""Env-var registry (#47): every environment variable the code reads must be
registered (name/type/default/YAML target/read timing), and the generated
docs table must be in sync with the registry."""
from __future__ import annotations

import ast
from pathlib import Path

from nhj import env_registry

_SRC = Path(env_registry.__file__).parent
_TYPES = {"str", "int", "float", "bool", "path", "url", "enum", "list"}
_TIMING = {"live", "startup", "import"}


def _literal_env_reads() -> set[str]:
    found: set[str] = set()
    for p in _SRC.rglob("*.py"):
        if p.name == "env_registry.py":
            continue
        for node in ast.walk(ast.parse(p.read_text())):
            name = None
            if isinstance(node, ast.Call):
                f = node.func
                getenv = isinstance(f, ast.Attribute) and f.attr == "getenv" \
                    and isinstance(f.value, ast.Name) and f.value.id == "os"
                environ_get = isinstance(f, ast.Attribute) and f.attr == "get" \
                    and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"
                if (getenv or environ_get) and node.args and isinstance(node.args[0], ast.Constant):
                    name = node.args[0].value
                elif isinstance(f, ast.Name) and f.id in ("_f", "_on") \
                        and node.args and isinstance(node.args[0], ast.Constant):
                    name = node.args[0].value   # duck_player getenv wrappers
            elif isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                    and node.value.attr == "environ" and isinstance(node.slice, ast.Constant) \
                    and isinstance(node.ctx, ast.Load):
                name = node.slice.value
            if isinstance(name, str):
                found.add(name)
    return found


def test_every_literal_env_read_is_registered():
    registered = {n for n in env_registry.REGISTRY if "<" not in n}
    assert _literal_env_reads() == registered, (
        "unregistered: " + ", ".join(sorted(_literal_env_reads() - registered))
        + " | stale: " + ", ".join(sorted(registered - _literal_env_reads())))


def test_dynamic_patterns_are_documented():
    assert "NHJ_<NAME>_<DIAL>" in env_registry.REGISTRY     # characters.py per-character dials


def test_registry_entries_are_complete():
    for name, e in env_registry.REGISTRY.items():
        assert e.name == name and e.description, name
        assert e.type in _TYPES, name
        assert e.timing in _TIMING, name


def test_docs_table_is_generated_from_registry():
    docs = (_SRC.parents[1] / "docs" / "configuration.md").read_text()
    start = "<!-- env-registry:start -->"
    end = "<!-- env-registry:end -->"
    assert start in docs and end in docs
    block = docs.split(start)[1].split(end)[0].strip("\n")
    assert block == env_registry.render_docs_table(), \
        "docs env table is stale — regenerate from nhj.env_registry.render_docs_table()"


def test_lookup():
    e = env_registry.lookup("NHJ_HOOK_WAIT")
    assert e is not None and e.type == "float" and e.default == "3.0"
    assert env_registry.lookup("NHJ_NOPE_XYZ") is None
