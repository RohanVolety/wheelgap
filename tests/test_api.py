import json

import pytest

import wheelgap
from wheelgap import Status


def test_make_targets_takes_cli_style_names():
    targets = wheelgap.make_targets(python="3.13,3.14t", platforms=["linux-aarch64", "alpine"])
    assert [t.label(True) for t in targets] == [
        "3.13 linux-aarch64",
        "3.13 musl-x86_64",
        "3.14t linux-aarch64",
        "3.14t musl-x86_64",
    ]
    assert len(wheelgap.make_targets(python="3.13", platforms="all")) == 8
    with pytest.raises(wheelgap.TargetError):
        wheelgap.make_targets(python="3.13", platforms="solaris")


@pytest.mark.parametrize("python", ["", " , "])
def test_make_targets_uses_running_python_for_empty_input(python):
    assert wheelgap.make_targets(python=python) == wheelgap.make_targets()


def test_make_targets_empty_python_keeps_other_options():
    kwargs = dict(platforms="linux-x86_64", glibc="2.28")
    assert wheelgap.make_targets(python="", **kwargs) == wheelgap.make_targets(**kwargs)


def test_package_checks_reject_empty_targets_before_lookup(fake_pypi):
    with pytest.raises(ValueError, match="at least one target"):
        wheelgap.check_packages(["purewheel"], targets=[])
    with pytest.raises(ValueError, match="at least one target"):
        wheelgap.check_package("purewheel", targets=[])
    with pytest.raises(ValueError, match="at least one target"):
        wheelgap.check_package("purewheel", targets=[], files=["purewheel-1.0-py3-none-any.whl"])
    assert not fake_pypi


def test_check_package_offline_with_files():
    targets = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "musl-aarch64"])
    files = ["x-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", "x-1.0.tar.gz"]
    result = wheelgap.check_package("x", "1.0", targets, files=files)
    assert result.statuses == {targets[0]: Status.WHEEL, targets[1]: Status.SOURCE}
    assert not result.ok and result.gaps == [targets[1]]
    assert not result.pure
    assert result.to_dict() == {
        "name": "x",
        "version": "1.0",
        "error": None,
        "results": {"3.13 linux-x86_64": "wheel", "3.13 musl-aarch64": "source"},
    }
    json.dumps(result.to_dict())  # JSON-friendly


def test_check_package_from_index(fake_pypi):
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    result = wheelgap.check_package("purewheel", targets=targets)
    assert result.version == "3.1" and result.ok and result.pure
    assert fake_pypi == ["https://pypi.org/pypi/purewheel/json"]


def test_check_packages_mixes_names_and_pins(fake_pypi):
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    results = wheelgap.check_packages(["purewheel", ("nowheels", "0.9"), "does-not-exist"], targets)
    by_name = {r.name: r for r in results}
    assert by_name["purewheel"].ok
    assert by_name["nowheels"].statuses[targets[0]] is Status.SOURCE
    assert by_name["does-not-exist"].error == "not on index"
    assert by_name["does-not-exist"].statuses[targets[0]] is Status.UNKNOWN


def test_check_lockfile_respects_markers_and_graph(fixtures):
    targets = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "win-amd64"])
    results = {r.name: r for r in wheelgap.check_lockfile(fixtures / "uvproj", targets)}
    assert results["winonly"].statuses == {targets[0]: Status.NOT_NEEDED, targets[1]: Status.WHEEL}
    assert "orphan" not in results  # installed on no target
    assert results["puresdist"].gaps == targets


def test_default_targets_use_running_python():
    targets = wheelgap.make_targets()
    assert {t.python for t in targets} == {wheelgap.running_python()}


def test_pinned_yanked_release_keeps_its_files(fake_pypi):
    # pip installs a release pinned with == even if it has been yanked (PEP 592).
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    assert wheelgap.check_package("yanked", "1.0", targets).statuses[targets[0]] is Status.WHEEL


@pytest.mark.parametrize(
    ("files", "pure"),
    [
        (["x-1-py3-none-any.whl"], True),
        (["x-1-py2.py3-none-any.whl"], True),
        (["x-1-py2-none-any.whl", "x-1.tar.gz"], False),  # e.g. futures: Python 2 only
        (["x-1-cp313-cp313-manylinux_2_17_x86_64.whl"], False),
    ],
)
def test_pure_means_a_python3_wheel_for_every_platform(files, pure):
    assert wheelgap.check_package("x", "1", wheelgap.make_targets("3.13", "linux"), files=files).pure is pure


def test_pinned_release_ignores_yanked_files_when_others_remain(fake_pypi):
    # pip only falls back to yanked files when every file of the pinned release is yanked.
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    assert wheelgap.check_package("halfyanked", "2.0", targets).statuses[targets[0]] is Status.SOURCE


# requires-python ==3.12.*, so uv kept only fastlib's cp312 wheel; PyPI (fake_pypi) has a cp313 one.
LOCK_312_ONLY = (
    'version = 1\nrequires-python = "==3.12.*"\n\n'
    '[[package]]\nname = "app"\nversion = "0"\nsource = { editable = "." }\ndependencies = [{ name = "fastlib" }]\n\n'
    '[[package]]\nname = "fastlib"\nversion = "2.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
    'sdist = { url = "https://files.pythonhosted.org/p/fastlib-2.0.0.tar.gz" }\n'
    'wheels = [{ url = "https://files.pythonhosted.org/p/fastlib-2.0.0-cp312-cp312-manylinux_2_17_x86_64.whl" }]\n'
)


def test_check_lockfile_rechecks_a_lock_for_another_python(fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    targets = wheelgap.make_targets(python="3.13", platforms="linux-x86_64")
    (result,) = wheelgap.check_lockfile(tmp_path, targets)
    assert result.name == "fastlib" and result.statuses == {targets[0]: Status.WHEEL}
    assert fake_pypi == ["https://pypi.org/pypi/fastlib/2.0.0/json"]
