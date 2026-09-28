"""East-north-up: the offsets as a local tangent plane at the wellhead.

The offsets are taken as coordinates in the topocentric frame at the wellhead:
east and north in the plane tangent to the ellipsoid there, up along its
normal, so a station ``TVD`` below the wellhead is at ``up = -TVD``. PROJ turns
that frame into geocentric coordinates and those into latitude and longitude,
all on the CRS's own ellipsoid, and the result is converted to the CRS.

Unlike the projection methods this keeps the frame flat as it leaves the
wellhead, so up stays the wellhead's up: a station at depth ``T`` and reach
``d`` lands ``d T / R`` further out than on the projection. The wellhead's
elevation is used as its ellipsoidal height, which it is not; an error ``dh``
in it moves a station by ``d dh / R``, 3 cm for 40 m at 5 km. The reported
elevation stays ``wellhead z - TVD`` like every other method.
"""

from __future__ import annotations

from pyproj import Transformer

from geodetic_engine.welltrajectory.methods.base import (
    FloatArray,
    LocalFrame,
    Placement,
)


def place(offsets: FloatArray, frame: LocalFrame) -> Placement:
    """Read the offsets in the wellhead's topocentric frame."""
    semi_major, semi_minor = frame.semi_axes
    ellipsoid = f"+a={semi_major!r} +b={semi_minor!r}"
    pipeline = Transformer.from_pipeline(
        "+proj=pipeline "
        f"+step +inv +proj=topocentric +lat_0={frame.latitude!r} "
        f"+lon_0={frame.longitude!r} +h_0={frame.height!r} {ellipsoid} "
        f"+step +inv +proj=cart {ellipsoid}"
    )
    longitude, latitude, _ = pipeline.transform(
        offsets[:, 0], offsets[:, 1], -offsets[:, 2], errcheck=True
    )
    return Placement(
        xy=frame.to_crs(longitude, latitude),
        operations=(
            "read offsets in the topocentric frame at the wellhead, on "
            f"{frame.geographic_crs.name}'s ellipsoid, through geocentric "
            "coordinates",
            f"converted from {frame.geographic_crs.name} to "
            f"{frame.horizontal_crs.name}",
        ),
    )
