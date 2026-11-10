import json

from wheelgap.check import check, explain, rejection
from wheelgap.cli import main
from wheelgap.sources import load_inputs
from wheelgap.targets import build_targets

GLIBC_ARM = "armlib-1.4.0-cp313-cp313-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"


def target(py="3.13", plat="linux-x86_64"):
    (t,) = build_targets([py], [plat])
    return t


def test_glibc_arm_wheel_does_not_count_for_alpine_arm(fixtures):
    (src,) = load_inputs([str(fixtures / "armgap")])
    targets = build_targets(["3.13"], ["linux-aarch64", "musl-aarch64", "musl-x86_64"])
    row = next(r for r in check([src], targets) if r.package.name == "armlib")
    assert [s.value for s in row.results.values()] == ["wheel", "source", "wheel"]


def test_rejection_reasons():
    musl_arm = target(plat="musl-aarch64")
    assert rejection(GLIBC_ARM, musl_arm) == "manylinux_2_17_aarch64 is a glibc wheel; musl (Alpine) needs musllinux"
    assert rejection("x-1-cp313-cp313-musllinux_1_2_aarch64.whl", target(plat="linux-aarch64")) == (
        "musllinux_1_2_aarch64 is a musl wheel; glibc Linux needs manylinux"
    )
    assert rejection("x-1-cp313-cp313-manylinux_2_39_aarch64.whl", target(plat="linux-aarch64")) == (
        "manylinux_2_39_aarch64 needs glibc 2.39, newer than 2.36 (--glibc)"
    )
    assert rejection("x-1-cp313-cp313-manylinux_2_12_aarch64.whl", target(plat="linux-aarch64")) == (
        "manylinux_2_12_aarch64 is older than glibc 2.17, the oldest pip accepts on aarch64"
    )
    assert rejection("x-1-cp313-cp313-manylinux2010_aarch64.whl", target(plat="linux-aarch64")) == (
        "manylinux2010_aarch64 is older than glibc 2.17, the oldest pip accepts on aarch64"
    )
    assert rejection("x-1-cp313-cp313-macosx_15_0_arm64.whl", target(plat="macos-arm64")) == (
        "macosx_15_0_arm64 needs macOS 15.0, newer than 14.0 (--macos)"
    )
    assert rejection("x-1-cp312-cp312-win_amd64.whl", target(plat="win-amd64")) == (
        "built for cp312-cp312, not CPython 3.13"
    )
    assert rejection("x-1-cp312-abi3-manylinux_2_17_x86_64.whl", target("3.14t")) == (
        "abi3 wheels can't be used on free-threaded Python (only abi3t ones can)"
    )
    assert rejection("x-1-cp313-cp313-win_amd64.whl", target()) == "win_amd64 doesn't match linux-x86_64"
    assert rejection("x-1-py3-none-any.whl", target()) is None


def test_explain_puts_closest_misses_first(fixtures):
    (src,) = load_inputs([str(fixtures / "armgap")])
    armlib = next(p for p in src.packages if p.name == "armlib")
    got = [f for f, _ in explain(armlib.files, target(plat="musl-aarch64"))]
    assert got[0] == GLIBC_ARM  # same arch, wrong libc: the one a bug reporter cares about
    assert not any(f.endswith(".tar.gz") for f in got)


def test_cli_explain(capsys, fixtures):
    code = main([str(fixtures / "armgap"), "-p", "3.13", "-t", "musl-aarch64", "--explain"])
    out = capsys.readouterr().out
    assert code == 1
    assert "armlib 1.4.0 on musl-aarch64: ● source" in out
    assert GLIBC_ARM in out and "musl (Alpine) needs musllinux" in out
    assert "newglibc 2.0 on musl-aarch64: ✗ missing" in out
    assert "Run with --explain" not in out


def test_cli_hints_at_explain(capsys, fixtures):
    main([str(fixtures / "armgap"), "-p", "3.13", "-t", "musl-aarch64"])
    assert "Run with --explain" in capsys.readouterr().out


def test_json_explain(capsys, fixtures):
    main([str(fixtures / "armgap"), "-p", "3.13", "-t", "linux-aarch64,musl-aarch64", "--json", "--explain"])
    data = json.loads(capsys.readouterr().out)
    armlib = next(p for p in data["packages"] if p["name"] == "armlib")
    assert list(armlib["rejected"]) == ["musl-aarch64"]  # only targets with a gap
    assert armlib["rejected"]["musl-aarch64"][0]["file"] == GLIBC_ARM
    newglibc = next(p for p in data["packages"] if p["name"] == "newglibc")
    assert "needs glibc 2.39" in newglibc["rejected"]["linux-aarch64"][0]["reason"]


def test_json_without_explain_has_no_rejected(capsys, fixtures):
    main([str(fixtures / "armgap"), "-p", "3.13", "-t", "musl-aarch64", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert all("rejected" not in p for p in data["packages"])
    assert all("via" not in p for p in data["packages"])


def test_explain_pyodide(capsys, fake_pypi):
    main(["nowheels==0.9", "fastlib==2.0.0", "-p", "3.13", "-t", "pyodide", "--explain"])
    out = capsys.readouterr().out
    assert "nowheels 0.9 on pyodide: ✗ missing" in out
    assert "pyodide can't build packages from source" in out
    assert rejection("x-1-cp314-cp314-pyemscripten_2026_0_wasm32.whl", target(plat="pyodide")) == (
        "pyemscripten_2026_0_wasm32 is for a different Pyodide ABI than Python 3.13"
    )


def test_cli_explain_shows_via(capsys, fixtures):
    main([str(fixtures / "uvproj"), "-p", "3.13", "-t", "linux-x86_64", "--explain", "--exit-zero"])
    out = capsys.readouterr().out
    assert "nativeext 0.3 on linux-x86_64: ● source\n  via: demo → fastlib → nativeext\n" in out


def test_cli_explain_via_for_the_original_report(capsys, fixtures):
    main([str(fixtures / "uvproj"), "-p", "3.12", "-t", "win-amd64", "--explain", "--exit-zero"])
    out = capsys.readouterr().out
    assert "winonly 311 on win-amd64: ✗ missing\n  via: demo → winonly\n" in out


def test_cli_explain_no_via_without_a_graph(capsys, fixtures):
    main([str(fixtures / "pylockproj"), "-p", "3.13", "-t", "macos-arm64", "--explain", "--exit-zero"])
    out = capsys.readouterr().out
    assert "macpkg 1.0 on macos-arm64: ● source" in out
    assert "via:" not in out


def test_json_explain_via(capsys, fixtures):
    main([str(fixtures / "uvproj"), "-p", "3.12", "-t", "linux-x86_64,win-amd64", "--json", "--explain", "--exit-zero"])
    data = json.loads(capsys.readouterr().out)
    packages = {p["name"]: p for p in data["packages"]}
    assert packages["nativeext"]["via"] == {
        "linux-x86_64": ["demo", "fastlib", "nativeext"],
        "win-amd64": ["demo", "fastlib", "nativeext"],
    }
    assert packages["winonly"]["via"] == {"win-amd64": ["demo", "winonly"]}  # only a gap on win-amd64
    assert all(p["via"].keys() == p["rejected"].keys() for p in data["packages"])


def test_json_explain_via_from_dependency_group(capsys, tmp_path):
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[manifest.dependency-groups]\ndev = [{ name = "devonly" }]\n\n'
        '[[package]]\nname = "devonly"\nversion = "1.0"\nsource = { registry = "https://pypi.org/simple" }\n'
        'sdist = { url = "https://example.invalid/devonly-1.0.tar.gz", hash = "sha256:00", size = 1 }\n',
        encoding="utf-8",
    )
    main([str(tmp_path), "-p", "3.13", "-t", "linux-x86_64", "--json", "--explain", "--exit-zero"])
    (devonly,) = json.loads(capsys.readouterr().out)["packages"]
    assert devonly["via"] == {"linux-x86_64": ["(dev group)", "devonly"]}  # same label as the text line


def test_json_explain_no_via_without_a_graph(capsys, fixtures):
    main([str(fixtures / "pylockproj"), "-p", "3.13", "-t", "macos-arm64", "--json", "--explain", "--exit-zero"])
    data = json.loads(capsys.readouterr().out)
    macpkg = next(p for p in data["packages"] if p["name"] == "macpkg")
    assert macpkg["rejected"] == {"macos-arm64": []}
    assert macpkg["via"] == {}  # no dependency graph, so no target has a path


def test_explain_abi3t_and_legacy_manylinux():
    assert rejection("x-1.0-cp315-abi3.abi3t-manylinux_2_28_x86_64.whl", target("3.14t")) == (
        "abi3t wheels need CPython 3.15+ (free-threaded)"
    )
    (old,) = build_targets(["3.13"], ["linux-x86_64"], glibc="2.12")
    assert rejection("x-1-cp313-cp313-manylinux2014_x86_64.whl", old) == (
        "manylinux2014_x86_64 needs glibc 2.17, newer than 2.12 (--glibc)"
    )
