from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

# Fake PyPI: name -> {version: [filenames]}, plus the "latest" version for unpinned lookups.
FAKE_INDEX = {
    "fastlib": {"2.0.0": ["fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl", "fastlib-2.0.0.tar.gz"]},
    "unpinned": {"4.2": ["unpinned-4.2-py3-none-any.whl"]},
    "winonly": {"311": ["winonly-311-cp313-cp313-win_amd64.whl"]},
    "purewheel": {"3.1": ["purewheel-3.1-py3-none-any.whl"]},
    "nowheels": {"0.9": ["nowheels-0.9.tar.gz"]},
    "ranged": {
        "1.5": ["ranged-1.5-py3-none-any.whl"],
        "1.9": ["ranged-1.9.tar.gz"],
        "2.0": ["ranged-2.0-py3-none-any.whl"],
    },
    "pythonranged": {
        "1.0": [{"filename": "pythonranged-1.0-py3-none-any.whl", "requires_python": ">=3.8"}],
        "2.0": [{"filename": "pythonranged-2.0-py3-none-any.whl", "requires_python": ">=3.10"}],
    },
    "yanked": {"1.0": [{"filename": "yanked-1.0-py3-none-any.whl", "yanked": True}]},
    "halfyanked": {"2.0": [{"filename": "halfyanked-2.0-py3-none-any.whl", "yanked": True}, "halfyanked-2.0.tar.gz"]},
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Tests shouldn't depend on the developer's colour or index settings."""
    for name in ("NO_COLOR", "FORCE_COLOR", "WHEELGAP_INDEX_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture
def fake_pypi(monkeypatch):
    import urllib.error

    from wheelgap import pypi

    calls = []

    def fetch_json(url, timeout=20.0):
        calls.append(url)
        parts = url.removeprefix(pypi.DEFAULT_INDEX + "/").removesuffix("/json").split("/")
        name = parts[0]
        if name not in FAKE_INDEX:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        versions = FAKE_INDEX[name]
        version = parts[1] if len(parts) > 1 else next(reversed(versions))
        files = versions.get(version)
        if files is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        def entries(fs):
            return [f if isinstance(f, dict) else {"filename": f} for f in fs]

        data = {"info": {"version": version}, "urls": entries(files)}
        if len(parts) == 1:  # the project endpoint lists every release, like PyPI
            data["releases"] = {v: entries(fs) for v, fs in versions.items()}
        return data

    monkeypatch.setattr(pypi, "fetch_json", fetch_json)
    return calls


def _simple_files(name):
    """Every file of every version in one flat list, like a PEP 691 project page."""
    files = []
    for fs in FAKE_INDEX[name].values():
        for f in fs:
            entry = f if isinstance(f, dict) else {"filename": f}
            item = {
                "filename": entry["filename"],
                "url": f"https://files.example/{entry['filename']}",
                "hashes": {},
            }
            if entry.get("yanked"):
                item["yanked"] = True  # PEP 592: a bool or a reason string
            if "requires_python" in entry:
                item["requires-python"] = entry["requires_python"]  # PEP 691 spelling
            files.append(item)
    return files


class FakeSimple:
    """A fake PEP 691 index: .calls records requests, .html_only makes it serve only PEP 503 HTML."""

    def __init__(self):
        self.calls = []  # (url, auth) pairs
        self.html_only = False


@pytest.fixture
def fake_simple(monkeypatch):
    import urllib.error
    from urllib.parse import urlsplit

    from wheelgap import pypi

    index = FakeSimple()

    def fetch_simple(url, auth=None, timeout=20.0):
        # Credentials must be split off before the fetch layer (they go in `auth`).
        assert "@" not in urlsplit(url).netloc, "credentials leaked into the request URL"
        index.calls.append((url, auth))
        if index.html_only:
            raise pypi.IndexFormatError(urlsplit(url).hostname or "index")
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if name not in FAKE_INDEX:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return {"meta": {"api-version": "1.0"}, "name": name, "files": _simple_files(name)}

    monkeypatch.setattr(pypi, "fetch_simple", fetch_simple)
    return index
