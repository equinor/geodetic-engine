"""Cut geometries at the antimeridian, or carry their longitudes on past it.

Private to the package, shared by :mod:`geodetic_engine.geodesy.geometry` and
:mod:`geodetic_engine.geojson`.

PROJ gives longitudes within half a turn of the prime meridian. Where the true
path of a segment crosses the antimeridian, its two ends come out at the two
ends of that range, and the segment between them, read as straight in
longitude and latitude as Simple Features, RFC 7946 and Esri read it, goes the
long way round instead. :func:`crossings` tells it from a segment that does go
the long way round by where the middle of its source segment goes: between
its ends, or past the antimeridian.

:func:`cut` then cuts a part that crosses into parts of a multipart geometry,
as RFC 7946 (3.1.9) and ``ogr2ogr -wrapdateline`` do, or unwraps it, carrying
its longitudes on past the antimeridian so that it stays one part. A ring that
winds round a pole, as one round it in a polar projection does, is closed
along the antimeridian through the pole. No position is moved by cutting: a
part gains a vertex on the antimeridian, where a straight line between the
positions either side crosses it, which densifying first keeps on the path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Final, Literal

import numpy as np
import shapely
from numpy.typing import NDArray
from shapely.errors import GEOSException
from shapely.geometry.base import BaseMultipartGeometry

type FloatArray = NDArray[np.float64]
type IndexArray = NDArray[np.intp]
type Antimeridian = Literal["split", "unwrap"]
type Line = tuple[FloatArray, IndexArray]

ANTIMERIDIANS: Final = ("split", "unwrap")


def crossings(
    lon: FloatArray,
    parts: IndexArray,
    half: float,
    middle: Callable[[IndexArray], FloatArray],
) -> NDArray[np.bool_]:
    """For each position, whether the segment to the next one crosses the antimeridian.

    Args:
        lon: The longitude of each position, within half a turn of the prime
            meridian.
        parts: The line or ring each position is on; a part's are consecutive.
        half: Half a turn, in the longitude's unit.
        middle: For the given positions, the longitude the middle of the
            source segment from each to the next goes to, NaN if unknown,
            which counts as crossing.
    """
    crossing = np.zeros(len(lon), dtype=np.bool_)
    if len(lon) < 2:
        return crossing
    candidates = np.flatnonzero(
        (parts[1:] == parts[:-1]) & (np.abs(np.diff(lon)) > half)
    )
    if not len(candidates):
        return crossing
    mid = middle(candidates)
    low = np.minimum(lon[candidates], lon[candidates + 1])
    high = np.maximum(lon[candidates], lon[candidates + 1])
    # The long way round passes between the ends; the short way, past them.
    crossing[candidates[~((mid >= low) & (mid <= high))]] = True
    return crossing


def turns(
    lon: FloatArray, parts: IndexArray, crossing: NDArray[np.bool_]
) -> IndexArray:
    """How many turns each position is carried past the antimeridian on its part.

    The first position of each part is carried none.
    """
    step = np.zeros(len(lon), dtype=np.intp)
    if len(lon) > 1:
        across = np.flatnonzero(crossing[:-1])
        # Eastwards over the antimeridian the longitude falls, and is carried on.
        step[across + 1] = np.where(lon[across + 1] < lon[across], 1, -1)
    total = np.cumsum(step)
    first = np.r_[True, parts[1:] != parts[:-1]]
    carried: IndexArray = total - total[np.flatnonzero(first)][np.cumsum(first) - 1]
    return carried


def cut(
    geometry: shapely.Geometry | None,
    lines: Iterator[Line],
    mode: Antimeridian,
    half: float,
    pole: float,
) -> shapely.Geometry | None:
    """``geometry`` cut at the antimeridian, or unwrapped there.

    Args:
        geometry: In a geographic CRS, longitude first, without M values.
        lines: For each of its lines and rings in order, empty ones left out:
            its positions, x, y and any z, and the turns from :func:`turns`.
        mode: ``"split"`` or ``"unwrap"``.
        half: Half a turn of longitude, in its unit.
        pole: The latitude of the poles, in its unit.
    """
    if geometry is None or shapely.is_empty(geometry):
        return geometry
    if isinstance(geometry, shapely.LineString):
        rows, turned = next(lines)
        if not turned.any():
            return geometry
        if mode == "unwrap":
            return type(geometry)(_unwrapped(rows, turned, half))
        pieces = [shapely.LineString(piece) for piece in _cut_line(rows, turned, half)]
        return pieces[0] if len(pieces) == 1 else shapely.MultiLineString(pieces)
    if isinstance(geometry, shapely.Polygon):
        rings = [next(lines) for _ in range(1 + len(geometry.interiors))]
        if not any(turned.any() for _, turned in rings):
            return geometry
        return _cut_polygon(rings, mode, half, pole)
    if isinstance(geometry, BaseMultipartGeometry) and not isinstance(
        geometry, shapely.MultiPoint
    ):
        parts = [cut(part, lines, mode, half, pole) for part in geometry.geoms]
        if isinstance(geometry, shapely.MultiLineString | shapely.MultiPolygon):
            flat = [
                piece
                for part in parts
                for piece in shapely.get_parts(part)
                if not piece.is_empty
            ]
            return type(geometry)(flat)
        return shapely.GeometryCollection(parts)
    return geometry


def _unwrapped(rows: FloatArray, turned: IndexArray, half: float) -> FloatArray:
    out = rows.copy()
    out[:, 0] = rows[:, 0] + 2 * half * turned
    return out


def _cut_line(rows: FloatArray, turned: IndexArray, half: float) -> list[FloatArray]:
    """The pieces of a line between the places it crosses the antimeridian."""
    pieces: list[FloatArray] = []
    current = [rows[0]]
    for index in range(1, len(rows)):
        direction = int(turned[index] - turned[index - 1])
        if direction:
            before, after = rows[index - 1], rows[index].copy()
            after[0] += 2 * half * direction
            edge = half * direction
            share = (edge - before[0]) / (after[0] - before[0])
            exit_ = before + share * (after - before)
            exit_[0] = edge
            current.append(exit_)
            pieces.append(np.array(current))
            entry = exit_.copy()
            entry[0] = -edge
            current = [entry]
        current.append(rows[index])
    pieces.append(np.array(current))
    return [piece for piece in pieces if len(piece) > 1]


def _cut_polygon(
    rings: list[Line], mode: Antimeridian, half: float, pole: float
) -> shapely.Geometry:
    """A polygon cut at the antimeridian, closed through any pole it winds round."""
    full = 2 * half
    shell_rows, shell_turns = rings[0]
    unwrapped = _ring(shell_rows, shell_turns, 0, full, pole)
    ccw = bool(shapely.is_ccw(shapely.LinearRing(unwrapped[:, :2])))
    shell = shapely.Polygon(unwrapped)
    shifts = [0] + [_shift(rows, turned, shell, full) for rows, turned in rings[1:]]
    if mode == "unwrap":
        sheets = [0]
    else:
        low, high = float(unwrapped[:, 0].min()), float(unwrapped[:, 0].max())
        sheets = list(
            range(
                int(np.floor((low + half) / full)),
                int(np.floor((high + half) / full)) + 1,
            )
        )
    window = shapely.box(-half, -2 * pole, half, 2 * pole)
    polygons: list[shapely.Polygon] = []
    for sheet in sheets:
        shell_k = shapely.Polygon(_ring(shell_rows, shell_turns, sheet, full, pole))
        holes = [
            shapely.Polygon(_ring(rows, turned + shift, sheet, full, pole))
            for (rows, turned), shift in zip(rings[1:], shifts[1:], strict=True)
        ]
        area = _difference(shell_k, holes)
        if mode == "split":
            area = _overlay(shapely.intersection, area, window)
        polygons.extend(
            part
            for part in shapely.get_parts(area)
            if isinstance(part, shapely.Polygon)
        )
    if not polygons:
        return shapely.Polygon()
    if len(polygons) > 1:
        # A ring round a pole spans a whole turn: its pieces from the sheets
        # either side meet along the meridian it starts on, and are one part.
        merged = shapely.union_all(np.array(polygons, dtype=object))
        polygons = [
            part
            for part in shapely.get_parts(merged)
            if isinstance(part, shapely.Polygon)
        ]
    oriented = shapely.orient_polygons(
        np.array(polygons, dtype=object), exterior_cw=not ccw
    )
    return oriented[0] if len(oriented) == 1 else shapely.MultiPolygon(list(oriented))


def _ring(
    rows: FloatArray, turned: IndexArray, sheet: int, full: float, pole: float
) -> FloatArray:
    """A ring's positions on a sheet of longitude, through a pole it winds round.

    A position on the sheet keeps its value, bit for bit; the others are
    carried the turns they are off it.
    """
    out = rows.copy()
    off = turned != sheet
    out[off, 0] = rows[off, 0] + full * (turned[off] - sheet)
    winding = int(turned[-1] - turned[0])
    if winding:
        latitude = pole if rows[:, 1].max() + rows[:, 1].min() > 0 else -pole
        last, first = out[-1].copy(), out[0].copy()
        last[1] = first[1] = latitude
        out = np.vstack([out, last, first, out[0]])
    return out


def _shift(
    rows: FloatArray, turned: IndexArray, shell: shapely.Polygon, full: float
) -> int:
    """The turns that put a hole inside its shell."""
    lon, lat = float(rows[0, 0]), float(rows[0, 1])
    for shift in (0, 1, -1, 2, -2):
        if shell.covers(shapely.Point(lon + full * shift, lat)):
            return shift
    return 0


def _difference(
    shell: shapely.Polygon, holes: list[shapely.Polygon]
) -> shapely.Geometry:
    area: shapely.Geometry = shell
    for hole in holes:
        area = _overlay(shapely.difference, area, hole)
    return area


def _overlay(
    operation: Callable[[shapely.Geometry, shapely.Geometry], shapely.Geometry],
    first: shapely.Geometry,
    second: shapely.Geometry,
) -> shapely.Geometry:
    try:
        return operation(first, second)
    except GEOSException:
        return operation(shapely.make_valid(first), second)
