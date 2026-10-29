"""The PROJ and pyproj releases this package pins, read from one file.

A checkout states them in ``.devcontainer/pins.env``; the build backend ships
that file in the wheel as ``geodetic_engine/_pins.env``. This module imports
nothing outside the standard library, so the release scripts load it by path
without importing the package, and with it pyproj.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

PACKAGED: Final = Path(__file__).with_name("_pins.env")
CHECKOUT: Final = Path(__file__).resolve().parents[1] / ".devcontainer" / "pins.env"

_VERSION: Final = r"\d+(?:\.\d+)+"
_SHA256: Final = r"[0-9a-f]{64}"
# Every key the file must set, with the form its value must take.
_FORMS: Final = {
    "PROJ_VERSION": re.compile(r"\d+\.\d+\.\d+"),
    "PROJ_SHA256": re.compile(_SHA256),
    "PROJ_DATA_VERSION": re.compile(_VERSION),
    "PROJ_DATA_SHA256": re.compile(_SHA256),
    "PYPROJ_VERSION": re.compile(_VERSION + r"(?:(?:a|b|rc)\d+)?(?:\.post\d+)?"),
    "PYPROJ_WHEEL_INDEX": re.compile(r"https://[^\s\"'`\\]+/"),
}
KEYS: Final = tuple(_FORMS)


@dataclass(frozen=True, slots=True)
class Pins:
    """The pinned releases.

    Attributes:
        proj_version: The PROJ release, for example ``"9.9.0"``.
        proj_sha256: SHA-256 of its source archive.
        proj_data_version: The proj-data grid package matching that release.
        proj_data_sha256: SHA-256 of the proj-data archive.
        pyproj_version: The pyproj release built against that PROJ.
        pyproj_wheel_index: The package index serving those pyproj wheels.
    """

    proj_version: str
    proj_sha256: str
    proj_data_version: str
    proj_data_sha256: str
    pyproj_version: str
    pyproj_wheel_index: str

    @property
    def pyproj_requirement(self) -> str:
        """The pyproj wheel this project builds against the pinned PROJ."""
        return f"pyproj=={self.pyproj_version}+proj{self.proj_version}"

    def get(self, key: str) -> str:
        """A value by its key in the file, or ``PYPROJ_REQUIREMENT``."""
        if key == "PYPROJ_REQUIREMENT":
            return self.pyproj_requirement
        if key not in _FORMS:
            raise ValueError(f"unknown pin {key!r}; known: {', '.join(KEYS)}")
        return str(getattr(self, key.lower()))


def parse(text: str, source: str = "pins.env") -> Pins:
    """Read the pins, refusing anything but the expected keys in their forms.

    Raises:
        ValueError: On an unknown, repeated, missing or malformed setting.
    """
    found: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in _FORMS:
            raise ValueError(
                f"{source}:{number}: expected KEY=VALUE with KEY one of "
                f"{', '.join(KEYS)}, got {line!r}"
            )
        if key in found:
            raise ValueError(f"{source}:{number}: {key} is set twice")
        if not _FORMS[key].fullmatch(value):
            raise ValueError(f"{source}:{number}: {key}={value!r} is malformed")
        found[key] = value
    missing = [key for key in KEYS if key not in found]
    if missing:
        raise ValueError(f"{source} does not set {', '.join(missing)}")
    return Pins(*(found[key] for key in KEYS))


def read() -> Pins:
    """The pins of this installation, or of the checkout it is run from."""
    path = PACKAGED if PACKAGED.is_file() else CHECKOUT
    return parse(path.read_text(encoding="utf-8"), str(path))
