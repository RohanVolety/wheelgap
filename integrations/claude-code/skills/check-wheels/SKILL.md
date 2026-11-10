---
name: check-wheels
description: Check whether a Python project's dependencies have prebuilt wheels for the platforms it ships to (Linux ARM64/Graviton, Alpine/musl, Windows on ARM, macOS, free-threaded 3.14t, Pyodide, iOS/Android), using wheelgap. Use when a Docker or CI build is slow or fails compiling a Python package ("Failed building wheel for", "Microsoft Visual C++ 14.0 or greater is required", "command 'gcc' failed"), when moving to ARM, Alpine or a new Python version, or when asked "will our dependencies install on X?"
license: MIT
---

# Check wheels with wheelgap

wheelgap reads a lockfile and reports, per package and target platform, whether pip/uv would install a prebuilt **wheel**, build from **source** (slow, needs a compiler), or find nothing (**missing**). Lockfiles that record their files (`uv.lock`, `pylock.toml`, `poetry.lock`, `pdm.lock`) are checked offline in about a second; `requirements*.txt`, `Pipfile.lock` and package names are looked up on PyPI.

## Run it

```bash
uvx wheelgap@latest -p 3.13 -t linux-aarch64,musl-aarch64 --json
```

- **Input:** in the project directory, wheelgap finds `uv.lock`, `pylock.toml`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `requirements.txt` or `requirements.lock` (the first one present). Pass any other file by path, e.g. `uvx wheelgap@latest requirements-prod.txt …`.
- **Python (`-p`):** always pass the project's Python, from `.python-version`, `requires-python`, the CI matrix or the Dockerfile. Without `-p`, wheelgap uses `.python-version`, else the Python it runs on, which may be free-threaded (3.14t) and would report gaps an ordinary project doesn't have. Use `-p 3.14t` only to check free-threading readiness.
- **Platforms (`-t`):** use what the project ships to.
  - A Dockerfile: `uvx wheelgap@latest --dockerfile Dockerfile --json` takes both Python and platform from the final stage.
  - A `[tool.wheelgap]` table in `pyproject.toml`: its defaults apply with no `-p`/`-t`.
  - Otherwise ask, or use `-t all` (the eight common platforms).
  - `uvx wheelgap@latest --list-targets` lists every target and alias.
- **Libc:** if the user names a deployment distro, pass `--distro NAME` (e.g. `ubuntu-22.04`, `debian-12`, `rhel-9`, `amazonlinux-2023`, `alpine`; `--list-targets` lists them). For a distro that isn't listed, verify its libc version and pass `--glibc X.Y` or `--musl X.Y`. None of these select platforms; Alpine still needs a `musl-*` target. With `--dockerfile`, leave `--distro` out: the image already sets the libc, and `--distro` would override it.

## Read the result

- **Don't rely on the exit code alone:** `0` no failing gaps, `1` gaps, `2` bad input (with `--json`, stdout is then `{"error": {"message": ..., "type": ...}}`; the message is on stderr too). `--exit-zero` or `fail-on = "missing"` can make source builds exit 0, so read `summary.with_gaps` and each package's `results`.
- **`results`** maps a target label to `wheel`, `source`, `missing`, `unknown`, `n/a` (the package isn't installed on that target, per its markers) or `bundled` (shipped by Pyodide, with `--pyodide-lock`). Labels come from `targets[].label`: the platform alone with one Python, `"3.13 linux-x86_64"` with several.
- **`unknown`** means a lookup failed; the package's `error` field says why. Never report it as fine.
- **`skipped`** lists packages that weren't checked (editable, git, path), so don't claim every dependency was covered.
- **Why:** `--explain` adds each rejected wheel and the reason (`rejected`), and for `uv.lock` the dependency path that pulls the package in (`via`).

## What to tell the user

1. Name the packages with gaps, per platform, with the reason from `--explain`, and which dependency pulls each in.
2. Offer concrete fixes:
   - pin a version that ships the wheel;
   - use a `-slim` Debian base instead of Alpine;
   - add build dependencies to the Dockerfile on purpose;
   - `--fail-on missing` to accept source builds in CI.
3. To accept a known gap without hiding it, use `--ignore NAME` or `ignore = ["NAME"]` in `[tool.wheelgap]`.

wheelgap checks wheel availability only, not whether a package works without the GIL.

For a quick look at one popular package without running anything, fetch `https://wheelgap.github.io/tracker/data/packages/<normalized-name>.json`. That's a nightly scan of the **latest release** of the top 5,000 PyPI packages, not the version a project pins.
