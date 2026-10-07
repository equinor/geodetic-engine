"""The IOGP P6/11 seismic bin grid parameters and the transformation they define.

A bin grid maps the local (inline, crossline) numbers of seismic bin centres,
``(I, J)``, onto the map grid (easting, northing) of a projected CRS. The
parameters are those of EPSG methods 9666 (``P6 I=J+90``, right-handed: the
I-axis bearing is the J-axis bearing plus 90 degrees) and 1049 (``P6 I=J-90``,
left-handed), as given in IOGP Guidance Note 7-2:

* ``E = E0 + h (I - I0) cos t k dI / incI + (J - J0) sin t k dJ / incJ``
* ``N = N0 - h (I - I0) sin t k dI / incI + (J - J0) cos t k dJ / incJ``

with ``h`` +1 for a right-handed grid and -1 for a left-handed one, ``t`` the
map grid bearing of the J-axis, ``k`` the bin grid scale factor, ``dI`` and
``dJ`` the bin widths and ``incI`` and ``incJ`` the bin node increments. The
bin widths are ground distances; ``k`` scales them onto the map grid.

PROJ runs the conversion, as its ``affine`` operation with the coefficients
PROJ itself gives these two methods (see its affine documentation). The
operation is built from those coefficients at full precision rather than from
the EPSG method definition: a conversion PROJ instantiates from one goes
through its PROJ-string export, which rounds every coefficient lying within
1e-9 of a tenth to that tenth, so a J-axis bearing within about a millionth of
a degree of a cardinal direction would be applied as exactly cardinal.

The inline number is ``I`` and the crossline number is ``J`` throughout, so the
J-axis is the direction of a constant inline and its bearing is the "inline
bearing" of the seismic industry.
"""

from __future__ import annotations

import dataclasses
import logging
import math
import numbers
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pyproj import Transformer
from pyproj.enums import TransformDirection

from geodetic_engine.bingrid.errors import InvalidParameterError

logger = logging.getLogger(__name__)

_METHODS = {
    "right": (9666, "P6 I=J+90 seismic bin grid coordinate operation", 1),
    "left": (1049, "P6 I=J-90 seismic bin grid coordinate operation", -1),
}


class Handedness(StrEnum):
    """Which side of the J-axis the I-axis points to, seen from above.

    Standing on a constant inline and looking towards increasing crosslines,
    the grid is right-handed if the next inline is to the right.
    """

    RIGHT = "right"
    """The I-axis bearing is the J-axis bearing plus 90 degrees (EPSG 9666)."""

    LEFT = "left"
    """The I-axis bearing is the J-axis bearing minus 90 degrees (EPSG 1049)."""

    @property
    def method_code(self) -> int:
        """EPSG coordinate operation method code, 9666 or 1049."""
        return _METHODS[self.value][0]

    @property
    def method_name(self) -> str:
        """EPSG coordinate operation method name."""
        return _METHODS[self.value][1]

    @property
    def sign(self) -> int:
        """+1 for a right-handed grid, -1 for a left-handed one."""
        return _METHODS[self.value][2]

    @classmethod
    def from_method_code(cls, code: int) -> Handedness:
        """The handedness an EPSG P6 method code states.

        Args:
            code: 9666 or 1049.

        Returns:
            The handedness.

        Raises:
            InvalidParameterError: If the code is not a P6 bin grid method.
        """
        for value, (method_code, _, _) in _METHODS.items():
            if not isinstance(code, bool) and code == method_code:
                return cls(value)
        raise InvalidParameterError(
            f"transformation method {code!r} is not a P6 bin grid method: "
            "9666 states a right-handed grid and 1049 a left-handed one"
        )


@dataclass(frozen=True, slots=True)
class P6Parameters:
    """A bin grid as the parameters of EPSG method 9666 or 1049.

    Attributes:
        origin_i: Inline number of the origin bin node.
        origin_j: Crossline number of the origin bin node.
        origin_easting: Map grid easting of the origin bin node.
        origin_northing: Map grid northing of the origin bin node.
        bin_width_i: Distance between adjacent bin nodes along the I-axis, as a
            ground distance in the CRS's linear unit.
        bin_width_j: Distance between adjacent bin nodes along the J-axis.
        bearing_j: Map grid bearing of the J-axis, clockwise from grid north,
            in degrees, normalised to ``[0, 360)``.
        handedness: Whether the I-axis is 90 degrees clockwise (right-handed)
            or counterclockwise (left-handed) from the J-axis.
        scale_factor: Bin grid scale factor ``k``: map grid distance per ground
            distance at the grid. 1 when the grid was designed on the map grid.
        increment_i: Change of the inline number between adjacent bin nodes.
        increment_j: Change of the crossline number between adjacent bin nodes.

    Raises:
        InvalidParameterError: On construction, if a value is outside its
            domain (see the attribute descriptions).

    Example:
        >>> grid = P6Parameters(
        ...     origin_i=1, origin_j=1,
        ...     origin_easting=456781.0, origin_northing=5836723.0,
        ...     bin_width_i=25.0, bin_width_j=12.5, bearing_j=20.0,
        ...     handedness=Handedness.RIGHT, scale_factor=0.99984,
        ... )
        >>> grid.to_map(300, 247).round(2)
        array([[ 464855.62, 5837055.9 ]])
    """

    origin_i: float
    origin_j: float
    origin_easting: float
    origin_northing: float
    bin_width_i: float
    bin_width_j: float
    bearing_j: float
    handedness: Handedness
    scale_factor: float = 1.0
    increment_i: int = 1
    increment_j: int = 1

    def __post_init__(self) -> None:
        for name in ("origin_i", "origin_j", "origin_easting", "origin_northing"):
            object.__setattr__(self, name, finite_number(name, getattr(self, name)))
        for name in ("bin_width_i", "bin_width_j", "scale_factor"):
            object.__setattr__(self, name, positive_number(name, getattr(self, name)))
        for name in ("increment_i", "increment_j"):
            object.__setattr__(self, name, node_increment(name, getattr(self, name)))
        bearing = normalised_bearing(finite_number("bearing_j", self.bearing_j))
        object.__setattr__(self, "bearing_j", bearing)
        object.__setattr__(self, "handedness", _handedness(self.handedness))

    @property
    def method_code(self) -> int:
        """EPSG coordinate operation method code, 9666 or 1049."""
        return self.handedness.method_code

    @property
    def bearing_i(self) -> float:
        """Map grid bearing of the I-axis, in degrees in ``[0, 360)``."""
        return normalised_bearing(self.bearing_j + 90.0 * self.handedness.sign)

    @property
    def grid_step_i(self) -> float:
        """Map grid distance per unit of inline number, ``k dI / incI``."""
        return self.scale_factor * self.bin_width_i / self.increment_i

    @property
    def grid_step_j(self) -> float:
        """Map grid distance per unit of crossline number, ``k dJ / incJ``."""
        return self.scale_factor * self.bin_width_j / self.increment_j

    def to_map(
        self,
        i: Iterable[Iterable[float]] | Iterable[float] | float,
        j: Iterable[float] | float | None = None,
    ) -> NDArray[np.float64]:
        """Map grid coordinates of bin grid positions.

        Args:
            i: Either every position in one go -- one ``(inline, crossline)``
                pair given flat, a sequence of pairs, or an array of shape
                ``(n, 2)`` -- when ``j`` is omitted; or the inline numbers, as a
                scalar or a sequence, when ``j`` is given.
            j: Crossline numbers, matching ``i``.

        Returns:
            Array of shape ``(n, 2)``: easting and northing per position.
        """
        nodes = pairs(i, j)
        easting, northing = _offsets_to_bin(self).transform(
            nodes[:, 0], nodes[:, 1], direction=TransformDirection.INVERSE
        )
        return np.column_stack(
            (self.origin_easting + easting, self.origin_northing + northing)
        )

    def to_bin(
        self,
        easting: Iterable[Iterable[float]] | Iterable[float] | float,
        northing: Iterable[float] | float | None = None,
    ) -> NDArray[np.float64]:
        """Bin grid positions of map grid coordinates.

        Args:
            easting: Either every point in one go -- one ``(easting,
                northing)`` pair given flat, a sequence of pairs, or an array
                of shape ``(n, 2)`` -- when ``northing`` is omitted; or the
                eastings, as a scalar or a sequence, when it is given.
            northing: Northings, matching ``easting``.

        Returns:
            Array of shape ``(n, 2)``: fractional inline and crossline numbers.
        """
        points = pairs(easting, northing)
        i, j = _offsets_to_bin(self).transform(
            points[:, 0] - self.origin_easting, points[:, 1] - self.origin_northing
        )
        return np.column_stack((i, j))

    def reanchored(self, i: float, j: float) -> P6Parameters:
        """The same grid with its origin moved to another bin node.

        Args:
            i: Inline number of the new origin.
            j: Crossline number of the new origin.

        Returns:
            Parameters that map every position exactly as these do.
        """
        ((easting, northing),) = self.to_map(i, j)
        return dataclasses.replace(
            self,
            origin_i=i,
            origin_j=j,
            origin_easting=float(easting),
            origin_northing=float(northing),
        )

    def to_json_dict(self) -> dict[str, Any]:
        """Render the parameters as plain data.

        Returns:
            Every attribute, with the method code and both axis bearings.
        """
        return {
            "method_code": self.method_code,
            "method_name": self.handedness.method_name,
            "handedness": self.handedness.value,
            "origin_i": self.origin_i,
            "origin_j": self.origin_j,
            "origin_easting": self.origin_easting,
            "origin_northing": self.origin_northing,
            "bin_width_i": self.bin_width_i,
            "bin_width_j": self.bin_width_j,
            "bearing_j": self.bearing_j,
            "bearing_i": self.bearing_i,
            "scale_factor": self.scale_factor,
            "increment_i": self.increment_i,
            "increment_j": self.increment_j,
        }


def finite_number(name: str, value: Any) -> float:
    """``value`` as a float, if it is a finite real number.

    Raises:
        InvalidParameterError: Naming ``name``, if it is not.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise InvalidParameterError(f"{name} must be a number, not {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise InvalidParameterError(f"{name} must be finite, not {value!r}")
    return number


def positive_number(name: str, value: Any) -> float:
    """``value`` as a float, if it is a finite and positive real number.

    Raises:
        InvalidParameterError: Naming ``name``, if it is not.
    """
    number = finite_number(name, value)
    if number <= 0.0:
        raise InvalidParameterError(f"{name} must be positive, not {value!r}")
    return number


def node_increment(name: str, value: Any) -> int:
    """``value`` as an int, if it is a positive integer.

    Raises:
        InvalidParameterError: Naming ``name``, if it is not.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise InvalidParameterError(f"{name} must be an integer, not {value!r}")
    if value < 1:
        raise InvalidParameterError(f"{name} must be at least 1, not {value!r}")
    return int(value)


def normalised_bearing(degrees: float) -> float:
    """A bearing in degrees, folded into ``[0, 360)``."""
    folded = math.fmod(degrees, 360.0)
    if folded < 0.0:
        folded += 360.0
    # Adding 360 to a tiny negative remainder rounds to exactly 360.
    return 0.0 if folded >= 360.0 else folded + 0.0


def pairs(
    first: Iterable[Iterable[float]] | Iterable[float] | float,
    second: Iterable[float] | float | None,
) -> NDArray[np.float64]:
    """Points given whole or per axis, as an array of shape ``(n, 2)``.

    Raises:
        ValueError: If the values do not make up two per point.
    """
    if second is None:
        values = np.asarray(_listed(first), dtype=np.float64)
        if values.ndim == 1 and values.size in (0, 2):
            return values.reshape(-1, 2)
        if values.ndim != 2 or values.shape[1] != 2:
            raise ValueError(
                f"expected one (x, y) pair or a sequence of pairs, got shape "
                f"{values.shape}"
            )
        return values
    x = np.atleast_1d(np.asarray(_listed(first), dtype=np.float64))
    y = np.atleast_1d(np.asarray(_listed(second), dtype=np.float64))
    if x.ndim != 1 or x.shape != y.shape:
        raise ValueError(
            f"the two axes' values must be matching scalars or sequences, got "
            f"shapes {x.shape} and {y.shape}"
        )
    return np.column_stack((x, y))


def _listed(values: Any) -> Any:
    """``values`` with its iterables as lists, since NumPy cannot read a generator."""
    if hasattr(values, "__array__") or isinstance(values, str | bytes):
        return values
    if isinstance(values, Iterable):
        return [_listed(value) for value in values]
    return values


def _offsets_to_bin(parameters: P6Parameters) -> Transformer:
    """PROJ's affine from map grid offsets off the origin to bin grid numbers.

    The coefficients are those PROJ documents for EPSG methods 9666 and 1049,
    with the origin's easting and northing left out: the offsets are taken in
    Python, which keeps the digits an affine on whole map coordinates loses.
    """
    theta = math.radians(parameters.bearing_j)
    sign = parameters.handedness.sign
    bins_per_unit_i = 1.0 / parameters.grid_step_i
    bins_per_unit_j = 1.0 / parameters.grid_step_j
    return _affine(
        parameters.origin_i,
        parameters.origin_j,
        sign * bins_per_unit_i * math.cos(theta),
        -sign * bins_per_unit_i * math.sin(theta),
        bins_per_unit_j * math.sin(theta),
        bins_per_unit_j * math.cos(theta),
    )


@lru_cache(maxsize=256)
def _affine(
    xoff: float, yoff: float, s11: float, s12: float, s21: float, s22: float
) -> Transformer:
    # repr() round-trips every double; PROJ parses the string without rounding.
    return Transformer.from_pipeline(
        f"+proj=affine +xoff={xoff!r} +yoff={yoff!r} "
        f"+s11={s11!r} +s12={s12!r} +s21={s21!r} +s22={s22!r}"
    )


def _handedness(value: Any) -> Handedness:
    if isinstance(value, Handedness):
        return value
    try:
        return Handedness(value)
    except ValueError:
        raise InvalidParameterError(
            f"handedness must be 'right' or 'left', not {value!r}"
        ) from None
