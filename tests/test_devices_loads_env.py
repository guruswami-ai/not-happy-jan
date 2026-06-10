"""Regression: `nhj devices` must load the .env before resolving adapters.

Display/haptic adapters read their device targets from env vars (ULANZI_*, LAMETRIC_*, …).
The command used to call load_adapters() without loading .env, so env-configured adapters
reported unavailable and the table showed audio alone — making a working install look broken."""
from __future__ import annotations

from typer.testing import CliRunner

from nhj import resources
from nhj.cli import cli


def test_devices_loads_dotenv(monkeypatch):
    called = {}

    def fake_load_dotenv(path=None, *args, **kwargs):
        called["path"] = path
        return False
    monkeypatch.setattr("dotenv.load_dotenv", fake_load_dotenv)

    result = CliRunner().invoke(cli, ["devices"])
    assert result.exit_code == 0, result.output
    assert called.get("path") == resources.env_file(), (
        f"devices did not load the env file (got {called.get('path')!r})")
