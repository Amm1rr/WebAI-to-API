import os
import sys


REQUIRED_PYTHON_VERSION = (3, 11)
MAX_PYTHON_VERSION = (3, 13)
SUPPORTED_RANGE_TEXT = "3.11 to <3.13"
WINDOWS_MINIMUM_PYTHON_VERSIONS = {
    (3, 11): (3, 11, 10),
    (3, 12): (3, 12, 4),
}
WINDOWS_SUPPORTED_RANGE_TEXT = " or ".join(
    f"{major}.{minor}.{patch}+"
    for major, minor, patch in WINDOWS_MINIMUM_PYTHON_VERSIONS.values()
)


def is_supported_python(version_info=None, *, platform_name=None):
    """Return whether Python satisfies project and Windows cache requirements."""
    version_info = sys.version_info if version_info is None else version_info
    platform_name = os.name if platform_name is None else platform_name
    version = tuple(version_info[:3])
    major_minor = version[:2]

    if not REQUIRED_PYTHON_VERSION <= major_minor < MAX_PYTHON_VERSION:
        return False
    if platform_name not in ("nt", "win32"):
        return True

    minimum_version = WINDOWS_MINIMUM_PYTHON_VERSIONS.get(major_minor)
    return minimum_version is not None and version >= minimum_version
