"""Scan the most-downloaded PyPI packages across every target: the data behind the tracker.

    python -m wheelgap.scan --top 5000 --out coverage.json

Each package's latest release is checked against every native platform, plus Pyodide where
there's a Pyodide build of that Python. The output is one JSON document (schema below) with
per-target totals, so a website or a later scan can diff it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .api import Result, check_packages
from .check import Status
from .pypi import fetch_json
from .targets import NATIVE_PLATFORMS, PYODIDE_PLATFORMS, Target, TargetError, build_targets, parse_python

TOP_PACKAGES_URL = "https://hugovk.dev/top-pypi-packages/top-pypi-packages.min.json"
DEFAULT_PYTHONS = "3.13,3.14,3.14t,3.15,3.15t"
SCHEMA_VERSION = 1


def scan_targets(pythons: str) -> list[Target]:
    """Every native platform for each Python, plus Pyodide where that Python has a Pyodide build."""
    targets: list[Target] = []
    for spec in (p for p in pythons.split(",") if p.strip()):
        major, minor, ft = parse_python(spec)
        platforms = list(NATIVE_PLATFORMS)
        if not ft and (major, minor) in PYODIDE_PLATFORMS:
            platforms.append("pyodide")
        targets += build_targets([spec], platforms)
    return list(dict.fromkeys(targets))


def load_top(source: str, limit: int) -> tuple[list[dict], str | None]:
    """The top `limit` rows ({project, download_count}) from hugovk's top-pypi-packages data."""
    if source.startswith(("http://", "https://")):
        data = fetch_json(source)
    else:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
    return data["rows"][:limit], data.get("last_update")


def summarise(results: list[Result], targets: list[Target]) -> dict[str, dict[str, int]]:
    summary = {}
    for t in targets:
        counts = Counter(r.statuses[t].value for r in results)
        summary[t.label(True)] = {
            s.value: counts.get(s.value, 0) for s in (Status.WHEEL, Status.SOURCE, Status.MISSING, Status.UNKNOWN)
        }
    return summary


def scan(rows: list[dict], targets: list[Target], workers: int = 16) -> list[Result]:
    return check_packages([r["project"] for r in rows], targets, workers=workers)


def report(
    rows: list[dict], results: list[Result], targets: list[Target], source: str, last_update: str | None
) -> dict:
    """The tracker's data. `results` in each package lines up with the top-level `targets` list."""
    return {
        "schema": SCHEMA_VERSION,
        "generated": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "wheelgap": __version__,
        "top_packages": {"source": source, "last_update": last_update},
        "targets": [
            {
                "label": t.label(True),
                "python": t.python,
                "platform": t.platform,
                # The limits a wheel had to meet, so the numbers keep their meaning if defaults change.
                "glibc": ".".join(map(str, t.glibc)),
                "musl": ".".join(map(str, t.musl)),
                "macos": ".".join(map(str, t.macos)),
            }
            for t in targets
        ],
        "lookup_errors": sum(1 for r in results if r.error),
        "summary": summarise(results, targets),
        "packages": [
            {
                "rank": rank,
                "name": r.name,
                "version": r.version,
                "downloads": row.get("download_count"),
                "pure": r.pure,
                "error": r.error,
                "results": [r.statuses[t].value for t in targets],
            }
            for rank, (row, r) in enumerate(zip(rows, results, strict=True), start=1)
        ],
    }


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m wheelgap.scan", description=__doc__.splitlines()[0])
    p.add_argument(
        "--top", type=positive_int, default=5000, metavar="N", help="how many packages to scan (default: 5000)"
    )
    p.add_argument("--python", default=DEFAULT_PYTHONS, metavar="VERS", help=f"default: {DEFAULT_PYTHONS}")
    p.add_argument("--top-packages", default=TOP_PACKAGES_URL, metavar="URL_OR_PATH", help="top-pypi-packages JSON")
    p.add_argument("--workers", type=positive_int, default=16, help="parallel index lookups (default: 16)")
    p.add_argument("--out", metavar="PATH", help="write here instead of stdout")
    p.add_argument(
        "--max-error-rate",
        type=float,
        default=0.05,
        metavar="FRACTION",
        help="exit 1 without writing if more than this share of lookups fail (default: 0.05)",
    )
    args = p.parse_args(argv)

    started = time.monotonic()
    try:
        targets = scan_targets(args.python)
    except TargetError as error:
        p.error(f"argument --python: {error}")
    if not targets:
        p.error("argument --python: provide at least one Python version")
    rows, last_update = load_top(args.top_packages, args.top)
    results = scan(rows, targets, args.workers)
    data = report(rows, results, targets, args.top_packages, last_update)

    errors = data["lookup_errors"]
    took = time.monotonic() - started
    print(
        f"scanned {len(results)} packages x {len(targets)} targets in {took:.0f}s ({errors} lookup errors)",
        file=sys.stderr,
    )
    if results and errors > args.max_error_rate * len(results):
        # A PyPI outage would otherwise publish a dataset full of "unknown"; keep the last good one instead.
        print(f"too many lookup errors ({errors} of {len(results)}); not writing the results", file=sys.stderr)
        return 1
    text = json.dumps(data, separators=(",", ":"))
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
