# Tests

Core regression suite for Not-Happy-Jan. Pure Python — no torch, GPU, network,
or audio hardware required to run or collect.

## Run

```bash
uv run --extra dev pytest          # the canonical command
uv run --extra dev pytest -q       # quiet
uv run --extra dev pytest tests/test_config.py   # one file
```

`pytest` is configured in `pyproject.toml` (`[tool.pytest.ini_options]`):
`testpaths = ["tests"]`, so a bare `pytest` only ever collects this directory.

## Conventions

- **All tests live in `tests/`.** Utility scripts elsewhere (e.g. under
  `scripts/`) are *not* tests and must never be named `test_*`.
  `test_collection_guard.py` fails the suite if anything outside `tests/` is
  collected (it caught the original recursive-fallback collection bug).
- **No heavy deps to collect or run.** Mock `subprocess`, `sounddevice`, the TTS
  server, and the network. Don't import torch/mlx in core tests.
- **Markers** (registered in `pyproject.toml`, enforced by `--strict-markers`):
  - `@pytest.mark.macos` — needs macOS / Apple Silicon (TTS, launchd, sounddevice)
  - `@pytest.mark.slow` — longer-running

## Coverage map (per audit issue)

| Area | File |
|---|---|
| Queue durability + worker lifecycle (#1) | `test_queue_manager.py` |
| Config loading + override precedence (#6) | `test_config.py` |
| Deterministic collection guard (#5) | `test_collection_guard.py` |
| MCP/hook option contract (#3) | _added with that PR_ |
| TTS speed + device recovery (#4) | _added with that PR_ |
| Wheel/package resource smoke (#2) | _added with that PR_ |
| Secret-scan pattern correctness | `test_secret_scan.py` |
