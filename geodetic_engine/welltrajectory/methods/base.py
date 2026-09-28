"""What every placement method is given, and what it gives back."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    ProjectionFactors,
    Transformation,
    projection_factors,
)

type FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True, eq=False)
class LocalFrame:
    """The wellhead, and the CRS and datum a trajectory is placed in.

    Built once per trajectory so that no method re-derives it.

    Attributes:
        crs: The trajectory CRS, as given.
        horizontal_crs: Its horizontal part in 2D, unwrapped from any binding;
            its coordinate values are :attr:`crs`'s horizontal ones.
        geographic_crs: The CRS's own geodetic CRS. Every method stays on this
            datum, so no datum transformation is ever applied.
        wellhead: The wellhead's ``(x, y)`` in :attr:`crs`.
        longitude: Wellhead longitude in degrees from the datum's own prime
            meridian.
        latitude: Wellhead latitude in degrees.
        height: Wellhead elevation in metres.
        factors: Grid convergence and scale factor at the wellhead.
    """

    crs: CoordinateReferenceSystem
    horizontal_crs: CoordinateReferenceSystem
    geographic_crs: CoordinateReferenceSystem
    wellhead: FloatArray
    longitude: float
    latitude: float
    height: float
    factors: ProjectionFactors

    @classmethod
    def at(cls, crs: object, x: float, y: float, height: float) -> LocalFrame:
        """Build the frame for a wellhead at ``(x, y)`` in ``crs``, ``height`` m up."""
        factors = projection_factors(crs, (x, y))
        to_degrees = _degrees_per_unit(factors.geographic_crs)
        return cls(
            crs=factors.crs,
            horizontal_crs=factors.horizontal_crs,
            geographic_crs=factors.geographic_crs,
            wellhead=np.array([x, y], dtype=np.float64),
            longitude=float(factors.longitude[0] * to_degrees[0]),
            latitude=float(factors.latitude[0] * to_degrees[1]),
            height=height,
            factors=factors,
        )

    @property
    def semi_axes(self) -> tuple[float, float]:
        """The datum ellipsoid's semi-major and semi-minor axes, in metres."""
        ellipsoid = self.geographic_crs.crs.ellipsoid
        if ellipsoid is None:
            raise ValueError(f"{self.geographic_crs.name} states no ellipsoid")
        return ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre

    @property
    def horizontal_unit(self) -> float:
        """Metres per unit of the CRS's first horizontal axis."""
        axes = self.horizontal_crs.axes
        return axes[self.horizontal_crs.value_axis_order[0]].unit_conversion_factor

    def to_crs(self, longitude: ArrayLike, latitude: ArrayLike) -> FloatArray:
        """Geographic degrees on the frame's datum, as ``(n, 2)`` CRS values."""
        to_degrees = _degrees_per_unit(self.geographic_crs)
        values = np.column_stack(
            [
                np.asarray(longitude, dtype=np.float64) / to_degrees[0],
                np.asarray(latitude, dtype=np.float64) / to_degrees[1],
            ]
        )
        if self.horizontal_crs.crs.is_geographic:
            return values
        conversion = Transformation(self.geographic_crs, self.horizontal_crs)
        return conversion.transform(values).coordinates.to_numpy()


@dataclass(frozen=True, slots=True, eq=False)
class Placement:
    """Where a method put the stations.

    Attributes:
        xy: ``(n, 2)`` horizontal position of each station, in ``xy`` order
            and the trajectory CRS's own units.
        operations: What was done, in order, for provenance.
        local_crs: The local CRS the offsets were read in, if the method used
            one.
    """

    xy: FloatArray
    operations: tuple[str, ...]
    local_crs: CoordinateReferenceSystem | None = None


type Place = Callable[[FloatArray, LocalFrame], Placement]
"""A placement method: ``(offsets, frame) -> Placement``.

``offsets`` is ``(n, 3)``: east, north and true vertical depth of each station
below the wellhead, in metres, referenced to true north.
"""


def _degrees_per_unit(crs: CoordinateReferenceSystem) -> tuple[float, float]:
    """Degrees per unit of a geographic CRS's longitude and latitude values."""
    order = crs.value_axis_order
    return (
        float(np.degrees(crs.axes[order[0]].unit_conversion_factor)),
        float(np.degrees(crs.axes[order[1]].unit_conversion_factor)),
    )
