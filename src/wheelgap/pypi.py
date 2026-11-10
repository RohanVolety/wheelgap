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
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from packaging.specifiers import SpecifierSet
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


def _request(req: urllib.request.Request, timeout: float, attempts: int, read):
    """Send `req`, retrying only what can succeed later: 429, 5xx and dropped connections."""
    last: Exception | None = None
    for attempt in range(attempts):
        delay = 0.5 * 2**attempt
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return read(resp)
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise  # 404, 403, 410...: asking again won't help
            last = e
            delay = _retry_after(e) or delay
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException, ConnectionError) as e:
            last = e  # includes connection resets and truncated responses
        if attempt + 1 < attempts:
            time.sleep(min(delay, 10.0))
    raise last  # type: ignore[misc]


def fetch_json(url: str, timeout: float = 20.0, attempts: int = 4) -> dict:
    """GET a JSON document (see _request for what is retried)."""
    req = urllib.request.Request(url, headers={"User-Agent": f"wheelgap/{__version__}", "Accept": "application/json"})
    return _request(req, timeout, attempts, json.load)


SIMPLE_ACCEPT = "application/vnd.pypi.simple.v1+json"


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


def fill_simple(pkg: Package, base: str, auth: str | None) -> Package:
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
        # A range, or no pin: the newest matching release that isn't fully yanked.
        live = {v: es for v, es in grouped.items() if any(not _is_yanked(e) for e in es)}
        chosen = max(SpecifierSet(pkg.spec or "").filter(live), default=None)
        if chosen is None:
            pkg.error = f"no release matches {pkg.spec}" if pkg.spec else "not on index"
            return pkg
        pkg.version = str(chosen)
        pkg.files = [e["filename"] for e in live[chosen] if not _is_yanked(e)]
    except (ValueError, TypeError, KeyError, AttributeError):  # InvalidVersion is a ValueError
        pkg.error = "unexpected response from the index"
    return pkg


def _files(entries: object) -> list[dict]:
    if not isinstance(entries, list) or not all(
        isinstance(e, dict) and isinstance(e.get("filename"), str) for e in entries
    ):
        raise ValueError("unexpected file list")
    return entries


def _newest_matching(data: dict, spec: str) -> tuple[str, list[dict]] | None:
    """The newest release that matches `spec` and has files that aren't yanked."""
    releases = data.get("releases")
    if not isinstance(releases, dict):
        # A mirror without the (deprecated) release list: fall back to the latest, if it matches.
        info, latest = data.get("info"), None
        if isinstance(info, dict):
            latest = info.get("version")
        live = [e for e in _files(data.get("urls", [])) if not e.get("yanked")]
        if isinstance(latest, str) and _in_spec(latest, spec) and live:
            return latest, live
        raise ValueError("no release list")  # reported as unknown, not as "can't install"
    usable = {}
    for text, entries in releases.items():
        try:
            live = [e for e in _files(entries) if not e.get("yanked")]
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


def fill(pkg: Package) -> Package:
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
            found = _newest_matching(data, pkg.spec)
            if found is None:
                pkg.error = f"no release matches {pkg.spec}"
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


def fill_all(
    packages: list[Package], refresh: bool = False, workers: int = 16, simple_index: str | None = None
) -> None:
    fn = fill
    if simple_index is not None:
        base, auth = split_credentials(simple_index)
        fn = partial(fill_simple, base=base, auth=auth)
    todo = [p for p in packages if not p.skip and (refresh or p.files is None)]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fn, todo))
