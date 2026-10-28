"""Verify that pyproj uses the PROJ release this package was tested against."""

from typing import Final

import pyproj

from geodetic_engine._pins import read

_PINS: Final = read()
PINNED_PROJ_VERSION: Final = _PINS.proj_version
PINNED_PYPROJ_VERSION: Final = _PINS.pyproj_version
PYPROJ_WHEEL_INDEX: Final = _PINS.pyproj_wheel_index
PYPROJ_WHEEL_REQUIREMENT: Final = _PINS.pyproj_requirement


def require_pinned_proj() -> None:
    """Refuse to run against a different PROJ and its EPSG dataset."""
    if pyproj.proj_version_str != PINNED_PROJ_VERSION:
        raise ImportError(
            f"geodetic-engine requires PROJ {PINNED_PROJ_VERSION}, but "
            f"pyproj {pyproj.__version__} uses PROJ {pyproj.proj_version_str}. "
            f'Install the pinned wheel with: pip install "{PYPROJ_WHEEL_REQUIREMENT}" '
            f"--extra-index-url {PYPROJ_WHEEL_INDEX}. If no wheel is available "
            "for your platform, build pyproj from source against the pinned PROJ."
        )
