# AGENTS.md

Instructions for coding agents (Codex, Claude Code, Copilot, Cursor and others) and anyone else working on wheelgap. [CONTRIBUTING.md](CONTRIBUTING.md) has the same rules for people, in more words.

## What wheelgap does

For each package in a lockfile and each target platform, wheelgap decides whether pip or uv would install a prebuilt wheel, build from source, or fail. **A wrong answer is the worst bug.** Where wheelgap can't be sure, it must give an error (or `unknown`), never a guess.

## Setup and checks

You need [uv](https://docs.astral.sh/uv/). Run all four before you push:

```bash
uv run --group dev ruff format .
uv run --group dev ruff check .
uv run --group dev mypy
uv run --group dev pytest
```

- One test: `uv run --group dev pytest tests/test_dockerfile.py -k parse_from`.
- One Python: `uv run --python 3.14t --group dev pytest`.
- CI runs pytest on Linux (Python 3.11–3.14 and 3.14t) and on macOS and Windows (3.14), plus ruff and mypy on Linux.
- Try a change by hand, offline: `uv run wheelgap tests/fixtures/uvproj -p 3.13 -t all --explain`.

## Code map (`src/wheelgap/`)

| File | What it does |
| --- | --- |
| `cli.py` | Command line: arguments, `[tool.wheelgap]` config, text / `--json` / `--markdown` output, exit codes |
| `api.py` | Python API (`make_targets`, `check_package(s)`, `check_lockfile`) |
| `sources.py` | Reads inputs: `uv.lock`, `pylock.toml`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, requirements files |
| `targets.py` | Targets (Python × platform) and the wheel tags each accepts |
| `check.py` | The status per package and target (`wheel`, `source`, `missing`, `unknown`, `n/a`, `bundled`) and `--explain` reasons |
| `pypi.py` | The only HTTP client (`fetch_json`: retries, https-only redirects) and the PyPI lookups (`fill`, `fill_all`, `recheck_lock`) |
| `dockerfile.py` | `--dockerfile`: reads the final stage's image and platform the way Docker/BuildKit does |
| `pyodide.py` | `--pyodide-lock`: packages Pyodide ships itself |
| `scan.py` | `python -m wheelgap.scan`: the data behind the [coverage tracker](https://wheelgap.github.io/tracker/) |

## Tests

- **No network, ever.** Use the `fake_pypi` fixture in `tests/conftest.py`. To add a package, add it to `FAKE_INDEX`. The fixture returns the list of URLs fetched, so tests can assert what was (or wasn't) looked up. Lockfile fixtures live in `tests/fixtures/`; small one-off lockfiles go in `tmp_path`.
- `fake_pypi` patches `wheelgap.pypi.fetch_json`. New code that fetches should call `pypi.fetch_json` at call time (as `pyodide.py` does), not import the name, so the fixture catches it.
- **Every behaviour change gets a test that fails without the change.** Check this by reverting your source change and running the test.
- **Write test files that contain non-ASCII text with `encoding="utf-8"`** (or use `write_bytes`). Windows CI otherwise writes cp1252.
- Put new cases in the existing parametrized tables where one fits, e.g. `test_dockerfiles_docker_rejects` and `test_parse_from` in `tests/test_dockerfile.py`.

## House rules

- **Dependencies:** standard library and `packaging` only.
- **All network access goes through `pypi.fetch_json`.**
- **Wheel tags:** use `packaging.tags` where it has an API, and mirror what pip accepts. The manylinux/musllinux platform lists in `targets.py` follow pip's rules; keep them that way.
- **`--dockerfile` must match Docker exactly or raise `DockerfileError`.** When you port BuildKit behaviour, link the file and commit in a comment (see `_heredocs` in `dockerfile.py`).
- **Output:** `--json` keys are a public interface. Add keys; don't rename or remove them. Notes go to stderr, never into `--json` or `--markdown` stdout.
- **Style:** match the surrounding code; ruff's line length is 120. Don't reformat or refactor code your change doesn't need.
- **`CHANGELOG.md`:** if users would notice the change, add one bullet at the top of `## Unreleased`, written for users, ending with the **PR** number and your handle: `(#123, thanks [@you](https://github.com/you))`. Add it once the PR is open and you know the number. Docs-only changes get no entry, and released sections are never edited.
- **Agent integrations:** when a flag, JSON key, status or exit code changes, update `integrations/claude-code/skills/check-wheels/SKILL.md` and `integrations/cursor/wheelgap.mdc` too, and only describe what's already released on PyPI.
- **README:** update it when users would notice the change (a new flag, target, input format or config key). A new input format also needs the `files` pattern in `.pre-commit-hooks.yaml`.

## Picking work and opening a PR

- Issues labelled `good first issue` say which files to change and which tests to add. Do the **first step** an issue describes; the "Later" items are separate PRs.
- **Check the assignee and existing PRs before you start.** Whoever asks gets an issue assigned for 18 hours from the assignment, and keeps it by opening a PR that links the issue (a draft is fine) within that time. While it's held for someone else, pick another issue. If the 18 hours pass with no PR, the maintainers fix the issue themselves, unless another PR is already waiting, which they review instead. An issue nobody has asked for goes to the first PR opened for it.
- In the PR description, use `Closes #N` only if the PR finishes the issue; otherwise write `Part of #N`.
- The maintainer may push fixes to your branch before merging, so leave "Allow edits by maintainers" on.
- Say in the PR description which AI tool helped (e.g. "Made with Codex"). The person opening the PR must have read and run the change. One issue per PR.
- Changes to `.github/`, `action.yml`, `pyproject.toml`, `uv.lock` or `src/wheelgap/pypi.py` get extra scrutiny, so expect them to take longer.
