# wheelgap

[![CI](https://github.com/wheelgap/wheelgap/actions/workflows/ci.yml/badge.svg)](https://github.com/wheelgap/wheelgap/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/wheelgap)](https://pypi.org/project/wheelgap/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![good first issues](https://img.shields.io/github/issues/wheelgap/wheelgap/good%20first%20issue?label=good%20first%20issues&color=7057ff)](https://github.com/wheelgap/wheelgap/labels/good%20first%20issue)

**Find the Python dependencies that have no prebuilt wheel for your target platform, before your Docker build does.**

Point it at your lockfile and the platforms you ship to (ARM servers, Alpine images, Windows on ARM, free-threaded Python 3.14t). It tells you which packages pip or uv will have to compile from source, and which can't be installed at all.

📊 **New: [wheel coverage for the top 5,000 PyPI packages](https://wheelgap.github.io/tracker/)**, by platform and Python version, updated nightly.

![wheelgap demo](docs/demo.gif)

## Install and run (under 30 seconds)

With [uv](https://docs.astral.sh/uv/):

```bash
uvx wheelgap
```

Or with pipx:

```bash
pipx run wheelgap
```

Or install it: `pip install wheelgap`.

Run either one in a project directory. It finds `uv.lock`, `pylock.toml`, `poetry.lock`, `pdm.lock`, `Pipfile.lock` or `requirements.txt` on its own.

## Usage

```bash
# Will my Alpine ARM image compile anything?
wheelgap -p 3.13 -t alpine-arm64

# Is my stack ready for free-threaded Python 3.14?
wheelgap -p 3.14t -t all

# Several Pythons at once, Linux on both architectures
wheelgap uv.lock -p 3.12,3.13,3.14 -t linux-x86_64,linux-aarch64

# No -p? A .python-version file next to uv.lock (written by uv or pyenv) sets the Python
wheelgap uv.lock -t linux-x86_64

# Check a Pipenv project's default and development dependencies
wheelgap Pipfile.lock -p 3.13 -t linux-x86_64,linux-aarch64

# Check a PDM project's locked distribution files
wheelgap pdm.lock -p 3.13 -t linux-x86_64,linux-aarch64

# No lockfile? Check packages directly (latest version, or pin one)
wheelgap psycopg2 uvloop numpy==2.3.4 -t windows,win-arm64

# Take the target from the last FROM line (e.g. python:3.13-alpine
# or ghcr.io/astral-sh/uv:0.13.7-python3.13-trixie-slim)
wheelgap --dockerfile Dockerfile

# Machine-readable, for scripts and CI
wheelgap --json > wheelgap.json

# Why is it a gap? List each rejected wheel and the reason
wheelgap -t alpine-arm64 --explain
```

Projects usually ship to the same platforms, so you can write the defaults down once in `pyproject.toml` (next to the lockfile, or in the current directory). Command-line flags still win.

```toml
[tool.wheelgap]
python = ["3.13", "3.14t"]
targets = ["linux-x86_64", "linux-aarch64", "musl-aarch64"]
glibc = "2.31"
```

Each cell in the table is one of:

| cell | meaning |
| --- | --- |
| `✓` | a compatible wheel exists, so the install is fast |
| `● source` | no wheel, only an sdist, so it compiles at install time (needs a compiler, headers, time) |
| `✗ missing` | no wheel and no sdist, so the install fails |
| `–` | not installed on this target (environment markers exclude it) |
| `✓ bundled` | no wheel on PyPI, but Pyodide ships its own build (with `--pyodide-lock`) |

Packages that are fine everywhere are hidden; `--all` shows them.

`--explain` lists, for each gap, the wheels that were rejected and why, closest misses first:

```
armlib 1.4.0 on musl-aarch64: ● source
  armlib-1.4.0-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl  manylinux_2_17_aarch64 is a glibc wheel; musl (Alpine) needs musllinux
```

For `uv.lock` inputs, `--explain` also shows why the package is installed at all: the shortest dependency path from your project (or from a dependency group, shown as `(dev group)`) to the gap package. Paths are worked out per target, since markers can pull a package in on one platform only. With `--json --explain`, each package gets a matching `via` object keyed by target, like `rejected`: `"via": {"win-arm64": ["myapp", "uvicorn", "uvloop"]}`. Other inputs have no dependency graph, so they show no path (and `"via": {}`).

```
uvloop 0.21.0 on win-arm64: ● source
  via: myapp → uvicorn → uvloop
```

If you think wheelgap got a package wrong, that output (or `--json --explain`) is all a bug report needs, with no need to share your lockfile.

**Exit codes:** `0` no gaps, `1` gaps found, `2` bad input. `--exit-zero` always exits `0`. `--fail-on missing` (or `fail-on = "missing"` in `[tool.wheelgap]`) accepts source builds and only exits `1` when a package can't be installed at all.

Use `-q` / `--quiet` (or `quiet = true` in `[tool.wheelgap]`) to show only the gap summary and package names. Clean runs are silent, JSON and Markdown output are unchanged, and quiet text output cannot be combined with `--explain` (`--json --explain` and `--markdown --explain` work).

### Targets

Run `wheelgap --list-targets` to see target aliases and compatibility limits, or add `--json` for a machine-readable list.

| name | aliases | wheel tags it accepts |
| --- | --- | --- |
| `linux-x86_64` | `linux`, `linux-amd64`, `linux/amd64` | `manylinux_2_*_x86_64` up to `--glibc` (default 2.36), legacy `manylinux2014`/`2010`/`1` |
| `linux-aarch64` | `linux-arm64`, `linux/arm64`, `linux/arm64/v8` | `manylinux_2_*_aarch64` |
| `musl-x86_64` | `alpine`, `musl` | `musllinux_1_*_x86_64` up to `--musl` (default 1.2) |
| `musl-aarch64` | `alpine-arm64` | `musllinux_1_*_aarch64` |
| `macos-arm64` | `macos`, `mac` | `macosx_*_arm64` and `universal2` up to `--macos` (default 14.0) |
| `macos-x86_64` | `macos-intel` | `macosx_*_x86_64` and `universal2` |
| `win-amd64` | `windows`, `win` | `win_amd64` |
| `win-arm64` | `windows-arm64` | `win_arm64` |
| `linux-armv7l` | `linux-arm`, `linux-armv7`, `linux/arm/v7` | `manylinux_2_*_armv7l` (32-bit ARM, e.g. Raspberry Pi OS), legacy `manylinux2014`, and plain `linux_armv7l` (which PyPI allows for the Pi) |
| `linux-ppc64le`, `linux-s390x`, `linux-riscv64` | `linux/ppc64le`, `linux/s390x`, `linux/riscv64` | `manylinux_2_*` for that arch (`riscv64` has no legacy `manylinux2014` tag) |
| `musl-armv7l`, `musl-ppc64le`, `musl-s390x`, `musl-riscv64` | `alpine-armv7` | `musllinux_1_*` for that arch (pip would also take a plain `linux_armv7l` wheel on Alpine, but it's a glibc build, so wheelgap doesn't count it) |
| `pyodide` | `wasm`, `wasm32`, `emscripten` | Pyodide's ABI for that Python: `pyodide_2024_0` (3.12), `pyodide_2025_0` / `pyemscripten_2025_0` (3.13), `pyemscripten_2026_0` (3.14) |

**Pyodide (Python in the browser)** only installs wheels: there's no compiler, so a package with only an sdist shows as `✗ missing`, not `● source`. Pyodide wheels are tied to one ABI per Python version and don't work across them. Pyodide also ships its own builds of many popular packages (numpy, pydantic-core and others) that aren't on PyPI. Point `--pyodide-lock` at the release's `pyodide-lock.json` (a path or URL, also `pyodide-lock = "..."` in `[tool.wheelgap]`) and those count as `✓ bundled`, with a note when Pyodide's version differs from the one you pinned:

```bash
wheelgap -p 3.12 -t pyodide --pyodide-lock https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide-lock.json
```

Docker platform names also work with `-t`, for example `-t linux/amd64,linux/arm64` (also `linux/arm64/v8`, `linux/arm/v7`, `linux/ppc64le`, `linux/s390x` and `linux/riscv64`).

`-t all` checks the eight common native platforms, and `-t everything` adds the armv7l, ppc64le, s390x and riscv64 Linux targets (glibc and musl). Pyodide is only checked when you name it. Python versions take a trailing `t` for free-threaded builds (`3.14t`), which correctly rejects `abi3` wheels. Without `-p`, wheelgap reads a `.python-version` file next to the lockfile (or `pyproject.toml`) if there is one, and otherwise uses the Python it is running on.

### In CI (GitHub Action)

```yaml
- uses: actions/checkout@v7
- uses: wheelgap/wheelgap@v0.5.0
  with:
    args: uv.lock -p 3.13 -t linux-x86_64,linux-aarch64
```

The job fails when a dependency would build from source on a target you ship, for example when a dependency bump drops ARM wheels. The gap table also goes to the run's summary page (set `summary: false` to turn that off).

Outside the Action, `--markdown` prints the same report as Markdown, for a job summary (`wheelgap --markdown >> "$GITHUB_STEP_SUMMARY"`) or a PR comment. With `--explain`, the rejected wheels go in a collapsed section.

### With pre-commit

```yaml
repos:
  - repo: https://github.com/wheelgap/wheelgap
    rev: v0.5.0
    hooks:
      - id: wheelgap
```

The hook runs when `uv.lock`, `poetry.lock`, `pdm.lock`, `pylock.toml`, `Pipfile.lock` or a `requirements*.txt` file changes. Put your targets in `[tool.wheelgap]` in `pyproject.toml` so the hook checks the right platforms. Pass extra options with `args:`, for example `args: [--fail-on, missing]`.

### From Python

```python
import wheelgap

targets = wheelgap.make_targets(python="3.13", platforms=["linux-aarch64", "musl-aarch64"])

result = wheelgap.check_package("numpy", "2.3.4", targets)  # looks the files up on PyPI
result.ok  # True if every target has a wheel
result.gaps  # the targets that don't
result.to_dict()  # {"name": ..., "results": {"3.13 musl-aarch64": "wheel", ...}}

for r in wheelgap.check_lockfile("uv.lock", targets):  # markers and the uv graph respected
    print(r.name, r.version, [t.platform for t in r.gaps])
```

`check_packages([...])` checks many packages in parallel, and `check_package(..., files=[...])` works offline from filenames you already have.

### Scanning the top PyPI packages

```bash
python -m wheelgap.scan --top 5000 --out coverage.json
```

This checks the latest release of the most-downloaded packages (from [top-pypi-packages](https://hugovk.dev/top-pypi-packages/)) against the eight common platforms (and Pyodide), for Python 3.13, 3.14 and 3.14t by default, and writes one JSON file with per-platform totals. It's the data behind the [coverage tracker on the roadmap](https://github.com/wheelgap/wheelgap/discussions/43).

## How it works

- **Lockfiles are read offline.** `uv.lock`, `pylock.toml`, `poetry.lock` and `pdm.lock` already record every file published for each pinned version. wheelgap parses the wheel filenames' tags with [`packaging`](https://packaging.pypa.io/) and checks them against the exact tag set each target accepts. That is the same logic pip and uv use to pick a wheel.
- **`requirements.txt` and bare package names** are looked up on the PyPI JSON API. Feed it a fully pinned file (from `uv pip compile`, `pip-compile` or `uv export`): wheelgap checks the lines you give it and doesn't resolve dependencies.
- **`Pipfile.lock`** reads both `default` and `develop` dependencies and looks up distribution filenames on PyPI for their pinned versions. Environment markers are preserved; Git, path, file and editable dependencies are skipped as non-index packages.
- **Environment markers are respected.** A `pywin32 ; sys_platform == 'win32'` dependency won't show up as a Linux gap. For `uv.lock` the dependency graph is walked per target, so transitive platform-specific packages are handled too.
- `--online` re-checks lockfile packages against PyPI. Use it when a project has uploaded new wheels for an existing version since you locked, or if your `uv.lock` was generated with `environments` restrictions.
- **Private indexes:** `--index-url URL` (or `index-url` in `[tool.wheelgap]`) sends the PyPI lookups described above to a simple index that serves the JSON form (PEP 691) instead. Credentials can go in the URL (`https://user:token@host/simple`) and never appear in output; in CI, `--index-url "$PRIVATE_INDEX"` keeps them out of your repo. An index that only serves HTML (PEP 503) gets a clear error.

## Why this exists

Missing wheels show up late, in the most expensive place:

- Your `python:3.13-alpine` image on an ARM runner takes 25 minutes because two transitive dependencies don't publish `musllinux_aarch64` wheels, and QEMU is compiling them.
- A build fails with `error: Microsoft Visual C++ 14.0 or greater is required` on a teammate's Snapdragon laptop.
- You try free-threaded Python 3.14t and half your stack quietly falls back to source builds, because `abi3` wheels don't apply there.

Sites like [pyreadiness.org](https://pyreadiness.org) and [free-threaded wheels](https://hugovk.github.io/free-threaded-wheels/) track the top few hundred packages. wheelgap checks your packages, at your pinned versions, for your targets, in about a second, and it can run in CI.

## Roadmap

wheelgap is growing into **caniuse for Python wheels**: an open map of which packages have wheels for which platforms. The CLI stays focused, and around it come a nightly tracker of the most-used PyPI packages, a community knowledge base of workarounds, and campaigns to help upstream projects ship missing wheels. **[Read the roadmap and join the discussion →](https://github.com/wheelgap/wheelgap/discussions/43)**

Next up for the CLI: iOS and Android targets (#11), private indexes (#19), and "nearest version with a wheel" hints (#20).

Contributions welcome. Issues labelled [good first issue](https://github.com/wheelgap/wheelgap/labels/good%20first%20issue) are a good start, and [CONTRIBUTING.md](CONTRIBUTING.md) has the 3-command dev setup. Questions are welcome in [Discussions](https://github.com/wheelgap/wheelgap/discussions).

## Maintainer

wheelgap is created and maintained by [@patrickballard](https://github.com/patrickballard), with help from [its contributors](https://github.com/wheelgap/wheelgap/graphs/contributors).

## License

[MIT](LICENSE)
