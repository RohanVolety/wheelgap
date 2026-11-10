"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

from . import __version__
from .api import running_python
from .check import Row, Status, check, explain, has_sdist
from .dockerfile import DockerfileError, read_dockerfile
from .pyodide import PyodideLock, PyodideLockError, apply_bundled, check_targets, load_pyodide_lock
from .pypi import check_index_url, fill_all, split_credentials
from .sources import Package, SourceError, load_inputs
from .targets import (
    ALIASES,
    DEFAULT_GLIBC,
    DEFAULT_MACOS,
    DEFAULT_MUSL,
    DEFAULT_PLATFORMS,
    EXTRA_ARCHES,
    PLATFORMS,
    PYODIDE_PLATFORMS,
    Target,
    TargetError,
    build_targets,
    parse_version_pair,
    resolve_platform,
)

EXAMPLES = """\
examples:
  wheelgap                                 check the lockfile in the current directory
  wheelgap uv.lock -t musl-aarch64         will my Alpine ARM image build from source?
  wheelgap -p 3.14t -t all                 is my stack ready for free-threaded 3.14?
  wheelgap psycopg2 uvloop -t win-amd64    check packages without a lockfile
  wheelgap requirements.txt --json         machine-readable output for CI
  wheelgap -t alpine-arm64 --explain       show which wheels were rejected, and why
"""


def musl_docker_names(item: str) -> str:
    """With an Alpine image, `-t linux/arm64` means musl on arm64: Docker names say only the architecture.

    wheelgap's own names (linux-aarch64, musl-aarch64) are left alone, since they name the libc too.
    """
    out = []
    for name in item.split(","):
        if "/" in name:
            resolved = resolve_platform(name)
            name = resolved.replace("linux-", "musl-", 1) if resolved.startswith("linux-") else resolved
        out.append(name)
    return ",".join(out)


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wheelgap",
        description="Find the dependencies that have no prebuilt wheel for your target platform, "
        "so they'd be compiled from source (slow) or fail to install.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "inputs",
        nargs="*",
        metavar="INPUT",
        help=(
            "uv.lock, pylock.toml, poetry.lock, Pipfile.lock, requirements.txt, "
            "a directory, or package specs (default: .)"
        ),
    )
    p.add_argument(
        "-p",
        "--python",
        action="append",
        default=[],
        metavar="VER",
        help=f"target Python, e.g. 3.13 or 3.14t (repeatable; default: .python-version, else {running_python()})",
    )
    p.add_argument(
        "-t",
        "--target",
        action="append",
        default=[],
        metavar="PLATFORM",
        help=(
            "target platform (repeatable; 'all' for the 8 common ones, 'everything' adds armv7l, ppc64le, "
            f"s390x and riscv64 Linux; default: {','.join(DEFAULT_PLATFORMS)})"
        ),
    )
    p.add_argument("--glibc", metavar="X.Y", help=f"glibc version for linux-* targets (default: {DEFAULT_GLIBC})")
    p.add_argument("--musl", metavar="X.Y", help=f"musl version for musl-* targets (default: {DEFAULT_MUSL})")
    p.add_argument("--macos", metavar="X.Y", help=f"minimum macOS for macos-* targets (default: {DEFAULT_MACOS})")
    p.add_argument(
        "--dockerfile",
        metavar="PATH",
        help="take -p/-t (and --glibc/--musl) from the last FROM line, e.g. FROM python:3.13-alpine",
    )
    p.add_argument(
        "--pyodide-lock",
        metavar="PATH_OR_URL",
        help="a Pyodide release's pyodide-lock.json: packages Pyodide ships itself count as available on -t pyodide",
    )
    p.add_argument(
        "--online", action="store_true", help="re-check lockfile packages against PyPI instead of trusting the lockfile"
    )
    p.add_argument("--all", action="store_true", help="show every package, not just the ones with gaps")
    p.add_argument("--explain", action="store_true", help="for each gap, list the wheels that were rejected and why")
    p.add_argument(
        "-q",
        "--quiet",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="print only the gap summary and affected package names (--no-quiet overrides quiet = true)",
    )
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument(
        "--markdown",
        action="store_true",
        help="print a Markdown report (e.g. for $GITHUB_STEP_SUMMARY) instead of a table",
    )
    p.add_argument("--exit-zero", action="store_true", help="exit 0 even when gaps are found")
    p.add_argument(
        "--fail-on",
        choices=FAIL_ON,
        help="exit 1 on any gap ('source', the default) or only when a package can't install ('missing')",
    )
    p.add_argument(
        "--index-url",
        metavar="URL",
        help="look packages up in this simple index (PEP 691 JSON) instead of PyPI's JSON API "
        "(lockfiles that list their files are still read offline unless --online); "
        "credentials can go in the URL (https://user:token@host/simple)",
    )
    p.add_argument("--color", choices=["auto", "always", "never"], default="auto")
    p.add_argument("--list-targets", action="store_true", help="list platforms, aliases and compatibility limits")
    p.add_argument("-V", "--version", action="version", version=f"wheelgap {__version__}")
    return p


FAIL_ON = ("source", "missing")
LIST_KEYS = ("python", "targets")
STR_KEYS = ("glibc", "musl", "macos", "fail-on", "pyodide-lock", "index-url")


def project_dir(inputs: list[str]) -> Path:
    """The folder of the first lockfile or directory given, else the current directory."""
    for item in inputs:
        p = Path(item)
        if p.is_dir() or p.is_file():
            return p if p.is_dir() else p.parent
    return Path(".")


def find_pyproject(inputs: list[str]) -> Path | None:
    """pyproject.toml in the project folder (see project_dir)."""
    candidate = project_dir(inputs) / "pyproject.toml"
    return candidate if candidate.is_file() else None


PYTHON_VERSION_FILE = ".python-version"
# uv and pyenv write "3.13", "3.13.1" or "3.14t"; anything else (system, pypy@3.10) is skipped.
PYTHON_VERSION_LINE = re.compile(r"3\.(\d+)(?:\.\d+)?(t?)")


def read_python_version(folder: Path) -> list[str]:
    """Versions from the .python-version file in folder, e.g. ['3.13'] for a line '3.13.1'."""
    path = folder / PYTHON_VERSION_FILE
    if not path.is_file():
        return []
    versions = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():  # -sig: Windows editors add a BOM
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        m = PYTHON_VERSION_LINE.fullmatch(entry)
        if m:
            versions.append(f"3.{m.group(1)}{m.group(2)}")
        else:
            print(f"wheelgap: note: ignoring {entry!r} in {path}", file=sys.stderr)
    return versions


def load_config(path: Path) -> dict:
    """Defaults from the [tool.wheelgap] table; command-line flags still win."""
    table = tomllib.loads(path.read_text(encoding="utf-8")).get("tool", {}).get("wheelgap", {})
    if not isinstance(table, dict):
        raise SourceError(f"{path}: [tool.wheelgap] must be a table")
    config = {}
    for key, value in table.items():
        if key in LIST_KEYS:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise SourceError(f"{path}: [tool.wheelgap] {key} must be a list of strings")
        elif key in STR_KEYS:
            if not isinstance(value, str):
                raise SourceError(f'{path}: [tool.wheelgap] {key} must be a string like "2.31"')
            if key == "fail-on" and value not in FAIL_ON:
                raise SourceError(f"{path}: [tool.wheelgap] fail-on must be one of: {', '.join(FAIL_ON)}")
        elif key == "quiet":
            if not isinstance(value, bool):
                raise SourceError(f"{path}: [tool.wheelgap] quiet must be a boolean")
        else:
            raise SourceError(f"{path}: unknown key in [tool.wheelgap]: {key}")
        config[key] = value
    return config


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def __call__(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def green(self, t: str) -> str:
        return self(t, "32")

    def yellow(self, t: str) -> str:
        return self(t, "33")

    def red(self, t: str) -> str:
        return self(t, "31")

    def dim(self, t: str) -> str:
        return self(t, "2")

    def bold(self, t: str) -> str:
        return self(t, "1")


def use_color(mode: str) -> bool:
    if mode != "auto":
        return mode == "always"
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


CELL = {
    Status.WHEEL: ("✓", "green"),
    Status.SOURCE: ("● source", "yellow"),
    Status.MISSING: ("✗ missing", "red"),
    Status.UNKNOWN: ("? unknown", "dim"),
    Status.NOT_NEEDED: ("–", "dim"),
    Status.BUNDLED: ("✓ bundled", "green"),
}


def render_table(rows: list[Row], targets: list[Target], style: Style) -> list[str]:
    multi_py = len({t.python for t in targets}) > 1
    headers = ["package", "version"] + [t.label(multi_py) for t in targets]
    body = []
    for r in rows:
        cells = [r.package.name, r.package.version or "?"]
        cells += [CELL[r.results[t]][0] for t in targets]
        body.append(cells)
    widths = [max(len(h), *(len(c[i]) for c in body)) for i, h in enumerate(headers)]

    def line(cells: list[str], colors: list[str] | None = None) -> str:
        parts = []
        for i, c in enumerate(cells):
            padded = c.ljust(widths[i])
            if colors and colors[i]:
                padded = getattr(style, colors[i])(padded)
            parts.append(padded)
        return "  ".join(parts).rstrip()

    out = [style.bold(line(headers))]
    for r, cells in zip(rows, body, strict=True):
        colors = ["", "dim"] + [CELL[r.results[t]][1] for t in targets]
        out.append(line(cells, colors))
    return out


EXPLAIN_LIMIT = 8


def gap_targets(row: Row, targets: list[Target]) -> list[Target]:
    return [t for t in targets if row.results[t] in (Status.SOURCE, Status.MISSING)]


def render_explain(rows: list[Row], targets: list[Target], style: Style) -> list[str]:
    multi_py = len({t.python for t in targets}) > 1
    out = []
    for r in rows:
        files = r.package.files or []
        for t in gap_targets(r, targets):
            cell, color = CELL[r.results[t]]
            head = f"{r.package.name} {r.package.version or '?'} on {t.label(multi_py)}:"
            out.append(f"{style.bold(head)} {getattr(style, color)(cell)}")
            path = r.via.get(t, [])
            if len(path) >= 2:  # a lone name says nothing about why the package is there
                out.append(f"  via: {' → '.join(path)}")
            rejected = explain(files, t)
            if not rejected:
                out.append(style.dim("  no wheels published for this version"))
            width = max((len(f) for f, _ in rejected[:EXPLAIN_LIMIT]), default=0)
            for f, reason in rejected[:EXPLAIN_LIMIT]:
                out.append(f"  {f.ljust(width)}  {style.dim(reason)}")
            if len(rejected) > EXPLAIN_LIMIT:
                out.append(style.dim(f"  … and {len(rejected) - EXPLAIN_LIMIT} more (--json lists them all)"))
            if not t.builds_sdists and has_sdist(files):
                out.append(style.dim(f"  there is an sdist, but {t.platform} can't build packages from source"))
            out.append("")
    return out


def bundled_line(rows: list[Row], lock: PyodideLock) -> str:
    def one(r: Row) -> str:
        have = lock.packages[r.package.key]
        pinned = r.package.version
        return f"{r.package.name} {have}" + (f" (you pinned {pinned})" if pinned and pinned != have else "")

    noun = "package comes" if len(rows) == 1 else "packages come"
    return f"{len(rows)} {noun} from Pyodide {lock.pyodide_version}'s own builds: " + ", ".join(map(one, rows))


def report_json(
    rows: list[Row],
    targets: list[Target],
    sources: list[str],
    skipped: list,
    explained: bool,
    pyodide_lock: PyodideLock | None = None,
) -> str:
    multi_py = len({t.python for t in targets}) > 1
    gaps = [r for r in rows if not r.ok]

    def package(r: Row) -> dict:
        out: dict[str, object] = {
            "name": r.package.name,
            "version": r.package.version,
            "error": r.package.error,
            "results": {t.label(multi_py): r.results[t].value for t in targets},
        }
        if pyodide_lock and Status.BUNDLED in r.results.values():
            out["bundled"] = pyodide_lock.packages[r.package.key]
        if explained:
            out["rejected"] = {
                t.label(multi_py): [{"file": f, "reason": why} for f, why in explain(r.package.files or [], t)]
                for t in gap_targets(r, targets)
            }
            # Same targets as `rejected`, minus any without a path (inputs with no dependency graph).
            out["via"] = {t.label(multi_py): r.via[t] for t in gap_targets(r, targets) if t in r.via}
        return out

    return json.dumps(
        {
            "version": __version__,
            "sources": sources,
            "targets": [{"label": t.label(multi_py), "python": t.python, "platform": t.platform} for t in targets],
            "packages": [package(r) for r in rows],
            "skipped": [{"name": p.name, "reason": p.skip} for p in skipped],
            "summary": {"packages": len(rows), "with_gaps": len(gaps)},
        },
        indent=2,
    )


def print_target_list(args: argparse.Namespace, config: dict) -> None:
    aliases: dict[str, list[str]] = {name: [] for name in PLATFORMS}
    for alias, name in ALIASES.items():
        aliases[name].append(alias)

    versions = {
        "manylinux": ("glibc", args.glibc or config.get("glibc", DEFAULT_GLIBC)),
        "musllinux": ("musl", args.musl or config.get("musl", DEFAULT_MUSL)),
        "macos": ("macOS", args.macos or config.get("macos", DEFAULT_MACOS)),
    }
    limits = {}
    for family, (label, version) in versions.items():
        major, minor = parse_version_pair(version, label)
        limits[family] = f", {label} <= {major}.{minor}"
    pythons = ", ".join(f"{a}.{b}" for a, b in PYODIDE_PLATFORMS)
    limits["pyodide"] = f", Python {pythons} (not in -t all)"

    rows = [
        (name, aliases[name], f"{family} {arch}{limits.get(family, '')}{' (not in -t all)' * (arch in EXTRA_ARCHES)}")
        for name, (family, arch) in PLATFORMS.items()
    ]
    if args.json:
        print(
            json.dumps(
                [{"name": name, "aliases": names, "accepts": accepts} for name, names, accepts in rows], indent=2
            )
        )
        return

    table = [("name", "aliases", "accepts")]
    table.extend((name, ", ".join(names) or "-", accepts) for name, names, accepts in rows)
    name_width = max(len(name) for name, _, _ in table)
    alias_width = max(len(names) for _, names, _ in table)
    for name, names, accepts in table:
        print(f"{name:<{name_width}}  {names:<{alias_width}}  {accepts}")
    print("Docker platform names like linux/arm64 also work.")


def worst_status(row: Row) -> Status:
    """The status that best describes a gap package: can't install, then unknown, then from source."""
    for s in (Status.MISSING, Status.UNKNOWN, Status.SOURCE):
        if s in row.results.values():
            return s
    return Status.WHEEL


def gap_summary(gaps: list[Row], total: int, style: Style) -> str:
    # Each package is counted once, under its worst status, so the parts always add up to len(gaps).
    worst = [worst_status(r) for r in gaps]
    source, missing, unknown = (worst.count(s) for s in (Status.SOURCE, Status.MISSING, Status.UNKNOWN))
    parts = []
    if source:
        parts.append(style.yellow(f"{source} will build from source"))
    if missing:
        parts.append(style.red(f"{missing} can't install at all"))
    if unknown:
        parts.append(style.dim(f"{unknown} unknown"))
    verb = "has" if len(gaps) == 1 else "have"
    return f"{style.bold(f'{len(gaps)} of {total} packages')} {verb} gaps: " + ", ".join(parts) + "."


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|~&])")


def _md(text: str) -> str:
    """Escape text so Markdown shows it literally (paths like ./__internal__/ stay as written)."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def _code(text: str) -> str:
    """A Markdown code span showing text exactly (paths and URLs stay readable and unlinked)."""
    if not text:
        return '`""`'  # `` isn't a code span, and would pair up with the backticks after it
    fence = "`" * (max((len(run) for run in re.findall(r"`+", text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"  # only used outside tables, so | needs no escaping


def render_markdown(
    rows: list[Row],
    targets: list[Target],
    source_labels: list[str],
    skipped: list[Package],
    args: argparse.Namespace,
    pyodide_lock: PyodideLock | None,
    source_allowed: bool = False,
) -> list[str]:
    """The same report as the text output, as GitHub-flavoured Markdown (for a job summary or a PR comment)."""
    plain = Style(False)
    gaps = [r for r in rows if not r.ok]
    n_t = len(targets)
    if gaps:
        noun = "package" if len(rows) == 1 else "packages"
        out = [f"### wheelgap: {len(gaps)} of {len(rows)} {noun} {'has' if len(gaps) == 1 else 'have'} gaps", ""]
    else:
        out = [
            f"### wheelgap: every package has a wheel for {'all ' if n_t > 1 else ''}{n_t} target{'s' * (n_t > 1)}",
            "",
        ]
    pys = ", ".join(dict.fromkeys(t.python for t in targets))
    n_pkgs = f"{len(rows)} package{'s' * (len(rows) != 1)}"
    out += [f"wheelgap {__version__} · {' + '.join(map(_code, source_labels))} · {n_pkgs} · Python {pys}", ""]

    shown = rows if args.all else gaps
    if shown:
        multi_py = len({t.python for t in targets}) > 1
        headers = ["package", "version"] + [t.label(multi_py) for t in targets]
        out.append("| " + " | ".join(_md(h) for h in headers) + " |")
        out.append("| " + " | ".join("---" for _ in headers) + " |")
        for r in shown:
            cells = [r.package.name, r.package.version or "?"] + [CELL[r.results[t]][0] for t in targets]
            out.append("| " + " | ".join(_md(c) for c in cells) + " |")
        out.append("")
        hidden = len(rows) - len(shown)
        if hidden:
            noun = "package" if hidden == 1 else "packages"
            out += [f"{hidden} {noun} with wheels for every target hidden (`--all` to show).", ""]

    if gaps:
        out += [gap_summary(gaps, len(rows), plain), ""]
        if source_allowed:
            out += ["Source builds are allowed (fail-on missing), so this run passes.", ""]
    bundled = [r for r in rows if Status.BUNDLED in r.results.values()]
    if bundled and pyodide_lock:
        out += [_md(bundled_line(bundled, pyodide_lock)), ""]
    if skipped:
        names = ", ".join(f"{_code(p.name)} ({p.skip})" for p in skipped)
        out += [f"Skipped {len(skipped)} non-index package{'s' * (len(skipped) != 1)}: {names}", ""]
    for r in rows:
        if r.package.error:
            out += [f"Could not check {_md(r.package.name)}: {_code(r.package.error)}", ""]
    if args.explain and gaps:
        # Fenced, so wheel filenames and reasons need no escaping.
        lines = render_explain(gaps, targets, plain)
        out += ["<details><summary>Why (rejected wheels)</summary>", "", "```", *lines, "```", "", "</details>", ""]
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Don't crash printing ✓ to a non-UTF-8 pipe (e.g. Windows cp1252).
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            # Windows pipes (CI logs) default to cp1252, which would turn ✓ into "?"; write UTF-8 instead.
            # A console (isatty) already handles Unicode, and PYTHONIOENCODING is the user's choice.
            encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
            if encoding != "utf8" and not stream.isatty() and not os.environ.get("PYTHONIOENCODING"):
                reconfigure(encoding="utf-8", errors="replace")
            else:
                reconfigure(errors="replace")
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.json and args.markdown:
        parser.error("--json and --markdown can't be used together")
    if args.list_targets and args.markdown:
        parser.error("--list-targets has no Markdown form (use --json)")
    try:
        if args.list_targets:
            pyproject = find_pyproject(args.inputs)
            print_target_list(args, load_config(pyproject) if pyproject else {})
            return 0
        pyproject = find_pyproject(args.inputs)
        config = load_config(pyproject) if pyproject else {}
        quiet = args.quiet if args.quiet is not None else config.get("quiet", False)
        # Quiet only changes the text output, so it can't clash with --explain when printing JSON or Markdown.
        if quiet and args.explain and not (args.json or args.markdown):
            parser.error("--explain cannot be used with quiet mode (--quiet or quiet = true; --no-quiet turns it off)")
        if args.dockerfile:
            # The image beats pyproject.toml; flags still win.
            image = read_dockerfile(Path(args.dockerfile))
            if image.platform.startswith("musl-") and args.target:
                args.target = [musl_docker_names(item) for item in args.target]
            config.update(python=[image.python], targets=[image.platform])
            config.update({k: v for k, v in (("glibc", image.glibc), ("musl", image.musl)) if v})
        targets = build_targets(
            args.python or config.get("python") or read_python_version(project_dir(args.inputs)) or [running_python()],
            args.target or config.get("targets", []),
            args.glibc or config.get("glibc", DEFAULT_GLIBC),
            args.musl or config.get("musl", DEFAULT_MUSL),
            args.macos or config.get("macos", DEFAULT_MACOS),
        )
        index_url = args.index_url if args.index_url is not None else config.get("index-url")
        if index_url is not None:
            split_credentials(index_url)
        else:
            check_index_url()  # WHEELGAP_INDEX_URL only matters for the JSON API
        sources = load_inputs(args.inputs or ["."])
        fail_on = args.fail_on or config.get("fail-on", "source")
        pyodide_lock = None
        if args.pyodide_lock:
            pyodide_lock = load_pyodide_lock(args.pyodide_lock)
            check_targets(pyodide_lock, targets)  # asked for explicitly, so a mismatch is an error
        elif (lock_spec := config.get("pyodide-lock")) and any(PLATFORMS[t.platform][0] == "pyodide" for t in targets):
            # A project default: only read (or fetched) on runs that check a pyodide target, so it never
            # affects `wheelgap -t linux` in the same project. A relative path is relative to pyproject.toml.
            if pyproject and not lock_spec.startswith(("http://", "https://")):
                lock_spec = str(pyproject.parent / lock_spec)
            pyodide_lock = load_pyodide_lock(lock_spec)
            check_targets(pyodide_lock, targets)  # pyodide targets but none for this lock's Python: say so
    except (TargetError, SourceError, DockerfileError, PyodideLockError, OSError) as e:
        print(f"wheelgap: error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # malformed TOML and friends
        print(f"wheelgap: error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    packages = [p for s in sources for p in s.packages]
    fill_all(packages, refresh=args.online, simple_index=index_url)
    skipped = [p for p in packages if p.skip]
    rows = [r for r in check(sources, targets) if r.needed]
    if pyodide_lock:
        apply_bundled(rows, targets, pyodide_lock)
    rows.sort(key=lambda r: r.package.key)
    gaps = [r for r in rows if not r.ok]
    source_labels = [s.label for s in sources]
    # With --fail-on missing, source builds are accepted; missing and unknown packages still fail.
    failing = gaps if fail_on == "source" else [r for r in gaps if r.fails_install]
    code = 0 if args.exit_zero or not failing else 1

    if args.json:
        print(report_json(rows, targets, source_labels, skipped, args.explain, pyodide_lock))
        return code

    if args.markdown:
        source_allowed = bool(gaps) and not failing  # judged on --fail-on alone, not --exit-zero
        print("\n".join(render_markdown(rows, targets, source_labels, skipped, args, pyodide_lock, source_allowed)))
        return code

    style = Style(use_color(args.color))
    if quiet:
        if gaps:
            print(gap_summary(gaps, len(rows), style))
            print("gaps: " + ", ".join(r.package.name for r in gaps))
        for r in rows:
            if r.package.error:  # say why a package is "unknown", even in quiet mode
                print(f"wheelgap: could not check {r.package.name}: {r.package.error}", file=sys.stderr)
        return code

    pys = ", ".join(dict.fromkeys(t.python for t in targets))
    n_pkgs = f"{len(rows)} package{'s' * (len(rows) != 1)}"
    print(style.dim(f"wheelgap {__version__} · {' + '.join(source_labels)} · {n_pkgs} · Python {pys}"))
    print()

    shown = rows if args.all else gaps
    if shown:
        for ln in render_table(shown, targets, style):
            print(ln)
        print()
        hidden = len(rows) - len(shown)
        if hidden:
            noun = "package" if hidden == 1 else "packages"
            print(style.dim(f"{hidden} {noun} with wheels for every target hidden (--all to show)"))
        if args.explain and gaps:
            if hidden:
                print()
            for ln in render_explain(gaps, targets, style):
                print(ln)

    bundled = [r for r in rows if Status.BUNDLED in r.results.values()]
    if bundled and pyodide_lock:
        print(style.dim(bundled_line(bundled, pyodide_lock)))

    if skipped:
        names = ", ".join(f"{p.name} ({p.skip})" for p in skipped)
        print(style.dim(f"skipped {len(skipped)} non-index package{'s' * (len(skipped) != 1)}: {names}"))

    errors = [r for r in rows if r.package.error]
    for r in errors:
        print(style.dim(f"could not check {r.package.name}: {r.package.error}"))

    n_t = len(targets)
    if not gaps:
        what = "The 1 package has" if len(rows) == 1 else f"All {len(rows)} packages have"
        print(style.green(f"✓ {what} a wheel for {'every one of ' if n_t > 1 else ''}{n_t} target{'s' * (n_t > 1)}."))
        return 0

    print(gap_summary(gaps, len(rows), style))
    if not args.explain:
        print(style.dim("Run with --explain to see which wheels were rejected and why."))
    if gaps and not failing and not args.exit_zero:
        print(style.dim("Source builds are allowed (--fail-on missing), so this run passes."))
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
