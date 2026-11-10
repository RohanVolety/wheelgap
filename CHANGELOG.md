# Changelog

## Unreleased
- `--index-url URL` (or `index-url` in `[tool.wheelgap]`) looks packages up in a private index that serves the simple API's JSON form (PEP 691) instead of PyPI's JSON API. It replaces every PyPI lookup; lockfiles that record their files are still read offline unless `--online`. `--index-url` beats the config key, and `WHEELGAP_INDEX_URL` still only sets the base of PyPI's JSON API. Credentials in the URL (`https://user:token@host/simple`) are sent as a Basic auth header and never appear in output, errors or `--json`. An index that only serves HTML (PEP 503) gets a clear error naming its host. (#19)
- `--markdown` prints the report as Markdown, and the GitHub Action now writes it to the run's summary page (`summary: false` turns that off). The step still passes or fails on the normal run. (#7)
- New Linux targets for 32-bit ARM (`linux-armv7l`, e.g. Raspberry Pi OS), `ppc64le`, `s390x` and `riscv64`, each with a `musl-*` twin for Alpine, and their Docker names (`linux/arm/v7` and so on) in `-t` and `--dockerfile`. Plain `linux_armv7l` wheels, which PyPI allows for the Raspberry Pi, count on `linux-armv7l` as they do for pip, but not on `musl-armv7l`: they're glibc builds. `-t all` still means the eight common platforms; `-t everything` adds these. (#8)

## 0.5.0 (2026-11-09)

wheelgap now lives at [github.com/wheelgap/wheelgap](https://github.com/wheelgap/wheelgap). Old links redirect, but update `uses:` and pre-commit `repo:` lines when you next touch them.

- Skip non-index Mercurial, Subversion and Bazaar dependencies (`hg`, `svn`, `bzr`) in `Pipfile.lock`, `pdm.lock` and `poetry.lock` instead of looking them up on PyPI. (#58, thanks [@chiragjangiir](https://github.com/chiragjangiir))
- `--explain` shows which dependency pulls in each gap package for `uv.lock` inputs: the shortest path from your project or a dependency group, worked out per target (`via: myapp → uvicorn → uvloop`). `--json --explain` adds a matching `via` object keyed by target, like `rejected`. (#45, thanks [@YSWFelicity](https://github.com/YSWFelicity))
- `-q` / `--quiet` prints only the gap summary and affected package names, and leaves clean runs silent. Set `quiet = true` in `[tool.wheelgap]` to use it by default. (#48, thanks [@Daiyuanranfeng](https://github.com/Daiyuanranfeng))

### Fixed

These came from independent second reviews of recent PRs, and a full audit of the codebase. (#55, #60)

- **Version ranges are respected.** `numpy>=1.26,<2` (or `~=`, `==1.26.*`) used to check the latest release (numpy 2.x). wheelgap now checks the newest release that matches the range (not yet narrowed by each release's `requires-python`).
- **uv.lock:** dependencies behind `[tool.uv] conflicts` (encoded as `extra == '...'` markers) were dropped as never installed, and path dependencies were treated as roots, so their dependencies counted everywhere. Markers with parts wheelgap can't know (extras, groups, `platform_release`, `platform_version`) now hold if they could hold for some value of those parts, while the platform and Python parts still apply. Poetry 1.x graphs also handle dependency cycles and version ranges on edges.
- **poetry.lock:** Poetry 1.x locks (lock-version < 2.1) keep markers only on dependency edges, so Windows-only packages such as pywin32 were reported missing on Linux. They're now walked like uv.lock. Dict markers that leave out an always-installed group, and packages with no files recorded, are handled too.
- **Free-threaded 3.15t `abi3t` wheels** (PEP 803) need `packaging` 26.3, which is now the minimum. `--explain` says when an `abi3t` wheel needs a newer CPython.
- **Python 3.7** targets use CPython's `cp37m` ABI, so their wheels are recognised.
- **PyPI lookups:** odd index responses give "unknown" instead of crashing; retries back off and honour `Retry-After`, retry dropped connections and skip permanent errors; an empty `WHEELGAP_INDEX_URL` means PyPI, and a `/simple` URL is a clear error; a missing pinned version says so ("version 1.0 not on index").
- **requirements.txt:** `-rfile` without a space, `--hash` with a space, comments after a tab, and `name@url` without spaces are all read correctly.
- **Output:** UTF-8 on Windows CI (no more "?" for ✓), `.python-version` files with a BOM, `--list-targets` uses the limits from `[tool.wheelgap]`, legacy `manylinux2014` wheels get the right `--explain` reason, more sdist formats are recognised, and `--macos 15` means 15.0.
- `--pyodide-lock`: a package whose lookup failed (e.g. a mistyped pin) was reported as bundled and could pass. It's now `unknown`, which fails.
- `pyodide-lock` in `[tool.wheelgap]` made every run without a matching Pyodide target exit 2. It's now quietly unused on those runs, and a relative path is read from the folder that holds `pyproject.toml`.
- **Yanked files:** a release pinned with `==` whose files have all been yanked was reported as having no files ("can't install"). pip still installs it (PEP 592), so its files now count, judged per target: they count only when every file that target could use is yanked.
- `pdm.lock`: packages with extras were listed twice (PDM writes one entry per extras variant). They now count once.
- The summary line could leave "unknown" packages out of its counts. Each gap package is now counted once, under its worst status.
- `quiet = true` in `[tool.wheelgap]` made `--json --explain` fail with a usage error. Quiet now only affects text output, `--no-quiet` turns it off for one run, and quiet runs still say why a package is unknown.
- `--explain`'s `via:` could credit a dependency group for a package that is also a runtime dependency. The project's own dependencies are now preferred.
- `--dockerfile` with an Alpine image plus `-t linux/arm64` checked glibc wheels. Docker platform names now keep the image's libc (musl).
- `--dockerfile`: Alpine 3.12 and earlier are checked against musl 1.1, not 1.2.
- `requirements.txt`: a bare VCS URL containing `@` (`git+ssh://git@github.com/...`) aborted the whole run. It's now skipped like other URLs.
- `python -m wheelgap.scan` now refuses to write results when more than 5% of lookups fail (`--max-error-rate`), records `lookup_errors` and each target's glibc/musl/macOS limits, and no longer counts Python 2-only wheels as "pure".

### Security

- Every GitHub Action used by wheelgap's own workflows is pinned to a full commit SHA, with Dependabot keeping the pins current, and PyPI releases now publish only from a `v*` tag through a protected environment. (#64)

## 0.4.0 (2026-11-09)

- **Python API**: `wheelgap.make_targets()`, `check_package()`, `check_packages()` and `check_lockfile()`, returning `Result` objects (`.ok`, `.gaps`, `.statuses`, `.to_dict()`), so other tools don't need to go through the CLI.
- **`python -m wheelgap.scan`**: check the latest release of the top N PyPI packages against every platform and write one JSON file with per-platform totals, the data behind the new [coverage tracker](https://wheelgap.github.io/tracker/).
- `--list-targets` now shows platform aliases and compatibility limits (the Python versions for `pyodide`), with JSON output available via `--json`. (#41, thanks [@Daiyuanranfeng](https://github.com/Daiyuanranfeng))
- wheelgap's own code is now type-checked with mypy in CI. (#42, thanks [@O1sumitkumar](https://github.com/O1sumitkumar))

## 0.3.0 (2026-11-09)

Thanks to [@draarivpatel-ui](https://github.com/draarivpatel-ui), [@Daiyuanranfeng](https://github.com/Daiyuanranfeng), [@Swarneil1905](https://github.com/Swarneil1905) and [@jwalith4418-Mj](https://github.com/jwalith4418-Mj), whose first PRs are in this release.

- `--pyodide-lock PATH_OR_URL` (or `pyodide-lock` in `[tool.wheelgap]`): read a Pyodide release's `pyodide-lock.json`, so packages Pyodide ships itself (numpy, pydantic-core…) count as `✓ bundled` on `-t pyodide`. A different bundled version is noted, and `--json` records it. (#40)
- New `pyodide` target (aliases `wasm`, `emscripten`) for Python in the browser: Python 3.12–3.14, accepting both the legacy `pyodide_*` and the PEP 783 `pyemscripten_*` tags for each ABI. sdist-only packages are reported as missing there, since Pyodide can't build from source. Not included in `-t all`. (#38)
- Support `pdm.lock` inputs, reading the distribution filenames and package markers recorded in the lockfile so PDM projects can be checked offline. Entries without a files list are looked up on PyPI. (#36, thanks [@draarivpatel-ui](https://github.com/draarivpatel-ui))
- Without `-p`, the Python versions come from a `.python-version` file next to the lockfile (or `pyproject.toml`) before falling back to the running Python. `-p`, `--dockerfile` and `[tool.wheelgap]` still win. Entries wheelgap can't use, such as `system`, are skipped with a note. (#34, thanks [@draarivpatel-ui](https://github.com/draarivpatel-ui))
- `--dockerfile` can infer targets from Astral uv's Python-tagged Debian and Alpine images (`ghcr.io/astral-sh/uv:python3.13-bookworm-slim`), including uv version pins. (#32, thanks [@Daiyuanranfeng](https://github.com/Daiyuanranfeng))
- Support `Pipfile.lock` inputs, including default and development dependencies, environment markers, and skipped non-index packages. Packages listed in both sections are checked once. (#30, thanks [@Swarneil1905](https://github.com/Swarneil1905))
- Accept Docker platform names (`linux/amd64`, `linux/arm64`, `linux/arm64/v8`) with `-t`. (#28, thanks [@jwalith4418-Mj](https://github.com/jwalith4418-Mj))
- New `--fail-on missing` (or `fail-on = "missing"` in `[tool.wheelgap]`): accept source builds and only fail when a package can't be installed at all. (#22)
- pre-commit hook: add `id: wheelgap` from this repo to `.pre-commit-config.yaml`. (#23)
- CI now also runs on free-threaded Python 3.14t. (#21)

## 0.2.0 (2026-11-09)

Both features in this release come from [@nodearray](https://github.com/nodearray), wheelgap's first contributor.

- Read default `python`, `targets`, `glibc`, `musl` and `macos` from `[tool.wheelgap]` in `pyproject.toml`. Command-line flags still win. (#5)
- New `--dockerfile PATH`: reads the last `FROM` line of an official `python` image (`python:3.13-alpine`, `--platform=linux/arm64`, `-slim-trixie` …) and uses it for `-p`/`-t`, plus `--glibc` for bookworm/trixie/bullseye and `--musl` for Alpine. Flags still win; other images exit with code 2. (#6)

## 0.1.1 (2026-11-08)

- New `--explain` flag: for each gap, lists the wheels that were rejected and why (wrong libc, glibc/musl/macOS too new, wrong Python or ABI, abi3 on free-threaded), closest misses first. With `--json`, adds a `rejected` list per gap target.
- Regression tests for the Alpine ARM trap: a `manylinux_*_aarch64` wheel never counts for `musl-aarch64`. (Thanks to a suggestion on r/madeinpython.)
- The summary line suggests `--explain` when there are gaps.
- Fixed "1 packages" in the header line.

## 0.1.0 (2026-11-08)

First release.

- Reads `uv.lock`, `pylock.toml` (PEP 751), `poetry.lock`, `requirements.txt` (with `-r` includes), or package names on the command line.
- Targets: `linux-x86_64`, `linux-aarch64`, `musl-x86_64`, `musl-aarch64`, `macos-arm64`, `macos-x86_64`, `win-amd64`, `win-arm64`, for any CPython version including free-threaded builds (`3.14t`).
- Evaluates environment markers and walks the `uv.lock` dependency graph per target, so Windows-only or old-Python-only packages don't show up as false gaps.
- Works offline from lockfiles that record their files; `--online` re-checks against PyPI.
- `--json` output and CI-friendly exit codes (0 clean, 1 gaps, 2 error). Also usable as a GitHub Action.
