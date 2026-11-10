"""Fill in distribution filenames from the PyPI JSON API."""

from __future__ import annotations

import base64
import http.client
import json
import math
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import IO
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

from . import __version__
from .sources import Package, SourceError

DEFAULT_INDEX = "https://pypi.org/pypi"


def index_url() -> str:
    # An empty value (a CI variable that isn't set) means the default, not a relative URL.
    return (os.environ.get("WHEELGAP_INDEX_URL") or DEFAULT_INDEX).rstrip("/")


def check_index_url() -> None:
    """Fail early, with a hint, for an index URL that can't work (pip's simple-index form)."""
    base = index_url()
    if base.endswith("/simple"):
        host = urlsplit(base).hostname or "the index"
        raise SourceError(
            f"WHEELGAP_INDEX_URL points at a simple index ({host}); wheelgap needs the JSON API, "
            "e.g. https://pypi.org/pypi. To use a simple index, pass --index-url instead."
        )


def _retry_after(e: urllib.error.HTTPError) -> float | None:
    try:
        value = float(e.headers.get("Retry-After", "")) if e.headers else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) and value >= 0 else None


class RedirectRefused(urllib.error.URLError):
    """An https:// request was redirected to a URL that isn't https://."""


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to follow redirects that downgrade https:// to plain http:// (or another scheme)."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if urlparse(req.full_url).scheme == "https" and urlparse(newurl).scheme != "https":
            fp.close()
            raise RedirectRefused(f"refused redirect from {req.full_url} to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_HTTPSRedirectHandler)


def _request(req: urllib.request.Request, timeout: float, attempts: int, read):
    """Send `req`, retrying only what can succeed later: 429, 5xx and dropped connections."""
    last: Exception | None = None
    for attempt in range(attempts):
        delay = 0.5 * 2**attempt
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                return read(resp)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise  # 404, 403, 410...: asking again won't help
            last = e
            delay = _retry_after(e) or delay
        except RedirectRefused:
            raise  # asking again gets the same redirect
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as e:
            last = e  # includes connection resets and truncated responses
        if attempt + 1 < attempts:
            time.sleep(min(delay, 10.0))
    raise last  # type: ignore[misc]


def fetch_json(url: str, timeout: float = 20.0, attempts: int = 4) -> dict:
    """GET a JSON document (see _request for what is retried)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"wheelgap/{__version__}", "Accept": "application/json"})
    return _request(req, timeout, attempts, json.load)


SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"
# HTML is accepted at a token q so an HTML-only index answers with HTML (and a clear error) instead of a 406.
SIMPLE_ACCEPT = f"{SIMPLE_JSON}, text/html;q=0.01"
LOOPBACK = ("localhost", "127.0.0.1", "::1")


class IndexFormatError(Exception):
    """The index answered, but not with the JSON simple API. Carries only the host, never the URL."""

    def __init__(self, host: str):
        super().__init__(f"{host} only serves the HTML simple API; wheelgap needs the JSON form (PEP 691)")


def split_credentials(url: str) -> tuple[str, str | None]:
    """('https://user:tok@host/simple') -> ('https://host/simple', 'Basic ...').

    The secret leaves the URL here, so nothing downstream can print it.
    """
    if not url.strip():
        raise SourceError("--index-url is empty (is a CI variable unset?)")
    try:
        parts = urlsplit(url)
        host, port, user, password = parts.hostname, parts.port, parts.username, parts.password
    except ValueError:
        raise SourceError("--index-url is not a valid URL") from None  # don't echo it back
    if parts.scheme not in ("http", "https") or not host:
        raise SourceError("--index-url must be an http(s) URL")
    if parts.scheme == "http" and host not in LOOPBACK:
        raise SourceError("--index-url must use https (plain http is only allowed for localhost)")
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc += f":{port}"
    clean = urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))
    auth = None
    if user is not None or password is not None:
        raw = f"{unquote(user or '')}:{unquote(password or '')}".encode()
        auth = "Basic " + base64.b64encode(raw).decode()
    return clean, auth


def fetch_simple(url: str, auth: str | None = None, timeout: float = 20.0, attempts: int = 4) -> dict:
    """GET a PEP 691 project page. `auth` is an Authorization header value (see split_credentials)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"wheelgap/{__version__}", "Accept": SIMPLE_ACCEPT})
    if auth:
        # Sent on this request only: urllib doesn't copy unredirected headers onto a redirect,
        # so the token can't follow a redirect to another host.
        req.add_unredirected_header("Authorization", auth)
    host = urlsplit(url).hostname or "index"

    def read(resp):
        kind = resp.headers.get_content_type()
        if kind != "application/json" and not kind.endswith("+json"):
            raise IndexFormatError(host)  # text/html: PEP 503 only
        return json.load(resp)

    return _request(req, timeout, attempts, read)


def _is_yanked(entry: dict) -> bool:
    y = entry.get("yanked", False)  # PEP 592/691: true, or a reason string
    return y is True or isinstance(y, str)


def _parse_filename(filename: str):
    try:
        if filename.endswith(".whl"):
            name, version, *_ = parse_wheel_filename(filename)
        else:
            name, version = parse_sdist_filename(filename)
    except (InvalidWheelFilename, InvalidSdistFilename, InvalidVersion):
        return None  # skip files that don't parse
    return name, version


def _group_by_version(data: object, key: str) -> dict[Version, list[dict]]:
    if not isinstance(data, dict):
        raise ValueError("not an object")
    target = canonicalize_name(key)
    grouped: dict[Version, list[dict]] = {}
    for entry in _files(data.get("files")):
        parsed = _parse_filename(entry["filename"])
        if parsed and parsed[0] == target:
            grouped.setdefault(parsed[1], []).append(entry)
    return grouped


def fill_simple(pkg: Package, base: str, auth: str | None, python: str | None = None) -> Package:
    try:
        data = fetch_simple(f"{base}/{quote(pkg.key)}/", auth)
    except urllib.error.HTTPError as e:
        pkg.error = "not on index" if e.code == 404 else f"HTTP {e.code}"
        return pkg
    except IndexFormatError as e:
        pkg.error = str(e)
        return pkg
    except Exception as e:  # never put str(e) here: it can contain the URL
        pkg.error = f"lookup failed ({type(e).__name__})"
        return pkg
    try:
        grouped = _group_by_version(data, pkg.key)
        if pkg.version:  # pinned: that release's files; yanked ones are kept but marked (PEP 592)
            entries = grouped.get(Version(pkg.version))
            if not entries:
                pkg.error = f"version {pkg.version} not on index"
                return pkg
            pkg.files = [e["filename"] for e in entries]
            pkg.yanked = frozenset(e["filename"] for e in entries if _is_yanked(e))
            return pkg

        # A range, or no pin: the newest matching release that isn't fully yanked. As in fill(), a range only
        # counts files whose Requires-Python admits `python`.
        def usable(e: dict) -> bool:
            return not _is_yanked(e) and (not pkg.spec or _supports_python(e, python, "requires-python"))

        live = {v: kept for v, es in grouped.items() if (kept := [e for e in es if usable(e)])}
        chosen = max(SpecifierSet(pkg.spec or "").filter(live), default=None)
        if chosen is None:
            if pkg.spec:
                pkg.error = f"no release matches {pkg.spec}" + (f" for Python {python}" if python else "")
            else:
                pkg.error = "not on index"
            return pkg
        pkg.version = str(chosen)
        pkg.files = [e["filename"] for e in live[chosen]]
    except (ValueError, TypeError, KeyError, AttributeError):  # InvalidVersion is a ValueError
        pkg.error = "unexpected response from the index"
    return pkg


def _files(entries: object) -> list[dict]:
    if not isinstance(entries, list) or not all(
        isinstance(e, dict) and isinstance(e.get("filename"), str) for e in entries
    ):
        raise ValueError("unexpected file list")
    return entries


def _supports_python(entry: dict, python: str | None, key: str = "requires_python") -> bool:
    """Missing or invalid Requires-Python metadata imposes no restriction. PEP 691 spells the key requires-python."""
    requires = entry.get(key)
    if python is None or not isinstance(requires, str):
        return True
    try:
        # A real interpreter is on a later patch release than x.y.0 (pip compares sys.version_info[:3]),
        # so `>=3.8.1` must admit 3.8 and `!=3.9.0` must admit 3.9: compare against a late patch.
        return SpecifierSet(requires).contains(f"{python}.99")
    except InvalidSpecifier:
        return True


def _newest_matching(data: dict, spec: str, python: str | None = None) -> tuple[str, list[dict]] | None:
    """The newest matching release with non-yanked files supporting `python`, when supplied."""
    releases = data.get("releases")
    if not isinstance(releases, dict):
        # A mirror without the (deprecated) release list: fall back to the latest, if it matches.
        info, latest = data.get("info"), None
        if isinstance(info, dict):
            latest = info.get("version")
        live = [e for e in _files(data.get("urls", [])) if not e.get("yanked") and _supports_python(e, python)]
        if isinstance(latest, str) and _in_spec(latest, spec) and live:
            return latest, live
        raise ValueError("no release list")  # reported as unknown, not as "can't install"
    usable = {}
    for text, entries in releases.items():
        try:
            live = [e for e in _files(entries) if not e.get("yanked") and _supports_python(e, python)]
            if live:
                usable[Version(text)] = (text, live)
        except (InvalidVersion, ValueError):
            continue
    chosen = max(SpecifierSet(spec).filter(usable), default=None)
    return usable[chosen] if chosen is not None else None


def _in_spec(version: str, spec: str) -> bool:
    try:
        return SpecifierSet(spec).contains(version, prereleases=True)
    except (InvalidVersion, ValueError):
        return False


def _not_found(base: str, name: str, version: str | None) -> str:
    if version:
        try:  # is it the version that's missing, or the whole project?
            fetch_json(f"{base}/{name}/json")
            return f"version {version} not on index"
        except urllib.error.HTTPError as e:
            if e.code != 404:
                return f"version {version} not on index (couldn't check the project: HTTP {e.code})"
        except Exception as e:
            return f"version {version} not on index (couldn't check the project: {type(e).__name__})"
    return "not on index"


def fill(pkg: Package, python: str | None = None) -> Package:
    base = index_url()
    name = quote(pkg.key)
    pinned = bool(pkg.version)
    url = f"{base}/{name}/{quote(pkg.version or '')}/json" if pinned else f"{base}/{name}/json"
    try:
        data = fetch_json(url)
    except urllib.error.HTTPError as e:
        pkg.error = _not_found(base, name, pkg.version) if e.code == 404 else f"HTTP {e.code}"
        return pkg
    except Exception as e:  # network trouble shouldn't kill the whole report
        pkg.error = f"lookup failed ({type(e).__name__})"
        return pkg
    try:
        if not isinstance(data, dict):
            raise ValueError("not an object")
        if not pinned and pkg.spec:
            # A range (numpy>=1.26,<2): check the release an installer would pick, not the latest.
            found = _newest_matching(data, pkg.spec, python)
            if found is None:
                pkg.error = f"no release matches {pkg.spec}" + (f" for Python {python}" if python else "")
                return pkg
            pkg.version, urls = found
        else:
            urls = _files(data.get("urls", []))
            if not pinned:
                info = data.get("info")
                pkg.version = info.get("version") if isinstance(info, dict) else None
        names = [u["filename"] for u in urls]
        if pinned:
            # Keep yanked files but mark them: check.status_for uses them only if nothing else fits (PEP 592).
            pkg.yanked = frozenset(u["filename"] for u in urls if u.get("yanked"))
        else:
            names = [u["filename"] for u in urls if not u.get("yanked")]
        pkg.files = names
    except (ValueError, TypeError, KeyError, AttributeError):
        pkg.error = "unexpected response from the index"
    return pkg


PYPI_SIMPLE = "https://pypi.org/simple"


def is_pypi(registry: str | None) -> bool:
    """Whether a package pinned to `registry` comes from PyPI: unset (the default index) or exactly PYPI_SIMPLE."""
    return (registry or PYPI_SIMPLE).rstrip("/") == PYPI_SIMPLE


def keeps_locked_files(p: Package) -> bool:
    """Under --online, a package from another index keeps the files in the lock: PyPI doesn't know it."""
    return not p.skip and p.files is not None and not is_pypi(p.registry)


def recheck_lock(packages: list[Package], simple_index: str | None = None) -> tuple[int, int]:
    """Add the index's files to lockfile packages whose lock may be missing some (see pythons_outside_lock).

    Only packages the lock says came from PyPI are looked up. The lock's own files are kept, and a package
    whose lookup fails stays as the lock described it. Returns (packages from other indexes, failed lookups).
    """
    locked = [p for p in packages if not p.skip and p.files is not None and p.version]  # pinned: same release
    todo = [p for p in locked if is_pypi(p.registry)]
    saved = [p.files or [] for p in todo]
    fill_all(todo, refresh=True, simple_index=simple_index)
    failed = 0
    for p, lock in zip(todo, saved, strict=True):
        if p.error:
            p.files, p.error = lock, None
            failed += 1
        else:
            p.files = list(dict.fromkeys(lock + (p.files or [])))
    return len(locked) - len(todo), failed


def fill_all(
    packages: list[Package],
    refresh: bool = False,
    workers: int = 16,
    python: str | None = None,
    simple_index: str | None = None,
) -> int:
    """Look packages up on PyPI. A package the lock says came from another index is never looked up: PyPI doesn't
    know it. With refresh (--online) it keeps its locked files; with no files (an old poetry.lock) it is skipped as
    a private index. `python` limits range resolution to releases supporting that interpreter.
    With `simple_index` (--index-url), lookups go to that PEP 691 simple index instead of PyPI's JSON API.
    Returns how many kept their locked files for that reason."""
    fn = partial(fill, python=python)
    if simple_index is not None:
        base, auth = split_credentials(simple_index)  # validate first: a bad URL fails before any lookup
        fn = partial(fill_simple, base=base, auth=auth, python=python)
    for p in packages:
        if p.files is None and not p.skip and not is_pypi(p.registry):
            p.skip = "private index"  # the name is internal; a namesake on PyPI is another package
    kept = {id(p) for p in packages if refresh and keeps_locked_files(p)}
    todo = [p for p in packages if not p.skip and id(p) not in kept and (refresh or p.files is None)]
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(fn, todo))
    return len(kept)
