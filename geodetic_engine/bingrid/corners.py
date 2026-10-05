"""The four corners that define a bin grid by the four-point ("loadsheet") method.

The corners are labelled by their inline and crossline numbers, never by the
order they arrive in:

* A: (min inline, min crossline)
* B: (min inline, max crossline) -- A to B is a constant inline
* C: (max inline, min crossline) -- A to C is a constant crossline
* D: (max inline, max crossline) -- redundant, and what makes a QC possible

Drawn as a polygon the corners are visited A, B, D, C.
"""

from __future__ import annotations

import logging
import math
import numbers
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from geodetic_engine.bingrid.errors import DegenerateBinGridError, InvalidCornersError
from geodetic_engine.bingrid.p6 import P6Parameters

logger = logging.getLogger(__name__)

LABELS = ("A", "B", "C", "D")


@dataclass(frozen=True, slots=True)
class BinGridCorner:
    """One corner: its bin grid numbers and its coordinates.

    Attributes:
        inline: Inline number (I), an integer.
        crossline: Crossline number (J), an integer.
        easting: First coordinate value, in ``xy`` order: the easting.
        northing: Second coordinate value: the northing.

    Raises:
        InvalidCornersError: On construction, if a number is not an integer.
        DegenerateBinGridError: On construction, if a coordinate is not finite.
    """

    inline: int
    crossline: int
    easting: float
    northing: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "inline", _integer("inline", self.inline))
        object.__setattr__(self, "crossline", _integer("crossline", self.crossline))
        object.__setattr__(self, "easting", _coordinate("easting", self.easting))
        object.__setattr__(self, "northing", _coordinate("northing", self.northing))


@dataclass(frozen=True, slots=True)
class BinGridCorners:
    """The four corners A, B, C and D of a bin grid, labelled by their numbers.

    Build with :meth:`from_corners`, which takes the corners in any order.

    Attributes:
        a: (min inline, min crossline).
        b: (min inline, max crossline).
        c: (max inline, min crossline).
        d: (max inline, max crossline).

    Raises:
        InvalidCornersError: On construction, if the corners' numbers are not
            those of A, B, C and D.
    """

    a: BinGridCorner
    b: BinGridCorner
    c: BinGridCorner
    d: BinGridCorner

    def __post_init__(self) -> None:
        corners = (self.a, self.b, self.c, self.d)
        if not all(isinstance(corner, BinGridCorner) for corner in corners):
            raise InvalidCornersError("A, B, C and D must each be a BinGridCorner")
        a, b, c, d = corners
        if not (
            a.inline == b.inline < c.inline == d.inline
            and a.crossline == c.crossline < b.crossline == d.crossline
        ):
            raise InvalidCornersError(
                f"corners {_numbers(corners)} are not A (min inline, min "
                "crossline), B (min, max), C (max, min) and D (max, max); "
                "use BinGridCorners.from_corners to label corners in any order"
            )

    @classmethod
    def from_corners(
        cls, corners: Iterable[BinGridCorner | tuple[int, int, float, float]]
    ) -> BinGridCorners:
        """Label four corners given in any order.

        Args:
            corners: Exactly four corners, each a :class:`BinGridCorner` or an
                ``(inline, crossline, easting, northing)`` tuple.

        Returns:
            The corners as A, B, C and D.

        Raises:
            InvalidCornersError: If there are not exactly four, or their inline
                and crossline numbers are not two distinct values each in all
                four combinations.
        """
        given = [_corner(corner) for corner in corners]
        if len(given) != 4:
            raise InvalidCornersError(
                f"a bin grid is defined by 4 corners, got {len(given)}"
            )
        numbers = {(corner.inline, corner.crossline) for corner in given}
        inlines = {inline for inline, _ in numbers}
        crosslines = {crossline for _, crossline in numbers}
        if len(numbers) != 4 or len(inlines) != 2 or len(crosslines) != 2:
            raise InvalidCornersError(
                f"the corners' (inline, crossline) numbers {_numbers(given)} are "
                "not the four combinations of two inline and two crossline numbers"
            )
        a, b, c, d = sorted(given, key=lambda corner: (corner.inline, corner.crossline))
        return cls(a, b, c, d)

    def __iter__(self) -> Iterator[BinGridCorner]:
        return iter((self.a, self.b, self.c, self.d))

    def labelled(self) -> tuple[tuple[str, BinGridCorner], ...]:
        """The corners with their labels, in order A, B, C, D."""
        return tuple(zip(LABELS, self, strict=True))

    @property
    def inline_range(self) -> tuple[int, int]:
        """Minimum and maximum inline number."""
        return self.a.inline, self.d.inline

    @property
    def crossline_range(self) -> tuple[int, int]:
        """Minimum and maximum crossline number."""
        return self.a.crossline, self.d.crossline

    @property
    def coordinates(self) -> NDArray[np.float64]:
        """Coordinates of A, B, C and D as an array of shape ``(4, 2)``."""
        return np.array([(c.easting, c.northing) for c in self], dtype=np.float64)

    @property
    def numbers(self) -> NDArray[np.float64]:
        """Inline and crossline numbers of A, B, C and D, shape ``(4, 2)``."""
        return np.array([(c.inline, c.crossline) for c in self], dtype=np.float64)

    def with_coordinates(
        self, coordinates: Iterable[Iterable[float]]
    ) -> BinGridCorners:
        """The same corners at other coordinates.

        Args:
            coordinates: Four ``(easting, northing)`` pairs, in order A, B, C, D.

        Returns:
            Corners with these coordinates and the same numbers.
        """
        points = [tuple(point) for point in coordinates]
        if len(points) != 4 or any(len(point) != 2 for point in points):
            raise InvalidCornersError(
                f"4 (easting, northing) pairs are needed, one per corner, got {points}"
            )
        a, b, c, d = (
            BinGridCorner(corner.inline, corner.crossline, easting, northing)
            for corner, (easting, northing) in zip(self, points, strict=True)
        )
        return BinGridCorners(a, b, c, d)

    def to_json_dict(self) -> list[dict[str, Any]]:
        """Render the corners as plain data, labelled, in order A, B, C, D."""
        return [
            {
                "label": label,
                "inline": corner.inline,
                "crossline": corner.crossline,
                "easting": corner.easting,
                "northing": corner.northing,
            }
            for label, corner in self.labelled()
        ]


def corners_from_p6(
    parameters: P6Parameters,
    *,
    inline_range: tuple[int, int],
    crossline_range: tuple[int, int],
) -> BinGridCorners:
    """The four corners of a grid defined by its P6 parameters.

    This is the four-point definition of a grid the P6/11 method defined, as the
    SDU note requires to be stored alongside it.

    Args:
        parameters: The grid.
        inline_range: Minimum and maximum inline number of the corners.
        crossline_range: Minimum and maximum crossline number of the corners.

    Returns:
        The corners, at the map grid coordinates the parameters give them.

    Raises:
        InvalidCornersError: If a range is not two integers, minimum first.
    """
    i_min, i_max = _range("inline_range", inline_range)
    j_min, j_max = _range("crossline_range", crossline_range)
    nodes = [(i_min, j_min), (i_min, j_max), (i_max, j_min), (i_max, j_max)]
    a, b, c, d = (
        BinGridCorner(i, j, easting, northing)
        for (i, j), (easting, northing) in zip(
            nodes, parameters.to_map(nodes).tolist(), strict=True
        )
    )
    return BinGridCorners(a, b, c, d)


def _corner(value: BinGridCorner | tuple[int, int, float, float]) -> BinGridCorner:
    if isinstance(value, BinGridCorner):
        return value
    try:
        inline, crossline, easting, northing = value
    except (TypeError, ValueError):
        raise InvalidCornersError(
            f"a corner is (inline, crossline, easting, northing), got {value!r}"
        ) from None
    return BinGridCorner(inline, crossline, easting, northing)


def _integer(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise InvalidCornersError(f"{name} number must be an integer, not {value!r}")
    return int(value)


def _coordinate(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise DegenerateBinGridError(f"{name} must be a finite number, not {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise DegenerateBinGridError(f"{name} must be finite, not {value!r}")
    return number


def _range(name: str, value: tuple[int, int]) -> tuple[int, int]:
    try:
        low, high = value
    except (TypeError, ValueError):
        raise InvalidCornersError(
            f"{name} must be (minimum, maximum), not {value!r}"
        ) from None
    low, high = _integer(f"{name} minimum", low), _integer(f"{name} maximum", high)
    if low >= high:
        raise InvalidCornersError(f"{name} must be (minimum, maximum), not {value!r}")
    return low, high


def _numbers(corners: Iterable[BinGridCorner]) -> str:
    return ", ".join(f"({c.inline}, {c.crossline})" for c in corners)
