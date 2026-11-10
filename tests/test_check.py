from wheelgap.check import Status, check, marker_holds, status_for
from wheelgap.pypi import fill_all
from wheelgap.sources import Package, Source, load_inputs
from wheelgap.targets import build_targets


def results(rows):
    return {r.package.name: [s.value for s in r.results.values()] for r in rows}


def test_status_for():
    (lin,) = build_targets(["3.13"], ["linux-x86_64"])
    assert status_for(["a-1-cp313-cp313-manylinux_2_17_x86_64.whl"], lin) is Status.WHEEL
    assert status_for(["a-1-cp313-cp313-win_amd64.whl", "a-1.tar.gz"], lin) is Status.SOURCE
    assert status_for(["a-1-cp313-cp313-win_amd64.whl"], lin) is Status.MISSING
    assert status_for([], lin) is Status.MISSING
    assert status_for(None, lin) is Status.UNKNOWN
    assert status_for(["garbage.whl", "a-1.zip"], lin) is Status.SOURCE


def test_uv_lock_graph(fixtures):
    sources = load_inputs([str(fixtures / "uvproj")])
    targets = build_targets(["3.13"], ["linux-x86_64", "musl-aarch64", "win-amd64"])
    got = results(check(sources, targets))
    assert got["fastlib"] == ["wheel", "source", "wheel"]
    assert got["purewheel"] == ["wheel", "wheel", "wheel"]
    assert got["puresdist"] == ["source", "source", "source"]
    assert got["winonly"] == ["n/a", "n/a", "wheel"]  # only pulled in on Windows
    assert got["oldpy"] == ["n/a", "n/a", "n/a"]  # only on Python < 3.13
    assert got["devtool"] == ["wheel", "wheel", "wheel"]  # dev group is included
    assert got["orphan"] == ["n/a", "n/a", "n/a"]  # nothing depends on it
    assert got["nativeext"] == ["source", "source", "source"]
    assert "gitdep" not in got and "demo" not in got


def test_uv_lock_via(fixtures):
    sources = load_inputs([str(fixtures / "uvproj")])
    lin, musl, win = targets = build_targets(["3.13"], ["linux-x86_64", "musl-aarch64", "win-amd64"])
    rows = {r.package.name: r for r in check(sources, targets)}
    assert rows["nativeext"].via == {t: ["demo", "fastlib", "nativeext"] for t in targets}
    assert rows["winonly"].via == {win: ["demo", "winonly"]}  # n/a targets get no path
    assert rows["orphan"].via == {}


def graph(edges, root_edges=(), root_groups=None, roots=(0,)):
    """A uv.lock-style Source whose package names are the keys of `edges`, in order."""
    names = list(edges)
    packages = [Package(n, "1", files=[f"{n}-1.tar.gz"]) for n in names]
    for i in roots:
        packages[i].skip = "editable"
    deps = {names.index(n): [(d, None, m) for d, m in out] for n, out in edges.items()}
    return Source(
        "uv.lock", packages, edges=deps, roots=list(roots), root_edges=list(root_edges), root_groups=root_groups or {}
    )


def via(src, targets, name):
    return next(r for r in check([src], targets) if r.package.name == name).via


def test_via_is_a_shortest_path():
    # A depth-first walk pops `long` first and reaches `c` through long → x.
    src = graph(
        {
            "root": [("short", None), ("long", None)],
            "short": [("c", None)],
            "long": [("x", None)],
            "x": [("c", None)],
            "c": [],
        }
    )
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    assert via(src, [t], "c") == {t: ["root", "short", "c"]}


def test_via_differs_per_target():
    win_only = "sys_platform == 'win32'"
    src = graph(
        {"root": [("w", win_only), ("l", None)], "w": [("c", None)], "l": [("x", None)], "x": [("c", None)], "c": []}
    )
    lin, win = build_targets(["3.13"], ["linux-x86_64", "win-amd64"])
    assert via(src, [lin, win], "c") == {lin: ["root", "l", "x", "c"], win: ["root", "w", "c"]}


def test_via_from_dependency_group():
    src = graph(
        {"root": [], "tool": [("dep", None)], "dep": []}, root_edges=[("tool", None, None)], root_groups={0: "dev"}
    )
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    assert via(src, [t], "tool") == {t: ["(dev group)", "tool"]}
    assert via(src, [t], "dep") == {t: ["(dev group)", "tool", "dep"]}


def test_via_from_plain_requirement():
    src = graph({"req": []}, root_edges=[("req", None, None)], roots=())
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    assert via(src, [t], "req") == {t: ["req"]}


def test_uv_lock_graph_python_312(fixtures):
    sources = load_inputs([str(fixtures / "uvproj")])
    (t,) = build_targets(["3.12"], ["linux-x86_64"])
    got = results(check(sources, [t]))
    assert got["oldpy"] == ["source"]
    assert got["fastlib"] == ["source"]  # only cp313 wheels


def test_uv_version_disambiguation():
    src = Source(
        "uv.lock",
        packages=[
            Package("root", "0", skip="editable"),
            Package("np", "1.0", files=["np-1.0.tar.gz"]),
            Package("np", "2.0", files=["np-2.0-py3-none-any.whl"]),
        ],
        edges={0: [("np", "1.0", "python_version < '3.10'"), ("np", "2.0", "python_version >= '3.10'")], 1: [], 2: []},
        roots=[0],
    )
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    rows = check([src], [t])
    assert [(r.package.version, r.results[t].value) for r in rows] == [("1.0", "n/a"), ("2.0", "wheel")]


def test_markers_on_packages(fixtures):
    sources = load_inputs([str(fixtures / "pylockproj")])
    targets = build_targets(["3.13"], ["linux-aarch64", "macos-arm64"])
    got = results(check(sources, targets))
    assert got["fastlib"] == ["wheel", "missing"]
    assert got["macpkg"] == ["n/a", "source"]
    assert all(r.via == {} for r in check(sources, targets))  # no graph, no paths


def test_bad_marker_counts_as_needed():
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    assert marker_holds("this is not a marker", t)
    assert marker_holds(None, t)
    assert not marker_holds("sys_platform == 'win32'", t)


def test_requirements_against_fake_index(fixtures, fake_pypi):
    sources = load_inputs([str(fixtures / "reqproj")])
    packages = [p for s in sources for p in s.packages]
    fill_all(packages)
    targets = build_targets(["3.13"], ["linux-x86_64", "win-amd64"])
    got = results(check(sources, targets))
    assert got == {
        "fastlib": ["wheel", "source"],
        "unpinned": ["wheel", "wheel"],
        "winonly": ["n/a", "wheel"],
        "purewheel": ["wheel", "wheel"],
    }
    assert next(p for p in packages if p.name == "unpinned").version == "4.2"
    assert not any("localpkg" in c for c in fake_pypi)


def test_lookup_errors_are_unknown(fake_pypi):
    pkgs = [Package("doesnotexist", "1.0"), Package("fastlib", "9.9")]
    fill_all(pkgs)
    assert [p.error for p in pkgs] == ["not on index", "version 9.9 not on index"]
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    rows = check([Source("x", pkgs)], [t])
    assert [r.results[t] for r in rows] == [Status.UNKNOWN, Status.UNKNOWN]


def test_online_refresh_overrides_lockfile(fixtures, fake_pypi):
    sources = load_inputs([str(fixtures / "uvproj")])
    packages = [p for s in sources for p in s.packages]
    fill_all(packages, refresh=True)
    fastlib = next(p for p in packages if p.name == "fastlib")
    assert fastlib.files == ["fastlib-2.0.0-cp313-cp313-manylinux_2_17_x86_64.whl", "fastlib-2.0.0.tar.gz"]


def test_pyodide_sdist_only_is_missing():
    (py,) = build_targets(["3.13"], ["pyodide"])
    (lin,) = build_targets(["3.13"], ["linux-x86_64"])
    assert status_for(["a-1.tar.gz"], lin) is Status.SOURCE
    assert status_for(["a-1.tar.gz"], py) is Status.MISSING  # no compiler in the browser
    assert status_for(["a-1-cp313-cp313-pyodide_2025_0_wasm32.whl", "a-1.tar.gz"], py) is Status.WHEEL


def test_via_prefers_the_projects_own_dependency_over_a_group():
    # numpy is a runtime dependency of the project and also listed in the dev group.
    src = graph({"app": [("numpy", None)], "numpy": []}, root_edges=[("numpy", None, None)], root_groups={0: "dev"})
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    assert via(src, [t], "numpy") == {t: ["app", "numpy"]}


def test_via_prefers_a_plain_requirement_over_a_group():
    src = graph(
        {"a": [("c", None)], "b": [("c", None)], "c": []},
        root_edges=[("a", None, None), ("b", None, None)],
        root_groups={0: "dev"},
        roots=(),
    )
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    assert via(src, [t], "c") == {t: ["b", "c"]}


def test_markers_wheelgap_cannot_know_count_as_needed():
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    # uv writes [tool.uv] conflicts as extra == '...' terms; installers know the OS release, we don't.
    for marker in ("extra == 'extra-7-proj-cu124'", "platform_release >= '20.0'", "'docs' in dependency_groups"):
        assert marker_holds(marker, t), marker
    assert not marker_holds("sys_platform == 'win32'", t)


def test_more_sdist_formats():
    (t,) = build_targets(["3.13"], ["linux-x86_64"])
    assert status_for(["foo-1.0.tar"], t) is Status.SOURCE
    assert status_for(["foo-1.0.tar.lz"], t) is Status.SOURCE


def test_yanked_files_count_only_when_every_usable_file_is_yanked():
    lin, win = build_targets(["3.13"], ["linux-x86_64", "win-amd64"])
    files = ["foo-2.0-cp313-cp313-manylinux_2_17_x86_64.whl", "foo-2.0-cp313-cp313-win_amd64.whl"]
    yanked = frozenset({files[0]})
    # Only the Linux wheel is yanked: pip on Linux has nothing else, so it installs the yanked wheel.
    assert status_for(files, lin, yanked=yanked) is Status.WHEEL
    assert status_for(files + ["foo-2.0.tar.gz"], lin, yanked=yanked) is Status.SOURCE  # a live sdist wins
    assert status_for(files, win, yanked=yanked) is Status.WHEEL


def test_unknowable_markers_still_respect_the_platform_part():
    lin, mac, win = build_targets(["3.13"], ["linux-x86_64", "macos-arm64", "win-amd64"])
    cpu = "sys_platform != 'darwin' and extra == 'extra-7-proj-cpu'"  # uv conflicts (torch cpu/cu124)
    assert marker_holds(cpu, lin) and not marker_holds(cpu, mac)
    cuda = "sys_platform == 'win32' and 'cuda' in extras"  # pylock.toml
    assert marker_holds(cuda, win) and not marker_holds(cuda, lin)
    pyobjc = "sys_platform == 'darwin' and platform_release >= '20.0'"
    assert marker_holds(pyobjc, mac) and not marker_holds(pyobjc, lin)


def test_three_valued_markers_from_the_review():
    lin, mac = build_targets(["3.13"], ["linux-x86_64", "macos-arm64"])
    for marker, on_linux, on_mac in [
        ("'cuda' in extras and 'cpu' not in extras", True, True),  # conflicting extras: unknown, so needed
        ("platform_release >= '20' and platform_release < '23'", True, True),
        ("sys_platform == 'linux' and 'microsoft' in platform_release", True, False),  # WSL check
        ("extra == 'a' and extra == 'b'", True, True),
        ("sys_platform == 'darwin' and extra == 'gpu' or python_version < '3.9'", False, True),
        ("(sys_platform == 'win32' or extra == 'x') and python_version >= '3.10'", True, True),
        ("sys_platform == 'win32' and python_version >= '3.10'", False, False),
    ]:
        assert marker_holds(marker, lin) is on_linux, (marker, "linux")
        assert marker_holds(marker, mac) is on_mac, (marker, "mac")


def test_marker_values_with_quotes_are_not_misread(monkeypatch):
    from packaging._parser import Value

    # packaging before 26.3 always re-serialised values in double quotes; wheelgap must not rely on
    # serialize() picking the right quotes, or this marker reads as "x" and "win32" and turns False.
    monkeypatch.setattr(Value, "serialize", lambda self: f'"{self.value}"')
    (lin,) = build_targets(["3.13"], ["linux-x86_64"])
    assert marker_holds("""sys_platform != 'x" and sys_platform == "win32'""", lin)


def test_explain_plain_linux_wheel_on_musl_and_ordering():
    from wheelgap.check import explain, rejection

    (musl,) = build_targets(["3.13"], ["musl-armv7l"])
    assert rejection("grpcio-1.85.1-cp313-cp313-linux_armv7l.whl", musl) == (
        "linux_armv7l is a plain Linux (glibc) build; wheelgap only counts musllinux wheels for musl"
    )
    files = ["x-1-cp313-abi3-macosx_11_0_universal2.whl", "x-1-cp313-cp313-linux_armv7l.whl"]
    # The same-arch Linux wheel comes first; a macOS universal2 wheel isn't "same arch" on Linux.
    assert [f for f, _ in explain(files, musl)][0] == "x-1-cp313-cp313-linux_armv7l.whl"


def test_mobile_explain_reasons_and_sdists():
    from wheelgap.check import rejection

    (ios,) = build_targets(["3.13"], ["ios"])
    (android,) = build_targets(["3.13"], ["android"])
    assert rejection("x-1-cp313-cp313-ios_15_0_arm64_iphoneos.whl", ios) == (
        "ios_15_0_arm64_iphoneos needs iOS 15.0, newer than 13.0 (--ios)"
    )
    assert rejection("x-1-cp313-cp313-android_26_arm64_v8a.whl", android) == (
        "android_26_arm64_v8a needs Android API level 26, newer than 24 (--android-api)"
    )
    assert rejection("x-1-cp313-cp313-ios_11_0_arm64_iphoneos.whl", ios) == (
        "ios_11_0_arm64_iphoneos is older than iOS 12.0, the oldest pip accepts"
    )
    assert rejection("x-1-cp313-cp313-ios_12_10_arm64_iphoneos.whl", ios) == (
        "ios_12_10_arm64_iphoneos isn't one of the iOS versions pip tries for 13.0 (x.0 to x.9 of each older release)"
    )
    assert rejection("x-1-cp313-cp313-android_15_arm64_v8a.whl", android) == (
        "android_15_arm64_v8a is older than API level 16, the oldest pip accepts"
    )
    # Mobile installs can't build sdists, so an sdist alone is "can't install", not "from source".
    assert status_for(["x-1.0.tar.gz"], ios) is Status.MISSING
