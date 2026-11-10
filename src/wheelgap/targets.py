"""Install targets: a Python version on a platform, expanded to the wheel tags it accepts."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property

from packaging import tags
from packaging.tags import Tag

# Shared with Dockerfile image parsing. Versions are the major/minor libc ABI,
# not the distribution's package revision (see the distro package links in README).
DEBIAN_GLIBC = {"bullseye": "2.31", "bookworm": "2.36", "trixie": "2.41"}
ALPINE_MUSL = "1.2"  # Alpine 3.13 and later; use --musl 1.1 for older releases.
DISTROS: dict[str, tuple[str, str]] = {
    "ubuntu-20.04": ("glibc", "2.31"),
    "ubuntu-22.04": ("glibc", "2.35"),
    "ubuntu-24.04": ("glibc", "2.39"),
    "debian-11": ("glibc", DEBIAN_GLIBC["bullseye"]),
    "debian-12": ("glibc", DEBIAN_GLIBC["bookworm"]),
    "debian-13": ("glibc", DEBIAN_GLIBC["trixie"]),
    "rhel-8": ("glibc", "2.28"),
    "rhel-9": ("glibc", "2.34"),
    "amazonlinux-2": ("glibc", "2.26"),
    "amazonlinux-2023": ("glibc", "2.34"),
    "alpine": ("musl", ALPINE_MUSL),
}
DISTRO_ALIASES = {
    "bullseye": "debian-11",
    "bookworm": "debian-12",
    "trixie": "debian-13",
    "rocky-8": "rhel-8",
    "rocky-9": "rhel-9",
    "alma-8": "rhel-8",
    "alma-9": "rhel-9",
}

# Short platform name -> (family, arch)
PLATFORMS: dict[str, tuple[str, str]] = {
    "linux-x86_64": ("manylinux", "x86_64"),
    "linux-aarch64": ("manylinux", "aarch64"),
    "musl-x86_64": ("musllinux", "x86_64"),
    "musl-aarch64": ("musllinux", "aarch64"),
    "macos-arm64": ("macos", "arm64"),
    "macos-x86_64": ("macos", "x86_64"),
    "win-amd64": ("windows", "win_amd64"),
    "win-arm64": ("windows", "win_arm64"),
    "pyodide": ("pyodide", "wasm32"),
    "linux-armv7l": ("manylinux", "armv7l"),
    "linux-ppc64le": ("manylinux", "ppc64le"),
    "linux-s390x": ("manylinux", "s390x"),
    "linux-riscv64": ("manylinux", "riscv64"),
    "musl-armv7l": ("musllinux", "armv7l"),
    "musl-ppc64le": ("musllinux", "ppc64le"),
    "musl-s390x": ("musllinux", "s390x"),
    "musl-riscv64": ("musllinux", "riscv64"),
    # PEP 730 / PEP 738: the arch is the multiarch / ABI part of the wheel tag.
    "ios-arm64": ("ios", "arm64_iphoneos"),
    "ios-simulator-arm64": ("ios", "arm64_iphonesimulator"),
    "ios-simulator-x86_64": ("ios", "x86_64_iphonesimulator"),
    "android-arm64": ("android", "arm64_v8a"),
    "android-x86_64": ("android", "x86_64"),
}

# Platforms whose installers can't build sdists: Pyodide has no compiler, and iOS/Android
# dependencies are installed by cross-platform pip runs, which need --only-binary :all:.
WHEEL_ONLY_FAMILIES = {"pyodide", "ios", "android"}

# Less common Linux architectures: checked by name, or with `-t everything`.
EXTRA_ARCHES = {"armv7l", "ppc64le", "s390x", "riscv64"}

# `-t all` means the eight common native platforms; `-t everything` adds the extra Linux
# architectures. Pyodide, iOS and Android are only checked when asked for by name (or `-t mobile`).
NATIVE_PLATFORMS = [
    name for name, (family, arch) in PLATFORMS.items() if family not in WHEEL_ONLY_FAMILIES and arch not in EXTRA_ARCHES
]
EVERY_NATIVE_PLATFORM = [name for name, (family, _) in PLATFORMS.items() if family not in WHEEL_ONLY_FAMILIES]
MOBILE_PLATFORMS = [name for name, (family, _) in PLATFORMS.items() if family in ("ios", "android")]

ALIASES: dict[str, str] = {
    "linux": "linux-x86_64",
    "linux-amd64": "linux-x86_64",
    "linux-arm64": "linux-aarch64",
    "linux-arm64-v8": "linux-aarch64",
    "manylinux-x86_64": "linux-x86_64",
    "manylinux-aarch64": "linux-aarch64",
    "alpine": "musl-x86_64",
    "musl": "musl-x86_64",
    "musl-arm64": "musl-aarch64",
    "musllinux-x86_64": "musl-x86_64",
    "musllinux-aarch64": "musl-aarch64",
    "alpine-arm64": "musl-aarch64",
    "alpine-aarch64": "musl-aarch64",
    "mac": "macos-arm64",
    "macos": "macos-arm64",
    "macos-aarch64": "macos-arm64",
    "macos-intel": "macos-x86_64",
    "windows": "win-amd64",
    "win": "win-amd64",
    "windows-amd64": "win-amd64",
    "windows-x86_64": "win-amd64",
    "win-x86_64": "win-amd64",
    "windows-arm64": "win-arm64",
    "linux-armv7": "linux-armv7l",
    "linux-arm": "linux-armv7l",
    "linux-arm-v7": "linux-armv7l",
    "linux-armhf": "linux-armv7l",
    "manylinux-armv7l": "linux-armv7l",
    "manylinux-ppc64le": "linux-ppc64le",
    "manylinux-s390x": "linux-s390x",
    "manylinux-riscv64": "linux-riscv64",
    "musl-armv7": "musl-armv7l",
    "musl-arm": "musl-armv7l",
    "alpine-armv7": "musl-armv7l",
    "alpine-armv7l": "musl-armv7l",
    "musllinux-armv7l": "musl-armv7l",
    "musllinux-ppc64le": "musl-ppc64le",
    "musllinux-s390x": "musl-s390x",
    "musllinux-riscv64": "musl-riscv64",
    "ios": "ios-arm64",
    "iphoneos": "ios-arm64",
    "ios-simulator": "ios-simulator-arm64",
    "iphonesimulator": "ios-simulator-arm64",
    "android": "android-arm64",
    "android-arm64-v8a": "android-arm64",
    "android-aarch64": "android-arm64",
    "pyodide-wasm32": "pyodide",
    "emscripten": "pyodide",
    "wasm": "pyodide",
    "wasm32": "pyodide",
}

# family -> (sys_platform, platform_system, os_name); arch -> platform_machine
_MARKER_OS = {
    "manylinux": ("linux", "Linux", "posix"),
    "musllinux": ("linux", "Linux", "posix"),
    "macos": ("darwin", "Darwin", "posix"),
    "windows": ("win32", "Windows", "nt"),
    "pyodide": ("emscripten", "Emscripten", "posix"),
    "ios": ("ios", "iOS", "posix"),
    "android": ("android", "Android", "posix"),
}
# Markers are evaluated where dependencies are installed: Briefcase runs pip in a cross venv that
# reports the arch ("arm64") for iOS devices, and Chaquopy reports aarch64/x86_64 on Android.
_MARKER_MACHINE = {
    "win_amd64": "AMD64",
    "win_arm64": "ARM64",
    "arm64_iphoneos": "arm64",
    "arm64_iphonesimulator": "arm64",
    "x86_64_iphonesimulator": "x86_64",
    "arm64_v8a": "aarch64",
}

DEFAULT_PLATFORMS = ["linux-x86_64", "linux-aarch64", "macos-arm64", "win-amd64"]
DEFAULT_GLIBC = "2.36"
DEFAULT_MUSL = "1.2"
DEFAULT_MACOS = "14.0"
DEFAULT_IOS = "13.0"  # CPython's own minimum (PEP 730), and Briefcase's default deployment target
DEFAULT_ANDROID_API = "24"  # CPython 3.14's minimum, and Briefcase's and Chaquopy's default minSdk
MOBILE_PYTHONS = (3, 13)  # the first CPython with official iOS and Android support

# Legacy manylinux aliases (PEP 513/571/599) and the glibc minor they map to.
_LEGACY_MANYLINUX = {17: "manylinux2014", 12: "manylinux2010", 5: "manylinux1"}
_LEGACY_ARCHES = {
    "manylinux1": {"x86_64", "i686"},
    "manylinux2010": {"x86_64", "i686"},
    "manylinux2014": {"x86_64", "i686", "aarch64", "armv7l", "ppc64", "ppc64le", "s390x"},
}

# Pyodide wheels are tied to one ABI per Python version and don't work across them
# (https://pyodide-build.readthedocs.io/en/latest/reference/platform.html). PEP 783 renamed
# the tag to pyemscripten_*; both spellings of the same ABI are accepted.
PYODIDE_PLATFORMS: dict[tuple[int, int], list[str]] = {
    (3, 11): ["emscripten_3_1_32_wasm32"],
    (3, 12): ["pyodide_2024_0_wasm32", "pyemscripten_2024_0_wasm32"],
    (3, 13): ["pyodide_2025_0_wasm32", "pyemscripten_2025_0_wasm32"],
    (3, 14): ["pyemscripten_2026_0_wasm32"],
}

_PY_RE = re.compile(r"^(?:cp|py)?(\d)\.?(\d{1,2})(t?)$", re.ASCII)  # \d is ASCII only: int() would take ٣


class TargetError(ValueError):
    pass


def resolve_distro(name: str) -> tuple[str, str]:
    """Return the libc option and version for a distro, without selecting platforms."""
    normalized = name.strip().lower()
    try:
        return DISTROS[DISTRO_ALIASES.get(normalized, normalized)]
    except KeyError:
        known = ", ".join((*DISTROS, *DISTRO_ALIASES))
        raise TargetError(f"unknown distro {name!r} (known: {known})") from None


def _norm(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace("/", "-")


_LOOKUP = {_norm(k): k for k in PLATFORMS} | {_norm(k): v for k, v in ALIASES.items()}


def resolve_platform(name: str) -> str:
    try:
        return _LOOKUP[_norm(name)]
    except KeyError:
        known = ", ".join(PLATFORMS)
        raise TargetError(f"unknown target {name!r} (known: {known})") from None


def parse_python(spec: str) -> tuple[int, int, bool]:
    """'3.14' -> (3, 14, False); '3.14t' / 'cp314t' -> (3, 14, True)."""
    m = _PY_RE.match(spec.strip().lower())
    if not m:
        raise TargetError(f"can't parse Python version {spec!r} (try 3.13 or 3.14t)")
    return int(m.group(1)), int(m.group(2)), bool(m.group(3))


_VERSION_EXAMPLES = {"glibc": "2.36", "musl": "1.2", "macOS": "14.0", "iOS": "13.0"}


def parse_version_pair(spec: str, what: str) -> tuple[int, int]:
    example = _VERSION_EXAMPLES.get(what, "2.36")
    try:
        parts = spec.strip().split(".")
        major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0  # "15" means 15.0
    except ValueError:
        raise TargetError(f"can't parse {what} version {spec!r} (expected e.g. {example})") from None
    # Tags are generated for every version below this one, so absurd values would never finish.
    if not (0 <= major <= 99 and 0 <= minor <= 99):
        raise TargetError(f"{what} version {spec!r} is out of range (expected e.g. {example})")
    return major, minor


IOS_FLOOR = (12, 0)  # the oldest iOS packaging (and so pip) considers
ANDROID_FLOOR = 16  # the oldest API level packaging (and so pip) considers


ANDROID_CEILING = 99  # far above any real API level (36 in 2025); pip tries every level down to 16

MANYLINUX_FLOOR = (2, 17)  # the glibc manylinux2014 was built against (#79)
MANYLINUX_X86_ARCHES = ("x86_64", "i686")  # the arches pip traces manylinux down to glibc 2.5 on


def parse_api_level(spec: str | int) -> int:
    try:
        level = int(str(spec).strip())
    except ValueError:
        raise TargetError(f"can't parse Android API level {spec!r} (expected e.g. 24)") from None
    if level < ANDROID_FLOOR:
        raise TargetError(f"Android API level {level} is too old ({ANDROID_FLOOR} is the lowest pip knows)")
    if level > ANDROID_CEILING:
        raise TargetError(f"Android API level {level} doesn't exist (expected e.g. 24)")
    return level


def parse_ios_version(spec: str) -> tuple[int, int]:
    version = parse_version_pair(spec, "iOS")
    if version < IOS_FLOOR:
        raise TargetError(f"iOS {spec} is too old (12.0 is the lowest pip knows)")
    return version


def manylinux_platforms(arch: str, glibc: tuple[int, int]) -> list[str]:
    major, minor = glibc
    out = []
    # pip accepts manylinux floors only down to 2.5 on x86_64 and i686; every other arch's first
    # manylinux was 2.17 (manylinux2014), and pip takes no manylinux tag below that (#79). The
    # floor applies only where that first manylinux exists: glibc major 2.
    oldest = MANYLINUX_FLOOR[1] if major == MANYLINUX_FLOOR[0] and arch not in MANYLINUX_X86_ARCHES else 5
    for m in range(minor, oldest - 1, -1):
        out.append(f"manylinux_{major}_{m}_{arch}")
        legacy = _LEGACY_MANYLINUX.get(m)
        if major == 2 and legacy and arch in _LEGACY_ARCHES[legacy]:
            out.append(f"{legacy}_{arch}")
    return out


def musllinux_platforms(arch: str, musl: tuple[int, int]) -> list[str]:
    major, minor = musl
    return [f"musllinux_{major}_{m}_{arch}" for m in range(minor, -1, -1)]


@dataclass(frozen=True)
class Target:
    major: int
    minor: int
    freethreaded: bool
    platform: str
    glibc: tuple[int, int] = (2, 36)
    musl: tuple[int, int] = (1, 2)
    macos: tuple[int, int] = (14, 0)
    ios: tuple[int, int] = (13, 0)
    android_api: int = 24

    @property
    def python(self) -> str:
        return f"{self.major}.{self.minor}{'t' if self.freethreaded else ''}"

    def label(self, with_python: bool) -> str:
        return f"{self.python} {self.platform}" if with_python else self.platform

    @property
    def builds_sdists(self) -> bool:
        """Whether an installer can fall back to building an sdist (not on Pyodide, iOS or Android)."""
        return PLATFORMS[self.platform][0] not in WHEEL_ONLY_FAMILIES

    def platform_tags(self) -> list[str]:
        family, arch = PLATFORMS[self.platform]
        if family == "pyodide":
            return list(PYODIDE_PLATFORMS[(self.major, self.minor)])
        if family == "manylinux":
            # pip also takes plain linux_<arch> wheels; PyPI only allows them for 32-bit ARM (Raspberry Pi).
            return manylinux_platforms(arch, self.glibc) + [f"linux_{arch}"]
        if family == "musllinux":
            return musllinux_platforms(arch, self.musl)
        if family == "macos":
            return list(tags.mac_platforms(self.macos, arch))
        if family == "ios":
            return list(tags.ios_platforms(self.ios, arch))
        if family == "android":
            return list(tags.android_platforms(self.android_api, arch))
        return [arch]

    @cached_property
    def marker_env(self) -> dict[str, str]:
        """PEP 508 environment for evaluating markers like `sys_platform == 'win32'`."""
        family, arch = PLATFORMS[self.platform]
        sys_platform, system, os_name = _MARKER_OS[family]
        version = f"{self.major}.{self.minor}"
        return {
            "implementation_name": "cpython",
            "implementation_version": f"{version}.0",
            "os_name": os_name,
            "platform_machine": _MARKER_MACHINE.get(arch, arch),
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_system": system,
            "platform_version": "",
            "python_full_version": f"{version}.0",
            "python_version": version,
            "sys_platform": sys_platform,
            "extra": "",
        }

    @cached_property
    def tags(self) -> frozenset[Tag]:
        version = (self.major, self.minor)
        interp = f"cp{self.major}{self.minor}"
        # CPython 3.7 and older built "m" (pymalloc) ABIs: cp37-cp37m.
        abi = interp + ("t" if self.freethreaded else "m" if (self.major, self.minor) < (3, 8) else "")
        plats = self.platform_tags()
        supported = set(tags.cpython_tags(version, [abi], plats))
        supported.update(tags.compatible_tags(version, interp, plats))
        return frozenset(supported)

    def accepts(self, wheel_tags: frozenset[Tag]) -> bool:
        return not self.tags.isdisjoint(wheel_tags)


def single_python(targets: Iterable[Target]) -> str | None:
    """The shared major.minor, ignoring the free-threaded ABI; None for mixed or empty targets."""
    versions = {(t.major, t.minor) for t in targets}
    if len(versions) == 1:
        major, minor = versions.pop()
        return f"{major}.{minor}"
    return None


def build_targets(
    pythons: list[str],
    platforms: list[str],
    glibc: str = DEFAULT_GLIBC,
    musl: str = DEFAULT_MUSL,
    macos: str = DEFAULT_MACOS,
    ios: str = DEFAULT_IOS,
    android_api: str | int = DEFAULT_ANDROID_API,
) -> list[Target]:
    plats: list[str] = []
    for item in platforms:
        for name in item.split(","):
            if not name.strip():
                continue
            if name.strip().lower() == "all":
                plats.extend(NATIVE_PLATFORMS)
            elif name.strip().lower() == "everything":
                plats.extend(EVERY_NATIVE_PLATFORM)
            elif name.strip().lower() == "mobile":
                plats.extend(MOBILE_PLATFORMS)
            else:
                plats.append(resolve_platform(name))
    plats = list(dict.fromkeys(plats)) or list(DEFAULT_PLATFORMS)

    pys = list(dict.fromkeys(parse_python(p) for item in pythons for p in item.split(",") if p.strip()))
    versions = dict(
        glibc=parse_version_pair(glibc, "glibc"),
        musl=parse_version_pair(musl, "musl"),
        macos=parse_version_pair(macos, "macOS"),
        ios=parse_ios_version(ios),
    )
    api_level = parse_api_level(android_api)
    if mobile := [p for p in plats if p in MOBILE_PLATFORMS]:
        for ma, mi, _ in pys:
            if (ma, mi) < MOBILE_PYTHONS:
                raise TargetError(f"{mobile[0]}: PEP 730/738 iOS and Android targets need Python 3.13+ (not {ma}.{mi})")
    if "pyodide" in plats:
        for ma, mi, ft in pys:
            if ft or (ma, mi) not in PYODIDE_PLATFORMS:
                known = ", ".join(f"{a}.{b}" for a, b in PYODIDE_PLATFORMS)
                raise TargetError(f"no Pyodide build for Python {ma}.{mi}{'t' * ft} (known: {known})")
    return [Target(ma, mi, ft, plat, android_api=api_level, **versions) for (ma, mi, ft) in pys for plat in plats]
