import json

import pytest

from wheelgap.scan import DEFAULT_PYTHONS, main, scan_targets


def write_top(tmp_path, names):
    path = tmp_path / "top.json"
    rows = [{"project": n, "download_count": 1000 - i} for i, n in enumerate(names)]
    path.write_text(json.dumps({"last_update": "2026-11-01 06:41:05", "rows": rows}))
    return path


def test_scan_targets_add_pyodide_only_where_it_exists():
    labels = [t.label(True) for t in scan_targets("3.13,3.14t")]
    assert "3.13 pyodide" in labels
    assert "3.14t pyodide" not in labels  # no free-threaded Pyodide
    assert sum(1 for x in labels if x.startswith("3.13 ")) == 9
    assert sum(1 for x in labels if x.startswith("3.14t ")) == 8


def test_scan_targets_removes_duplicate_versions_in_order():
    assert scan_targets("3.14t,3.13,cp314t,3.13") == scan_targets("3.14t,3.13")


def test_scan_defaults_include_python_315(fake_pypi, tmp_path, capsys):
    top = write_top(tmp_path, ["purewheel"])
    assert main(["--top-packages", str(top)]) == 0
    labels = [target["label"] for target in json.loads(capsys.readouterr().out)["targets"]]
    assert labels == [target.label(True) for target in scan_targets(DEFAULT_PYTHONS)]
    for version in ("3.13", "3.14", "3.14t", "3.15", "3.15t"):
        assert f"{version} linux-x86_64" in labels
    assert "3.15 pyodide" not in labels
    assert "3.15t pyodide" not in labels


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--top", "-5", "must be a positive integer"),
        ("--top", "0", "must be a positive integer"),
        ("--workers", "0", "must be a positive integer"),
        ("--workers", "-1", "must be a positive integer"),
        ("--top", "1.5", "must be a positive integer"),
        ("--workers", "abc", "must be a positive integer"),
        ("--python", " , ", "provide at least one Python version"),
        ("--python", "invalid", "can't parse Python version"),
    ],
)
def test_scan_rejects_invalid_arguments_before_loading_data(tmp_path, capsys, flag, value, message):
    with pytest.raises(SystemExit) as error:
        main([flag, value, "--top-packages", str(tmp_path / "missing.json")])
    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_scan_writes_the_tracker_data(fake_pypi, tmp_path, capsys):
    top = write_top(tmp_path, ["purewheel", "fastlib", "nowheels", "does-not-exist"])
    out = tmp_path / "coverage.json"
    assert main(["--top", "3", "--python", "3.13,cp313", "--top-packages", str(top), "--out", str(out)]) == 0
    data = json.loads(out.read_text())

    assert data["schema"] == 1
    assert data["top_packages"]["last_update"] == "2026-11-01 06:41:05"
    labels = [t["label"] for t in data["targets"]]
    assert labels[0] == "3.13 linux-x86_64" and labels[-1] == "3.13 pyodide"
    assert [p["name"] for p in data["packages"]] == ["purewheel", "fastlib", "nowheels"]  # --top 3

    by_name = {p["name"]: p for p in data["packages"]}
    assert by_name["purewheel"]["pure"] and set(by_name["purewheel"]["results"]) == {"wheel"}
    fastlib = dict(zip(labels, by_name["fastlib"]["results"], strict=True))
    assert fastlib["3.13 linux-x86_64"] == "wheel"
    assert fastlib["3.13 linux-aarch64"] == "source"
    assert fastlib["3.13 pyodide"] == "missing"  # Pyodide can't build the sdist
    assert by_name["nowheels"]["rank"] == 3 and by_name["nowheels"]["downloads"] == 998

    assert data["summary"]["3.13 linux-x86_64"] == {"wheel": 2, "source": 1, "missing": 0, "unknown": 0}
    assert data["summary"]["3.13 pyodide"] == {"wheel": 1, "source": 0, "missing": 2, "unknown": 0}
    assert "scanned 3 packages x 9 targets" in capsys.readouterr().err


def test_scan_reports_lookup_errors(fake_pypi, tmp_path, capsys):
    top = write_top(tmp_path, ["does-not-exist"])
    main(["--top", "1", "--python", "3.13", "--top-packages", str(top), "--max-error-rate", "1"])
    out, err = capsys.readouterr()
    pkg = json.loads(out)["packages"][0]
    assert pkg["error"] == "not on index" and set(pkg["results"]) == {"unknown"}
    assert "(1 lookup errors)" in err


def test_scan_refuses_to_write_when_too_many_lookups_fail(fake_pypi, tmp_path, capsys):
    top = write_top(tmp_path, ["does-not-exist", "purewheel"])
    out = tmp_path / "coverage.json"
    assert main(["--top", "2", "--python", "3.13", "--top-packages", str(top), "--out", str(out)]) == 1
    assert not out.exists()  # the last good dataset stays in place
    assert "too many lookup errors (1 of 2)" in capsys.readouterr().err
    assert (
        main(
            ["--top", "2", "--python", "3.13", "--top-packages", str(top), "--out", str(out), "--max-error-rate", "0.5"]
        )
        == 0
    )
    data = json.loads(out.read_text())
    assert data["lookup_errors"] == 1
    assert data["targets"][0]["glibc"] == "2.36" and data["targets"][0]["musl"] == "1.2"
