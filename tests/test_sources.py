import json

import pytest

from wheelgap.check import Status, check
from wheelgap.sources import (
    Source,
    SourceError,
    find_lockfile,
    load_inputs,
    parse_poetry_lock,
    parse_requirements,
    pythons_outside_lock,
)
from wheelgap.targets import build_targets


def by_name(source):
    return {p.name: p for p in source.packages}


def test_uv_lock(fixtures):
    (src,) = load_inputs([str(fixtures / "uvproj")])
    pkgs = by_name(src)
    assert src.label == "uv.lock"
    assert pkgs["demo"].skip == "editable"
    assert pkgs["gitdep"].skip == "git"
    assert pkgs["fastlib"].files == [
        "fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "fastlib-2.0.0-cp313-cp313-macosx_11_0_arm64.whl",
        "fastlib-2.0.0-cp313-cp313-win_amd64.whl",
        "fastlib-2.0.0.tar.gz",
    ]
    assert pkgs["puresdist"].files == ["puresdist-1.0.tar.gz"]
    assert src.roots == [0]
    assert ("winonly", None, "sys_platform == 'win32'") in src.edges[0]
    assert ("devtool", None, None) in src.edges[0]


def test_uv_lock_dependency_group_names(tmp_path):
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[manifest]\nrequirements = [{ name = "app" }]\n\n'
        '[manifest.dependency-groups]\ndev = [{ name = "pytest" }]\ndocs = [{ name = "sphinx" }]\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    assert [name for name, _, _ in src.root_edges] == ["pytest", "sphinx", "app"]
    assert src.root_groups == {0: "dev", 1: "docs"}  # the plain requirement has no group


def test_poetry_lock(fixtures):
    (src,) = load_inputs([str(fixtures / "poetryproj" / "poetry.lock")])
    pkgs = by_name(src)
    assert pkgs["fastlib"].files == ["fastlib-2.0.0-cp313-cp313-musllinux_1_2_aarch64.whl", "fastlib-2.0.0.tar.gz"]
    assert pkgs["backport"].markers == ['python_version < "3.13"']
    assert pkgs["localpkg"].skip == "directory"


@pytest.mark.parametrize("input_name", ["pdmproj", "pdmproj/pdm.lock"])
def test_pdm_lock(fixtures, input_name):
    (src,) = load_inputs([str(fixtures / input_name)])
    pkgs = by_name(src)
    assert src.label == "pdm.lock"
    assert pkgs["fastlib"].files == ["fastlib-2.0.0-cp313-cp313-musllinux_1_2_aarch64.whl", "fastlib-2.0.0.tar.gz"]
    assert pkgs["backport"].markers == ["python_version < '3.13'"]
    assert pkgs["localpkg"].skip == "path"
    assert all(p.origin == "pdm.lock" for p in src.packages)


def test_pdm_lock_legacy_metadata_files(tmp_path, fake_pypi):
    path = tmp_path / "pdm.lock"
    lock = (
        '[metadata]\nlock_version = "3.0"\n\n'
        '[[package]]\nname = "oldpkg"\nversion = "1.0"\n\n'
        '[[package]]\nname = "newpkg"\nversion = "2.0"\n\n'
        '[[package]]\nname = "jinja2"\nversion = "3.1.2"\n\n'
        "[metadata.files]\n"
        '"oldpkg 1.0" = [\n'
        '    {file = "oldpkg-1.0-py3-none-any.whl", hash = "sha256:00"},\n'
        "]\n"
        '"Jinja2 3.1.2" = [\n'
        '    {url = "https://files.pythonhosted.org/packages/Jinja2-3.1.2-py3-none-any.whl", hash = "sha256:00"},\n'
        "]\n"
    )
    path.write_text(lock, encoding="utf-8")
    (src,) = load_inputs([str(path)])
    pkgs = by_name(src)
    assert pkgs["oldpkg"].files == ["oldpkg-1.0-py3-none-any.whl"]
    assert pkgs["newpkg"].files is None  # not in metadata.files either: fall back to PyPI
    assert pkgs["jinja2"].files == ["Jinja2-3.1.2-py3-none-any.whl"]  # normalized-name fallback
    from wheelgap.pypi import fill_all

    fill_all(src.packages)
    # Only the package missing from [metadata.files] is looked up on the index.
    assert fake_pypi and all("/newpkg/" in url for url in fake_pypi)


def test_pylock(fixtures):
    (src,) = load_inputs([str(fixtures / "pylockproj")])
    pkgs = by_name(src)
    assert pkgs["fastlib"].files == ["fastlib-2.0.0-cp313-abi3-manylinux_2_28_aarch64.whl"]
    assert pkgs["macpkg"].files == ["macpkg-1.0.tar.gz"]
    assert pkgs["macpkg"].markers == ["sys_platform == 'darwin'"]


def test_requirements(fixtures):
    src = parse_requirements(fixtures / "reqproj" / "requirements.txt")
    pkgs = by_name(src)
    assert pkgs["fastlib"].version == "2.0.0"
    assert pkgs["fastlib"].files is None
    assert pkgs["unpinned"].version is None
    assert pkgs["winonly"].markers == ['sys_platform == "win32"']
    assert pkgs["purewheel"].version == "3.1"  # pulled in via -r
    assert pkgs["./localpkg"].skip == "editable"
    assert len(src.packages) == 5


@pytest.mark.parametrize("input_name", ["pipenvproj", "pipenvproj/Pipfile.lock"])
def test_pipfile_lock(fixtures, input_name):
    (src,) = load_inputs([str(fixtures / input_name)])
    pkgs = by_name(src)
    assert src.label == "Pipfile.lock"
    assert len(src.packages) == 7
    assert pkgs["fastlib"].version == "2.0.0"
    assert pkgs["fastlib"].markers is None
    assert pkgs["winonly"].version == "311"
    assert pkgs["winonly"].markers == ["sys_platform == 'win32'"]
    assert pkgs["purewheel"].version == "3.1"
    assert pkgs["purewheel"].markers == ["python_version >= '3.11'"]
    assert pkgs["gitdep"].skip == "git"
    assert pkgs["localpkg"].skip == "path"
    assert pkgs["archivepkg"].skip == "file"
    assert pkgs["editablepkg"].skip == "editable"
    assert all(p.origin == "Pipfile.lock" for p in src.packages)
    assert all(p.files is None for p in src.packages)
    assert all(pkgs[name].skip is None for name in ("fastlib", "winonly", "purewheel"))
    assert all(pkgs[name].version is None for name in ("gitdep", "localpkg", "archivepkg", "editablepkg"))


@pytest.mark.parametrize("section", ["default", "develop"])
def test_pipfile_lock_with_one_section(tmp_path, section):
    path = tmp_path / "Pipfile.lock"
    path.write_text(json.dumps({section: {"purewheel": {"version": "==3.1"}}}), encoding="utf-8")
    (src,) = load_inputs([str(path)])
    assert [(p.name, p.version) for p in src.packages] == [("purewheel", "3.1")]


def test_pipfile_lock_package_in_both_sections(tmp_path):
    path = tmp_path / "Pipfile.lock"
    lock = {
        "default": {
            "certifi": {"version": "==2025.1.31"},
            "colorama": {"version": "==0.4.6", "markers": "os_name == 'nt'"},
        },
        "develop": {
            "Certifi": {"version": "==2025.1.31"},
            "colorama": {"version": "==0.4.6", "markers": "sys_platform == 'win32'"},
            "pytest": {"version": "==8.3.5"},
        },
    }
    path.write_text(json.dumps(lock), encoding="utf-8")
    (src,) = load_inputs([str(path)])
    assert [(p.name, p.version) for p in src.packages] == [
        ("certifi", "2025.1.31"),
        ("colorama", "0.4.6"),
        ("pytest", "8.3.5"),
    ]
    assert by_name(src)["colorama"].markers == ["os_name == 'nt'", "sys_platform == 'win32'"]


def test_empty_pipfile_lock(tmp_path):
    path = tmp_path / "Pipfile.lock"
    path.write_text("{}", encoding="utf-8")
    (src,) = load_inputs([str(tmp_path)])
    assert src.label == "Pipfile.lock"
    assert src.packages == []


def test_adhoc_specs():
    (src,) = load_inputs(["numpy==2.3.4", "psycopg2"])
    assert [(p.name, p.version) for p in src.packages] == [("numpy", "2.3.4"), ("psycopg2", None)]


def test_wildcard_pin_is_not_a_pin():
    (src,) = load_inputs(["numpy==2.*"])
    assert src.packages[0].version is None


def test_missing_file_errors():
    with pytest.raises(SourceError, match="no such file"):
        load_inputs(["nope/uv.lock"])


def test_no_lockfile_in_dir(fixtures):
    with pytest.raises(SourceError, match="no lockfile found"):
        find_lockfile(fixtures / "empty")


def test_bad_requirement():
    with pytest.raises(SourceError):
        load_inputs(["not a valid ! spec"])


def test_requirements_lock(tmp_path):
    path = tmp_path / "requirements.lock"
    path.write_text("fastlib==2.0.0\nwinonly==3.11 ; sys_platform == 'win32'\n", encoding="utf-8")
    (src,) = load_inputs([str(path)])
    pkgs = by_name(src)
    assert src.label == "requirements.lock"
    assert pkgs["fastlib"].version == "2.0.0"
    assert pkgs["winonly"].markers == ['sys_platform == "win32"']


def test_requirements_dev_lock(tmp_path):
    path = tmp_path / "requirements-dev.lock"
    path.write_text("pytest==8.3.5\n", encoding="utf-8")
    (src,) = load_inputs([str(path)])
    pkgs = by_name(src)
    assert src.label == "requirements-dev.lock"
    assert pkgs["pytest"].version == "8.3.5"


def test_requirements_uvicorn_lock(tmp_path):
    path = tmp_path / "requirements-uvicorn.lock"
    path.write_text("uvicorn==0.34.0\n", encoding="utf-8")
    (src,) = load_inputs([str(path)])
    pkgs = by_name(src)
    assert src.label == "requirements-uvicorn.lock"
    assert pkgs["uvicorn"].version == "0.34.0"


def test_find_lockfile_requirements_lock(tmp_path):
    (tmp_path / "requirements.lock").write_text("numpy==2.0.0\n", encoding="utf-8")
    found = find_lockfile(tmp_path)
    assert found.name == "requirements.lock"


def test_pipfile_lock_custom_categories(tmp_path):
    path = tmp_path / "Pipfile.lock"
    lock = {
        "docs": {
            "sphinx": {"version": "==7.2.6"},
            "furo": {"version": "==2024.1.29"},
        },
        "_meta": {"sources": []},
        "default": {
            "requests": {"version": "==2.31.0"},
            "sphinx": {"version": "==7.2.6"},
        },
        "develop": {
            "pytest": {"version": "==8.0.0"},
        },
        "tests": {
            "pytest-cov": {"version": "==4.1.0"},
        },
    }
    path.write_text(json.dumps(lock), encoding="utf-8")
    (src,) = load_inputs([str(path)])
    assert [(p.name, p.version) for p in src.packages] == [
        ("requests", "2.31.0"),
        ("sphinx", "7.2.6"),
        ("pytest", "8.0.0"),
        ("furo", "2024.1.29"),
        ("pytest-cov", "4.1.0"),
    ]
    assert len(src.packages) == 5


def _two_source_pipfile_lock(tmp_path):
    path = tmp_path / "Pipfile.lock"
    lock = {
        "_meta": {
            "sources": [
                {"name": "pypi", "url": "https://pypi.org/simple", "verify_ssl": True},
                {"name": "legacy", "url": "https://pypi.python.org/simple", "verify_ssl": True},
                {"name": "internal", "url": "https://pkgs.example.com/simple", "verify_ssl": True},
                {"name": "broken", "url": "https://[bad/simple", "verify_ssl": True},
            ]
        },
        "default": {
            "acme-utils": {"version": "==2.1.0", "index": "internal"},
            "requests": {"version": "==2.31.0", "index": "pypi"},
            "six": {"version": "==1.16.0", "index": "legacy"},
            "fastlib": {"version": "==2.0.0"},
            "ghost": {"version": "==1.0.0", "index": "not-in-meta"},
            "oddball": {"version": "==1.0.0", "index": "broken"},
        },
    }
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def test_pipfile_lock_private_index_is_skipped(tmp_path):
    (src,) = load_inputs([str(_two_source_pipfile_lock(tmp_path))])
    assert [(p.name, p.skip) for p in src.packages] == [
        ("acme-utils", "private index"),
        ("requests", None),
        ("six", None),
        ("fastlib", None),
        ("ghost", "private index"),  # an index the lock doesn't describe: can't tell it's PyPI
        ("oddball", "private index"),  # a URL urlparse rejects: skipped, not a crash
    ]


def test_pipfile_lock_private_index_in_one_category_skips_the_package(tmp_path):
    # Pipenv writes "index" on direct dependencies: the same package can be direct in develop, transitive in default.
    path = tmp_path / "Pipfile.lock"
    lock = {
        "_meta": {"sources": [{"name": "internal", "url": "https://pkgs.example.com/simple"}]},
        "default": {"acme-utils": {"version": "==2.1.0"}},
        "develop": {"acme-utils": {"version": "==2.1.0", "index": "internal"}},
    }
    path.write_text(json.dumps(lock), encoding="utf-8")
    (src,) = load_inputs([str(path)])
    assert [(p.name, p.skip) for p in src.packages] == [("acme-utils", "private index")]


def test_pipfile_lock_malformed_package_entry_raises(tmp_path):
    path = tmp_path / "Pipfile.lock"
    lock = {"default": {"requests": "==2.31.0"}}
    path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(SourceError, match="isn't a package entry"):
        load_inputs([str(path)])


@pytest.mark.parametrize("section", ["default", "develop", "docs"])
@pytest.mark.parametrize("value", [None, [], "x"])
def test_pipfile_lock_malformed_section_raises(tmp_path, section, value):
    path = tmp_path / "Pipfile.lock"
    path.write_text(json.dumps({"_meta": {}, section: value}), encoding="utf-8")
    with pytest.raises(SourceError, match=f"{section} isn't a package section"):
        load_inputs([str(path)])


def test_pdm_lock_extras_variants_count_once(tmp_path):
    # PDM writes one entry per extras variant (seen in PDM's own lockfile for coverage[toml]).
    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "coverage"\nversion = "7.13.5"\nmarker = "sys_platform == \'linux\'"\n'
        'files = [{file = "coverage-7.13.5.tar.gz", hash = "sha256:00"}]\n\n'
        '[[package]]\nname = "coverage"\nversion = "7.13.5"\nextras = ["toml"]\n'
        'files = [{file = "coverage-7.13.5.tar.gz", hash = "sha256:00"}]\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    assert [(p.name, p.version) for p in src.packages] == [("coverage", "7.13.5")]
    assert src.packages[0].markers is None  # the unconditional variant wins


def test_requirements_bare_vcs_urls_are_skipped(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text(
        "git+ssh://git@github.com/org/repo.git#egg=foo\n"  # the "@" in git@ used to abort the whole run
        "hg+https://hg.example.com/bar#egg=bar&subdirectory=src\n"
        "https://example.com/pkgs/baz-1.0.tar.gz\n"
        "named @ git+https://github.com/org/named.git\n"
        "requests==2.32.3\n",
        encoding="utf-8",
    )
    (src,) = load_inputs([str(path)])
    got = {p.name: (p.skip, p.version) for p in src.packages}
    assert got["foo"] == ("url", None) and got["bar"] == ("url", None)
    assert got["https://example.com/pkgs/baz-1.0.tar.gz"] == ("url", None)
    assert got["named"] == ("url", None)
    assert got["requests"] == (None, "2.32.3")


def test_uv_path_dependency_is_not_a_root(tmp_path):
    # winhelper is a path dependency only installed on Windows; its own dependency must follow suit.
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "app"\nversion = "0"\nsource = { editable = "." }\n'
        'dependencies = [{ name = "winhelper", marker = "sys_platform == \'win32\'" }]\n\n'
        '[[package]]\nname = "winhelper"\nversion = "1"\nsource = { directory = "libs/winhelper" }\n'
        'dependencies = [{ name = "pywin32" }]\n\n'
        '[[package]]\nname = "pywin32"\nversion = "311"\nsource = { registry = "https://pypi.org/simple" }\n'
        'wheels = [{ url = "https://files.pythonhosted.org/p/pywin32-311-cp313-cp313-win_amd64.whl" }]\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    lin, win = build_targets(["3.13"], ["linux-x86_64", "win-amd64"])
    rows = {r.package.name: r for r in check([src], [lin, win])}
    assert rows["pywin32"].results == {lin: Status.NOT_NEEDED, win: Status.WHEEL}


def test_poetry_dict_markers_with_an_unconditional_group(tmp_path):
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "colorama"\nversion = "0.4.6"\ngroups = ["main", "dev"]\n'
        'markers = { main = "sys_platform == \\"win32\\"" }\nfiles = []\n\n[metadata]\nlock-version = "2.1"\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    assert src.packages[0].markers is None  # dev always installs it, so it's needed everywhere


def test_poetry_1x_lock_walks_dependency_markers(tmp_path):
    # Before lock-version 2.1, markers only sit on dependency edges.
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "app-lib"\nversion = "1.0"\n\n[package.dependencies]\n'
        'pywin32 = {version = ">=300", markers = "sys_platform == \\"win32\\""}\nrequests = "*"\n\n'
        '[[package]]\nname = "pywin32"\nversion = "306"\nfiles = []\n\n'
        '[[package]]\nname = "requests"\nversion = "2.32.3"\nfiles = []\n\n'
        '[metadata]\nlock-version = "1.1"\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    lin, win = build_targets(["3.13"], ["linux-x86_64", "win-amd64"])
    rows = {r.package.name: r for r in check([src], [lin, win])}
    assert rows["pywin32"].results[lin] is Status.NOT_NEEDED  # used to be a false "missing" on Linux
    assert rows["pywin32"].results[win] is not Status.NOT_NEEDED
    assert rows["requests"].results[lin] is not Status.NOT_NEEDED


def test_poetry_package_without_files_is_looked_up(tmp_path):
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "x"\nversion = "1"\n\n[metadata]\nlock-version = "2.1"\n'
    )
    (src,) = load_inputs([str(tmp_path)])
    assert src.packages[0].files is None  # not [] (which would mean "can't install")


def test_requirements_parsing_edge_cases(tmp_path):
    (tmp_path / "more.txt").write_text("purewheel==3.1\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text(
        "-rmore.txt\n"  # no space: used to be silently dropped
        "fastlib==2.0.0 \\\n    --hash sha256:00\n"  # --hash with a space used to abort the run
        "nowheels==0.9\t# a comment after a tab used to abort the run\n"
        "requests==2.32.3\t# see https://docs.python-requests.org\n"  # used to be skipped as a URL
        "named@https://example.com/named-1.0.tar.gz\n"
        "git+https://example.com/repo.git#subdirectory=src&egg=deep ; python_version >= '3.10'\n",
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path / "requirements.txt")])
    got = {p.name: (p.version, p.skip) for p in src.packages}
    assert got["purewheel"] == ("3.1", None)
    assert got["fastlib"] == ("2.0.0", None)
    assert got["nowheels"] == ("0.9", None)
    assert got["requests"] == ("2.32.3", None)
    assert got["named"] == (None, "url")
    assert got["deep"] == (None, "url")


def test_poetry_1x_dependency_cycle_is_still_walked(tmp_path):
    # sphinx and sphinxcontrib-applehelp depend on each other, so neither is "depended on by nothing".
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "requests"\nversion = "2.32.3"\nfiles = []\n\n'
        '[[package]]\nname = "sphinx"\nversion = "7.2.6"\nfiles = []\n\n[package.dependencies]\n'
        '"sphinxcontrib-applehelp" = "*"\ndocutils = ">=0.18"\n\n'
        '[[package]]\nname = "sphinxcontrib-applehelp"\nversion = "1.0.8"\nfiles = []\n\n'
        '[package.dependencies]\nSphinx = ">=5"\n\n'
        '[[package]]\nname = "docutils"\nversion = "0.20.1"\nfiles = []\n\n'
        '[metadata]\nlock-version = "1.1"\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    (lin,) = build_targets(["3.13"], ["linux-x86_64"])
    rows = {r.package.name: r.results[lin] for r in check([src], [lin])}
    assert all(s is not Status.NOT_NEEDED for s in rows.values()), rows  # docutils used to be "n/a"


def test_poetry_1x_edges_pick_the_version_in_range(tmp_path):
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "app-lib"\nversion = "1.0"\nfiles = []\n\n[package.dependencies]\n'
        'numpy = [{version = "1.24.4", markers = "python_version < \\"3.9\\""}, '
        '{version = ">=1.26", markers = "python_version >= \\"3.9\\""}]\n\n'
        '[[package]]\nname = "numpy"\nversion = "1.24.4"\nfiles = []\n\n'
        '[[package]]\nname = "numpy"\nversion = "1.26.4"\nfiles = []\n\n'
        '[metadata]\nlock-version = "1.1"\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    (t,) = build_targets(["3.12"], ["linux-x86_64"])
    got = {(r.package.name, r.package.version): r.results[t] for r in check([src], [t])}
    assert got[("numpy", "1.24.4")] is Status.NOT_NEEDED  # only for Python < 3.9
    assert got[("numpy", "1.26.4")] is not Status.NOT_NEEDED


def test_pipfile_lock_vcs_skipped(tmp_path):
    path = tmp_path / "Pipfile.lock"
    lock = {
        "default": {
            "hgpkg": {"hg": "https://hg.example.com/repo"},
            "svnpkg": {"svn": "svn://svn.example.com/repo"},
            "bzrpkg": {"bzr": "lp:mybranch"},
        }
    }
    path.write_text(json.dumps(lock), encoding="utf-8")
    (src,) = load_inputs([str(path)])
    pkgs = by_name(src)
    assert pkgs["hgpkg"].skip == "hg"
    assert pkgs["svnpkg"].skip == "svn"
    assert pkgs["bzrpkg"].skip == "bzr"


def test_pdm_lock_vcs_skipped(tmp_path):
    path = tmp_path / "pdm.lock"
    lock = (
        'version = "4.5"\n\n'
        '[[package]]\nname = "hgpkg"\nversion = "0.1"\nhg = "https://hg.example.com/repo"\n\n'
        '[[package]]\nname = "svnpkg"\nversion = "0.1"\nsvn = "svn://svn.example.com/repo"\n\n'
        '[[package]]\nname = "bzrpkg"\nversion = "0.1"\nbzr = "lp:mybranch"\n'
    )
    path.write_text(lock, encoding="utf-8")
    (src,) = load_inputs([str(path)])
    pkgs = by_name(src)
    assert pkgs["hgpkg"].skip == "hg"
    assert pkgs["svnpkg"].skip == "svn"
    assert pkgs["bzrpkg"].skip == "bzr"


def test_poetry_lock_vcs_skipped(tmp_path):
    path = tmp_path / "poetry.lock"
    lock = (
        '[[package]]\nname = "hgpkg"\nversion = "0.1"\n[package.source]\ntype = "hg"\n\n'
        '[[package]]\nname = "svnpkg"\nversion = "0.1"\n[package.source]\ntype = "svn"\n\n'
        '[[package]]\nname = "bzrpkg"\nversion = "0.1"\n[package.source]\ntype = "bzr"\n'
    )
    path.write_text(lock, encoding="utf-8")
    (src,) = load_inputs([str(path)])
    pkgs = by_name(src)
    assert pkgs["hgpkg"].skip == "hg"
    assert pkgs["svnpkg"].skip == "svn"
    assert pkgs["bzrpkg"].skip == "bzr"


def test_poetry_1x_roots_are_cycles_nothing_else_reaches(tmp_path):
    # a1<->a2 and z1<->z2 are cycles; z1 -> a1 only on Windows. Only the z cycle is a starting point,
    # so a1/a2 must not be needed on Linux (they used to be made roots too, by index order).
    pkg = '[[package]]\nname = "{n}"\nversion = "1"\nfiles = []\n\n[package.dependencies]\n{deps}\n\n'
    lock = (
        pkg.format(n="a1", deps='a2 = "*"')
        + pkg.format(n="a2", deps='a1 = "*"')
        + pkg.format(n="z1", deps='z2 = "*"\na1 = {version = "*", markers = "sys_platform == \\"win32\\""}')
        + pkg.format(n="z2", deps='z1 = "*"')
        + '[metadata]\nlock-version = "1.1"\n'
    )
    (tmp_path / "poetry.lock").write_text(lock, encoding="utf-8")
    (src,) = load_inputs([str(tmp_path)])
    lin, win = build_targets(["3.13"], ["linux-x86_64", "win-amd64"])
    rows = {r.package.name: r.results for r in check([src], [lin, win])}
    assert rows["a1"][lin] is Status.NOT_NEEDED and rows["a2"][lin] is Status.NOT_NEEDED
    assert rows["a1"][win] is not Status.NOT_NEEDED and rows["z1"][lin] is not Status.NOT_NEEDED


def test_poetry_1x_direct_dependency_from_pyproject_is_a_root(tmp_path):
    # colorama is a direct dependency, and click also needs it on Windows only.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\npython = "^3.10"\nclick = "*"\ncolorama = "*"\n', encoding="utf-8"
    )
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "click"\nversion = "8.1.7"\nfiles = []\n\n[package.dependencies]\n'
        'colorama = {version = "*", markers = "platform_system == \\"Windows\\""}\n\n'
        '[[package]]\nname = "colorama"\nversion = "0.4.6"\nfiles = []\n\n[metadata]\nlock-version = "1.1"\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path)])
    (lin,) = build_targets(["3.13"], ["linux-x86_64"])
    rows = {r.package.name: r.results[lin] for r in check([src], [lin])}
    assert rows["colorama"] is not Status.NOT_NEEDED


@pytest.mark.parametrize(
    "pyproject",
    [
        'project = "x"\n',
        '[tool.poetry.group]\ndev = "x"\n',
        "[project]\ndependencies = [1]\n",
        b"\xff\xfe".decode("latin-1"),
    ],
)
def test_odd_pyproject_never_breaks_a_poetry_run(tmp_path, pyproject):
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="latin-1")
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "x"\nversion = "1"\nfiles = []\n\n[metadata]\nlock-version = "1.1"\n', encoding="utf-8"
    )
    (src,) = load_inputs([str(tmp_path)])
    assert [p.name for p in src.packages] == ["x"]


@pytest.mark.parametrize(
    ("requires", "pythons", "outside"),
    [
        ("==3.13.*", ["3.13", "3.14t"], ["3.14t"]),
        (">=3.11.4", ["3.11", "3.12"], []),  # a patch-level floor still serves 3.11
        (">=3.11, <3.15", ["3.10", "3.14", "3.15"], ["3.10", "3.15"]),
        (None, ["3.14"], []),
        ("not a specifier", ["3.14"], []),
        ("==3.12.4", ["3.12", "3.13"], ["3.13"]),  # a pinned patch still serves its minor
        (">=3.11,<3.11.5", ["3.11"], []),
        ("==3.14.0rc2", ["3.14"], []),
        (">=3.10, !=3.12.*", ["3.12"], []),  # uv only uses the bounds, so 3.12's wheels stay
    ],
)
def test_pythons_outside_lock(requires, pythons, outside):
    source = Source("uv.lock", requires_python=requires)
    assert pythons_outside_lock(source, build_targets(pythons, ["linux-x86_64"])) == outside


def test_uv_lock_records_requires_python(tmp_path):
    (tmp_path / "uv.lock").write_text('version = 1\nrequires-python = "==3.13.*"\n', encoding="utf-8")
    (src,) = load_inputs([str(tmp_path)])
    assert src.requires_python == "==3.13.*"


def test_pylock_records_requires_python_and_index(tmp_path):
    (tmp_path / "pylock.toml").write_text(
        'lock-version = "1.0"\nrequires-python = "==3.13.*"\n\n[[packages]]\nname = "fastlib"\nversion = "2.0.0"\n'
        'index = "https://pypi.org/simple"\n'
        'wheels = [{ name = "fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl" }]\n',
        encoding="utf-8",
    )
    (src,) = load_inputs([str(tmp_path / "pylock.toml")])
    assert src.requires_python == "==3.13.*" and src.packages[0].registry == "https://pypi.org/simple"


def test_pipfile_lock_non_string_version_raises(tmp_path):
    path = tmp_path / "Pipfile.lock"
    path.write_text(json.dumps({"default": {"requests": {"version": 2}}}), encoding="utf-8")
    with pytest.raises(SourceError, match="default.requests has a version that isn't a string"):
        load_inputs([str(path)])


def test_poetry_lock_records_a_legacy_source_as_the_registry(tmp_path):
    path = tmp_path / "poetry.lock"
    path.write_text(
        '[[package]]\nname = "purewheel"\nversion = "3.1"\n'
        'files = [{file = "purewheel-3.1-cp313-cp313-win_amd64.whl", hash = "sha256:00"}]\n\n'
        '[package.source]\ntype = "legacy"\nurl = "https://pkgs.example.com/simple"\nreference = "private"\n\n'
        '[metadata]\nlock-version = "2.1"\n',
        encoding="utf-8",
    )
    (pkg,) = parse_poetry_lock(path).packages
    assert pkg.registry == "https://pkgs.example.com/simple"
