# Contributing

Not-Happy-Jan currently targets Apple Silicon Macs and is in alpha. Bug reports,
focused fixes, documentation improvements, and tests are welcome.

## Development setup

```bash
git clone https://github.com/guruswami-ai/not-happy-jan
cd not-happy-jan
uv run --extra dev pytest -q
```

Before opening a pull request, run:

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests   # lint policy in pyproject [tool.ruff.lint]
bash -n install.sh
uv build
```

Keep changes narrowly scoped and add regression tests for behavior changes.
Do not commit models, generated audio, datasets, credentials, `.env` files,
voice samples, or media without documented redistribution permission.

Use GitHub issues for bugs and feature proposals. Report vulnerabilities using
the private process in [SECURITY.md](SECURITY.md).
