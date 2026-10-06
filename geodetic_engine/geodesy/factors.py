"""Projection factors: grid convergence and point scale factor, from PROJ.

A map projection distorts. At any one point that distortion is summarised by
how far grid north is turned from true north, the **grid convergence**, and by
how much a short distance is stretched, the **point scale factor**. Surveys
need both: an azimuth measured against true north has to be turned onto the
grid, and a ground distance has to be scaled onto it.

Convention, used throughout this package:

* ``grid_convergence`` (gamma) is the angle from true north to grid north,
  positive clockwise. For a conformal projection,
  ``grid azimuth = true azimuth - gamma``. The azimuth helpers refuse angular
  distortion, where this simple conversion would give the wrong bearing.
* ``scale_factor`` (k) is grid distance divided by ellipsoidal distance along
  the parallel. For a conformal projection it equals the scale in every
  direction, and it is the value surveying calls the scale factor.

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
    "grid_convergence is the clockwise angle from true north to grid north "
    "for a conformal projection; grid azimuth = true azimuth - grid_convergence. "
    "scale_factor is the parallel scale k; grid distance = k * ellipsoidal "
    "distance along the parallel, or in every direction for conformal projections."
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
        coordinates_crs: The CRS :attr:`coordinates` are in: :attr:`horizontal_crs`,
            or :attr:`geographic_crs` when they were given as longitude and
            latitude.
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
    coordinates_crs: CoordinateReferenceSystem
    longitude: FloatArray
    latitude: FloatArray
    grid_convergence: FloatArray
    scale_factor: FloatArray
    meridional_scale: FloatArray
    areal_scale: FloatArray
    angular_distortion: FloatArray

    def to_true_azimuth(self, grid_azimuth: ArrayLike) -> FloatArray:
        """Grid azimuths plus convergence, in degrees in ``[0, 360)``.

        Raises:
            UnsupportedCRSError: If the projection does not preserve angles.
            ValueError: If an azimuth is not finite.
        """
        _require_conformal(self)
        return np.mod(_azimuths(grid_azimuth) + self.grid_convergence, 360.0)

    def to_grid_azimuth(self, true_azimuth: ArrayLike) -> FloatArray:
        """True azimuths minus convergence, in degrees in ``[0, 360)``.

        Raises:
            UnsupportedCRSError: If the projection does not preserve angles.
            ValueError: If an azimuth is not finite.
        """
        _require_conformal(self)
        return np.mod(_azimuths(true_azimuth) - self.grid_convergence, 360.0)

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
            "coordinates_crs": _identify(self.coordinates_crs),
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
        TransformationFailedError: If a horizontal coordinate is not finite,
            a geographic coordinate is out of range, or PROJ cannot evaluate
            finite factors. Projected factors are undefined at the poles.

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

    angular_units = np.array(
        [
            geographic_crs.axes[index].unit_conversion_factor
            for index in geographic_crs.value_axis_order[:2]
        ]
    )
    if np.any(
        np.abs(lonlat * angular_units) > np.array([2 * np.pi, np.pi / 2]) + 1e-12
    ):
        raise TransformationFailedError(
            f"longitude or latitude is outside the range of {geographic_crs.name}"
        )

    count = len(points)
    if horizontal.is_projected:
        values = _proj_factors(horizontal, geographic_crs, lonlat, resolved)
    else:
        values = {name: np.ones(count) for name in _PROJ_NAMES}
        values["grid_convergence"] = np.zeros(count)
        values["angular_distortion"] = np.zeros(count)

    horizontal_crs = CoordinateReferenceSystem(horizontal, resolved.definition)
    return ProjectionFactors(
        crs=resolved,
        horizontal_crs=horizontal_crs,
        geographic_crs=geographic_crs,
        projected=bool(horizontal.is_projected),
        coordinates=points,
        coordinates_crs=geographic_crs if geographic else horizontal_crs,
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
    units = np.array(
        [
            geographic_crs.axes[index].unit_conversion_factor
            for index in geographic_crs.value_axis_order[:2]
        ]
    )
    position = lonlat * units
    if np.any(np.abs(position[:, 1]) >= np.pi / 2):
        raise TransformationFailedError("projection factors are undefined at the poles")
    try:
        factors = Proj(projected).get_factors(
            lonlat[:, 0] * np.degrees(units[0]),
            lonlat[:, 1] * np.degrees(units[1]),
            errcheck=True,
        )
    except ProjError as error:
        raise TransformationFailedError(
            f"PROJ could not evaluate projection factors in {_identify(described)}: "
            f"{error}"
        ) from error
    values = {
        name: np.asarray(getattr(factors, proj_name), dtype=np.float64).reshape(-1)
        for name, proj_name in _PROJ_NAMES.items()
    }
    operation = projected.coordinate_operation
    if operation is not None and operation.method_code == "1024":
        _web_mercator_scales(values, geographic_crs, position[:, 1])
    if not all(np.all(np.isfinite(value)) for value in values.values()):
        raise TransformationFailedError(
            f"PROJ produced non-finite projection factors in {_identify(described)}"
        )
    return values


def _web_mercator_scales(
    values: dict[str, FloatArray],
    geographic_crs: CoordinateReferenceSystem,
    latitude: FloatArray,
) -> None:
    """Correct PROJ's spherical Web Mercator scales to the base ellipsoid."""
    ellipsoid = geographic_crs.crs.ellipsoid
    if ellipsoid is None:
        raise UnsupportedCRSError(f"{geographic_crs.name} has no ellipsoid")
    eccentricity = 1 - (ellipsoid.semi_minor_metre / ellipsoid.semi_major_metre) ** 2
    weight = np.sqrt(1 - eccentricity * np.sin(latitude) ** 2)
    parallel = values["scale_factor"] * weight
    meridian = values["meridional_scale"] * weight**3 / (1 - eccentricity)
    values["scale_factor"] = parallel
    values["meridional_scale"] = meridian
    values["areal_scale"] = parallel * meridian
    values["angular_distortion"] = np.degrees(
        2
        * np.arcsin(np.clip(np.abs(meridian - parallel) / (meridian + parallel), 0, 1))
    )


def _require_conformal(factors: ProjectionFactors) -> None:
    if np.any(factors.angular_distortion > 1e-5):
        raise UnsupportedCRSError(
            f"{factors.horizontal_crs.name} does not preserve angles here; "
            "scale factor and grid convergence need a conformal projection"
        )


def _grid_axes(crs: CoordinateReferenceSystem) -> FloatArray:
    """Map native horizontal axis directions onto grid east and north."""
    axes = [crs.axes[index] for index in crs.value_axis_order[:2]]
    east = next(
        (
            index
            for index, axis in enumerate(axes)
            if axis.direction in ("east", "west")
        ),
        None,
    )
    north = next(
        (
            index
            for index, axis in enumerate(axes)
            if axis.direction in ("north", "south")
        ),
        None,
    )
    if east is not None and north is not None and east != north:
        result = np.zeros((2, 2))
        result[0, east] = 1 if axes[east].direction == "east" else -1
        result[1, north] = 1 if axes[north].direction == "north" else -1
        return result
    if [axis.abbrev.upper() for axis in axes] == ["E", "N"]:
        return np.eye(2)
    raise UnsupportedCRSError(
        f"{crs.name} has no identifiable grid east and north axes"
    )


def _azimuths(azimuth: ArrayLike) -> FloatArray:
    angles = np.asarray(azimuth, dtype=np.float64)
    if not np.all(np.isfinite(angles)):
        raise ValueError("azimuths must be finite")
    return angles


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
    if not np.all(np.isfinite(points[:, :2])):
        raise TransformationFailedError("horizontal coordinates must be finite")
    return points[:, :2].copy()


def _identify(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or crs.name
