Follow [AGENTS.md](../AGENTS.md) in the repository root. The essentials:

- Tests never use the network: use the `fake_pypi` fixture in `tests/conftest.py` (add packages to `FAKE_INDEX`).
- Dependencies are the standard library and `packaging` only; all network access goes through `pypi.fetch_json`.
- `--dockerfile` must match Docker exactly or raise `DockerfileError`.
- Before pushing: `uv run --group dev ruff format .`, `ruff check .`, `mypy` and `pytest` (each via `uv run --group dev`).
- CHANGELOG: one bullet under `## Unreleased` ending `(#PR, thanks [@you](https://github.com/you))`; none for docs-only changes.
