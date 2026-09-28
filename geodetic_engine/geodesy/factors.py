"""Projection factors: grid convergence and point scale factor, from PROJ.

A map projection distorts. At any one point that distortion is summarised by
how far grid north is turned from true north, the **grid convergence**, and by
how much a short distance is stretched, the **point scale factor**. Surveys
need both: an azimuth measured against true north has to be turned onto the
grid, and a ground distance has to be scaled onto it.

Convention, used throughout this package:

* ``grid_convergence`` (gamma) is the angle from true north to grid north,
  positive clockwise, so positive where grid north lies east of true north.
  Hence ``grid azimuth = true azimuth - gamma``. PROJ's
  ``meridian_convergence`` already follows it; ``tests/geodesy/test_factors.py``
  pins that against the closed form and against a transformed grid bearing.
* ``scale_factor`` (k) is the parallel scale: ``grid distance = k *
  ellipsoidal distance``. For a conformal projection it equals the meridional
  scale, and it is the value surveying calls the scale factor.

Everything is computed on the CRS's own geodetic datum. A projected coordinate
is unprojected onto its own base CRS, which is a conversion rather than a
transformation, so no datum shift is ever involved or needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray
from pyproj import CRS, Proj
from pyproj.exceptions import ProjError

from geodetic_engine.geodesy.crs import CoordinateReferenceSystem
from geodetic_engine.geodesy.errors import (
    TransformationFailedError,
    UnsupportedCRSError,
)
from geodetic_engine.geodesy.transformation import Transformation

CONVENTION = (
    "grid_convergence is the angle from true north to grid north, positive "
    "clockwise; grid azimuth = true azimuth - grid_convergence. scale_factor "
    "is the parallel scale k; grid distance = k * ellipsoidal distance."
)

# PROJ's name for each factor this module reports, by the name it reports it.
_PROJ_NAMES = {
    "grid_convergence": "meridian_convergence",
    "scale_factor": "parallel_scale",
    "meridional_scale": "meridional_scale",
    "areal_scale": "areal_scale",
    "angular_distortion": "angular_distortion",
}

type FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True, eq=False)
class ProjectionFactors:
    """Projection factors at one or more points, one array entry per point.

    Attributes:
        crs: The CRS the factors were asked for.
        horizontal_crs: The part of it the factors describe: the base of a
            bound CRS, the horizontal part of a compound one, in 2D. Its
            coordinate values are the same as :attr:`crs`'s horizontal ones.
        geographic_crs: Its own geodetic CRS, which :attr:`longitude` and
            :attr:`latitude` are expressed in.
        projected: False for a geographic CRS, where there is no grid: the
            convergence is then zero and every scale is one.
        coordinates: The points as given, one row per point, in ``xy`` order.
        longitude: Longitude in :attr:`geographic_crs`'s own unit and prime
            meridian, which is degrees from Greenwich almost always.
        latitude: Latitude, in the same unit.
        grid_convergence: Gamma in degrees; see the module docstring for sign.
        scale_factor: Point scale factor k, the parallel scale.
        meridional_scale: Scale along the meridian, h.
        areal_scale: Areal scale factor.
        angular_distortion: Maximum angular distortion in degrees; zero for a
            conformal projection.
    """

    crs: CoordinateReferenceSystem
    horizontal_crs: CoordinateReferenceSystem
    geographic_crs: CoordinateReferenceSystem
    projected: bool
    coordinates: FloatArray
    longitude: FloatArray
    latitude: FloatArray
    grid_convergence: FloatArray
    scale_factor: FloatArray
    meridional_scale: FloatArray
    areal_scale: FloatArray
    angular_distortion: FloatArray

    def to_true_azimuth(self, grid_azimuth: ArrayLike) -> FloatArray:
        """Grid azimuths in degrees, turned onto true north, in ``[0, 360)``."""
        grid = np.asarray(grid_azimuth, dtype=np.float64)
        return np.mod(grid + self.grid_convergence, 360.0)

    def to_grid_azimuth(self, true_azimuth: ArrayLike) -> FloatArray:
        """True azimuths in degrees, turned onto grid north, in ``[0, 360)``."""
        true = np.asarray(true_azimuth, dtype=np.float64)
        return np.mod(true - self.grid_convergence, 360.0)

    def to_json_dict(self) -> dict[str, Any]:
        """Render the factors as plain data, one entry per point."""
        columns = {
            "longitude": self.longitude,
            "latitude": self.latitude,
            **{name: getattr(self, name) for name in _PROJ_NAMES},
        }
        return {
            "crs": _identify(self.crs),
            "geographic_crs": _identify(self.geographic_crs),
            "projected": self.projected,
            "coordinate_order": "xy",
            "angle_unit": "degree",
            "convention": CONVENTION,
            "points": [
                {"coordinates": row.tolist()}
                | {name: float(values[index]) for name, values in columns.items()}
                for index, row in enumerate(self.coordinates)
            ],
        }


def projection_factors(
    crs: Any, coordinates: ArrayLike, *, geographic: bool = False
) -> ProjectionFactors:
    """Grid convergence, scale factor and distortion at each point.

    A bound CRS is read through its base CRS and a compound CRS through its
    horizontal part, since neither the binding nor a height changes the map
    projection.

    Args:
        crs: Anything :meth:`CoordinateReferenceSystem.from_user_input`
            accepts.
        coordinates: One point given flat, or one row per point, in ``xy``
            order. A third value per point, such as a height, is ignored.
        geographic: Read the points as longitude and latitude in the CRS's own
            geodetic CRS instead of as values in ``crs`` itself.

    Returns:
        The factors, one entry per point.

    Raises:
        UnsupportedCRSError: If the CRS has no projected or geographic
            horizontal part: geocentric, engineering or vertical.
        ValueError: If the points are not two or three values each.
        TransformationFailedError: If PROJ cannot evaluate a point.

    Example:
        >>> factors = projection_factors("EPSG:32631", (500000.0, 6600000.0))
        >>> round(float(factors.scale_factor[0]), 6)
        0.9996
    """
    resolved = CoordinateReferenceSystem.from_user_input(crs)
    horizontal = _horizontal(resolved.crs)
    base = horizontal.geodetic_crs if horizontal.is_projected else horizontal
    if base is None or not (horizontal.is_projected or horizontal.is_geographic):
        raise UnsupportedCRSError(
            f"{_identify(resolved)} has no map projection or geographic "
            "horizontal part, so it has no projection factors"
        )
    geographic_crs = CoordinateReferenceSystem.from_user_input(base.to_2d())

    points = _points(coordinates)
    lonlat = points
    if horizontal.is_projected and not geographic:
        # Same datum at both ends, so this is only the projection's inverse.
        transformation = Transformation(horizontal, geographic_crs)
        lonlat = transformation.transform(points).coordinates.to_numpy()

    count = len(points)
    if horizontal.is_projected:
        values = _proj_factors(horizontal, geographic_crs, lonlat, resolved)
    else:
        values = {name: np.ones(count) for name in _PROJ_NAMES}
        values["grid_convergence"] = np.zeros(count)
        values["angular_distortion"] = np.zeros(count)

    return ProjectionFactors(
        crs=resolved,
        horizontal_crs=CoordinateReferenceSystem(horizontal, resolved.definition),
        geographic_crs=geographic_crs,
        projected=bool(horizontal.is_projected),
        coordinates=points,
        longitude=lonlat[:, 0].copy(),
        latitude=lonlat[:, 1].copy(),
        **values,
    )


def _proj_factors(
    projected: CRS,
    geographic_crs: CoordinateReferenceSystem,
    lonlat: FloatArray,
    described: CoordinateReferenceSystem,
) -> dict[str, FloatArray]:
    """Evaluate PROJ's factors at points given in the base CRS's own values."""
    # PROJ reads longitude relative to the CRS's own prime meridian, which is
    # what the unprojection produced, but only ever in degrees.
    degrees = [
        np.degrees(geographic_crs.axes[index].unit_conversion_factor)
        for index in geographic_crs.value_axis_order[:2]
    ]
    try:
        factors = Proj(projected).get_factors(
            lonlat[:, 0] * degrees[0], lonlat[:, 1] * degrees[1], errcheck=True
        )
    except ProjError as error:
        raise TransformationFailedError(
            f"PROJ could not evaluate projection factors in {_identify(described)}: "
            f"{error}"
        ) from error
    return {
        name: np.asarray(getattr(factors, proj_name), dtype=np.float64).reshape(-1)
        for name, proj_name in _PROJ_NAMES.items()
    }


def _horizontal(crs: CRS) -> CRS:
    """The part of a CRS that carries its map projection, in two dimensions."""
    if crs.is_bound and crs.source_crs is not None:
        return _horizontal(crs.source_crs)
    if crs.is_compound:
        return _horizontal(crs.sub_crs_list[0])
    return crs.to_2d()


def _points(coordinates: ArrayLike) -> FloatArray:
    """Read points as ``(n, 2)`` floats, dropping any height."""
    points = np.asarray(coordinates, dtype=np.float64)
    if points.ndim == 1:
        points = points[np.newaxis, :]
    if points.ndim != 2 or points.shape[1] not in (2, 3) or not len(points):
        raise ValueError(
            "give one point as (x, y) or one row of (x, y[, z]) per point, "
            f"not an array of shape {np.shape(coordinates)}"
        )
    return points[:, :2].copy()


def _identify(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or crs.name
