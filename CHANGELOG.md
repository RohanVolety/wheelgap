# Changelog

## Unreleased

- `--index-url URL` (or `index-url` in `[tool.wheelgap]`) looks packages up in a private index that serves the simple API's JSON form (PEP 691) instead of PyPI's JSON API. It replaces every PyPI lookup; lockfiles that record their files are still read offline unless `--online`. `--index-url` beats the config key, and `WHEELGAP_INDEX_URL` still only sets the base of PyPI's JSON API. The URL must be https (plain http only for localhost). Credentials in it (`https://user:token@host/simple`) are sent as a Basic auth header and never appear in output, errors or `--json`; they're refused in `pyproject.toml`, which is committed. An index that only serves HTML (PEP 503) gets a clear error naming its host. (#159, thanks [@RohanVolety](https://github.com/RohanVolety))
- `--ignore` (and `ignore = [...]` in `[tool.wheelgap]`) now prints a note on stderr when a name matches no package in any input, so a typo or a stale entry no longer hides silently. `-q` hides it; the exit code is unchanged. (#158, thanks [@bouhafara](https://github.com/bouhafara))
- Show which targets have gaps in the text summary when checking multiple targets. (#157, thanks [@jwalith4418-Mj](https://github.com/jwalith4418-Mj))
- Version ranges now select the newest matching release whose non-yanked files support the target's Python through `Requires-Python`, when all targets share one Python major/minor version. A target Python such as `3.8` counts as a current 3.8.x, so `>=3.8.1` admits it. Missing or invalid metadata remains unrestricted; runs checking different Python versions keep the existing selection behavior. (#156, thanks [@Swarneil1905](https://github.com/Swarneil1905))
- Packages from a private index that the lock has no file list for (an old-format `poetry.lock`, which keeps hashes under `[metadata.hashes]`) are listed as skipped instead of being looked up on public PyPI by name, so internal package names aren't sent to pypi.org. (#153, thanks [@bfs2021](https://github.com/bfs2021))
- Projects with `[tool.uv] required-environments` are now checked for the native targets those markers select, instead of the built-in defaults, when no targets are set by `-t`, `--dockerfile` or `[tool.wheelgap]`. Inferred targets are explained on stderr; Linux markers include both glibc and musl. Add `[tool.wheelgap] targets` or `-t` to check other targets too; markers that match no native target keep the defaults, with a note. (#150, thanks [@Swarneil1905](https://github.com/Swarneil1905))
- `--dockerfile` says when an official python image isn't published for the `--platform` asked for, instead of checking a target Docker can't pull: `FROM --platform=linux/riscv64 python:3.13-bookworm` is now an error (`... isn't published for linux/riscv64 (3.13-trixie is)`), as are s390x on current `bookworm` tags and ppc64le or s390x on current `bullseye` tags. Patch tags and the end-of-life tags that were still built for those arches are accepted. (#143, thanks [@sarankumar1325](https://github.com/sarankumar1325))
- `--offline` never contacts an index, for CI and coding agents without network: an input that needs a lookup (a requirements file, `Pipfile.lock`, package names, lockfile entries with no file list) exits 2 with an error naming the packages (as does a `--pyodide-lock` URL), and the `requires-python` re-check is skipped with a note that those Pythons may show false gaps. It can't be combined with `--online`. (#140, thanks [@sarankumar1325](https://github.com/sarankumar1325))

## 0.8.0 (2026-11-10)

Name the distro you deploy to: `--distro ubuntu-22.04` (or `distro = "..."` in `[tool.wheelgap]`) sets the glibc or musl version for Ubuntu, Debian, RHEL, Rocky, Alma, Amazon Linux and Alpine. `--dockerfile` now gets the glibc right for `python:3.13` and `python:3.13-slim`, whose tags name no Debian release. Packages from a private index are no longer looked up on public PyPI, either in `Pipfile.lock` or under `--online`. With `--json`, input errors are also printed as JSON on stdout. Also new: the [check-wheels skill](https://github.com/wheelgap/wheelgap/tree/main/integrations) for coding agents (Claude Code plugin, Agent Skills and a Cursor rule).

- Set the glibc or musl version by distro name with `--distro` or `distro` in `[tool.wheelgap]`, without changing the selected platforms. Explicit libc flags still win; `--list-targets` lists the supported distros. (#138, thanks [@Swarneil1905](https://github.com/Swarneil1905))
- `Pipfile.lock` packages pinned to a private index (an `index` whose source URL isn't on pypi.org or pypi.python.org, or isn't listed in `_meta.sources`; a PyPI mirror counts as private too) are listed as skipped instead of being looked up on public PyPI, so internal package names aren't sent to pypi.org. (#136, thanks [@sarankumar1325](https://github.com/sarankumar1325))
- With `--json`, an input error (a missing lockfile, an unknown target, a bad `[tool.wheelgap]` value) is now also printed on stdout as `{"error": {"message": ..., "type": ...}}`, so scripts and agents don't have to parse stderr. The stderr line and exit code 2 are unchanged. (#137, thanks [@sarankumar1325](https://github.com/sarankumar1325))
- `--dockerfile` reads an official python `X.Y` image whose tag names no Debian release (`python:3.13`, `python:3.13-slim`) as the release Docker Hub builds it on: trixie (glibc 2.41) for Python 3.9 and later and the release its images stopped on for older ones (bullseye, glibc 2.31, for 3.6), so `manylinux_2_39`/`manylinux_2_41` wheels are no longer reported as source builds there. As for tagged images, this beats `glibc` in `[tool.wheelgap]` (`--glibc` still wins). Patch tags like `python:3.12.7-slim` can still be on an older release, so they keep the `--glibc` default. Pin `-bookworm` or `-trixie` for an answer that doesn't change. (#135, thanks [@sarankumar1325](https://github.com/sarankumar1325))
- `--online` no longer looks up packages from a private index on public PyPI: only packages whose lockfile index is PyPI are re-checked, the rest keep their locked files, and a note says how many. `poetry.lock` packages now record their source index too. (#133, thanks [@Niranjan-cyber](https://github.com/Niranjan-cyber))

## 0.7.0 (2026-11-09)

Accept known gaps without hiding them: `--ignore NAME`, or `ignore = [...]` in `[tool.wheelgap]`, takes a package out of the table, the counts and the exit code, and lists it as ignored in every output format. Also new: a [recipes page](https://github.com/wheelgap/wheelgap/blob/main/docs/recipes.md) (#123, thanks [@moelomda](https://github.com/moelomda)) and [AGENTS.md](https://github.com/wheelgap/wheelgap/blob/main/AGENTS.md) for coding agents working on wheelgap.

- `--ignore NAME` accepts known package gaps without failing CI, and lists ignored packages in text, JSON and Markdown reports. Repeat the flag or separate names with commas. (#119, thanks [@moelomda](https://github.com/moelomda))
- Set `ignore = ["psycopg2"]` in `[tool.wheelgap]` to ignore accepted gaps by default. Command-line `--ignore` flags replace this list. (#122, thanks [@moelomda](https://github.com/moelomda))

## 0.6.3 (2026-11-09)

`--dockerfile` now reads Dockerfiles the way Docker does, down to whitespace, line endings, tags and stage names, so it errors where Docker would instead of guessing. The coverage scanner adds Python 3.15 and 3.15t, and `--list-targets` shows the glibc 2.17 manylinux floor outside x86.

- The coverage scanner checks Python 3.15 and free-threaded 3.15t by default, alongside 3.13, 3.14 and 3.14t. (#117, thanks [@moelomda](https://github.com/moelomda))
- `--dockerfile` accepts quoted `--platform` values such as `FROM --platform="linux/arm64" python:3.13` (and rejects an unterminated quote). (#101, thanks [@Mr45401](https://github.com/Mr45401))
- `--list-targets` shows the glibc 2.17 floor for non-x86 Linux targets (e.g. `glibc 2.17 to 2.36` for `linux-aarch64`), and the README targets table says so too. A `Pipfile.lock` or `pyodide-lock.json` that isn't a JSON object, or a Pipfile.lock entry with a non-string version, is now a clear error instead of an `AttributeError`. With `-q`, the lock note added in 0.6.2 only appears when some packages couldn't be re-checked. (#99)

### Fixed

- `--dockerfile` rejects an upper-case repository name (`FROM PYTHON:3.13`) or tag variant (`python:3.13-SLIM`) instead of lowercasing it, and a tag with characters Docker doesn't allow, as Docker does. Python versions are matched with ASCII digits only, so `FROM python:3.1٣` and `-p 3.1٣` are errors instead of being read as 3.13. (#108, thanks [@bfs2021](https://github.com/bfs2021))
- `--dockerfile` splits lines on `\n` only, matching Docker: a lone `\r` (old Mac line endings), NEL and other Unicode line breaks no longer start a new line. CRLF files still work. (#106, thanks [@chiragjangiir](https://github.com/chiragjangiir))
- `--dockerfile` splits `FROM` lines on ASCII whitespace only, matching Docker, so a non-breaking space (e.g. after `FROM` or between words) is an error instead of quietly splitting words. A Dockerfile that isn't UTF-8 now gets a clear error. (#103, thanks [@chiragjangiir](https://github.com/chiragjangiir))
- `--dockerfile` rejects stage names Docker rejects (e.g. `AS 1build` or `AS my@stage`) instead of accepting them. (#100, thanks [@chiragjangiir](https://github.com/chiragjangiir))

## 0.6.2 (2026-11-09)

More accurate answers: no false gaps from narrowly locked `uv.lock` and `pylock.toml` files, pip's real manylinux floor on ARM and other non-x86 Linux, stricter Dockerfile parsing, and safer fetching.

### Fixed

- A `uv.lock` or `pylock.toml` whose `requires-python` leaves out the Python being checked (e.g. `==3.13.*` with `-p 3.14t`) no longer reports false gaps. uv keeps only the wheels that can serve `requires-python`, so wheelgap now also looks up the lock's PyPI packages on the index (keeping the lock's files if a lookup fails) and prints a note. (#97)
- `linux-aarch64`, `linux-armv7l`, `linux-ppc64le`, `linux-s390x` and `linux-riscv64` no longer accept `manylinux` wheels older than glibc 2.17 (e.g. `manylinux_2_12_aarch64`), matching pip: those arches had no manylinux before manylinux2014. (#92, thanks [@shounakjoshi88-a11y](https://github.com/shounakjoshi88-a11y))
- `--dockerfile` rejects `FROM` lines that Docker rejects instead of reading a target from them: `--platform` without `=`, a repeated `--platform`, unknown flags, and anything other than `IMAGE` or `IMAGE AS NAME` (e.g. `AS` with no name). (#88, thanks [@Vansh300901](https://github.com/Vansh300901))
- An `https://` lookup (PyPI or `--pyodide-lock`) no longer follows a redirect to plain `http://`; it fails with an error naming both URLs. (#95, thanks [@Mr45401](https://github.com/Mr45401))

## 0.6.1 (2026-11-09)

Two more lockfile formats, and safer `--pyodide-lock` fetching.

- Read Rye and uv requirements lockfiles (`requirements.lock`, `requirements-dev.lock`), and the pre-commit hook runs when they change. (#61, thanks [@chiragjangiir](https://github.com/chiragjangiir))
- Read custom package categories in `Pipfile.lock` (such as `[docs]` or `[tests]`), not just `default` and `develop`. (#59, thanks [@chiragjangiir](https://github.com/chiragjangiir))

### Fixed

- `--pyodide-lock` refuses plain `http://` URLs (a lockfile fetched without TLS could be changed to mark anything as bundled), names the URL when a fetch fails, and no longer counts test bundles such as `aiohttp-tests` (shipped as `.tar` files, not wheels) as bundled packages. (#68, thanks [@SatriaBagusNurhadianto](https://github.com/SatriaBagusNurhadianto))
- A malformed `Pipfile.lock` (a section or package entry that isn't a JSON object) is now a clear error naming it, instead of an `AttributeError`. (#59, thanks [@chiragjangiir](https://github.com/chiragjangiir))
- The summary line says "1 of 1 package", not "1 of 1 packages". (#81, thanks [@HarshRajSinghania](https://github.com/HarshRajSinghania))

## 0.6.0 (2026-11-09)

New targets for phones and more Linux hardware, and a job summary for the GitHub Action.

- iOS and Android targets (PEP 730 / PEP 738): `ios-arm64`, `ios-simulator-arm64`, `ios-simulator-x86_64`, `android-arm64` and `android-x86_64`, all at once with `-t mobile`. `--ios` and `--android-api` (or `ios` and `android-api` in `[tool.wheelgap]`) set the minimum versions, 13.0 and API level 24 by default. Like Pyodide, mobile installs only use wheels, and they need Python 3.13+. (#77)
- New Linux targets for 32-bit ARM (`linux-armv7l`, e.g. Raspberry Pi OS), `ppc64le`, `s390x` and `riscv64`, each with a `musl-*` twin for Alpine, and their Docker names (`linux/arm/v7` and so on) in `-t` and `--dockerfile`. Plain `linux_armv7l` wheels, which PyPI allows for the Raspberry Pi, count on `linux-armv7l` as they do for pip, but not on `musl-armv7l`: they're glibc builds. `-t all` still means the eight common platforms; `-t everything` adds these. (#72)
- `--markdown` prints the report as Markdown, and the GitHub Action now writes it to the run's summary page (`summary: false` turns that off). The step still passes or fails on the normal run. (#74)
- `--dockerfile` follows named stages: with `FROM python:3.13-alpine AS base` and later `FROM base AS runtime`, the final stage is checked as `python:3.13-alpine`, through any chain of stages, keeping the platform that stage was built for (as Docker does). A name that's neither an earlier stage nor a python image gets a clear error. Line continuations now follow Docker's rules (blank and comment lines inside one, `# escape=`), and Python `from ... import` lines in heredocs are no longer mistaken for `FROM`. (#73)
- Read package files from the `[metadata.files]` table in older `pdm.lock` files (PDM before 2.8) instead of looking them up on PyPI. (#67, thanks [@nxxis](https://github.com/nxxis))

### Fixed

- The scanner rejects invalid arguments before fetching data and lists each Python target once. `check_package()` and `check_packages()` raise `ValueError` for an empty target list instead of reporting no gaps, and empty Python input to `make_targets()` uses the running interpreter. (#78, thanks [@Daiyuanranfeng](https://github.com/Daiyuanranfeng))
- Out-of-range versions for `--glibc`, `--musl` and `--macos` (e.g. `2.3000000`, which never finished) are now errors, as is an out-of-range `--ios`. (#77)

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
