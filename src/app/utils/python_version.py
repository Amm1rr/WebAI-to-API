import os
import sys


REQUIRED_PYTHON_VERSION = (3, 11)
MAX_PYTHON_VERSION = (3, 13)
SUPPORTED_PYTHON_RANGE = ">=3.11,<3.13"
SUPPORTED_RANGE_TEXT = "3.11 to <3.13"
WINDOWS_MINIMUM_PYTHON_VERSIONS = {
    (3, 11): (3, 11, 10),
    (3, 12): (3, 12, 4),
}
WINDOWS_SUPPORTED_RANGE_TEXT = " or ".join(
    f"{major}.{minor}.{patch}+"
    for major, minor, patch in WINDOWS_MINIMUM_PYTHON_VERSIONS.values()
)


def classify_python_version(version_info=None, *, platform_name=None):
    """Return support status and rejection details for a Python version."""
    version_info = sys.version_info if version_info is None else version_info
    platform_name = os.name if platform_name is None else platform_name
    version = tuple(version_info[:3])
    major_minor = version[:2]

    result = {
        "supported": False,
        "version": ".".join(str(part) for part in version),
        "reason": "unsupported_major_minor",
        "supported_range": SUPPORTED_PYTHON_RANGE,
        "required": SUPPORTED_PYTHON_RANGE,
    }

    if not REQUIRED_PYTHON_VERSION <= major_minor < MAX_PYTHON_VERSION:
        return result
    if platform_name not in ("nt", "win32"):
        result["supported"] = True
        result["reason"] = "supported"
        return result

    minimum_version = WINDOWS_MINIMUM_PYTHON_VERSIONS.get(major_minor)
    required = ".".join(str(part) for part in minimum_version) + "+"
    result["required"] = required
    if version < minimum_version:
        result["reason"] = "windows_patch_too_old"
        return result

    result["supported"] = True
    result["reason"] = "supported"
    return result


def is_supported_python(version_info=None, *, platform_name=None):
    """Return whether Python satisfies project and Windows cache requirements."""
    return classify_python_version(
        version_info,
        platform_name=platform_name,
    )["supported"]
