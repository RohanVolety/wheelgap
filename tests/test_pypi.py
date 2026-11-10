import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

import wheelgap
from wheelgap import Status, pypi
from wheelgap.cli import main
from wheelgap.pyodide import PyodideLockError, load_pyodide_lock
from wheelgap.sources import Package, SourceError, load_inputs


@pytest.mark.parametrize("python,expected", [("3.9", "1.0"), ("3.13", "2.0"), (None, "2.0")])
def test_range_requires_python(fake_pypi, python, expected):
    pkg = pypi.fill(Package("pythonranged", None, spec=">=1,<3"), python=python)
    assert pkg.error is None and pkg.version == expected
    assert pkg.files == [f"pythonranged-{expected}-py3-none-any.whl"]
    assert fake_pypi == ["https://pypi.org/pypi/pythonranged/json"]


@pytest.mark.parametrize(
    "python,expected", [("3.9", "1.0"), ("3.13", "2.0"), ("3.13,3.13t", "2.0"), ("3.9,3.13", "2.0")]
)
def test_range_requires_python_cli(fake_pypi, capsys, tmp_path, monkeypatch, python, expected):
    monkeypatch.chdir(tmp_path)
    assert main(["pythonranged>=1,<3", "-p", python, "-t", "linux,windows", "--json"]) == 0
    output = capsys.readouterr()
    assert not output.err
    (package,) = json.loads(output.out)["packages"]
    assert package["version"] == expected
    assert set(package["results"].values()) == {"wheel"}


@pytest.mark.parametrize("python,expected", [("3.9", "1.0"), ("3.13", "2.0"), ("3.9,3.13", "2.0")])
def test_range_requires_python_lockfile_api(fake_pypi, tmp_path, python, expected):
    path = tmp_path / "requirements.txt"
    path.write_text("pythonranged>=1,<3\n", encoding="utf-8")
    targets = wheelgap.make_targets(python=python, platforms="linux,windows")
    (result,) = wheelgap.check_lockfile(path, targets)
    assert result.version == expected and result.ok
    assert fake_pypi == ["https://pypi.org/pypi/pythonranged/json"]


@pytest.mark.parametrize("requires_python", [None, "", "not a specifier", 42])
def test_range_requires_python_missing_or_invalid(monkeypatch, requires_python):
    entry = {"filename": "x-2-py3-none-any.whl", "requires_python": requires_python}
    monkeypatch.setattr(pypi, "fetch_json", lambda url: {"releases": {"2": [entry]}})
    pkg = pypi.fill(Package("x", None, spec=">=1"), python="3.9")
    assert pkg.version == "2" and not pkg.error


@pytest.mark.parametrize(
    "requires_python,python,ok",
    [(">=3.8.1", "3.8", True), ("!=3.9.0", "3.9", True), ("<3.9.5", "3.9", False), (">=3.10", "3.9", False)],
)
def test_range_requires_python_patch_level_specifiers(monkeypatch, requires_python, python, ok):
    # A target "3.8" stands for a current 3.8.x interpreter, not 3.8.0 (flake8 6 requires >=3.8.1).
    entry = {"filename": "x-2-py3-none-any.whl", "requires_python": requires_python}
    monkeypatch.setattr(pypi, "fetch_json", lambda url: {"releases": {"2": [entry]}})
    pkg = pypi.fill(Package("x", None, spec=">=1"), python=python)
    assert (pkg.version == "2") is ok


@pytest.mark.parametrize("yanked,expected", [(False, "2"), (True, "1")])
def test_range_requires_python_mixed_files(monkeypatch, yanked, expected):
    older = {"filename": "x-1-py3-none-any.whl", "requires_python": ">=3.8"}
    newer = [
        {"filename": "x-2-py3-none-any.whl", "requires_python": ">=3.10"},
        {"filename": "x-2.tar.gz", "requires_python": ">=3.8", "yanked": yanked},
    ]
    monkeypatch.setattr(pypi, "fetch_json", lambda url: {"releases": {"1": [older], "2": newer}})
    pkg = pypi.fill(Package("x", None, spec=">=1"), python="3.9")
    assert pkg.version == expected and not pkg.error
    assert pkg.files == (["x-2.tar.gz"] if expected == "2" else [older["filename"]])


def test_range_requires_python_no_compatible_release(fake_pypi):
    pkg = pypi.fill(Package("pythonranged", None, spec=">=2"), python="3.9")
    assert pkg.version is None and pkg.files is None
    assert pkg.error == "no release matches >=2 for Python 3.9"


@pytest.mark.parametrize("python,expected", [("3.9", None), ("3.13", "2.0")])
def test_range_requires_python_mirror_fallback(monkeypatch, python, expected):
    data = {"info": {"version": "2.0"}, "urls": [{"filename": "x-2.0.tar.gz", "requires_python": ">=3.10"}]}
    monkeypatch.setattr(pypi, "fetch_json", lambda url: data)
    pkg = pypi.fill(Package("x", None, spec=">=1"), python=python)
    assert pkg.version == expected
    if expected is None:
        assert pkg.error == "unexpected response from the index" and pkg.files is None
    else:
        assert not pkg.error and pkg.files == ["x-2.0.tar.gz"]


@pytest.mark.parametrize("version", [None, "2.0"])
def test_range_requires_python_keeps_bare_names_and_pins_unchanged(fake_pypi, version):
    pkg = pypi.fill(Package("pythonranged", version), python="3.9")
    assert pkg.version == "2.0" and not pkg.error


@pytest.mark.parametrize("python,expected", [("3.9", "3.9"), ("3.13,3.13t", "3.13"), ("3.9,3.13", None)])
def test_range_requires_python_package_api_forwarding(fake_pypi, monkeypatch, python, expected):
    from wheelgap import api

    calls = []

    def fill_all(packages, **kwargs):
        calls.append(kwargs)
        return pypi.fill_all(packages, **kwargs)

    monkeypatch.setattr(api, "fill_all", fill_all)
    targets = wheelgap.make_targets(python=python, platforms="linux,windows")
    (result,) = wheelgap.check_packages(["purewheel"], targets, workers=2)
    assert result.ok
    assert calls == [{"workers": 2, "python": expected}]


def test_version_range_checks_the_newest_matching_release(fake_pypi, tmp_path):
    # ranged has 1.5 (wheel), 1.9 (sdist only) and 2.0 (wheel); "<2" must check 1.9, not 2.0.
    (tmp_path / "requirements.txt").write_text("ranged>=1.5,<2\n", encoding="utf-8")
    (src,) = load_inputs([str(tmp_path / "requirements.txt")])
    pypi.fill_all(src.packages)
    (pkg,) = src.packages
    assert (pkg.version, pkg.files) == ("1.9", ["ranged-1.9.tar.gz"])


def test_version_range_with_no_match(fake_pypi):
    pkg = Package("ranged", None, spec=">=3")
    pypi.fill(pkg)
    assert pkg.error == "no release matches >=3"


def test_empty_index_url_means_pypi(monkeypatch):
    monkeypatch.setenv("WHEELGAP_INDEX_URL", "")
    assert pypi.index_url() == pypi.DEFAULT_INDEX


def test_simple_index_url_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("WHEELGAP_INDEX_URL", "https://pypi.org/simple/")
    with pytest.raises(SourceError, match="needs the JSON API"):
        pypi.check_index_url()


@pytest.mark.parametrize("response", [None, [], {"urls": "x"}, {"urls": [{"name": "no-filename.whl"}]}])
def test_odd_index_responses_are_unknown_not_a_crash(monkeypatch, response):
    monkeypatch.setattr(pypi, "fetch_json", lambda url, timeout=20.0: response)
    pkg = pypi.fill(Package("x", "1.0"))
    assert pkg.error == "unexpected response from the index" and pkg.files is None


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_json_retries_transient_failures_only(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(pypi.time, "sleep", sleeps.append)

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise ConnectionResetError("reset by peer")  # a dropped connection is worth a retry
        if len(calls) == 2:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {"Retry-After": "2"}, None)
        return FakeResponse(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(pypi._OPENER, "open", urlopen)
    assert pypi.fetch_json("https://pypi.org/pypi/x/json") == {"ok": True}
    assert sleeps == [0.5, 2.0]  # backoff, then the server's Retry-After

    calls.clear()

    def forbidden(req, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 403, "no", {}, None)

    monkeypatch.setattr(pypi._OPENER, "open", forbidden)
    with pytest.raises(urllib.error.HTTPError):
        pypi.fetch_json("https://pypi.org/pypi/x/json")
    assert calls == [1]  # a 403 won't change on retry


def test_redirect_handler_refuses_https_downgrades():
    handler = pypi._HTTPSRedirectHandler()
    fp = io.BytesIO()
    headers = http.client.HTTPMessage()
    req_https = urllib.request.Request("https://cdn.example/pyodide-lock.json")
    with pytest.raises(
        pypi.RedirectRefused,
        match=r"refused redirect from https://cdn\.example/pyodide-lock\.json to http://mirror\.example/lock\.json",
    ):
        handler.redirect_request(req_https, fp, 302, "Found", headers, "http://mirror.example/lock.json")

    redirected = handler.redirect_request(req_https, fp, 302, "Found", headers, "https://mirror.example/lock.json")
    assert redirected is not None and redirected.full_url == "https://mirror.example/lock.json"

    req_http = urllib.request.Request("http://local.example/pypi/x/json")
    plain = handler.redirect_request(req_http, fp, 302, "Found", headers, "http://local.example/v2/x/json")
    assert plain is not None and plain.full_url == "http://local.example/v2/x/json"


def test_opener_uses_the_https_redirect_handler():
    handlers = [h for h in pypi._OPENER.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert len(handlers) == 1 and isinstance(handlers[0], pypi._HTTPSRedirectHandler)
    headers = http.client.HTTPMessage()
    headers["Location"] = "http://mirror.example/lock.json"
    req = urllib.request.Request("https://cdn.example/pyodide-lock.json")
    with pytest.raises(pypi.RedirectRefused):  # the real 3xx path: Location header, urljoin, then the check
        handlers[0].http_error_302(req, io.BytesIO(), 302, "Found", headers)


def test_refused_redirect_is_not_retried_and_surfaces_cleanly(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(pypi.time, "sleep", sleeps.append)

    def redirect_to_http(req, timeout):
        calls.append(req.full_url)
        handler = pypi._HTTPSRedirectHandler()
        handler.redirect_request(
            req, io.BytesIO(), 302, "Found", http.client.HTTPMessage(), "http://evil.example/lock.json"
        )

    monkeypatch.setattr(pypi._OPENER, "open", redirect_to_http)
    url = "https://cdn.example/pyodide-lock.json"
    with pytest.raises(
        PyodideLockError,
        match=f"couldn't fetch {url}: refused redirect from {url} to http://evil.example/lock.json",
    ):
        load_pyodide_lock(url)
    assert calls == [url] and not sleeps

    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    result = wheelgap.check_package("fastlib", "2.0.0", targets)
    assert result.error == "lookup failed (RedirectRefused)" and result.statuses[targets[0]] is Status.UNKNOWN


def test_pinned_version_missing_is_named(fake_pypi):
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    result = wheelgap.check_package("fastlib", "9.9", targets)
    assert result.error == "version 9.9 not on index" and result.statuses[targets[0]] is Status.UNKNOWN


def test_range_on_a_mirror_without_a_release_list(monkeypatch):
    data = {"info": {"version": "2.0"}, "urls": [{"filename": "x-2.0-py3-none-any.whl"}]}
    monkeypatch.setattr(pypi, "fetch_json", lambda url, timeout=20.0: data)
    assert pypi.fill(Package("x", None, spec=">=1")).version == "2.0"  # latest matches: fine
    assert pypi.fill(Package("x", None, spec="<2")).error == "unexpected response from the index"


@pytest.mark.parametrize("value", ["-1", "nan", "inf", "soon"])
def test_bad_retry_after_values_are_ignored(value):
    err = urllib.error.HTTPError("u", 503, "busy", {"Retry-After": value}, None)
    assert pypi._retry_after(err) is None


def test_missing_version_when_the_project_check_fails(monkeypatch):
    def fetch(url, timeout=20.0):
        code = 404 if "/1.0/" in url else 503
        raise urllib.error.HTTPError(url, code, "x", {}, None)

    monkeypatch.setattr(pypi, "fetch_json", fetch)
    assert pypi.fill(Package("x", "1.0")).error == "version 1.0 not on index (couldn't check the project: HTTP 503)"


def test_mirror_fallback_with_no_usable_files_is_unknown(monkeypatch):
    data = {"info": {"version": "2.0"}, "urls": [{"filename": "x-2.0.tar.gz", "yanked": True}]}
    monkeypatch.setattr(pypi, "fetch_json", lambda url, timeout=20.0: data)
    pkg = pypi.fill(Package("x", None, spec=">=1"))
    assert pkg.error == "unexpected response from the index" and pkg.files is None


def test_fill_all_skips_a_private_package_with_no_files(fake_pypi):
    pkg = Package(name="purewheel", version="3.1", files=None, registry="https://pkgs.example.com/simple")
    assert pypi.fill_all([pkg]) == 0
    assert pkg.skip == "private index"
    assert not [url for url in fake_pypi if "purewheel" in url], fake_pypi


def test_fill_all_still_looks_up_a_pypi_package_with_no_files(fake_pypi):
    pkg = Package(name="purewheel", version="3.1", files=None)
    assert pypi.fill_all([pkg]) == 0
    assert pkg.skip is None and pkg.files == ["purewheel-3.1-py3-none-any.whl"]
    assert fake_pypi == ["https://pypi.org/pypi/purewheel/3.1/json"]


@pytest.mark.parametrize(
    ("registry", "expected"),
    [
        (None, True),
        ("https://pypi.org/simple", True),
        ("https://pypi.org/simple/", True),
        ("https://pkgs.example.com/simple", False),
        ("https://pypi.python.org/simple", False),  # the same exact rule recheck_lock() used before
    ],
)
def test_is_pypi(registry, expected):
    assert pypi.is_pypi(registry) is expected


@pytest.mark.parametrize(
    ("package", "expected"),
    [
        (Package("kept", "1.0", files=["kept-1.0.whl"], registry="https://pkgs.example.com/simple"), True),
        (Package("onpypi", "1.0", files=["onpypi-1.0.whl"]), False),
        (
            Package("onpypi-explicit", "1.0", files=["onpypi_explicit-1.0.whl"], registry="https://pypi.org/simple"),
            False,
        ),
        (Package("nofiles", None), False),
        (Package("skipped", "1.0", files=["skipped-1.0.whl"], skip="path"), False),
    ],
)
def test_keeps_locked_files(package, expected):
    assert pypi.keeps_locked_files(package) is expected
