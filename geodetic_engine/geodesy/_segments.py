"""What a segment between two positions is, and vertices along it.

Private to the package, shared by :mod:`geodetic_engine.geodesy.geometry` and
:mod:`geodetic_engine.geojson`.

A segment between two positions is ``"straight"`` in the CRS they are in, as
Simple Features, RFC 7946 and Esri read it, or a ``"geodesic"``: the shortest
line on the ellipsoid of that CRS, as boundaries on land and at sea are
commonly defined. Neither is straight in another CRS, so vertices are put
along it before transforming, so that no segment is longer than a length
asked for: in the CRS's own units for a straight segment, and in metres along
the ellipsoid for a geodesic.

Positions are rows in value order, ``xy`` then any height, and each line or
ring is a run of consecutive rows with the same part number. The rows given
come back unchanged, bit for bit, with the vertices inserted between them.
"""

from __future__ import annotations

import math
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray
from pyproj import CRS, Geod, Transformer

from geodetic_engine.geodesy.crs import CoordinateReferenceSystem

type FloatArray = NDArray[np.float64]
type IndexArray = NDArray[np.intp]
type Segments = Literal["straight", "geodesic"]

SEGMENTS: Final = ("straight", "geodesic")


def densify_rows(
    rows: FloatArray,
    parts: IndexArray,
    crs: CoordinateReferenceSystem,
    length: float,
    segments: Segments,
) -> tuple[FloatArray, IndexArray, IndexArray]:
    """``rows`` with vertices between them, no segment longer than ``length``.

    Args:
        rows: Positions, one row each, in value order.
        parts: The line or ring each row is on; a part's rows are consecutive.
        crs: The CRS the positions are in.
        length: The longest segment: in ``crs`` units for straight segments,
            in metres along the ellipsoid for geodesics.
        segments: ``"straight"`` or ``"geodesic"``.

    Returns:
        The positions with those inserted, the part each is on, and for each
        the row of ``rows`` it is or follows.

    Raises:
        ValueError: If ``length`` is not a positive number, ``segments`` is
            neither of the above, or a geodesic is asked of a CRS that is not
            on an ellipsoid with longitude and latitude or a map projection.
    """
    require_length(length)
    if segments not in SEGMENTS:
        raise ValueError(f"segments must be one of {SEGMENTS}, not {segments!r}")
    count = len(rows)
    if count < 2:
        return rows.copy(), parts.copy(), np.arange(count, dtype=np.intp)
    same = parts[1:] == parts[:-1]
    if segments == "straight":
        lengths = np.hypot(*(rows[1:, :2] - rows[:-1, :2]).T)
        pieces = _pieces(lengths, length, same)
        start, fraction = _inserted(pieces)
        inserted = rows[start] + fraction[:, None] * (rows[start + 1] - rows[start])
    else:
        geodesic = _Geodesic(crs)
        lon, lat = geodesic.degrees(rows[:, 0], rows[:, 1])
        azimuth, _, lengths = geodesic.geod.inv(lon[:-1], lat[:-1], lon[1:], lat[1:])
        pieces = _pieces(np.asarray(lengths), length, same)
        start, fraction = _inserted(pieces)
        inserted = np.empty((len(start), rows.shape[1]))
        if len(start):
            along = np.asarray(lengths)[start] * fraction
            ins_lon, ins_lat, _ = geodesic.geod.fwd(
                lon[start], lat[start], np.asarray(azimuth)[start], along
            )
            # Near the vertex before, as the geodesic runs: 181, not -179.
            ins_lon = (
                lon[start] + (np.asarray(ins_lon) - lon[start] + 180.0) % 360.0 - 180.0
            )
            inserted[:, 0], inserted[:, 1] = geodesic.values(
                ins_lon, np.asarray(ins_lat)
            )
        if rows.shape[1] > 2:
            low, high = rows[start, 2:], rows[start + 1, 2:]
            inserted[:, 2:] = low + fraction[:, None] * (high - low)
    return _merged(rows, parts, pieces, start, inserted)


def require_length(length: object) -> None:
    """Refuse a segment length that is not a positive, finite number."""
    if (
        isinstance(length, bool)
        or not isinstance(length, int | float)
        or not math.isfinite(length)
        or length <= 0
    ):
        raise ValueError(f"densify is a positive segment length, not {length!r}")


def _pieces(lengths: FloatArray, length: float, same: NDArray[np.bool_]) -> IndexArray:
    """The pieces each segment is cut into: one for no vertex inserted."""
    pieces = np.ones(len(lengths), dtype=np.intp)
    long = same & (lengths > length)
    pieces[long] = np.ceil(lengths[long] / length).astype(np.intp)
    return pieces


def _inserted(pieces: IndexArray) -> tuple[IndexArray, FloatArray]:
    """For each vertex inserted, the row before it, and how far along it is."""
    extra = pieces - 1
    start = np.repeat(np.arange(len(pieces), dtype=np.intp), extra)
    step = np.arange(len(start)) - np.repeat(np.cumsum(extra) - extra, extra) + 1
    return start, step / pieces[start]


def _merged(
    rows: FloatArray,
    parts: IndexArray,
    pieces: IndexArray,
    start: IndexArray,
    inserted: FloatArray,
) -> tuple[FloatArray, IndexArray, IndexArray]:
    extra = np.append(pieces - 1, 0)
    at = np.arange(len(rows)) + np.concatenate([[0], np.cumsum(extra[:-1])])
    out = np.empty((len(rows) + len(start), rows.shape[1]))
    out[at] = rows
    origins = np.empty(len(out), dtype=np.intp)
    origins[at] = np.arange(len(rows))
    if len(start):
        step = np.arange(len(start)) - np.repeat(np.cumsum(extra) - extra, extra) + 1
        where = at[start] + step
        out[where] = inserted
        origins[where] = start
    return out, parts[origins], origins


class _Geodesic:
    """A CRS's ellipsoid, and its positions in degrees of longitude and latitude."""

    def __init__(self, crs: CoordinateReferenceSystem) -> None:
        horizontal = _horizontal(crs.crs)
        geod = horizontal.get_geod() if horizontal is not None else None
        if (
            horizontal is None
            or geod is None
            or not (horizontal.is_geographic or horizontal.is_projected)
        ):
            raise ValueError(
                f"geodesics are drawn on an ellipsoid, and {_label(crs)} gives "
                "neither longitude and latitude nor a map projection of them"
            )
        self.geod: Geod = geod
        self._factors: tuple[float, float] | None = None
        self._inverse: Transformer | None = None
        self._forward: Transformer | None = None
        if horizontal.is_geographic:
            lon_axis, lat_axis = (crs.axes[index] for index in crs.value_axis_order[:2])
            self._factors = (
                math.degrees(lon_axis.unit_conversion_factor),
                math.degrees(lat_axis.unit_conversion_factor),
            )
        else:
            base = horizontal.geodetic_crs
            self._inverse = Transformer.from_crs(horizontal, base, always_xy=True)
            self._forward = Transformer.from_crs(base, horizontal, always_xy=True)

    def degrees(self, x: FloatArray, y: FloatArray) -> tuple[FloatArray, FloatArray]:
        if self._factors is not None:
            return x * self._factors[0], y * self._factors[1]
        assert self._inverse is not None
        lon, lat = self._inverse.transform(x, y)
        return np.asarray(lon), np.asarray(lat)

    def values(self, lon: FloatArray, lat: FloatArray) -> tuple[FloatArray, FloatArray]:
        if self._factors is not None:
            return lon / self._factors[0], lat / self._factors[1]
        assert self._forward is not None
        x, y = (np.asarray(value) for value in self._forward.transform(lon, lat))
        if not (np.isfinite(x).all() and np.isfinite(y).all()):
            raise ValueError(
                "a geodesic leaves the area the source CRS's map projection covers"
            )
        return x, y


def _horizontal(crs: CRS) -> CRS | None:
    """The CRS of a position's longitude and latitude, or easting and northing."""
    current: CRS | None = crs.source_crs if crs.is_bound else crs
    if current is not None and current.is_compound:
        current = current.sub_crs_list[0]
    if current is not None and current.is_bound:
        current = current.source_crs
    if current is None or not (current.is_geographic or current.is_projected):
        return None
    return current


def _label(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or repr(crs.name)
