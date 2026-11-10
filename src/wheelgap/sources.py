"""Read packages (and, where the lockfile records them, their distribution files) from inputs."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

if TYPE_CHECKING:
    from .targets import Target

LOCKFILES = (
    "uv.lock",
    "pylock.toml",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "requirements.txt",
    "requirements.lock",
)


class SourceError(ValueError):
    pass


@dataclass
class Package:
    name: str
    version: str | None
    # Distribution filenames. None means "unknown, ask the index".
    files: list[str] | None = None
    # Set for packages that don't come from an index (editable, git, path, ...).
    skip: str | None = None
    error: str | None = None
    origin: str = ""
    # Environment markers; the package is installed if any of them is true. None means always.
    markers: list[str] | None = None
    # A version range that isn't an exact pin (numpy>=1.26,<2): the index picks the newest match.
    spec: str | None = None
    # Files of a pinned release that the index marks as yanked (see check.status_for).
    yanked: frozenset[str] = frozenset()
    # uv.lock / pylock.toml / poetry.lock: the index the lock says the package came from, e.g. https://pypi.org/simple.
    registry: str | None = None

    @property
    def key(self) -> str:
        return canonicalize_name(self.name)


# A dependency edge in a lockfile graph: (name, version or None, marker or None)
Edge = tuple[str, str | None, str | None]


@dataclass
class Source:
    label: str
    packages: list[Package] = field(default_factory=list)
    # uv.lock only: which packages each package depends on, and where the walk starts.
    # When set, a package applies to a target only if it's reachable along edges whose markers hold.
    edges: dict[int, list[Edge]] | None = None
    roots: list[int] = field(default_factory=list)
    root_edges: list[Edge] = field(default_factory=list)
    # Index into root_edges -> name of the dependency group it comes from, e.g. {0: "dev"}.
    root_groups: dict[int, str] = field(default_factory=dict)
    # uv.lock only: the project's requires-python. uv keeps only the wheels that can serve it.
    requires_python: str | None = None


def pythons_outside_lock(source: Source, targets: Iterable[Target]) -> list[str]:
    """Target Pythons the lockfile's requires-python leaves out, e.g. ['3.14t'] for `==3.13.*`.

    uv keeps only the wheels that can serve requires-python (abi3 and pure wheels survive), so the lock may
    be missing wheels for those Pythons. Like uv, only the bounds count: `!=3.12.*` doesn't exclude 3.12.
    """
    if not source.requires_python:
        return []
    try:
        spec = SpecifierSet(",".join(str(s) for s in SpecifierSet(source.requires_python) if s.operator != "!="))
    except InvalidSpecifier:
        return []
    out = []
    for t in targets:
        # A minor is served if any of its releases fits: its first and last patch, or a version the spec names.
        tries = [Version(f"{t.major}.{t.minor}.0"), Version(f"{t.major}.{t.minor}.999")]
        for s in spec:
            try:
                v = Version(s.version.removesuffix(".*"))
            except ValueError:
                continue
            if v.release[:2] == (t.major, t.minor):
                tries.append(v)
        if not any(spec.contains(v, prereleases=True) for v in tries):
            out.append(t.python)
    return list(dict.fromkeys(out))


def _filename(entry: dict) -> str | None:
    for k in ("filename", "file", "name"):
        if isinstance(entry.get(k), str) and entry[k].endswith((".whl", ".tar.gz", ".zip", ".tar.bz2", ".tgz")):
            return entry[k]
    url = entry.get("url")
    if isinstance(url, str):
        return unquote(Path(urlparse(url).path).name)
    path = entry.get("path")
    if isinstance(path, str):
        return Path(path).name
    return None


def _uv_edges(p: dict) -> list[Edge]:
    groups = [p.get("dependencies", [])]
    for table in ("optional-dependencies", "dev-dependencies"):
        groups.extend(p.get(table, {}).values())
    return [(d["name"], d.get("version"), d.get("marker")) for g in groups for d in g]


def parse_uv_lock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    manifest = data.get("manifest", {})
    members = {canonicalize_name(m) for m in manifest.get("members", [])}
    edges: dict[int, list[Edge]] = {}
    source = Source(path.name, edges=edges)
    for p in data.get("package", []):
        src = p.get("source", {})
        pkg = Package(p["name"], p.get("version"), origin=path.name)
        kind = next((k for k in ("editable", "virtual", "git", "path", "directory", "url") if k in src), None)
        if kind and "registry" not in src:
            pkg.skip = kind
        else:
            pkg.registry = src.get("registry")
            files = [f for f in (_filename(w) for w in p.get("wheels", [])) if f]
            if p.get("sdist"):
                files.append(_filename(p["sdist"]) or f"{p['name']}-{p.get('version')}.tar.gz")
            pkg.files = files
        i = len(source.packages)
        source.packages.append(pkg)
        edges[i] = _uv_edges(p)
        # Roots are the project itself (source "."), plus workspace members. A path dependency elsewhere
        # (directory = "libs/x") is reached through its dependents' edges and their markers instead.
        if (kind in ("editable", "virtual") and src.get(kind) == ".") or pkg.key in members:
            source.roots.append(i)
    for name, group in manifest.get("dependency-groups", {}).items():
        for d in group:
            source.root_groups[len(source.root_edges)] = name
            source.root_edges.append((d["name"], None, d.get("marker")))
    source.root_edges.extend((d["name"], None, d.get("marker")) for d in manifest.get("requirements", []))
    if not source.roots and not source.root_edges:
        source.edges = None  # no idea where the graph starts; treat every package as needed
    if isinstance(data.get("requires-python"), str):
        source.requires_python = data["requires-python"]
    return source


def parse_pylock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    pkgs = []
    for p in data.get("packages", []):
        pkg = Package(p["name"], p.get("version"), origin=path.name, registry=p.get("index"))
        if p.get("marker"):
            pkg.markers = [p["marker"]]
        if any(k in p for k in ("vcs", "directory", "archive")) and "wheels" not in p and "sdist" not in p:
            pkg.skip = next(k for k in ("vcs", "directory", "archive") if k in p)
        else:
            files = [f for f in (_filename(w) for w in p.get("wheels", [])) if f]
            if p.get("sdist"):
                files.append(_filename(p["sdist"]) or f"{p['name']}-{p.get('version')}.tar.gz")
            pkg.files = files
        pkgs.append(pkg)
    requires = data.get("requires-python")
    return Source(path.name, pkgs, requires_python=requires if isinstance(requires, str) else None)


def _poetry_edges(p: dict) -> list[Edge]:
    """A poetry.lock package's dependencies as edges. A value is a version string, a table, or a list of
    tables for different markers (numpy = [{version = ">=2", markers = "python_version >= '3.10'"}, ...])."""
    out: list[Edge] = []
    for name, spec in p.get("dependencies", {}).items():
        for s in spec if isinstance(spec, list) else [spec]:
            version = s.get("version") if isinstance(s, dict) else s
            marker = s.get("markers") if isinstance(s, dict) else None
            out.append((name, version if isinstance(version, str) and version != "*" else None, marker))
    return out


def parse_poetry_lock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    legacy_files = metadata.get("files", {})
    pkgs: list[Package] = []
    edges: dict[int, list[Edge]] = {}
    for p in data.get("package", []):
        pkg = Package(p["name"], p.get("version"), origin=path.name)
        markers = p.get("markers")
        if isinstance(markers, str):
            pkg.markers = [markers]
        elif isinstance(markers, dict):
            # Poetry leaves a group out of the dict when the package is always installed for it.
            always = any(g not in markers for g in p.get("groups", []))
            pkg.markers = None if always else ([m for m in markers.values() if isinstance(m, str)] or None)
        src_type = p.get("source", {}).get("type")
        if src_type in ("git", "hg", "svn", "bzr", "directory", "file", "url"):
            pkg.skip = src_type
        else:
            # A "legacy" source is a simple index: remember it so the package isn't looked up on PyPI.
            url = p.get("source", {}).get("url")
            pkg.registry = url if isinstance(url, str) else None
            entries = p.get("files")
            if entries is None:
                entries = legacy_files.get(p["name"])
            # No files recorded at all (old [metadata.hashes] locks): PyPI packages are looked up on the index,
            # packages from another index are skipped (fill_all).
            pkg.files = None if entries is None else [f for f in (_filename(e) for e in entries) if f]
        edges[len(pkgs)] = _poetry_edges(p)
        pkgs.append(pkg)
    # Lock files before 2.1 (all of Poetry 1.x) only record markers on dependency edges, not on
    # packages, so walk the graph from the packages nothing depends on, as for uv.lock.
    if _version_tuple(str(metadata.get("lock-version", "2.1"))) < (2, 1) and pkgs:
        roots = set(_graph_roots(pkgs, edges))
        # The project's own dependencies are roots too, even if another package also depends on them
        # under a marker (colorama directly, and via click only on Windows).
        declared = _project_dependencies(path.parent / "pyproject.toml")
        roots |= {i for i, p in enumerate(pkgs) if p.key in declared}
        return Source(path.name, pkgs, edges=edges, roots=sorted(roots))
    return Source(path.name, pkgs)


def _graph_roots(pkgs: list[Package], edges: dict[int, list[Edge]]) -> list[int]:
    """Where to start walking a lock that doesn't record its project: every package in a group of
    packages (a strongly connected component, usually just one package) that nothing outside the group
    depends on. That covers dependency cycles such as sphinx <-> sphinxcontrib-*, which no package
    "depended on by nothing" would reach."""
    by_name: dict[str, list[int]] = {}
    for i, p in enumerate(pkgs):
        by_name.setdefault(p.key, []).append(i)
    succ = {
        i: sorted({j for name, _, _ in edges.get(i, []) for j in by_name.get(canonicalize_name(name), [])})
        for i in range(len(pkgs))
    }
    comp = _components(len(pkgs), succ)
    has_outside_parent = {comp[j] for i in succ for j in succ[i] if comp[i] != comp[j]}
    return [i for i in range(len(pkgs)) if comp[i] not in has_outside_parent]


def _components(n: int, succ: dict[int, list[int]]) -> list[int]:
    """Tarjan's strongly connected components, iteratively (lockfiles can be deep)."""
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    comp = [-1] * n
    stack: list[int] = []
    on_stack: set[int] = set()
    counter = count = 0
    for root in range(n):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, child = work.pop()
            if child == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            if child < len(succ[v]):
                work.append((v, child + 1))
                w = succ[v][child]
                if w not in index:
                    work.append((w, 0))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            if low[v] == index[v]:
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp[w] = count
                    if w == v:
                        break
                count += 1
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return comp


def _project_dependencies(pyproject: Path) -> set[str]:
    """Names a project declares itself (PEP 621 and Poetry tables), to use as roots for poetry.lock.
    A missing or odd pyproject.toml just means no extra roots: it must never break the run."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return set()

    def table(value: object) -> dict:
        return value if isinstance(value, dict) else {}

    def strings(value: object) -> list[str]:
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    project = table(data.get("project"))
    specs = strings(project.get("dependencies"))
    for extra in table(project.get("optional-dependencies")).values():
        specs += strings(extra)
    for group in table(data.get("dependency-groups")).values():
        specs += strings(group)
    names: set[str] = set()
    for spec in specs:
        try:
            names.add(canonicalize_name(Requirement(spec).name))
        except InvalidRequirement:
            continue
    poetry = table(table(data.get("tool")).get("poetry"))
    tables = [table(poetry.get("dependencies")), table(poetry.get("dev-dependencies"))]
    tables += [table(table(g).get("dependencies")) for g in table(poetry.get("group")).values()]
    names |= {canonicalize_name(name) for t in tables for name in t if name.lower() != "python"}
    return names


def _version_tuple(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in text.split("."))
    except ValueError:
        return (0,)


def _add_once(pkgs: list[Package], seen: dict[tuple[str, str | None], Package], pkg: Package) -> None:
    """Append `pkg` unless the same name and version is already there; then widen that entry's markers.

    Pipenv lists a package used by several categories (default, develop, docs...) in each of them, and PDM
    writes one entry per extras variant (`coverage` and `coverage[toml]`). Either way it's one install,
    needed if any entry is.
    """
    key = (pkg.key, pkg.version)
    first = seen.get(key)
    if first is None:
        seen[key] = pkg
        pkgs.append(pkg)
        return
    if pkg.skip == "private index":
        first.skip = pkg.skip  # Pipenv may pin the index in one category only: keep the name off pypi.org anyway
    if first.markers is not None:
        first.markers = None if pkg.markers is None else first.markers + pkg.markers


def parse_pdm_lock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    legacy_files = data.get("metadata", {}).get("files", {})
    pkgs: list[Package] = []
    seen: dict[tuple[str, str | None], Package] = {}
    for p in data.get("package", []):
        pkg = Package(p["name"], p.get("version"), origin=path.name)
        if p.get("marker"):
            pkg.markers = [p["marker"]]
        src_type = next((k for k in ("git", "hg", "svn", "bzr", "path", "url", "file") if k in p), None)
        if src_type:
            pkg.skip = src_type
        else:
            entries = p.get("files")
            if entries is None:
                # Older lockfiles (PDM before 2.8) keep files in one [metadata.files] table
                # keyed by "name version", with the name as PDM wrote it, which isn't always
                # normalized ("Jinja2 3.1.2" for jinja2): try the exact key, then match names.
                entries = legacy_files.get(f"{p['name']} {p.get('version')}")
                if entries is None:
                    target_name = canonicalize_name(p["name"])
                    target_version = p.get("version")
                    for key, value in legacy_files.items():
                        key_name, _, key_version = key.rpartition(" ")
                        if key_version == target_version and canonicalize_name(key_name) == target_name:
                            entries = value
                            break
            if entries is not None:
                pkg.files = [f for f in (_filename(e) for e in entries) if f]
            # Not in either place: leave files as None so the PyPI lookup fills them in.
        _add_once(pkgs, seen, pkg)
    return Source(path.name, pkgs)


def parse_pipfile_lock(path: Path) -> Source:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SourceError(f"{path.name}: not a Pipfile.lock (expected a JSON object)")
    pkgs: list[Package] = []
    seen: dict[tuple[str, str | None], Package] = {}
    for key, val in data.items():
        if key != "_meta" and not isinstance(val, dict):
            raise SourceError(f"{path.name}: {key} isn't a package section")
    sections = [s for s in ("default", "develop") if s in data]
    sections += [key for key in data if key not in ("_meta", "default", "develop")]
    meta = data.get("_meta")
    sources = meta.get("sources") if isinstance(meta, dict) else None
    # Pipenv writes "index": "<source name>" for a package pinned to one of the Pipfile's [[source]] entries.
    source_urls = {
        s["name"]: s["url"]
        for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("url"), str)
    }
    for section in sections:
        for name, p in data[section].items():
            if not isinstance(p, dict):
                raise SourceError(f"{path.name}: {section}.{name} isn't a package entry")
            version = p.get("version")
            if version is not None and not isinstance(version, str):
                raise SourceError(f"{path.name}: {section}.{name} has a version that isn't a string")
            pkg = Package(name, version.removeprefix("==") if version else None, origin=path.name)
            if p.get("markers"):
                pkg.markers = [p["markers"]]
            pkg.skip = next((kind for kind in ("git", "hg", "svn", "bzr", "path", "file") if kind in p), None)
            if pkg.skip is None and p.get("editable"):
                pkg.skip = "editable"
            index = p.get("index")
            if pkg.skip is None and index is not None and not _is_public_pypi(source_urls.get(str(index))):
                pkg.skip = "private index"  # don't send internal names to pypi.org, or check a namesake there
            _add_once(pkgs, seen, pkg)
    return Source(path.name, pkgs)


def _is_public_pypi(url: str | None) -> bool:
    try:
        return url is not None and urlparse(url).hostname in ("pypi.org", "pypi.python.org")
    except ValueError:  # e.g. "https://[bad/simple": not something we can call PyPI
        return False


def _pinned_version(req: Requirement) -> str | None:
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        return specs[0].version
    return None


def requirement_to_package(text: str, origin: str) -> Package:
    try:
        req = Requirement(text)
    except InvalidRequirement as e:
        raise SourceError(f"{origin}: can't parse requirement {text!r}: {e}") from None
    markers = [str(req.marker)] if req.marker else None
    if req.url:
        return Package(req.name, None, skip="url", origin=origin, markers=markers)
    version = _pinned_version(req)
    spec = str(req.specifier) if req.specifier and version is None else None
    return Package(req.name, version, origin=origin, markers=markers, spec=spec)


# "name @ url", "name@url" and "name[extra] @ url": PEP 508 direct references, which Requirement parses.
# A bare URL can't match: its scheme ("git+ssh:") has characters a package name can't.
_NAMED_URL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?\s*@")
_EGG_RE = re.compile(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)")
_COMMENT_RE = re.compile(r"\s#")
_INCLUDE_RE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)?(\S.*)$")


def parse_requirements(path: Path, _seen: set[Path] | None = None) -> Source:
    seen = _seen if _seen is not None else set()
    path = path.resolve()
    if path in seen:
        return Source(path.name)
    seen.add(path)
    pkgs: list[Package] = []
    lines = path.read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
    for raw in lines:
        line = _COMMENT_RE.split(raw, 1)[0].strip()  # " # note" or "<TAB># note"
        if not line or line.startswith("#"):
            continue
        if m := _INCLUDE_RE.match(line):  # -r file, -rfile, --requirement file, --requirement=file
            pkgs.extend(parse_requirements(path.parent / m.group(1).strip(), seen).packages)
            continue
        if line.startswith("-e ") or line.startswith("--editable"):
            name = line.split("#egg=", 1)[1] if "#egg=" in line else line.split(None, 1)[-1]
            pkgs.append(Package(name, None, skip="editable", origin=path.name))
            continue
        if line.startswith("-"):
            continue  # pip options: --index-url, -c, --hash continuation, ...
        tokens = line.split()
        # Drop --hash=sha256:... and "--hash sha256:..." (option and value as two tokens).
        line = " ".join(
            t for k, t in enumerate(tokens) if not t.startswith("--hash") and (k == 0 or tokens[k - 1] != "--hash")
        )
        if line.startswith((".", "/")):
            pkgs.append(Package(line, None, skip="path", origin=path.name))
            continue
        if "://" in line and not _NAMED_URL_RE.match(line):
            # A bare URL (git+ssh://git@github.com/org/repo.git#egg=foo): never from the index.
            # "name @ url" lines are left to Requirement, which knows the name and markers.
            egg = _EGG_RE.search(line)
            name = egg.group(1) if egg else line.split(";", 1)[0].strip()
            pkgs.append(Package(name, None, skip="url", origin=path.name))
            continue
        pkgs.append(requirement_to_package(line, path.name))
    return Source(path.name, pkgs)


PARSERS = {
    "uv.lock": parse_uv_lock,
    "pylock.toml": parse_pylock,
    "poetry.lock": parse_poetry_lock,
    "pdm.lock": parse_pdm_lock,
    "Pipfile.lock": parse_pipfile_lock,
}


def load_file(path: Path) -> Source:
    name = path.name
    if name in PARSERS:
        return PARSERS[name](path)
    if name.startswith("pylock.") and name.endswith(".toml"):
        return parse_pylock(path)
    if name.startswith("requirements") and name.endswith(".lock"):
        return parse_requirements(path)
    if name.endswith(".lock") and "uv" in name:
        return parse_uv_lock(path)
    if name.endswith((".txt", ".in")):
        return parse_requirements(path)
    raise SourceError(
        f"don't know how to read {path} "
        "(expected uv.lock, pylock.toml, poetry.lock, pdm.lock, Pipfile.lock, requirements.lock or requirements.txt)"
    )


def find_lockfile(directory: Path) -> Path:
    for name in LOCKFILES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    raise SourceError(
        f"no lockfile found in {directory} (looked for {', '.join(LOCKFILES)}). "
        "Pass a file, or package names like `wheelgap numpy psycopg2`."
    )


def load_inputs(inputs: list[str]) -> list[Source]:
    """Each input is a file, a directory (lockfile auto-detected) or a requirement like 'numpy==2.3.4'."""
    sources: list[Source] = []
    adhoc = Source("command line")
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            sources.append(load_file(find_lockfile(p)))
        elif p.is_file():
            sources.append(load_file(p))
        elif "/" in item or item.endswith((".lock", ".txt", ".toml")):
            raise SourceError(f"{item}: no such file")
        else:
            adhoc.packages.append(requirement_to_package(item, "command line"))
    if adhoc.packages:
        sources.append(adhoc)
    return sources
