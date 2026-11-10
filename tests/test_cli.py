import json
import sys
import sysconfig

import pytest

from wheelgap import cli
from wheelgap.cli import main
from wheelgap.targets import ALIASES, DEFAULT_PLATFORMS, PLATFORMS


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.parametrize(
    "markers,expected",
    [
        (["sys_platform == 'linux'"], ["linux-x86_64", "linux-aarch64", "musl-x86_64", "musl-aarch64"]),
        (["platform_machine == 'aarch64'"], ["linux-aarch64", "musl-aarch64"]),
        (
            ["sys_platform == 'darwin'", "sys_platform == 'win32'"],
            ["macos-arm64", "macos-x86_64", "win-amd64", "win-arm64"],
        ),
        (
            ["sys_platform == 'linux' and platform_machine == 'aarch64'", "platform_machine == 'aarch64'"],
            ["linux-aarch64", "musl-aarch64"],
        ),
    ],
)
@pytest.mark.parametrize("input_kind", ["directory", "file", "package"])
def test_uv_required_environments(capsys, tmp_path, monkeypatch, fake_pypi, markers, expected, input_kind):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv]\nrequired-environments = " + json.dumps(markers), encoding="utf-8"
    )
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("purewheel==3.1\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    item = {"directory": str(tmp_path), "file": str(requirements), "package": "purewheel==3.1"}[input_kind]
    code, out, err = run(capsys, item, "-p", "3.13", "--json")
    assert code == 0
    assert [t["platform"] for t in json.loads(out)["targets"]] == expected
    project_path = "pyproject.toml" if input_kind == "package" else str(tmp_path / "pyproject.toml")
    assert "[tool.uv] required-environments" in err and project_path in err
    assert all(name in err for name in expected)
    if "musl-aarch64" in expected:
        assert "glibc" in err and "musl" in err
    assert fake_pypi == ["https://pypi.org/pypi/purewheel/3.1/json"]


@pytest.mark.parametrize(
    "config,image,flags,expected",
    [
        ("", False, ["-t", "win-amd64"], "win-amd64"),
        ('targets = ["macos-arm64"]', False, [], "macos-arm64"),
        ('targets = ["macos-arm64"]', True, [], "musl-x86_64"),
        ('targets = ["macos-arm64"]', True, ["-t", "win-amd64"], "win-amd64"),
    ],
)
def test_uv_required_environments_precedence(capsys, tmp_path, monkeypatch, fake_pypi, config, image, flags, expected):
    # Even invalid uv settings are irrelevant when another source selects the targets.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv]\nrequired-environments = ["invalid marker"]\n[tool.wheelgap]\n' + config,
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    if image:
        (tmp_path / "Dockerfile").write_text("FROM python:3.13-alpine\n", encoding="utf-8")
        flags = [*flags, "--dockerfile", str(tmp_path / "Dockerfile")]
    code, out, err = run(capsys, "purewheel==3.1", "-p", "3.13", "--json", *flags)
    assert code == 0 and not err
    assert [t["platform"] for t in json.loads(out)["targets"]] == [expected]


@pytest.mark.parametrize("python_source", ["flag", "config", "version-file", "running", "multiple"])
def test_uv_required_environments_uses_selected_python(capsys, tmp_path, monkeypatch, fake_pypi, python_source):
    text = "[tool.uv]\nrequired-environments = [\"python_version >= '3.13' and sys_platform == 'win32'\"]\n"
    flags = []
    (tmp_path / ".python-version").write_text("3.12\n", encoding="utf-8")
    if python_source == "config":
        text += '[tool.wheelgap]\npython = ["3.13"]\n'
    elif python_source == "version-file":
        (tmp_path / ".python-version").write_text("3.13\n", encoding="utf-8")
    elif python_source == "running":
        (tmp_path / ".python-version").unlink()
        monkeypatch.setattr(cli, "running_python", lambda: "3.13")
    else:
        flags = ["-p", "3.12,3.13"] if python_source == "multiple" else ["-p", "3.13"]
    (tmp_path / "pyproject.toml").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "purewheel==3.1", "--json", *flags)
    assert code == 0 and "required-environments" in err
    expected_pys = ["3.12", "3.13"] if python_source == "multiple" else ["3.13"]
    assert [(t["python"], t["platform"]) for t in json.loads(out)["targets"]] == [
        (py, plat) for py in expected_pys for plat in ("win-amd64", "win-arm64")
    ]


@pytest.mark.parametrize(
    "setting,message",
    [
        ("required-environments = 42", "a string or a list of strings"),
        ('required-environments = "linux"', "invalid marker"),
        ("required-environments = [42]", "list of strings"),
        ('required-environments = ["sys_platform == \'linux\'", "invalid"]', "invalid marker"),
        ('required-environments = [""]', "invalid marker"),
    ],
)
def test_uv_required_environments_errors(capsys, tmp_path, monkeypatch, fake_pypi, setting, message):
    (tmp_path / "pyproject.toml").write_text("[tool.uv]\n" + setting, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "purewheel==3.1", "-p", "3.13", "--json")
    assert code == 2 and not fake_pypi
    assert message in err and "required-environments" in err
    assert "error" in json.loads(out)


def test_uv_required_environments_accepts_a_single_string(capsys, tmp_path, monkeypatch, fake_pypi):
    # uv documents required-environments as a string or a list of strings.
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv]\nrequired-environments = \"sys_platform == 'win32'\"", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "purewheel==3.1", "-p", "3.13", "--json")
    assert code == 0 and "required-environments" in err
    assert [t["platform"] for t in json.loads(out)["targets"]] == ["win-amd64", "win-arm64"]


@pytest.mark.parametrize(
    "setting",
    [
        "required-environments = [\"sys_platform == 'emscripten'\"]",
        "required-environments = [\"sys_platform == 'android'\"]",
        "required-environments = [\"python_version < '3.13'\"]",
    ],
)
def test_uv_required_environments_matching_no_target_falls_back_to_defaults(
    capsys, tmp_path, monkeypatch, fake_pypi, setting
):
    # A mobile-only project, or `wheelgap requests` run in such a folder, still gets an answer.
    (tmp_path / "pyproject.toml").write_text("[tool.uv]\n" + setting, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "purewheel==3.1", "-p", "3.13", "--json")
    assert code == 0
    assert "matches none of the native targets" in err and "default targets" in err
    assert [t["platform"] for t in json.loads(out)["targets"]] == DEFAULT_PLATFORMS


@pytest.mark.parametrize("setting", ["", "required-environments = []", "environments = [\"sys_platform == 'linux'\"]"])
def test_uv_required_environments_absent_or_empty(capsys, tmp_path, monkeypatch, fake_pypi, setting):
    (tmp_path / "pyproject.toml").write_text("[tool.uv]\n" + setting, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "purewheel==3.1", "-p", "3.13", "--json")
    assert code == 0 and not err
    assert [t["platform"] for t in json.loads(out)["targets"]] == DEFAULT_PLATFORMS


@pytest.mark.parametrize("flags", [[], ["--markdown"]])
def test_uv_required_environments_note_is_on_stderr(capsys, tmp_path, monkeypatch, fake_pypi, flags):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv]\nrequired-environments = [\"sys_platform == 'linux'\"]", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "purewheel==3.1", "-p", "3.13", *flags)
    assert code == 0
    assert "required-environments" in err and "required-environments" not in out


def test_uv_required_environments_note_is_hidden_with_quiet(capsys, tmp_path, monkeypatch, fake_pypi):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv]\nrequired-environments = [\"sys_platform == 'linux'\"]", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, "purewheel==3.1", "-p", "3.13", "--quiet")
    assert code == 0 and not err and not out


@pytest.mark.parametrize(
    "config,image,flags,expected",
    [
        ("", None, ["--distro", "ubuntu-24.04"], "wheel"),
        ("", None, ["--distro", "ubuntu-22.04"], "missing"),
        ('distro = "ubuntu-24.04"', None, [], "wheel"),
        ('distro = "ubuntu-24.04"\nglibc = "2.35"', None, [], "missing"),
        ('glibc = "2.35"', None, ["--distro", "ubuntu-24.04"], "wheel"),
        ('distro = "ubuntu-22.04"', None, ["--distro", "ubuntu-24.04"], "wheel"),
        ('distro = "ubuntu-24.04"', "3.13-bookworm", [], "missing"),
        ('distro = "ubuntu-22.04"', "3.13-trixie", [], "wheel"),
        ('glibc = "2.41"', "3.13-bookworm", ["--distro", "ubuntu-24.04"], "wheel"),
        ("", "3.13-trixie", ["--distro", "ubuntu-22.04"], "missing"),
        ("", "3.13-bookworm", ["--distro", "ubuntu-24.04", "--glibc", "2.35"], "missing"),
        ("", None, ["--glibc", "2.39", "--distro", "ubuntu-22.04"], "wheel"),
        ('distro = "ubuntu-24.04"', None, ["--glibc", "2.35"], "missing"),
        ('distro = "ubuntu-22.04"', "3.13", [], "wheel"),  # python:3.13 is trixie (2.41), which beats config distro
        ('distro = "ubuntu-22.04"', "3.13.1-slim", [], "missing"),  # a patch tag sets no glibc: config distro applies
    ],
)
def test_distro_glibc_precedence(capsys, fixtures, tmp_path, fake_pypi, config, image, flags, expected):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\n" + config + "\n", encoding="utf-8")
    args = [str(tmp_path), "-p", "3.13", "-t", "linux-aarch64", "--json", *flags]
    if image:
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(f"FROM python:{image}\n", encoding="utf-8")
        args += ["--dockerfile", str(dockerfile)]
    code, out, err = run(capsys, *args)
    assert not err and not fake_pypi
    data = json.loads(out)
    package = next(p for p in data["packages"] if p["name"] == "newglibc")
    assert package["results"] == {"linux-aarch64": expected}
    assert code == (0 if expected == "wheel" else 1)


@pytest.mark.parametrize(
    "config,image,flags,expected",
    [
        ('musl = "1.1"', None, ["--distro", "alpine"], "wheel"),
        ('distro = "alpine"', None, [], "wheel"),
        ('distro = "alpine"\nmusl = "1.1"', None, [], "source"),
        ("", "3.13-alpine3.12", ["--distro", "alpine"], "wheel"),
        ('distro = "alpine"', "3.13-alpine3.12", [], "source"),
        ("", None, ["--distro", "alpine", "--musl", "1.1"], "source"),
        ("", None, ["--musl", "1.1", "--distro", "alpine"], "source"),
        ('musl = "1.1"', None, ["--distro", "ubuntu-24.04"], "source"),
    ],
)
def test_distro_musl_precedence(capsys, fixtures, tmp_path, fake_pypi, config, image, flags, expected):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\n" + config + "\n", encoding="utf-8")
    args = [str(tmp_path), "-p", "3.13", "-t", "musl-x86_64", "--json", *flags]
    if image:
        dockerfile = tmp_path / "Dockerfile"
        dockerfile.write_text(f"FROM python:{image}\n", encoding="utf-8")
        args += ["--dockerfile", str(dockerfile)]
    code, out, err = run(capsys, *args)
    assert code == 1 and not err and not fake_pypi  # newglibc is always missing on musl
    package = next(p for p in json.loads(out)["packages"] if p["name"] == "armlib")
    assert package["results"] == {"musl-x86_64": expected}


@pytest.mark.parametrize("distro", ["alpine", "ubuntu-22.04"])
@pytest.mark.parametrize("selection", [[], ["-t", "all"], ["-t", "win-amd64"], ["-t", "linux/arm64"]])
def test_distro_does_not_select_targets(capsys, fixtures, distro, selection):
    args = [str(fixtures / "uvproj"), "-p", "3.13", "--json", *selection]
    _, before, _ = run(capsys, *args)
    _, after, _ = run(capsys, *args, "--distro", distro)
    assert json.loads(before)["targets"] == json.loads(after)["targets"]


@pytest.mark.parametrize("flags", [[], ["--distro", "debian-12"]])
@pytest.mark.parametrize("config", ["distro = 22", 'distro = "typo"', 'distro = ""'])
def test_invalid_config_distro(capsys, tmp_path, fake_pypi, config, flags):
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\n" + config, encoding="utf-8")
    code, out, err = run(capsys, str(tmp_path), *flags)
    assert code == 2 and not out and not fake_pypi
    assert "distro" in err and "ubuntu-22.04" in err


@pytest.mark.parametrize("extra", [[], ["--glibc", "2.35"], ["--list-targets", "--json"]])
def test_invalid_cli_distro(capsys, fake_pypi, extra):
    code, out, err = run(capsys, "--distro", "typo", *extra)
    assert code == 2 and not fake_pypi
    assert "unknown distro 'typo'" in err and "ubuntu-22.04" in err
    if "--json" in extra:  # with --json an input error is also printed as JSON on stdout
        assert json.loads(out)["error"]["type"] == "TargetError"
    else:
        assert not out


@pytest.mark.parametrize("as_json", [False, True])
def test_distro_list_targets_limits(capsys, as_json):
    args = ["--list-targets", "--distro", "amazonlinux-2"] + (["--json"] if as_json else [])
    code, out, err = run(capsys, *args)
    assert code == 0 and not err
    assert "glibc <= 2.26" in out
    if as_json:
        assert [row["name"] for row in json.loads(out)] == list(PLATFORMS)
    else:
        assert "ubuntu-22.04" in out and "glibc 2.35" in out and "rocky-9" in out


def test_gaps_exit_1(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures / "uvproj"), "-p", "3.13", "-t", "linux-x86_64,musl-aarch64")
    assert code == 1
    assert "fastlib" in out and "● source" in out
    assert "purewheel" not in out  # fine everywhere, so hidden
    assert "have gaps" in out
    assert "skipped 2 non-index packages" in out
    assert "\033[" not in out  # no colour when not a TTY


def test_docker_platform_names_on_cli(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "uvproj"),
        "-p",
        "3.13",
        "-t",
        "linux/amd64,linux/arm64,linux/arm64/v8",
        "--json",
    )
    assert code == 1
    assert not err
    data = json.loads(out)
    assert [target["label"] for target in data["targets"]] == ["linux-x86_64", "linux-aarch64"]


def test_exit_zero_and_all(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures / "uvproj"), "-p", "3.13", "--all", "--exit-zero")
    assert code == 0
    assert "purewheel" in out and "–" in out


def test_clean_exit_0(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures / "pylockproj"), "-p", "3.13", "-t", "linux-aarch64")
    assert code == 0
    assert "The 1 package has a wheel for 1 target." in out


def test_quiet_reports_only_the_gap_summary_and_names(capsys, fixtures):
    result = run(capsys, str(fixtures / "armgap"), "-p", "3.13", "-t", "musl-aarch64", "--quiet", "--all")
    assert result == (
        1,
        "2 of 2 packages have gaps: 1 will build from source, 1 can't install at all.\ngaps: armlib, newglibc\n",
        "",
    )


def test_quiet_with_no_gaps_is_silent(capsys, fixtures):
    assert run(capsys, str(fixtures / "pylockproj"), "-p", "3.13", "-t", "linux-aarch64", "-q") == (0, "", "")


def test_quiet_preserves_json_and_exit_status(capsys, fixtures):
    args = (str(fixtures / "armgap"), "-p", "3.13", "-t", "musl-aarch64", "--json")
    assert run(capsys, *args, "--quiet") == run(capsys, *args)


def test_quiet_preserves_list_targets_output(capsys):
    assert run(capsys, "--list-targets", "--quiet") == run(capsys, "--list-targets")


def test_quiet_still_reports_bad_input(capsys, fixtures):
    code, out, err = run(capsys, str(fixtures / "empty"), "--quiet")
    assert code == 2 and not out and "no lockfile found" in err


def test_quiet_preserves_fail_on_missing(capsys, fake_pypi):
    code, out, err = run(capsys, "nowheels", "-p", "3.13", "-t", "linux", "--fail-on", "missing", "--quiet")
    assert code == 0 and not err
    summary, names = out.splitlines()
    assert "will build from source" in summary
    assert names == "gaps: nowheels"


def test_quiet_from_pyproject(capsys, fixtures, tmp_path):
    (tmp_path / "pylock.toml").write_bytes((fixtures / "pylockproj" / "pylock.toml").read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nquiet = true\n", encoding="utf-8")
    assert run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-aarch64") == (0, "", "")


def test_pyproject_quiet_must_be_boolean(capsys, tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.wheelgap]\nquiet = "true"\n', encoding="utf-8")
    code, out, err = run(capsys, str(tmp_path))
    assert code == 2 and not out and "quiet must be a boolean" in err


@pytest.mark.parametrize("from_config", [False, True])
def test_quiet_conflicts_with_explain(capsys, tmp_path, from_config):
    args = ["--explain"]
    if from_config:
        (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nquiet = true\n", encoding="utf-8")
        args.append(str(tmp_path))
    else:
        args.append("--quiet")
    with pytest.raises(SystemExit) as caught:
        main(args)
    assert caught.value.code == 2
    error = capsys.readouterr().err.splitlines()[-1]
    assert "--quiet" in error and "--explain" in error


def test_json(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures / "uvproj"), "-p", "3.13,3.14t", "-t", "win-amd64", "--json")
    assert code == 1
    data = json.loads(out)
    assert [t["label"] for t in data["targets"]] == ["3.13 win-amd64", "3.14t win-amd64"]
    fastlib = next(p for p in data["packages"] if p["name"] == "fastlib")
    assert fastlib["results"] == {"3.13 win-amd64": "wheel", "3.14t win-amd64": "source"}
    assert {"name": "gitdep", "reason": "git"} in data["skipped"]
    assert data["summary"]["with_gaps"] >= 1


def test_color_always(capsys, fixtures):
    _, out, _ = run(capsys, str(fixtures / "uvproj"), "-p", "3.13", "--color", "always")
    assert "\033[33m" in out


def test_adhoc_packages(capsys, fake_pypi):
    code, out, _ = run(capsys, "nowheels", "purewheel", "-p", "3.13", "-t", "linux")
    assert code == 1
    assert "nowheels" in out and "0.9" in out


@pytest.mark.parametrize("input_name", ["pipenvproj", "pipenvproj/Pipfile.lock"])
def test_pipfile_lock_json(capsys, fixtures, fake_pypi, input_name):
    code, out, err = run(
        capsys, str(fixtures / input_name), "-p", "3.13", "-t", "linux-x86_64,musl-aarch64,win-amd64", "--json"
    )
    assert code == 1
    assert not err
    data = json.loads(out)
    assert data["sources"] == ["Pipfile.lock"]
    packages = {p["name"]: p for p in data["packages"]}
    assert packages["fastlib"]["version"] == "2.0.0"
    assert packages["fastlib"]["results"] == {"linux-x86_64": "wheel", "musl-aarch64": "source", "win-amd64": "source"}
    assert packages["winonly"]["results"] == {"linux-x86_64": "n/a", "musl-aarch64": "n/a", "win-amd64": "wheel"}
    assert packages["purewheel"]["results"] == {"linux-x86_64": "wheel", "musl-aarch64": "wheel", "win-amd64": "wheel"}
    assert data["skipped"] == [
        {"name": "gitdep", "reason": "git"},
        {"name": "localpkg", "reason": "path"},
        {"name": "archivepkg", "reason": "file"},
        {"name": "editablepkg", "reason": "editable"},
    ]
    assert sorted(fake_pypi) == [
        "https://pypi.org/pypi/fastlib/2.0.0/json",
        "https://pypi.org/pypi/purewheel/3.1/json",
        "https://pypi.org/pypi/winonly/311/json",
    ]


@pytest.mark.parametrize("input_name", ["pdmproj", "pdmproj/pdm.lock"])
def test_pdm_lock_json(capsys, fixtures, fake_pypi, input_name):
    code, out, err = run(capsys, str(fixtures / input_name), "-p", "3.13", "-t", "linux-x86_64,musl-aarch64", "--json")
    assert code == 1
    assert not err
    data = json.loads(out)
    assert data["sources"] == ["pdm.lock"]
    packages = {p["name"]: p for p in data["packages"]}
    assert packages["fastlib"]["results"] == {"linux-x86_64": "source", "musl-aarch64": "wheel"}
    assert "backport" not in packages  # marker excludes it on 3.13
    assert data["skipped"] == [{"name": "localpkg", "reason": "path"}]
    assert not fake_pypi


def test_pdm_lock_entry_without_files_is_looked_up(capsys, fake_pypi, tmp_path):
    (tmp_path / "pdm.lock").write_text(
        '[[package]]\nname = "purewheel"\nversion = "3.1"\n\n'
        '[[package]]\nname = "nowheels"\nversion = "0.9"\nfiles = []\n',
        encoding="utf-8",
    )
    code, out, _ = run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-x86_64", "--json")
    packages = {p["name"]: p for p in json.loads(out)["packages"]}
    assert packages["purewheel"]["results"] == {"linux-x86_64": "wheel"}  # filled in from PyPI
    assert packages["nowheels"]["results"] == {"linux-x86_64": "missing"}  # an empty list is taken as recorded
    assert fake_pypi == ["https://pypi.org/pypi/purewheel/3.1/json"]
    assert code == 1


def test_pdm_lock_marker_on_older_python(capsys, fixtures, fake_pypi):
    code, out, err = run(capsys, str(fixtures / "pdmproj"), "-p", "3.12", "-t", "linux-x86_64", "--json")
    assert code == 1
    assert not err
    packages = {p["name"]: p for p in json.loads(out)["packages"]}
    assert packages["backport"]["results"] == {"linux-x86_64": "source"}
    assert not fake_pypi


def test_pipfile_lock_in_current_directory(capsys, fixtures, fake_pypi, monkeypatch):
    monkeypatch.chdir(fixtures / "pipenvproj")
    code, out, err = run(capsys, "-p", "3.13", "-t", "linux-x86_64")
    assert code == 0
    assert not err
    assert "skipped 4 non-index packages" in out
    assert "editablepkg (editable)" in out
    assert "All 2 packages have a wheel for 1 target." in out


def test_pipfile_lock_private_index_is_not_looked_up(capsys, tmp_path, fake_pypi):
    path = tmp_path / "Pipfile.lock"
    lock = {
        "_meta": {
            "sources": [
                {"name": "pypi", "url": "https://pypi.org/simple"},
                {"name": "internal", "url": "https://pkgs.example.com/simple"},
            ]
        },
        "default": {
            "acme-utils": {"version": "==2.1.0", "index": "internal"},
            "fastlib": {"version": "==2.0.0", "index": "pypi"},
        },
    }
    path.write_text(json.dumps(lock), encoding="utf-8")
    code, out, err = run(capsys, str(path), "-p", "3.13", "-t", "linux-x86_64", "--json")
    assert code == 0, err
    data = json.loads(out)
    assert data["skipped"] == [{"name": "acme-utils", "reason": "private index"}]
    assert [p["name"] for p in data["packages"]] == ["fastlib"]
    assert fake_pypi and not any("acme" in url for url in fake_pypi)


def test_poetry_lock_private_index_with_no_files_is_not_looked_up(capsys, tmp_path, fake_pypi):
    path = tmp_path / "poetry.lock"
    path.write_text(
        '[[package]]\nname = "acme-utils"\nversion = "2.1.0"\n\n'
        '[package.source]\ntype = "legacy"\nurl = "https://pkgs.example.com/simple"\nreference = "private"\n\n'
        '[metadata]\nlock-version = "1.1"\npython-versions = ">=3.8"\n\n'
        '[metadata.hashes]\nacme-utils = ["sha256:00"]\n',
        encoding="utf-8",
    )
    code, out, err = run(capsys, str(path), "-p", "3.13", "-t", "linux-x86_64", "--json")
    assert code == 0, err
    data = json.loads(out)
    assert data["skipped"] == [{"name": "acme-utils", "reason": "private index"}]
    assert not data["packages"]
    assert not any("acme" in url for url in fake_pypi)


def test_offline_allows_a_private_index_package_with_no_files(capsys, tmp_path, fake_pypi):
    # fill_all skips it without contacting any index, so --offline has nothing to refuse.
    path = tmp_path / "poetry.lock"
    path.write_text(
        '[[package]]\nname = "acme-utils"\nversion = "2.1.0"\n\n'
        '[package.source]\ntype = "legacy"\nurl = "https://pkgs.example.com/simple"\nreference = "private"\n\n'
        '[metadata]\nlock-version = "1.1"\npython-versions = ">=3.8"\n\n'
        '[metadata.hashes]\nacme-utils = ["sha256:00"]\n',
        encoding="utf-8",
    )
    code, out, err = run(capsys, str(path), "--offline", "-p", "3.13", "-t", "linux-x86_64", "--json")
    assert code == 0, err
    assert json.loads(out)["skipped"] == [{"name": "acme-utils", "reason": "private index"}]
    assert fake_pypi == []


def test_malformed_pipfile_lock_exits_2(capsys, tmp_path, fake_pypi):
    path = tmp_path / "Pipfile.lock"
    path.write_text("{invalid", encoding="utf-8")
    code, _, err = run(capsys, str(path))
    assert code == 2
    assert "JSONDecodeError" in err
    assert not fake_pypi


def test_errors_exit_2(capsys, fixtures):
    code, _, err = run(capsys, str(fixtures / "empty"))
    assert code == 2 and "no lockfile found" in err
    code, _, err = run(capsys, str(fixtures / "uvproj"), "-t", "solaris")
    assert code == 2 and "unknown target" in err
    code, _, err = run(capsys, str(fixtures / "uvproj"), "-p", "four")
    assert code == 2


def test_list_targets(capsys):
    code, out, err = run(capsys, "--list-targets")
    assert code == 0 and not err
    lines = out.splitlines()
    assert lines[0].split() == ["name", "aliases", "accepts"]
    alpine = next(line for line in lines if line.startswith("musl-x86_64 "))
    assert "alpine" in alpine and "musl <= 1.2" in alpine
    assert "linux/arm64" in out


def test_list_targets_json_includes_every_platform_and_alias(capsys):
    code, out, err = run(capsys, "--list-targets", "--json")
    assert code == 0 and not err
    rows = json.loads(out)
    assert [row["name"] for row in rows] == list(PLATFORMS)
    assert {alias: row["name"] for row in rows for alias in row["aliases"]} == ALIASES
    by_name = {row["name"]: row for row in rows}
    assert "glibc <= 2.36" in by_name["linux-x86_64"]["accepts"]
    assert "macOS <= 14.0" in by_name["macos-arm64"]["accepts"]
    assert by_name["pyodide"]["accepts"] == "pyodide wasm32, Python 3.11, 3.12, 3.13, 3.14 (not in -t all)"
    assert by_name["linux-s390x"]["accepts"] == "manylinux s390x, glibc 2.17 to 2.36 (not in -t all)"
    assert by_name["linux-aarch64"]["accepts"] == "manylinux aarch64, glibc 2.17 to 2.36"


def test_list_targets_respects_compatibility_overrides(capsys):
    flags = ("--list-targets", "--glibc", "2.28", "--musl", "1.1", "--macos", "13.0")
    code, out, err = run(capsys, *flags)
    assert code == 0 and not err
    assert "glibc <= 2.28" in out

    code, out, err = run(capsys, *flags, "--json")
    assert code == 0 and not err
    by_name = {row["name"]: row["accepts"] for row in json.loads(out)}
    assert "glibc <= 2.28" in by_name["linux-x86_64"] and "glibc 2.17 to 2.28" in by_name["linux-aarch64"]
    assert "musl <= 1.1" in by_name["musl-aarch64"]
    assert "macOS <= 13.0" in by_name["macos-x86_64"]


def test_mobile_flags_and_config(capsys, fake_pypi, tmp_path, monkeypatch):
    code, out, _ = run(capsys, "--list-targets", "--ios", "16.4", "--android-api", "30", "--json")
    by_name = {row["name"]: row["accepts"] for row in json.loads(out)}
    assert by_name["ios-arm64"] == "ios arm64_iphoneos, iOS <= 16.4 (wheels only; -t mobile, not in -t all)"
    assert by_name["android-x86_64"] == "android x86_64, API level <= 30 (wheels only; -t mobile, not in -t all)"

    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.wheelgap]\nios = "15.0"\nandroid-api = 28\n')  # an int is fine
    by_name = {row["name"]: row["accepts"] for row in json.loads(run(capsys, "--list-targets", "--json")[1])}
    assert "iOS <= 15.0" in by_name["ios-arm64"] and "API level <= 28" in by_name["android-arm64"]
    code, out, _ = run(capsys, "nowheels==0.9", "purewheel==3.1", "-p", "3.13", "-t", "mobile", "--json")
    data = json.loads(out)
    assert code == 1
    statuses = {p["name"]: set(p["results"].values()) for p in data["packages"]}
    assert statuses == {"nowheels": {"missing"}, "purewheel": {"wheel"}}
    code, _, err = run(capsys, "--list-targets", "--ios", "11.0")
    assert code == 2 and "iOS 11.0 is too old" in err
    code, _, err = run(capsys, "six", "-p", "3.12", "-t", "android")
    assert code == 2 and "need Python 3.13" in err
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nandroid-api = true\n")
    code, _, err = run(capsys, "--list-targets")
    assert code == 2 and 'android-api must be an integer or a string like "24"' in err


def test_list_targets_rejects_invalid_compatibility_version(capsys):
    code, out, err = run(capsys, "--list-targets", "--glibc", "bad")
    assert code == 2 and not out
    assert "can't parse glibc version" in err


def test_defaults_from_pyproject(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures / "configproj"), "--json")
    assert code == 1
    assert [t["label"] for t in json.loads(out)["targets"]] == ["3.13 win-amd64", "3.14t win-amd64"]


def test_flags_beat_pyproject(capsys, fixtures):
    _, out, _ = run(capsys, str(fixtures / "configproj"), "-p", "3.13", "-t", "linux-x86_64", "--json")
    assert [t["label"] for t in json.loads(out)["targets"]] == ["linux-x86_64"]


def test_bad_pyproject_exit_2(capsys, fixtures, tmp_path):
    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nglibc = 2.31\n")
    code, _, err = run(capsys, str(tmp_path))
    assert code == 2 and "glibc must be a string" in err


def test_json_reports_input_errors_on_stdout(capsys, fixtures, tmp_path):
    def json_error(*argv):
        code, out, err = run(capsys, *argv, "--json")
        assert code == 2 and err.startswith("wheelgap: error: ")  # stderr and exit code as without --json
        return json.loads(out)["error"]

    def plain_out(*argv):
        code, out, _ = run(capsys, *argv)
        assert code == 2
        return out

    missing = str(tmp_path / "nonexistent.lock")
    error = json_error(missing)
    assert set(error) == {"message", "type"} and "nonexistent.lock" in error["message"]
    assert plain_out(missing) == ""

    error = json_error("six", "-t", "nosuchplatform")
    assert error["type"] == "TargetError" and "nosuchplatform" in error["message"]
    assert plain_out("six", "-t", "nosuchplatform") == ""

    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nglibc = 2.31\n")
    error = json_error(str(tmp_path))
    assert error["type"] == "SourceError" and "glibc must be a string" in error["message"]
    assert plain_out(str(tmp_path)) == ""

    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap\n")  # malformed TOML: the catch-all branch
    error = json_error(str(tmp_path))
    assert error["type"] == "TOMLDecodeError"
    assert plain_out(str(tmp_path)) == ""

    error = json_error("--list-targets", "--glibc", "bad")  # --list-targets --json normally prints a list
    assert error["type"] == "TargetError" and "glibc" in error["message"]


def test_running_python_reports_freethreaded(monkeypatch):
    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: 1 if name == "Py_GIL_DISABLED" else None)
    assert cli.running_python() == f"{sys.version_info.major}.{sys.version_info.minor}t"


@pytest.mark.skipif(not sysconfig.get_config_var("Py_GIL_DISABLED"), reason="needs a free-threaded build")
def test_default_python_on_freethreaded_build(capsys, fixtures):
    code = main([str(fixtures / "pylockproj"), "-t", "linux-aarch64", "--json"])
    assert code in (0, 1)
    assert json.loads(capsys.readouterr().out)["targets"][0]["python"].endswith("t")


def project_with_lock(tmp_path, fixtures):
    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    return tmp_path


def test_python_version_file_sets_default(capsys, fixtures, tmp_path):
    project = project_with_lock(tmp_path, fixtures)
    (project / ".python-version").write_text("3.14t\n", encoding="utf-8")
    code, out, err = run(capsys, str(project), "-t", "win-amd64", "--json")
    assert code == 1 and not err
    assert [t["python"] for t in json.loads(out)["targets"]] == ["3.14t"]


def test_python_version_file_several_lines_and_patch_versions(capsys, fixtures, tmp_path):
    project = project_with_lock(tmp_path, fixtures)
    (project / ".python-version").write_text("3.13.1\n# both builds\n\n3.14t\n", encoding="utf-8")
    _, out, _ = run(capsys, str(project), "-t", "win-amd64", "--json")
    assert [t["python"] for t in json.loads(out)["targets"]] == ["3.13", "3.14t"]


def test_flags_beat_python_version_file(capsys, fixtures, tmp_path):
    project = project_with_lock(tmp_path, fixtures)
    (project / ".python-version").write_text("3.14t\n", encoding="utf-8")
    _, out, _ = run(capsys, str(project), "-p", "3.12", "-t", "win-amd64", "--json")
    assert [t["python"] for t in json.loads(out)["targets"]] == ["3.12"]


def test_pyproject_beats_python_version_file(capsys, fixtures, tmp_path):
    project = project_with_lock(tmp_path, fixtures)
    (project / ".python-version").write_text("3.14t\n", encoding="utf-8")
    (project / "pyproject.toml").write_text('[tool.wheelgap]\npython = ["3.12"]\n', encoding="utf-8")
    _, out, _ = run(capsys, str(project), "-t", "win-amd64", "--json")
    assert [t["python"] for t in json.loads(out)["targets"]] == ["3.12"]


def test_python_version_file_skips_unusable_lines(capsys, fixtures, tmp_path):
    project = project_with_lock(tmp_path, fixtures)
    (project / ".python-version").write_text("system\npypy@3.10\n3.13\n", encoding="utf-8")
    code, out, err = run(capsys, str(project), "-t", "win-amd64", "--json")
    assert code == 1
    assert "ignoring 'system'" in err and "ignoring 'pypy@3.10'" in err
    assert [t["python"] for t in json.loads(out)["targets"]] == ["3.13"]


def test_python_version_file_with_nothing_usable_uses_running_python(capsys, fixtures, tmp_path):
    project = project_with_lock(tmp_path, fixtures)
    (project / ".python-version").write_text("system\n", encoding="utf-8")
    _, out, _ = run(capsys, str(project), "-t", "win-amd64", "--json")
    assert [t["python"] for t in json.loads(out)["targets"]] == [cli.running_python()]


def test_text_summary_lists_only_targets_with_gaps(capsys, fixtures):
    code, out, _ = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64,musl-aarch64,win-arm64",
        "--glibc",
        "2.39",
    )
    assert code == 1
    lines = out.splitlines()
    summary_index = next(i for i, line in enumerate(lines) if "have gaps:" in line)
    assert lines[summary_index - 2 : summary_index] == [
        "musl-aarch64  1 from source, 1 missing",
        "win-arm64     1 from source, 1 missing",
    ]
    assert not any(line.startswith("linux-aarch64 ") for line in lines)


def test_single_target_has_no_per_target_summary(capsys, fixtures):
    code, out, _ = run(capsys, str(fixtures / "armgap"), "-p", "3.13", "-t", "musl-aarch64")
    assert code == 1
    assert "have gaps:" in out
    assert not any(line.startswith("musl-aarch64 ") for line in out.splitlines())


def test_gap_summary_counts_each_package_once():
    from wheelgap.check import Row, Status
    from wheelgap.cli import Style, gap_summary
    from wheelgap.sources import Package
    from wheelgap.targets import build_targets

    lin, pyo = build_targets(["3.13"], ["linux", "pyodide"])
    a = Row(Package("a", "1"), {lin: Status.SOURCE, pyo: Status.MISSING})
    b = Row(Package("b", "1"), {lin: Status.UNKNOWN, pyo: Status.UNKNOWN})
    c = Row(Package("c", "1"), {lin: Status.SOURCE, pyo: Status.WHEEL})
    assert gap_summary([a, b, c], 3, Style(False)) == (
        "3 of 3 packages have gaps: 1 will build from source, 1 can't install at all, 1 unknown."
    )
    # singular when only one package in total
    assert gap_summary([a], 1, Style(False)) == ("1 of 1 package has gaps: 1 can't install at all.")


def test_quiet_from_pyproject_allows_json_explain(capsys, fixtures, tmp_path):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nquiet = true\n", encoding="utf-8")
    code, out, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "musl-aarch64", "--json", "--explain")
    assert code == 1 and not err  # used to exit 2 with a usage error
    assert "rejected" in json.loads(out)["packages"][0]


def test_no_quiet_overrides_pyproject(capsys, fixtures, tmp_path):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nquiet = true\n", encoding="utf-8")
    code, out, _ = run(capsys, str(tmp_path), "-p", "3.13", "-t", "musl-aarch64", "--no-quiet", "--explain")
    assert code == 1 and "via: app → armlib" in out and "is a glibc wheel" in out  # full --explain output


def test_quiet_still_says_why_a_package_is_unknown(capsys, fake_pypi):
    code, out, err = run(capsys, "does-not-exist==1.0", "-p", "3.13", "-t", "linux", "-q")
    assert code == 1
    assert "1 unknown" in out
    assert "could not check does-not-exist: not on index" in err


def test_python_version_file_with_a_bom(capsys, fixtures, tmp_path):
    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    (tmp_path / ".python-version").write_text("﻿3.12\n", encoding="utf-8")  # as Notepad saves it
    code, out, err = run(capsys, str(tmp_path), "-t", "win-amd64", "--json")
    assert not err and [t["python"] for t in json.loads(out)["targets"]] == ["3.12"]


def test_list_targets_uses_pyproject_limits(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.wheelgap]\nglibc = "2.17"\n', encoding="utf-8")
    code, out, _ = run(capsys, "--list-targets")
    assert code == 0 and "glibc <= 2.17" in out


def test_simple_index_url_exits_2(capsys, monkeypatch):
    monkeypatch.setenv("WHEELGAP_INDEX_URL", "https://pypi.org/simple")
    code, _, err = run(capsys, "requests==2.32.3", "-t", "linux")
    assert code == 2 and "needs the JSON API" in err


def test_output_is_utf8_on_a_cp1252_pipe(monkeypatch, capsys):
    import io
    import sys as _sys

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(_sys, "stdout", stream)
    assert main(["--list-targets"]) == 0
    stream.flush()
    assert stream.encoding.lower().replace("-", "") == "utf8"


def test_markdown_report(capsys, fake_pypi, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")  # as in the Action: Markdown must still have no colour codes
    code, out, err = run(
        capsys,
        "fastlib==2.0.0",
        "nowheels==0.9",
        "purewheel==3.1",
        "-p",
        "3.13",
        "-t",
        "linux-x86_64,win-amd64",
        "--markdown",
    )
    assert code == 1 and not err
    lines = out.splitlines()
    assert lines[0] == "### wheelgap: 2 of 3 packages have gaps"
    assert "| package | version | linux-x86\\_64 | win-amd64 |" in lines
    assert "| --- | --- | --- | --- |" in lines
    assert "| fastlib | 2.0.0 | ✓ | ● source |" in lines
    assert "| nowheels | 0.9 | ● source | ● source |" in lines
    assert "purewheel" not in out  # hidden like the text table, unless --all
    assert "2 of 3 packages have gaps: 2 will build from source." in lines
    assert "\033[" not in out and "<details>" not in out


def test_markdown_all_explain_and_quiet(capsys, fake_pypi, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nquiet = true\n")
    code, out, err = run(
        capsys, "fastlib==2.0.0", "purewheel==3.1", "-p", "3.13", "-t", "win-amd64", "--markdown", "--all", "--explain"
    )
    assert code == 1 and not err  # quiet = true doesn't clash with --explain, as with --json
    assert "| purewheel | 3.1 | ✓ |" in out.splitlines()
    assert "<details><summary>Why (rejected wheels)</summary>" in out
    assert "fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl" in out


def test_markdown_clean_run(capsys, fake_pypi):
    code, out, _ = run(
        capsys, "purewheel==3.1", "-p", "3.13", "-t", "linux-x86_64,win-amd64", "--markdown", "--exit-zero"
    )
    assert code == 0
    assert out.splitlines()[0] == "### wheelgap: every package has a wheel for all 2 targets"
    assert "|" not in out


def test_markdown_and_json_conflict(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["six", "--json", "--markdown"])
    assert exc.value.code == 2
    assert "--json and --markdown" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--list-targets", "--json", "--markdown"])
    assert "--json and --markdown" in capsys.readouterr().err


def test_markdown_escapes_and_fail_on_missing(capsys, fake_pypi, tmp_path):
    req = tmp_path / "req_[a]#b.txt"  # characters every OS allows in a filename
    req.write_text("nowheels==0.9\nhttps://files.example.com/my_pkg-1.0.tar.gz\n")
    code, out, _ = run(capsys, str(req), "-p", "3.13", "-t", "win-amd64", "--markdown", "--fail-on", "missing")
    assert code == 0
    assert "`req_[a]#b.txt`" in out  # paths and URLs go in code spans, shown exactly and never linked
    assert "`https://files.example.com/my_pkg-1.0.tar.gz` (url)" in out
    assert "Source builds are allowed (fail-on missing), so this run passes." in out
    # The Action's summary run adds --exit-zero; the note depends on --fail-on, not on that.
    code, out2, _ = run(capsys, str(req), "-p", "3.13", "-t", "win-amd64", "--markdown", "--exit-zero")
    assert "Source builds are allowed" not in out2
    _, out3, _ = run(
        capsys, str(req), "-p", "3.13", "-t", "win-amd64", "--markdown", "--fail-on", "missing", "--exit-zero"
    )
    assert "Source builds are allowed" in out3


def test_markdown_helpers():
    from wheelgap.cli import _code, _md

    assert _md("./vendor/__x__/ a|b R&amp;D") == "./vendor/\\_\\_x\\_\\_/ a\\|b R\\&amp;D"
    assert _code("a`b") == "``a`b``"
    assert _code("`x`") == "`` `x` ``"
    assert _code("a|b") == "`a|b`"
    assert _code("") == '`""`'


def test_markdown_lookup_errors_and_empty_names(capsys, fake_pypi, tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("nosuchpkg==1.0\n-e git+https://x.example/r.git#egg=\n")
    code, out, _ = run(capsys, str(req), "-p", "3.13", "-t", "win-amd64", "--markdown")
    lines = out.splitlines()
    assert any(ln.startswith("Could not check nosuchpkg: `") for ln in lines)
    assert any(
        ln.startswith('Skipped 1 non-index package: `""`') or '`""`' in ln for ln in lines if ln.startswith("Skipped")
    )


# requires-python ==3.12.*, so uv kept only fastlib's cp312 wheel; PyPI (fake_pypi) has a cp313 one.
LOCK_312_ONLY = (
    'version = 1\nrequires-python = "==3.12.*"\n\n'
    '[[package]]\nname = "app"\nversion = "0"\nsource = { editable = "." }\ndependencies = [{ name = "fastlib" }]\n\n'
    '[[package]]\nname = "fastlib"\nversion = "2.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
    'sdist = { url = "https://files.pythonhosted.org/p/fastlib-2.0.0.tar.gz" }\n'
    'wheels = [{ url = "https://files.pythonhosted.org/p/fastlib-2.0.0-cp312-cp312-manylinux_2_17_x86_64.whl" }]\n'
)


def test_lock_for_another_python_is_rechecked_online(capsys, fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    code, out, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-x86_64")
    assert code == 0, out  # PyPI's cp313 wheel counts; the lock alone would say "source"
    assert "uv.lock is locked for Python ==3.12.*, so it may be missing wheels for Python 3.13" in err
    assert fake_pypi == ["https://pypi.org/pypi/fastlib/2.0.0/json"]


def test_lock_inside_requires_python_stays_offline(capsys, fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    code, _, err = run(capsys, str(tmp_path), "-p", "3.12", "-t", "linux-x86_64")
    assert code == 0 and "note:" not in err
    assert fake_pypi == []


def test_failed_recheck_keeps_the_lock_answers(capsys, tmp_path, monkeypatch):
    import urllib.error

    from wheelgap import pypi

    def offline(url, timeout=20.0):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(pypi, "fetch_json", offline)
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    code, out, err = run(capsys, str(tmp_path), "-p", "3.12,3.13", "-t", "linux-x86_64", "--json")
    rows = {p["name"]: p["results"] for p in json.loads(out)["packages"]}
    # 3.12 is still answered from the lock; 3.13 falls back to the lock's (possibly incomplete) files.
    assert rows["fastlib"] == {"3.12 linux-x86_64": "wheel", "3.13 linux-x86_64": "source"}
    assert "1 that couldn't be looked up may show false gaps" in err and code == 1


def test_recheck_leaves_packages_from_other_indexes_alone(capsys, fake_pypi, tmp_path):
    lock = LOCK_312_ONLY.replace('registry = "https://pypi.org/simple"', 'registry = "https://download.example/whl"')
    (tmp_path / "uv.lock").write_text(lock, encoding="utf-8")
    code, _, err = run(capsys, str(tmp_path), "-p", "3.12,3.13", "-t", "linux-x86_64")
    assert fake_pypi == [] and "1 from other indexes may show false gaps" in err


def test_list_targets_shows_the_manylinux_floor_outside_x86(capsys):
    code, out, _ = run(capsys, "--list-targets", "--json", "--glibc", "2.12")
    assert code == 0
    by_name = {row["name"]: row["accepts"] for row in json.loads(out)}
    assert by_name["linux-x86_64"] == "manylinux x86_64, glibc <= 2.12"
    assert by_name["linux-aarch64"] == "manylinux aarch64, no manylinux (glibc 2.12 is below the 2.17 floor)"


def test_quiet_hides_the_lock_note_unless_lookups_failed(capsys, fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    code, _, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-x86_64", "-q")
    assert code == 0 and err == ""
    assert fake_pypi == ["https://pypi.org/pypi/fastlib/2.0.0/json"]  # still re-checked, just quietly


def test_pipfile_lock_that_is_not_a_json_object_is_a_clear_error(capsys, tmp_path):
    (tmp_path / "Pipfile.lock").write_text("[]", encoding="utf-8")
    code, _, err = run(capsys, str(tmp_path / "Pipfile.lock"), "-p", "3.13", "-t", "linux-x86_64")
    assert code == 2 and "not a Pipfile.lock (expected a JSON object)" in err


def test_quiet_still_prints_the_lock_note_when_the_answer_may_be_wrong(capsys, tmp_path, monkeypatch):
    import urllib.error

    from wheelgap import pypi

    def offline(url, timeout=20.0):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(pypi, "fetch_json", offline)
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    _, _, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-x86_64", "-q")
    assert "couldn't be looked up may show false gaps" in err

    other = LOCK_312_ONLY.replace('registry = "https://pypi.org/simple"', 'registry = "https://download.example/whl"')
    (tmp_path / "uv.lock").write_text(other, encoding="utf-8")
    _, _, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-x86_64", "-q")
    assert "from other indexes may show false gaps" in err


@pytest.mark.parametrize("color", ["never", "always"])
def test_ignore_known_gap_text(capsys, fixtures, color):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "NewGlibc",
        "--all",
        "--explain",
        "--color",
        color,
    )
    assert code == 0 and not err
    assert "The 1 package has a wheel for 1 target." in out
    assert "armlib" in out
    assert out.count("newglibc") == 1  # only the disclosure, never a table or explanation row
    assert "ignored: newglibc" in out
    if color == "always":
        assert "\033[2mignored: newglibc\033[0m" in out


def test_ignore_known_gap_json(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "NewGlibc",
        "--json",
        "--explain",
    )
    data = json.loads(out)
    assert code == 0 and not err
    assert data["ignored"] == ["newglibc"]
    assert data["summary"] == {"packages": 1, "with_gaps": 0}
    assert [p["name"] for p in data["packages"]] == ["armlib"]


def test_ignore_known_gap_markdown(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "NewGlibc",
        "--markdown",
        "--all",
        "--explain",
    )
    assert code == 0 and not err
    assert "Ignored: `newglibc`" in out
    assert out.count("newglibc") == 1
    assert "| armlib |" in out
    assert "1 package · Python 3.13" in out
    assert "every package has a wheel" in out


def test_ignore_repeated_comma_separated_and_duplicate_names(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "musl-aarch64",
        "--ignore",
        "NewGlibc, ArmLib",
        "--ignore",
        "NEWGLIBC,not-installed",
        "--json",
    )
    data = json.loads(out)
    assert code == 0
    assert "wheelgap: note: --ignore matches no package: not-installed" in err
    assert data["ignored"] == ["armlib", "newglibc"]
    assert data["packages"] == []
    assert data["summary"] == {"packages": 0, "with_gaps": 0}


def test_ignore_canonicalizes_punctuation(capsys, fixtures, tmp_path):
    lock = (fixtures / "armgap" / "uv.lock").read_text().replace("newglibc", "new_glibc_core")
    (tmp_path / "uv.lock").write_text(lock)
    code, out, err = run(
        capsys,
        str(tmp_path),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "NEW.GLIBC--CORE",
        "--json",
    )
    data = json.loads(out)
    assert code == 0 and not err
    assert data["ignored"] == ["new_glibc_core"]  # reported as the lockfile names it
    assert [p["name"] for p in data["packages"]] == ["armlib"]


def test_ignore_does_not_hide_other_gaps(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "musl-aarch64",
        "--ignore",
        "NewGlibc",
        "--json",
    )
    data = json.loads(out)
    assert code == 1 and not err
    assert data["ignored"] == ["newglibc"]
    assert data["summary"] == {"packages": 1, "with_gaps": 1}
    assert data["packages"][0]["results"] == {"musl-aarch64": "source"}


def test_ignore_is_disclosed_in_quiet_output(capsys, fixtures):
    assert run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "NewGlibc",
        "--quiet",
    ) == (0, "ignored: newglibc\n", "")


@pytest.mark.parametrize("output", ["--json", "--markdown", "--color=never"])
def test_no_ignore_keeps_gaps(capsys, fixtures, output):
    code, out, err = run(capsys, str(fixtures / "armgap"), "-p", "3.13", "-t", "linux-aarch64", output)
    assert code == 1 and not err
    if output == "--json":
        data = json.loads(out)
        assert data["ignored"] == []
        assert data["summary"] == {"packages": 2, "with_gaps": 1}
        assert [p["name"] for p in data["packages"]] == ["armlib", "newglibc"]
    else:
        assert "1 of 2 packages has gaps" in out
        assert "newglibc" in out
        assert "ignored" not in out.lower()


@pytest.mark.parametrize("output", ["--json", "--markdown", "--color=never", "--quiet"])
def test_ignore_config_matches_cli(capsys, fixtures, tmp_path, output):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    args = (str(tmp_path), "-p", "3.13", "-t", "linux-aarch64", output)
    expected = run(capsys, *args, "--ignore", "NewGlibc")
    (tmp_path / "pyproject.toml").write_text('[tool.wheelgap]\nignore = ["NewGlibc"]\n')
    actual = run(capsys, *args)
    assert actual == expected
    assert actual[0] == 0 and not actual[2]
    assert "newglibc" in actual[1]


@pytest.mark.parametrize("value", ['["NewGlibc, ArmLib", "NEWGLIBC"]', '"NewGlibc, ArmLib"'])
def test_ignore_config_accepts_list_or_string(capsys, fixtures, tmp_path, value):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text(f"[tool.wheelgap]\nignore = {value}\n")
    code, out, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "musl-aarch64", "--json")
    assert code == 0 and not err
    data = json.loads(out)
    assert data["ignored"] == ["armlib", "newglibc"]
    assert data["packages"] == []
    assert data["summary"] == {"packages": 0, "with_gaps": 0}


@pytest.mark.parametrize("override, ignored, total", [("ArmLib", ["armlib"], 1), ("", [], 2)])
def test_ignore_cli_replaces_config(capsys, fixtures, tmp_path, override, ignored, total):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text('[tool.wheelgap]\nignore = ["NewGlibc"]\n')
    code, out, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-aarch64", "--ignore", override, "--json")
    assert code == 1 and not err  # the configured newglibc ignore was replaced, not extended
    data = json.loads(out)
    assert data["ignored"] == ignored
    assert data["summary"] == {"packages": total, "with_gaps": 1}
    assert "newglibc" in [p["name"] for p in data["packages"]]


def test_ignore_config_empty_list_keeps_gaps(capsys, fixtures, tmp_path):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    args = (str(tmp_path), "-p", "3.13", "-t", "linux-aarch64", "--json")
    expected = run(capsys, *args)
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\nignore = []\n")
    assert run(capsys, *args) == expected
    assert expected[0] == 1


@pytest.mark.parametrize("value", ["true", "1", '["newglibc", 1]', '{name = "newglibc"}'])
def test_ignore_config_rejects_invalid_types(capsys, fixtures, tmp_path, value):
    (tmp_path / "uv.lock").write_bytes((fixtures / "armgap" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text(f"[tool.wheelgap]\nignore = {value}\n")
    code, out, err = run(capsys, str(tmp_path), "-p", "3.13", "-t", "linux-aarch64")
    assert code == 2 and not out
    assert "[tool.wheelgap] ignore must be a list of strings" in err


# #114: a uv.lock package the lock says came from a private index, whose only locked wheel is a Windows one.
PRIVATE_UV_LOCK = (
    'version = 1\nrequires-python = ">=3.12"\n\n'
    '[[package]]\nname = "app"\nversion = "0"\nsource = { editable = "." }\n'
    'dependencies = [{ name = "purewheel" }]\n\n'
    '[[package]]\nname = "purewheel"\nversion = "3.1"\n'
    'source = { registry = "https://pkgs.example.com/simple" }\n'
    'wheels = [{ url = "https://pkgs.example.com/purewheel-3.1-cp313-cp313-win_amd64.whl" }]\n'
)


# The same package in a poetry.lock, recorded through its [package.source] table.
PRIVATE_POETRY_LOCK = (
    '[[package]]\nname = "purewheel"\nversion = "3.1"\ndescription = ""\noptional = false\n'
    'python-versions = ">=3.9"\ngroups = ["main"]\n'
    "files = [\n"
    '    {file = "purewheel-3.1-cp313-cp313-win_amd64.whl", hash = "sha256:00"},\n'
    "]\n\n"
    "[package.source]\n"
    'type = "legacy"\n'
    'url = "https://pkgs.example.com/simple"\n'
    'reference = "private"\n\n'
    '[metadata]\nlock-version = "2.1"\npython-versions = ">=3.9"\ncontent-hash = "00"\n'
)


# And in a pylock.toml, through the package's index key.
PRIVATE_PYLOCK = (
    'lock-version = "1.0"\ncreated-by = "pip"\n\n'
    '[[packages]]\nname = "purewheel"\nversion = "3.1"\nindex = "https://pkgs.example.com/simple"\n'
    'wheels = [{ name = "purewheel-3.1-cp313-cp313-win_amd64.whl", '
    'url = "https://pkgs.example.com/purewheel-3.1-cp313-cp313-win_amd64.whl", hashes = { sha256 = "00" } }]\n'
)


@pytest.mark.parametrize(
    ("name", "lock"),
    [("uv.lock", PRIVATE_UV_LOCK), ("poetry.lock", PRIVATE_POETRY_LOCK), ("pylock.toml", PRIVATE_PYLOCK)],
)
def test_online_keeps_private_index_packages_off_pypi(capsys, fake_pypi, tmp_path, name, lock):
    (tmp_path / name).write_text(lock, encoding="utf-8")
    code, out, err = run(capsys, str(tmp_path), "--online", "-p", "3.13", "-t", "linux-x86_64")
    assert not [url for url in fake_pypi if "purewheel" in url], fake_pypi
    # Its locked files still count: the only locked wheel is a Windows one, so there's a gap.
    assert code == 1
    assert "purewheel" in out
    assert "--online looks up PyPI packages only; kept the locked files of 1 package from other indexes" in err


def test_online_quiet_hides_the_private_index_note(capsys, fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(PRIVATE_UV_LOCK, encoding="utf-8")
    code, _, err = run(capsys, str(tmp_path), "--online", "-q", "-p", "3.13", "-t", "linux-x86_64")
    assert code == 1 and err == ""


def test_online_still_warns_about_private_packages_locked_for_other_pythons(capsys, fake_pypi, tmp_path):
    # --online can't refresh a private-index package, so a lock made for 3.12 may lack its 3.13 wheels.
    lock = LOCK_312_ONLY.replace('registry = "https://pypi.org/simple"', 'registry = "https://download.example/whl"')
    (tmp_path / "uv.lock").write_text(lock, encoding="utf-8")
    code, _, err = run(capsys, str(tmp_path), "--online", "-q", "-p", "3.13", "-t", "linux-x86_64")
    assert fake_pypi == [] and "1 from other indexes may show false gaps" in err


def test_offline_uses_the_lock_and_never_contacts_the_index(capsys, fixtures, fake_pypi):
    args = [str(fixtures / "uvproj"), "-p", "3.13", "-t", "linux-x86_64", "--json"]
    expected = run(capsys, *args)
    assert fake_pypi == []  # a uv.lock inside its requires-python needs no lookups
    assert run(capsys, *args, "--offline") == expected
    assert fake_pypi == [] and json.loads(expected[1])["packages"]


def test_offline_refuses_inputs_that_need_the_index(capsys, fixtures, fake_pypi):
    code, out, err = run(capsys, str(fixtures / "reqproj" / "requirements.txt"), "--offline", "-p", "3.13")
    assert code == 2 and not out
    assert err == (
        "wheelgap: error: --offline: requirements.txt needs the index for 4 packages "
        "(fastlib, unpinned, winonly, …); use a lockfile that lists each package's files, or drop --offline\n"
    )
    assert fake_pypi == []


def test_offline_names_at_most_three_packages(capsys, tmp_path, fake_pypi):
    req = tmp_path / "requirements.txt"
    req.write_text("a==1\nb==1\nc==1\nd==1\n", encoding="utf-8")
    code, out, err = run(capsys, str(req), "--offline", "-p", "3.13", "--json")
    assert code == 2 and fake_pypi == []
    message = (
        "--offline: requirements.txt needs the index for 4 packages (a, b, c, …); "
        "use a lockfile that lists each package's files, or drop --offline"
    )
    assert json.loads(out)["error"]["message"] == message


def test_offline_skips_the_requires_python_recheck(capsys, fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    code, out, err = run(capsys, str(tmp_path), "--offline", "-q", "-p", "3.13", "-t", "linux-x86_64", "--json")
    assert fake_pypi == []
    rows = {p["name"]: p["results"] for p in json.loads(out)["packages"]}
    assert rows["fastlib"] == {"linux-x86_64": "source"}  # only the lock's cp312 wheel, as the note warns
    assert "uv.lock is locked for Python ==3.12.*, so it may be missing wheels for Python 3.13" in err
    assert "with --offline its packages weren't re-checked on the index, so they may show false gaps" in err


def test_offline_refuses_a_pyodide_lock_url(capsys, fixtures, fake_pypi):
    url = "https://cdn.example.com/pyodide-lock.json"
    code, out, err = run(capsys, str(fixtures / "uvproj"), "--offline", "--pyodide-lock", url, "-p", "3.13")
    assert code == 2 and not out and fake_pypi == []
    assert f"--offline: {url} would be downloaded" in err


def test_offline_and_online_are_a_usage_error(capsys, fixtures):
    with pytest.raises(SystemExit) as excinfo:
        main([str(fixtures / "uvproj"), "--offline", "--online"])
    assert excinfo.value.code == 2
    assert "--offline and --online can't be used together" in capsys.readouterr().err


def test_ignore_matches_no_package_prints_note(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "newglibc,nosuchpkg",
    )
    assert code == 0
    assert "wheelgap: note: --ignore matches no package: nosuchpkg" in err
    note_line = err.split("matches no package:")[-1]
    assert "newglibc" not in note_line


def test_ignore_all_names_match_no_note(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "newglibc",
    )
    assert "matches no package" not in err


def test_ignore_unmatched_is_hidden_with_quiet(capsys, fixtures):
    code, out, err = run(
        capsys,
        str(fixtures / "armgap"),
        "-p",
        "3.13",
        "-t",
        "linux-aarch64",
        "--ignore",
        "nosuchpkg",
        "--quiet",
    )
    assert err == ""
    assert code == 1


def test_ignore_unmatched_does_not_change_exit_code(capsys, fixtures):
    args = [str(fixtures / "armgap"), "-p", "3.13", "-t", "linux-aarch64"]
    code_plain, _, _ = run(capsys, *args)
    code_unmatched, _, err = run(capsys, *args, "--ignore", "nosuchpkg")
    assert "matches no package: nosuchpkg" in err
    assert code_unmatched == code_plain == 1
