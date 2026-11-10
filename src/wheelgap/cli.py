"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

from packaging.markers import InvalidMarker, Marker
from packaging.utils import canonicalize_name

from . import __version__
from .api import running_python
from .check import Row, Status, check, explain, has_sdist, marker_holds
from .dockerfile import DockerfileError, read_dockerfile
from .pyodide import PyodideLock, PyodideLockError, apply_bundled, check_targets, load_pyodide_lock
from .pypi import check_index_url, fill_all, is_pypi, keeps_locked_files, recheck_lock, split_credentials
from .sources import Package, Source, SourceError, load_inputs, pythons_outside_lock
from .targets import (
    ALIASES,
    DEFAULT_ANDROID_API,
    DEFAULT_GLIBC,
    DEFAULT_IOS,
    DEFAULT_MACOS,
    DEFAULT_MUSL,
    DEFAULT_PLATFORMS,
    DISTRO_ALIASES,
    DISTROS,
    EXTRA_ARCHES,
    MANYLINUX_FLOOR,
    MANYLINUX_X86_ARCHES,
    NATIVE_PLATFORMS,
    PLATFORMS,
    PYODIDE_PLATFORMS,
    Target,
    TargetError,
    build_targets,
    parse_api_level,
    parse_ios_version,
    parse_version_pair,
    resolve_distro,
    resolve_platform,
    single_python,
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
            f"s390x and riscv64 Linux, 'mobile' is iOS and Android; default: {','.join(DEFAULT_PLATFORMS)})"
        ),
    )
    p.add_argument("--glibc", metavar="X.Y", help=f"glibc version for linux-* targets (default: {DEFAULT_GLIBC})")
    p.add_argument("--musl", metavar="X.Y", help=f"musl version for musl-* targets (default: {DEFAULT_MUSL})")
    p.add_argument(
        "--distro",
        metavar="NAME",
        help="default glibc/musl version from a distro, e.g. ubuntu-22.04 (does not select -t)",
    )
    p.add_argument("--macos", metavar="X.Y", help=f"minimum macOS for macos-* targets (default: {DEFAULT_MACOS})")
    p.add_argument("--ios", metavar="X.Y", help=f"minimum iOS for ios-* targets (default: {DEFAULT_IOS})")
    p.add_argument(
        "--android-api",
        metavar="N",
        help=f"minimum Android API level for android-* targets (default: {DEFAULT_ANDROID_API})",
    )
    p.add_argument(
        "--dockerfile",
        metavar="PATH",
        help="take -p/-t (and --glibc/--musl) from the final stage's image, e.g. FROM python:3.13-alpine",
    )
    p.add_argument(
        "--pyodide-lock",
        metavar="PATH_OR_HTTPS_URL",
        help="a Pyodide release's pyodide-lock.json: packages Pyodide ships itself count as available on -t pyodide",
    )
    p.add_argument(
        "--online", action="store_true", help="re-check lockfile packages against PyPI instead of trusting the lockfile"
    )
    p.add_argument("--offline", action="store_true", help="never contact an index; fail if an input needs one")
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
    p.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="NAME",
        help="ignore known package gaps (repeatable, comma-separated); list ignored names in the report",
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
LIST_KEYS = ("python", "targets", "ignore")
STR_KEYS = ("glibc", "musl", "macos", "ios", "android-api", "fail-on", "distro", "pyodide-lock", "index-url")


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
PYTHON_VERSION_LINE = re.compile(r"3\.(\d+)(?:\.\d+)?(t?)", re.ASCII)


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
            if key == "android-api" and isinstance(value, int) and not isinstance(value, bool):
                value = str(value)  # an API level is naturally written as a number
            if not isinstance(value, str):
                example = {
                    "android-api": 'an integer or a string like "24"',
                    "ios": 'a string like "13.0"',
                    "musl": 'a string like "1.2"',
                    "macos": 'a string like "14.0"',
                    "fail-on": 'a string like "missing"',
                    "distro": 'a string like "ubuntu-22.04"',
                    "pyodide-lock": "a path or URL string",
                }.get(key, 'a string like "2.31"')
                raise SourceError(f"{path}: [tool.wheelgap] {key} must be {example}")
            if key == "fail-on" and value not in FAIL_ON:
                raise SourceError(f"{path}: [tool.wheelgap] fail-on must be one of: {', '.join(FAIL_ON)}")
        elif key == "quiet":
            if not isinstance(value, bool):
                raise SourceError(f"{path}: [tool.wheelgap] quiet must be a boolean")
        else:
            raise SourceError(f"{path}: unknown key in [tool.wheelgap]: {key}")
        config[key] = value
    return config


def load_uv_targets(path: Path, pythons: list[str]) -> list[str]:
    """Native platforms needed by any required-environments marker for any selected Python."""
    table = tomllib.loads(path.read_text(encoding="utf-8")).get("tool", {}).get("uv", {})
    if not isinstance(table, dict):
        raise SourceError(f"{path}: [tool.uv] must be a table")
    markers = table.get("required-environments", [])
    if isinstance(markers, str):  # uv accepts a single marker as well as a list
        markers = [markers]
    if not isinstance(markers, list) or not all(isinstance(m, str) for m in markers):
        raise SourceError(f"{path}: [tool.uv] required-environments must be a string or a list of strings")
    if not markers:
        return []
    # marker_holds conservatively accepts unparseable package markers. A project setting
    # should instead report a typo, rather than silently select every native platform.
    for marker in markers:
        try:
            Marker(marker)
        except InvalidMarker as e:
            raise SourceError(f"{path}: [tool.uv] required-environments has an invalid marker {marker!r}: {e}") from e
    targets = build_targets(pythons, NATIVE_PLATFORMS)
    platforms = list(dict.fromkeys(t.platform for t in targets if any(marker_holds(m, t) for m in markers)))
    if not platforms:
        # e.g. a mobile-only project, or `wheelgap requests` run in a project folder: don't fail the run
        print(
            f"wheelgap: note: {path} [tool.uv] required-environments matches none of the native targets "
            f"for Python {', '.join(pythons)}, so the default targets are checked; select targets with -t",
            file=sys.stderr,
        )
    return platforms


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
    ignored: list[str],
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
            "ignored": ignored,
            "summary": {"packages": len(rows), "with_gaps": len(gaps)},
        },
        indent=2,
    )


def libc_versions(args: argparse.Namespace, config: dict) -> tuple[str, str]:
    """CLI libc > CLI distro > Dockerfile/config libc > config distro > built-ins."""
    defaults = {}
    if "distro" in config:
        key, version = resolve_distro(config["distro"])  # validate it even when --distro wins
        if args.distro is None:
            defaults[key] = version
    defaults.update(config)
    if args.distro is not None:
        key, version = resolve_distro(args.distro)
        defaults[key] = version
    return args.glibc or defaults.get("glibc", DEFAULT_GLIBC), args.musl or defaults.get("musl", DEFAULT_MUSL)


def print_target_list(args: argparse.Namespace, config: dict) -> None:
    aliases: dict[str, list[str]] = {name: [] for name in PLATFORMS}
    for alias, name in ALIASES.items():
        aliases[name].append(alias)

    glibc_version, musl_version = libc_versions(args, config)
    versions = {
        "manylinux": ("glibc", glibc_version),
        "musllinux": ("musl", musl_version),
        "macos": ("macOS", args.macos or config.get("macos", DEFAULT_MACOS)),
    }
    limits = {}
    for family, (label, version) in versions.items():
        major, minor = parse_version_pair(version, label)
        limits[family] = f", {label} <= {major}.{minor}"
    major, minor = parse_ios_version(args.ios or config.get("ios", DEFAULT_IOS))
    limits["ios"] = f", iOS <= {major}.{minor}"
    pythons = ", ".join(f"{a}.{b}" for a, b in PYODIDE_PLATFORMS)
    limits["pyodide"] = f", Python {pythons} (not in -t all)"
    limits["ios"] += " (wheels only; -t mobile, not in -t all)"
    api = parse_api_level(args.android_api or config.get("android-api", DEFAULT_ANDROID_API))
    limits["android"] = f", API level <= {api} (wheels only; -t mobile, not in -t all)"

    glibc = parse_version_pair(versions["manylinux"][1], "glibc")
    if glibc[0] == MANYLINUX_FLOOR[0]:  # pip's 2.17 floor outside x86 (no manylinux before manylinux2014)
        floor = ".".join(map(str, MANYLINUX_FLOOR))
        below = f", no manylinux (glibc {glibc[0]}.{glibc[1]} is below the {floor} floor)"
        floored = f", glibc {floor} to {glibc[0]}.{glibc[1]}" if glibc >= MANYLINUX_FLOOR else below
    else:
        floored = limits["manylinux"]

    def accepted(family: str, arch: str) -> str:
        limit = floored if family == "manylinux" and arch not in MANYLINUX_X86_ARCHES else limits.get(family, "")
        return f"{family} {arch}{limit}{' (not in -t all)' * (arch in EXTRA_ARCHES)}"

    rows = [(name, aliases[name], accepted(family, arch)) for name, (family, arch) in PLATFORMS.items()]
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
    print("\nDistros (--distro supplies a libc version, not a target):")
    for name, (libc, version) in DISTROS.items():
        names = ", ".join(alias for alias, canonical in DISTRO_ALIASES.items() if canonical == name)
        print(f"  {name:<18} {libc} {version}" + (f" (also {names})" if names else ""))


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
    noun = "package" if total == 1 else "packages"
    return f"{style.bold(f'{len(gaps)} of {total} {noun}')} {verb} gaps: " + ", ".join(parts) + "."


def target_lines(gaps: list[Row], targets: list[Target], style: Style) -> list[str]:
    if len(targets) < 2:
        return []

    multi_py = len({target.python for target in targets}) > 1
    lines = []
    for target in targets:
        statuses = [row.results[target] for row in gaps]
        parts = []
        if source := statuses.count(Status.SOURCE):
            parts.append(style.yellow(f"{source} from source"))
        if missing := statuses.count(Status.MISSING):
            parts.append(style.red(f"{missing} missing"))
        if unknown := statuses.count(Status.UNKNOWN):
            parts.append(style.dim(f"{unknown} unknown"))
        if parts:
            lines.append((target.label(multi_py), ", ".join(parts)))

    width = max((len(label) for label, _ in lines), default=0)
    return [f"{label:<{width}}  {counts}" for label, counts in lines]


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
    ignored: list[str],
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
    if ignored:
        out += [f"Ignored: {', '.join(map(_code, ignored))}", ""]
    for r in rows:
        if r.package.error:
            out += [f"Could not check {_md(r.package.name)}: {_code(r.package.error)}", ""]
    if args.explain and gaps:
        # Fenced, so wheel filenames and reasons need no escaping.
        lines = render_explain(gaps, targets, plain)
        out += ["<details><summary>Why (rejected wheels)</summary>", "", "```", *lines, "```", "", "</details>", ""]
    return out


def input_error_json(e: Exception) -> int:
    """With --json, also put an input error on stdout so scripts don't have to parse stderr."""
    print(json.dumps({"error": {"message": str(e), "type": type(e).__name__}}))
    return 2


def require_offline(sources: list[Source]) -> None:
    """--offline: raise SourceError if an input has packages that can only be answered by looking them up."""
    problems = []
    for s in sources:
        # A private-index package with no files is skipped by fill_all, not looked up, so it doesn't count.
        need = [p.name for p in s.packages if not p.skip and p.files is None and is_pypi(p.registry)]
        if need:
            names = ", ".join(need[:3]) + (", …" if len(need) > 3 else "")
            problems.append(
                f"{s.label} needs the index for {len(need)} package{'s' if len(need) != 1 else ''} ({names})"
            )
    if problems:
        raise SourceError(
            f"--offline: {'; '.join(problems)}; use a lockfile that lists each package's files, or drop --offline"
        )


def refuse_pyodide_url(spec: str) -> None:
    """--offline: a pyodide-lock.json URL would be downloaded, so ask for a local copy instead."""
    if "://" in spec:
        raise PyodideLockError(f"--offline: {spec} would be downloaded; save it and pass its path, or drop --offline")


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
    if args.offline and args.online:
        parser.error("--offline and --online can't be used together")
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
        glibc_version, musl_version = libc_versions(args, config)
        pythons = (
            args.python or config.get("python") or read_python_version(project_dir(args.inputs)) or [running_python()]
        )
        platforms = args.target or config.get("targets", [])
        if not platforms and pyproject:
            platforms = load_uv_targets(pyproject, pythons)
            if platforms and not quiet:
                note = (
                    f"wheelgap: note: targets from {pyproject} [tool.uv] required-environments: {', '.join(platforms)}"
                )
                if any(p.startswith("musl-") for p in platforms):
                    note += "; Linux markers select both glibc and musl targets"
                print(note, file=sys.stderr)
        targets = build_targets(
            pythons,
            platforms,
            glibc_version,
            musl_version,
            args.macos or config.get("macos", DEFAULT_MACOS),
            args.ios or config.get("ios", DEFAULT_IOS),
            args.android_api or config.get("android-api", DEFAULT_ANDROID_API),
        )
        index_url = args.index_url if args.index_url is not None else config.get("index-url")
        if index_url is not None:
            _, auth = split_credentials(index_url)
            if auth is not None and args.index_url is None:
                raise SourceError(
                    "[tool.wheelgap] index-url must not contain credentials: "
                    "pass the URL with --index-url from a CI secret instead"
                )
        else:
            check_index_url()  # WHEELGAP_INDEX_URL only matters for the JSON API
        sources = load_inputs(args.inputs or ["."])
        if args.offline:
            require_offline(sources)
        fail_on = args.fail_on or config.get("fail-on", "source")
        pyodide_lock = None
        if args.pyodide_lock:
            if args.offline:
                refuse_pyodide_url(args.pyodide_lock)
            pyodide_lock = load_pyodide_lock(args.pyodide_lock)
            check_targets(pyodide_lock, targets)  # asked for explicitly, so a mismatch is an error
        elif (lock_spec := config.get("pyodide-lock")) and any(PLATFORMS[t.platform][0] == "pyodide" for t in targets):
            # A project default: only read (or fetched) on runs that check a pyodide target, so it never
            # affects `wheelgap -t linux` in the same project. A relative path is relative to pyproject.toml.
            if pyproject and not lock_spec.lower().startswith(("http://", "https://")):
                lock_spec = str(pyproject.parent / lock_spec)
            if args.offline:
                refuse_pyodide_url(lock_spec)
            pyodide_lock = load_pyodide_lock(lock_spec)
            check_targets(pyodide_lock, targets)  # pyodide targets but none for this lock's Python: say so
    except (TargetError, SourceError, DockerfileError, PyodideLockError, OSError) as e:
        print(f"wheelgap: error: {e}", file=sys.stderr)
        return input_error_json(e) if args.json else 2
    except Exception as e:  # malformed TOML and friends
        print(f"wheelgap: error: {type(e).__name__}: {e}", file=sys.stderr)
        return input_error_json(e) if args.json else 2

    packages = [p for s in sources for p in s.packages]
    other_index = fill_all(packages, refresh=args.online, python=single_python(targets), simple_index=index_url)
    if args.online and other_index and not quiet:
        print(
            f"wheelgap: note: --online looks up PyPI packages only; kept the locked files of {other_index} "
            f"package{'s' if other_index != 1 else ''} from other indexes",
            file=sys.stderr,
        )
    for s in sources:
        if outside := pythons_outside_lock(s, targets):
            if args.offline:  # no re-check, so every locked package may lack wheels for those Pythons
                print(
                    f"wheelgap: note: {s.label} is locked for Python {s.requires_python}, so it may be missing "
                    f"wheels for Python {', '.join(outside)}; with --offline its packages weren't re-checked on the "
                    "index, so they may show false gaps for those Pythons",
                    file=sys.stderr,
                )
                continue
            if args.online:  # fill_all looked up the PyPI packages; the others kept the lock's files
                other = sum(1 for p in s.packages if keeps_locked_files(p))
                failed = 0
                if not other:
                    continue
            else:
                other, failed = recheck_lock(s.packages, simple_index=index_url)
            note = (
                f"wheelgap: note: {s.label} is locked for Python {s.requires_python}, so it may be missing "
                f"wheels for Python {', '.join(outside)}; looked its PyPI packages up on the index too"
            )
            unchecked = [f"{other} from other indexes"] * bool(other)
            unchecked += [f"{failed} that couldn't be looked up"] * bool(failed)
            if unchecked:
                note += f"; {' and '.join(unchecked)} may show false gaps for those Pythons"
            if unchecked or not quiet:  # quiet runs stay quiet unless the answer may be wrong
                print(note, file=sys.stderr)
    skipped = [p for p in packages if p.skip]
    rows = [r for r in check(sources, targets) if r.needed]
    if pyodide_lock:
        apply_bundled(rows, targets, pyodide_lock)
    rows.sort(key=lambda r: r.package.key)
    ignore_names = args.ignore or config.get("ignore", [])
    raw_ignore = [name.strip() for item in ignore_names for name in item.split(",") if name.strip()]
    ignore = {canonicalize_name(name) for name in raw_ignore}
    if raw_ignore and not quiet:
        known_keys = {p.key for p in packages}
        unmatched = list(dict.fromkeys(name for name in raw_ignore if canonicalize_name(name) not in known_keys))
        if unmatched:
            print(f"wheelgap: note: --ignore matches no package: {', '.join(unmatched)}", file=sys.stderr)
    # One entry per package, named as its first input writes it (PyYAML, not pyyaml).
    first_names = {r.package.key: r.package.name for r in reversed(rows) if r.package.key in ignore}
    ignored = sorted(first_names.values(), key=str.lower)
    rows = [r for r in rows if r.package.key not in ignore]
    gaps = [r for r in rows if not r.ok]
    source_labels = [s.label for s in sources]
    # With --fail-on missing, source builds are accepted; missing and unknown packages still fail.
    failing = gaps if fail_on == "source" else [r for r in gaps if r.fails_install]
    code = 0 if args.exit_zero or not failing else 1

    if args.json:
        print(report_json(rows, targets, source_labels, skipped, ignored, args.explain, pyodide_lock))
        return code

    if args.markdown:
        source_allowed = bool(gaps) and not failing  # judged on --fail-on alone, not --exit-zero
        print(
            "\n".join(
                render_markdown(rows, targets, source_labels, skipped, ignored, args, pyodide_lock, source_allowed)
            )
        )
        return code

    style = Style(use_color(args.color))
    if quiet:
        if gaps:
            print(gap_summary(gaps, len(rows), style))
            print("gaps: " + ", ".join(r.package.name for r in gaps))
        if ignored:
            print(style.dim("ignored: " + ", ".join(ignored)))
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

    if ignored:
        print(style.dim("ignored: " + ", ".join(ignored)))

    errors = [r for r in rows if r.package.error]
    for r in errors:
        print(style.dim(f"could not check {r.package.name}: {r.package.error}"))

    n_t = len(targets)
    if not gaps:
        what = "The 1 package has" if len(rows) == 1 else f"All {len(rows)} packages have"
        print(style.green(f"✓ {what} a wheel for {'every one of ' if n_t > 1 else ''}{n_t} target{'s' * (n_t > 1)}."))
        return 0

    for line in target_lines(gaps, targets, style):
        print(line)
    print(gap_summary(gaps, len(rows), style))
    if not args.explain:
        print(style.dim("Run with --explain to see which wheels were rejected and why."))
    if gaps and not failing and not args.exit_zero:
        print(style.dim("Source builds are allowed (--fail-on missing), so this run passes."))
    return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
