"""wheelgap: find the dependencies that have no prebuilt wheel for your target platform."""

__version__ = "0.8.0"

from .api import Result, check_lockfile, check_package, check_packages, make_targets, running_python  # noqa: E402
from .check import Status  # noqa: E402
from .targets import Target, TargetError  # noqa: E402

__all__ = [
    "Result",
    "Status",
    "Target",
    "TargetError",
    "__version__",
    "check_lockfile",
    "check_package",
    "check_packages",
    "make_targets",
    "running_python",
]
