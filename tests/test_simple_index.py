import base64
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import FAKE_INDEX

from wheelgap import pypi
from wheelgap.sources import Package, SourceError

BASE = "https://private.example/simple"


def fill(name, version=None, spec=None, base=BASE, auth=None, python=None):
    pkg = Package(name, version, spec=spec)
    return pypi.fill_simple(pkg, base, auth, python)


def test_pinned_gets_every_file_of_that_version(fake_simple):
    pkg = fill("fastlib", "2.0.0")
    assert pkg.error is None
    assert pkg.version == "2.0.0"
    assert sorted(pkg.files) == [
        "fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl",
        "fastlib-2.0.0.tar.gz",
    ]
    assert fake_simple.calls == [(f"{BASE}/fastlib/", None)]


def test_unpinned_takes_the_newest_release(fake_simple):
    pkg = fill("ranged")
    assert pkg.version == "2.0"
    assert pkg.files == ["ranged-2.0-py3-none-any.whl"]


def test_range_takes_the_newest_match(fake_simple):
    pkg = fill("ranged", spec=">=1.5,<2")
    assert pkg.version == "1.9"
    assert pkg.files == ["ranged-1.9.tar.gz"]


def test_range_with_no_match(fake_simple):
    pkg = fill("ranged", spec=">=3")
    assert pkg.error == "no release matches >=3"


def test_pinned_version_missing(fake_simple):
    pkg = fill("fastlib", "9.9.9")
    assert pkg.error == "version 9.9.9 not on index"


def test_unknown_project(fake_simple):
    assert fill("nosuchpkg", "1.0").error == "not on index"


def test_fully_yanked_pin_keeps_files_marked(fake_simple):
    pkg = fill("yanked", "1.0")
    assert pkg.files == ["yanked-1.0-py3-none-any.whl"]
    assert pkg.yanked == {"yanked-1.0-py3-none-any.whl"}


def test_half_yanked_pin(fake_simple):
    pkg = fill("halfyanked", "2.0")
    assert sorted(pkg.files) == ["halfyanked-2.0-py3-none-any.whl", "halfyanked-2.0.tar.gz"]
    assert pkg.yanked == {"halfyanked-2.0-py3-none-any.whl"}


def test_unpinned_skips_fully_yanked_release(fake_simple):
    pkg = fill("yanked")
    assert pkg.error == "not on index"


def test_unpinned_drops_yanked_files(fake_simple):
    pkg = fill("halfyanked")
    assert pkg.files == ["halfyanked-2.0.tar.gz"]


def test_odd_version_parses_from_filename(fake_simple):
    pkg = fill("winonly", "311")
    assert pkg.files == ["winonly-311-cp313-cp313-win_amd64.whl"]


def test_html_only_index_names_the_host_only(fake_simple):
    fake_simple.html_only = True
    pkg = fill("fastlib", "2.0.0", base="https://private.example/secret-path/simple")
    assert "private.example" in pkg.error
    assert "PEP 691" in pkg.error
    assert "secret-path" not in pkg.error


def test_http_error_is_reported_without_the_url(monkeypatch):
    def boom(url, auth=None, timeout=20.0):
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(pypi, "fetch_simple", boom)
    assert fill("fastlib", "2.0.0").error == "HTTP 403"


def test_network_failure_does_not_echo_the_exception(monkeypatch):
    def boom(url, auth=None, timeout=20.0):
        raise OSError(f"cannot reach {url} with s3cret")

    monkeypatch.setattr(pypi, "fetch_simple", boom)
    assert fill("fastlib", "2.0.0").error == "lookup failed (OSError)"


def test_malformed_response(monkeypatch):
    monkeypatch.setattr(pypi, "fetch_simple", lambda *a, **k: {"files": "nope"})
    assert fill("fastlib", "2.0.0").error == "unexpected response from the index"


def test_unparseable_filenames_are_skipped(monkeypatch):
    data = {"files": [{"filename": "not-a-package.zip"}, {"filename": "fastlib-1.0.tar.gz"}]}
    monkeypatch.setattr(pypi, "fetch_simple", lambda *a, **k: data)
    pkg = fill("fastlib")
    assert pkg.files == ["fastlib-1.0.tar.gz"]


def test_other_projects_files_are_ignored(monkeypatch):
    data = {"files": [{"filename": "other-1.0.tar.gz"}, {"filename": "fastlib-1.0.tar.gz"}]}
    monkeypatch.setattr(pypi, "fetch_simple", lambda *a, **k: data)
    assert fill("fastlib").files == ["fastlib-1.0.tar.gz"]


def test_split_credentials_removes_them_from_the_url():
    url, auth = pypi.split_credentials("https://user:s3cret@private.example:8443/simple/")
    assert url == "https://private.example:8443/simple"
    assert "s3cret" not in url
    assert auth == "Basic dXNlcjpzM2NyZXQ="


def test_split_credentials_without_userinfo():
    assert pypi.split_credentials("https://pypi.org/simple") == ("https://pypi.org/simple", None)


def test_split_credentials_decodes_percent_escapes():
    _, auth = pypi.split_credentials("https://user:p%40ss@host/simple")

    assert base64.b64decode(auth.removeprefix("Basic ")) == b"user:p@ss"


@pytest.mark.parametrize("bad", ["ftp://user:s3cret@host/simple", "not a url", "https://user:s3cret@/simple"])
def test_bad_index_url_raises_without_echoing_it(bad):
    with pytest.raises(SourceError) as exc:
        pypi.split_credentials(bad)
    assert "s3cret" not in str(exc.value)


def test_fill_all_sends_credentials_as_a_header_not_in_the_url(fake_simple):
    pkgs = [Package("fastlib", "2.0.0")]
    pypi.fill_all(pkgs, simple_index="https://user:s3cret@private.example/simple")
    ((url, auth),) = fake_simple.calls
    assert url == "https://private.example/simple/fastlib/"
    assert auth is not None and auth.startswith("Basic ")
    assert pkgs[0].error is None
    assert "s3cret" not in repr(pkgs[0])


def test_check_index_url_hint_does_not_leak_credentials(monkeypatch):
    monkeypatch.setenv("WHEELGAP_INDEX_URL", "https://user:s3cret@private.example/simple")
    with pytest.raises(SourceError) as exc:
        pypi.check_index_url()
    assert "s3cret" not in str(exc.value)
    assert "--index-url" in str(exc.value)


def test_cli_does_not_print_the_token(fake_simple, tmp_path, capsys):
    from wheelgap.cli import main

    req = tmp_path / "requirements.txt"
    req.write_text("fastlib==2.0.0\n")
    main([str(req), "--online", "--json", "--index-url", "https://user:s3cret@private.example/simple"])
    out = capsys.readouterr()
    assert "s3cret" not in out.out + out.err
    assert fake_simple.calls and fake_simple.calls[0][1] is not None


def test_cli_bad_index_url_exits_2_without_the_token(capsys):
    from wheelgap.cli import main

    assert main(["--index-url", "ftp://user:s3cret@x/simple"]) == 2
    out = capsys.readouterr()
    assert "s3cret" not in out.out + out.err


# --- the simple path behaves like the JSON path ---

CASES = [(n, None, None) for n in FAKE_INDEX if n != "yanked"] + [
    ("nosuchpkg", None, None),
    ("ranged", None, ">=1.5,<2"),
    ("ranged", None, ">=3"),
    ("fastlib", "2.0.0", None),
    ("fastlib", "9.9.9", None),
    ("yanked", "1.0", None),
    ("halfyanked", "2.0", None),
    ("nosuchpkg", "1.0", None),
]


@pytest.mark.parametrize("name,version,spec", CASES)
def test_simple_path_matches_json_path(name, version, spec, fake_pypi, fake_simple):
    def shape(p):
        return (p.version, sorted(p.files) if p.files is not None else None, p.yanked, p.error)

    assert shape(pypi.fill(Package(name, version, spec=spec))) == shape(fill(name, version, spec))


def test_prerelease_is_ignored_unless_pinned(monkeypatch):
    data = {"files": [{"filename": "fastlib-2.0rc1.tar.gz"}, {"filename": "fastlib-1.0.tar.gz"}]}
    monkeypatch.setattr(pypi, "fetch_simple", lambda *a, **k: data)
    assert fill("fastlib").version == "1.0"
    assert fill("fastlib", "2.0rc1").files == ["fastlib-2.0rc1.tar.gz"]


def test_pin_spelling_does_not_matter(monkeypatch):
    data = {"files": [{"filename": "fastlib-1.0.tar.gz"}]}
    monkeypatch.setattr(pypi, "fetch_simple", lambda *a, **k: data)
    assert fill("fastlib", "1.0.0").files == ["fastlib-1.0.tar.gz"]


def test_yanked_reason_string_counts_as_yanked(monkeypatch):
    data = {"files": [{"filename": "fastlib-1.0.tar.gz", "yanked": "broken build"}]}
    monkeypatch.setattr(pypi, "fetch_simple", lambda *a, **k: data)
    assert fill("fastlib", "1.0").yanked == {"fastlib-1.0.tar.gz"}


# --- an unset CI variable must not fall back to public PyPI ---


def test_fill_all_rejects_an_empty_index_url():
    with pytest.raises(SourceError, match="empty"):
        pypi.fill_all([Package("fastlib", "2.0.0")], simple_index="")


def test_cli_empty_index_url_exits_2(capsys):
    from wheelgap.cli import main

    assert main(["flask", "--index-url", ""]) == 2
    assert "empty" in capsys.readouterr().err


# --- the real fetch_simple, over HTTP ---

PAGE = {
    "meta": {"api-version": "1.0"},
    "name": "fastlib",
    "files": [{"filename": "fastlib-1.0.tar.gz", "url": "x", "hashes": {}}],
}


def _send(h, ctype, body=None):
    body = json.dumps(PAGE).encode() if body is None else body
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


def _redirect(h, location):
    h.send_response(302)
    h.send_header("Location", location)
    h.send_header("Content-Length", "0")
    h.end_headers()


@pytest.fixture
def serve():
    servers = []

    def start(respond):
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
                respond(self)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}", seen

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def test_real_request_sends_accept_and_basic_auth(serve):
    base, seen = serve(lambda h: _send(h, pypi.SIMPLE_JSON))
    _, auth = pypi.split_credentials("https://user:s3cret@x/simple")
    assert pypi.fetch_simple(f"{base}/simple/fastlib/", auth)["name"] == "fastlib"
    path, headers = seen[0]
    assert path == "/simple/fastlib/"
    assert headers["accept"] == pypi.SIMPLE_ACCEPT
    assert headers["authorization"] == "Basic dXNlcjpzM2NyZXQ="


def test_real_html_response_is_rejected_by_content_type(serve):
    base, _ = serve(lambda h: _send(h, "text/html", b"<html></html>"))
    with pytest.raises(pypi.IndexFormatError, match="127.0.0.1"):
        pypi.fetch_simple(f"{base}/simple/fastlib/")


def test_authorization_is_not_forwarded_on_a_redirect(serve):
    other, other_seen = serve(lambda h: _send(h, pypi.SIMPLE_JSON))
    first, _ = serve(lambda h: _redirect(h, f"{other}/simple/fastlib/"))
    pypi.fetch_simple(f"{first}/simple/fastlib/", "Basic abc")
    assert other_seen, "the redirect was not followed"
    assert "authorization" not in other_seen[0][1]


def _project(tmp_path, index_url):
    (tmp_path / "pyproject.toml").write_text(f'[tool.wheelgap]\nindex-url = "{index_url}"\n')
    req = tmp_path / "requirements.txt"
    req.write_text("fastlib==2.0.0\n")
    return str(req)


def test_config_index_url_is_used(fake_simple, tmp_path):
    from wheelgap.cli import main

    main([_project(tmp_path, "https://config.example/simple"), "--json"])
    assert fake_simple.calls[0][0].startswith("https://config.example/simple/")


def test_flag_beats_config(fake_simple, tmp_path):
    from wheelgap.cli import main

    main([_project(tmp_path, "https://config.example/simple"), "--json", "--index-url", "https://flag.example/simple"])
    assert fake_simple.calls[0][0].startswith("https://flag.example/simple/")


def test_config_index_url_refuses_credentials(fake_simple, tmp_path, capsys):
    from wheelgap.cli import main

    # pyproject.toml is committed: a token there would be published with the repo.
    assert main([_project(tmp_path, "https://user:s3cret@config.example/simple"), "--json"]) == 2
    out = capsys.readouterr()
    assert not fake_simple.calls
    assert "--index-url" in out.out + out.err
    assert "s3cret" not in out.out + out.err


def test_config_credentials_are_refused_however_the_url_is_spelled(fake_simple, tmp_path, capsys):
    from wheelgap.cli import main

    # urlsplit drops tabs, so a check on the raw text alone would miss this one.
    assert main([_project(tmp_path, "https:\\t//user:s3cret@config.example/simple"), "--json"]) == 2
    assert not fake_simple.calls
    assert "s3cret" not in "".join(capsys.readouterr())


def test_config_index_url_must_be_a_string(tmp_path, capsys):
    from wheelgap.cli import main

    req = _project(tmp_path, "unused")
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nindex-url = 5\n")
    assert main([req]) == 2
    assert "index-url" in capsys.readouterr().err


# --- Requires-Python, plain http, and the requires-python re-check ---


@pytest.mark.parametrize("spec", [">=1", ">=2", "<2"])
def test_simple_path_matches_json_path_for_a_python(spec, fake_pypi, fake_simple):
    def shape(p):
        return (p.version, sorted(p.files) if p.files is not None else None, p.error)

    json_pkg = pypi.fill(Package("pythonranged", None, spec=spec), python="3.9")
    assert shape(json_pkg) == shape(fill("pythonranged", spec=spec, python="3.9"))


def test_range_skips_releases_whose_requires_python_excludes_the_target(fake_simple):
    assert fill("pythonranged", spec=">=1", python="3.9").version == "1.0"
    assert fill("pythonranged", spec=">=1", python="3.12").version == "2.0"
    assert fill("pythonranged", spec=">=2", python="3.9").error == "no release matches >=2 for Python 3.9"
    assert fill("pythonranged", python="3.9").version == "2.0"  # no range: the latest, as fill() does


def test_fill_all_passes_the_python_to_the_simple_index(fake_simple):
    pkgs = [Package("pythonranged", None, spec=">=1")]
    pypi.fill_all(pkgs, python="3.9", simple_index=BASE)
    assert pkgs[0].version == "1.0"


def test_plain_http_is_refused_except_for_localhost():
    with pytest.raises(SourceError, match="https") as exc:
        pypi.split_credentials("http://user:s3cret@private.example/simple")
    assert "s3cret" not in str(exc.value)
    assert pypi.split_credentials("http://127.0.0.1:8080/simple") == ("http://127.0.0.1:8080/simple", None)
    assert pypi.split_credentials("http://localhost/simple/")[0] == "http://localhost/simple"


def test_recheck_lock_uses_the_simple_index(fake_simple):
    pkg = Package("fastlib", "2.0.0")
    pkg.files = []
    assert pypi.recheck_lock([pkg], simple_index=BASE) == (0, 0)
    assert fake_simple.calls[0][0] == f"{BASE}/fastlib/"
    assert pkg.files


def test_accept_header_lets_an_html_only_index_answer_with_html():
    assert pypi.SIMPLE_ACCEPT.startswith(pypi.SIMPLE_JSON + ",")
    assert "text/html;q=0.01" in pypi.SIMPLE_ACCEPT
