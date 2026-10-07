"""Azimuthal equidistant: the offsets as coordinates of a projection at the wellhead.

An azimuthal equidistant projection centred on the wellhead keeps every
distance and azimuth *from the wellhead* true. The minimum curvature offsets
are exactly that, ground distances east and north of the wellhead measured
against true north, so they are read as its easting and northing and converted
to the trajectory CRS. The projection is built on the CRS's own datum, so the
conversion is a change of projection only.

This is the default. It carries the scale factor and convergence of the
target projection implicitly and exactly, at every station rather than only
at the wellhead, and needs nothing but PROJ. Depth is ignored: the offsets
are placed on the ellipsoid, as every conventional method places them.
"""

from __future__ import annotations

from pyproj.crs import CoordinateOperation, ProjectedCRS

from geodetic_engine.geodesy import CoordinateReferenceSystem, Transformation
from geodetic_engine.welltrajectory.methods.base import (
    FloatArray,
    LocalFrame,
    Placement,
)


def place(offsets: FloatArray, frame: LocalFrame) -> Placement:
    """Read the offsets as a wellhead-centred azimuthal equidistant projection."""
    local = CoordinateReferenceSystem.from_user_input(local_crs(frame))
    conversion = Transformation(local, frame.horizontal_crs)
    xy = conversion.transform(offsets[:, :2]).coordinates.to_numpy()
    return Placement(
        xy=xy,
        operations=(
            f"read offsets as {local.name}",
            f"converted from {local.name} to {frame.horizontal_crs.name}",
        ),
        local_crs=local,
    )


def local_crs(frame: LocalFrame) -> ProjectedCRS:
    """The azimuthal equidistant projection centred on the wellhead."""
    conversion = CoordinateOperation.from_json_dict(
        {
            "type": "Conversion",
            "name": "Azimuthal Equidistant at the wellhead",
            "method": {
                "name": "Azimuthal Equidistant",
                "id": {"authority": "EPSG", "code": 1125},
            },
            "parameters": [
                _parameter("Latitude of natural origin", frame.latitude, 8801),
                _parameter("Longitude of natural origin", frame.longitude, 8802),
                _parameter("False easting", 0.0, 8806, "metre"),
                _parameter("False northing", 0.0, 8807, "metre"),
            ],
        }
    )
    return ProjectedCRS(
        conversion=conversion,
        name=(
            f"{frame.geographic_crs.name} / Azimuthal Equidistant "
            f"Lat={frame.latitude:.8f} Lon={frame.longitude:.8f}"
        ),
        geodetic_crs=frame.geographic_crs.crs,
    )


def _parameter(
    name: str, value: float, code: int, unit: str = "degree"
) -> dict[str, object]:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "id": {"authority": "EPSG", "code": code},
    }
