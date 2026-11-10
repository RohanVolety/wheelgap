import json
import urllib.error

import pytest

from wheelgap import pypi
from wheelgap.cli import main
from wheelgap.pyodide import PyodideLockError, load_pyodide_lock, parse_pyodide_lock


@pytest.fixture
def lock(fixtures):
    return str(fixtures / "pyodide-lock.json")


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_parse_keeps_only_packages_by_canonical_name(lock):
    parsed = load_pyodide_lock(lock)
    assert parsed.python == (3, 13) and parsed.abi == "2025_0" and parsed.pyodide_version == "0.29.0"
    # no cpython_module / shared_library, and no test bundle (fastlib-tests ships as a .tar)
    assert parsed.packages == {"fastlib": "2.0.0", "markupsafe": "3.0.2"}


def test_bundled_package_counts_as_available(capsys, fake_pypi, lock):
    code, out, _ = run(
        capsys, "fastlib==2.0.0", "nowheels==0.9", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", lock, "--all"
    )
    assert code == 1  # nowheels is still missing
    assert "✓ bundled" in out
    assert "1 package comes from Pyodide 0.29.0's own builds: fastlib 2.0.0" in out
    assert "nowheels" in out and "✗ missing" in out


def test_bundled_only_is_a_pass(capsys, fake_pypi, lock):
    code, out, _ = run(capsys, "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", lock)
    assert code == 0
    assert "has a wheel" in out


def test_quiet_omits_pyodide_bundled_details(capsys, fake_pypi, lock):
    code, out, err = run(
        capsys, "fastlib==2.0.0", "nowheels==0.9", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", lock, "--quiet"
    )
    assert code == 1 and not err
    assert out == "1 of 2 packages has gaps: 1 can't install at all.\ngaps: nowheels\n"


def test_different_bundled_version_is_flagged(capsys, fake_pypi, tmp_path):
    path = tmp_path / "pyodide-lock.json"
    info = {"abi_version": "2025_0", "python": "3.13.2", "version": "0.29.0"}
    path.write_text(json.dumps({"info": info, "packages": {"nowheels": {"name": "nowheels", "version": "0.8"}}}))
    code, out, _ = run(capsys, "nowheels==0.9", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", str(path))
    assert code == 0
    assert "nowheels 0.8 (you pinned 0.9)" in out


def test_json_and_other_targets(capsys, fake_pypi, lock):
    code, out, _ = run(
        capsys, "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide,musl-aarch64", "--pyodide-lock", lock, "--json"
    )
    fastlib = next(p for p in json.loads(out)["packages"] if p["name"] == "fastlib")
    # Only the pyodide target changes; fastlib still builds from source on Alpine ARM.
    assert fastlib["results"] == {"pyodide": "bundled", "musl-aarch64": "source"}
    assert fastlib["bundled"] == "2.0.0"
    assert code == 1


def test_lock_for_another_python_is_an_error(capsys, fake_pypi, lock):
    code, _, err = run(capsys, "fastlib==2.0.0", "-p", "3.12", "-t", "pyodide", "--pyodide-lock", lock)
    assert code == 2
    assert "is for Python 3.13 (Pyodide 0.29.0)" in err and "-p 3.13 -t pyodide" in err


def test_abi_that_doesnt_match_python_is_an_error():
    data = {"info": {"abi_version": "2024_0", "python": "3.13.0"}, "packages": {}}
    with pytest.raises(PyodideLockError, match="abi_version 2024_0 doesn't match Python 3.13"):
        parse_pyodide_lock(data, "x.json")


def test_not_a_lockfile(capsys, fake_pypi, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    code, _, err = run(capsys, "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", str(bad))
    assert code == 2 and "not valid JSON" in err
    bad.write_text('{"packages": {}}')
    code, _, err = run(capsys, "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", str(bad))
    assert code == 2 and "not a pyodide-lock.json" in err


def test_url_is_fetched(capsys, fake_pypi, lock, monkeypatch):
    fake_index = pypi.fetch_json
    lock_data = json.loads(open(lock).read())

    def fetch_json(url, timeout=20.0):
        return lock_data if url.endswith("pyodide-lock.json") else fake_index(url, timeout)

    monkeypatch.setattr(pypi, "fetch_json", fetch_json)
    url = "https://cdn.example/pyodide/v0.29.0/full/pyodide-lock.json"
    code, out, _ = run(capsys, "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", url)
    assert code == 0


@pytest.mark.parametrize("url", ["http://cdn.example/pyodide-lock.json", "HTTP://cdn.example/pyodide-lock.json"])
def test_plain_http_url_is_refused(monkeypatch, url):
    def fetch_json(url, timeout=20.0):
        raise AssertionError("must not fetch over plain http")

    monkeypatch.setattr(pypi, "fetch_json", fetch_json)
    with pytest.raises(PyodideLockError, match="plain http:// URLs are not allowed"):
        load_pyodide_lock(url)


def test_upper_case_https_url_is_fetched(lock, monkeypatch):
    lock_data = json.loads(open(lock).read())
    monkeypatch.setattr(pypi, "fetch_json", lambda url, timeout=20.0: lock_data)
    assert load_pyodide_lock("HTTPS://cdn.example/pyodide-lock.json").python == (3, 13)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (urllib.error.HTTPError("u", 404, "Not Found", None, None), "HTTP 404"),
        (urllib.error.URLError("no route"), "no route"),
        (ValueError("bad JSON"), "bad JSON"),
    ],
)
def test_failed_fetch_names_the_url(monkeypatch, error, message):
    def fetch_json(url, timeout=20.0):
        raise error

    monkeypatch.setattr(pypi, "fetch_json", fetch_json)
    url = "https://cdn.example/pyodide-lock.json"
    with pytest.raises(PyodideLockError, match=f"couldn't fetch {url}: {message}"):
        load_pyodide_lock(url)


def test_unexpected_errors_are_not_reported_as_fetch_failures(monkeypatch):
    def fetch_json(url, timeout=20.0):
        raise TypeError("a bug")

    monkeypatch.setattr(pypi, "fetch_json", fetch_json)
    with pytest.raises(TypeError):
        load_pyodide_lock("https://cdn.example/pyodide-lock.json")


def test_lock_from_pyproject(capsys, fake_pypi, lock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(f'[tool.wheelgap]\npyodide-lock = "{lock}"\n'.replace("\\", "/"))
    code, out, _ = run(capsys, "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide")
    assert code == 0


@pytest.mark.parametrize("scheme", ["http", "HTTP"])
def test_plain_http_lock_from_pyproject_is_refused(capsys, fake_pypi, tmp_path, monkeypatch, scheme):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(f'[tool.wheelgap]\npyodide-lock = "{scheme}://cdn.example/lock.json"\n')
    code, _, err = run(capsys, "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide")
    assert code == 2 and "plain http:// URLs are not allowed" in err


def test_failed_lookup_is_not_bundled(capsys, fake_pypi, lock):
    # The name is in the lock, but this pinned version isn't on the index: that's a gap, not a pass.
    code, out, _ = run(capsys, "fastlib==9.9", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", lock, "--json")
    fastlib = json.loads(out)["packages"][0]
    assert fastlib["results"] == {"pyodide": "unknown"}
    assert code == 1
    assert (
        run(capsys, "fastlib==9.9", "-p", "3.13", "-t", "pyodide", "--pyodide-lock", lock, "--fail-on", "missing")[0]
        == 1
    )


def test_lock_from_pyproject_is_unused_without_a_matching_target(capsys, fake_pypi, lock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(f'[tool.wheelgap]\npyodide-lock = "{lock}"\n'.replace("\\", "/"))
    code, out, err = run(capsys, "purewheel", "-p", "3.13", "-t", "linux")
    assert code == 0 and not err  # used to exit 2: "no pyodide target uses Python 3.13"


def test_lock_from_pyproject_is_relative_to_pyproject(capsys, fake_pypi, fixtures, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyodide-lock.json").write_bytes((fixtures / "pyodide-lock.json").read_bytes())
    (project / "pyproject.toml").write_text('[tool.wheelgap]\npyodide-lock = "pyodide-lock.json"\n')
    (project / "requirements.txt").write_text("fastlib==2.0.0\n")
    code, out, err = run(capsys, str(project), "-p", "3.13", "-t", "pyodide", "--json")  # run from elsewhere
    assert not err
    assert json.loads(out)["packages"][0]["results"] == {"pyodide": "bundled"}
    assert code == 0


def test_lock_from_pyproject_is_not_even_read_without_a_pyodide_target(capsys, fake_pypi, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.wheelgap]\npyodide-lock = "missing/pyodide-lock.json"\n')
    code, _, err = run(capsys, "purewheel", "-p", "3.13", "-t", "linux")
    assert code == 0 and not err  # a missing (or offline) lock doesn't matter to a linux-only run


def test_lock_from_pyproject_for_another_python_is_an_error(capsys, fake_pypi, lock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(f'[tool.wheelgap]\npyodide-lock = "{lock}"\n'.replace("\\", "/"))
    code, _, err = run(capsys, "fastlib==2.0.0", "-p", "3.12", "-t", "pyodide")
    assert code == 2 and "is for Python 3.13" in err  # rather than silently calling bundled packages missing


def test_lock_covering_only_some_pyodide_targets_warns(capsys, fake_pypi, lock):
    code, out, err = run(capsys, "fastlib==2.0.0", "-p", "3.12,3.13", "-t", "pyodide", "--pyodide-lock", lock)
    assert "pyodide results for Python 3.12 don't include packages that Pyodide bundles" in err


def test_lock_that_is_not_a_json_object(tmp_path):
    bad = tmp_path / "pyodide-lock.json"
    bad.write_text("[]")
    with pytest.raises(PyodideLockError, match="not a pyodide-lock.json"):
        load_pyodide_lock(str(bad))
