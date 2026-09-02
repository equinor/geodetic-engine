"""Coordinate values, the CRS they are expressed in, and their epoch.

Coordinate **values** are always ordered ``xy``: longitude before latitude for
geographic CRSs, easting before northing for projected ones, then height or
depth, matching PROJ's ``always_xy`` convention. This is not the order the EPSG
dataset declares for every CRS; see :mod:`geodetic_engine.geodesy.crs` for the
distinction and for how to read a CRS's declared axis order.

Values are stored one list per axis rather than one list per point, because
that is the shape PROJ wants: a whole batch crosses into PROJ in a single call
instead of once per point. A single point is a batch of one, so there is no
separate code path for it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from geodetic_engine.geodesy.crs import CoordinateReferenceSystem

# PROJ transforms at most x, y, z: a fourth spatial component has no meaning to
# it, so one extra value beyond what a CRS declares is tolerated (a height
# alongside a 2D horizontal CRS, carried through unchanged) but no more than
# that.
_MAX_COMPONENTS = 3


def _allowed_widths(dimension: int) -> set[int]:
    """How many values a point may carry: the CRS's axes, plus one, if room."""
    widths = {dimension}
    if dimension < _MAX_COMPONENTS:
        widths.add(dimension + 1)
    return widths


@dataclass(frozen=True, slots=True)
class PositionSet:
    """A batch of coordinates in one CRS, at one optional epoch.

    Attributes:
        crs: The CRS the coordinates are expressed in.
        columns: One tuple of values per axis, in ``xy`` value order, each of
            the same length. Units are those of the corresponding axis in
            :attr:`~geodetic_engine.geodesy.crs.CoordinateReferenceSystem.axes`.
            May hold one column more than :attr:`crs` declares axes: a height
            alongside a 2D horizontal CRS, which PROJ carries through
            unchanged rather than consuming, matching how
            :meth:`pyproj.Transformer.transform` accepts an optional ``zz``
            regardless of what the CRS pair declares.
        coordinate_epoch: Decimal year the coordinates were observed at, for
            example ``2010.0``. Required when the CRS is dynamic, meaningless
            otherwise.

    Example:
        >>> crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
        >>> points = PositionSet.from_rows(crs, [(5.32, 60.39), (10.75, 59.91)])
        >>> points.count
        2
        >>> points.columns[0]
        (5.32, 10.75)

        The first column is longitude even though ``EPSG:4326`` declares
        latitude first, because values are always in ``xy`` order.
    """

    crs: CoordinateReferenceSystem
    columns: tuple[tuple[float, ...], ...]
    coordinate_epoch: float | None = None

    def __post_init__(self) -> None:
        allowed = _allowed_widths(self.crs.dimension)
        if len(self.columns) not in allowed:
            raise ValueError(
                f"{self.crs!r} has {self.crs.dimension} axes but "
                f"{len(self.columns)} coordinate columns were given; "
                f"{sorted(allowed)} columns are accepted"
            )
        lengths = {len(column) for column in self.columns}
        if len(lengths) > 1:
            raise ValueError(
                f"coordinate columns have differing lengths: {sorted(lengths)}"
            )

    @classmethod
    def from_rows(
        cls,
        crs: Any,
        rows: Iterable[Iterable[float]],
        *,
        coordinate_epoch: float | None = None,
    ) -> PositionSet:
        """Build a set from one sequence of values per point.

        Args:
            crs: The CRS the coordinates are expressed in; anything
                :meth:`CoordinateReferenceSystem.from_user_input` accepts.
            rows: One iterable per point, each holding that point's values in
                ``xy`` order, in the CRS's axis units. A 2D numpy array of
                shape ``(n_points, n_axes)`` works, one row per point. Every
                row may carry one value beyond what the CRS declares -- a
                height alongside a 2D horizontal CRS -- which is carried
                through to the result unchanged rather than being consumed;
                see :class:`PositionSet`.
            coordinate_epoch: Decimal year, for example ``2010.0``.

        Returns:
            The batch.

        Raises:
            ValueError: If a row's number of values is not the CRS's declared
                dimension, or that plus one, or if rows disagree on how many
                values they carry.

        Example:
            >>> crs = CoordinateReferenceSystem.from_user_input("EPSG:4979")
            >>> PositionSet.from_rows(crs, [(5.32, 60.39, 112.4)]).count
            1
        """
        resolved = CoordinateReferenceSystem.from_user_input(crs)
        # Materialised up front: rows may be a one-shot iterable (or a numpy
        # array, which is not a Sequence), and the values are read once per
        # axis below, so the input must survive more than one pass.
        materialized = [tuple(float(value) for value in row) for row in rows]
        widths = {len(row) for row in materialized}
        if len(widths) > 1:
            raise ValueError(
                f"points have differing numbers of values: {sorted(widths)}"
            )
        allowed = _allowed_widths(resolved.dimension)
        width = widths.pop() if widths else resolved.dimension
        if width not in allowed:
            raise ValueError(
                f"points have {width} values each but {resolved!r} declares "
                f"{resolved.dimension} axes; {sorted(allowed)} values are "
                "accepted (the extra being a height PROJ carries through "
                "unchanged)"
            )
        columns = tuple(
            tuple(row[axis] for row in materialized) for axis in range(width)
        )
        return cls(crs=resolved, columns=columns, coordinate_epoch=coordinate_epoch)

    @property
    def count(self) -> int:
        """How many points the set holds."""
        return len(self.columns[0]) if self.columns else 0

    @property
    def rows(self) -> tuple[tuple[float, ...], ...]:
        """The coordinates as one tuple per point, in ``xy`` value order."""
        return tuple(zip(*self.columns, strict=True))


@dataclass(frozen=True, slots=True)
class Position:
    """A single coordinate, in one CRS, at one optional epoch.

    A convenience over :class:`PositionSet` for the one-point case. All
    behaviour lives in :class:`PositionSet`; this only wraps and unwraps.

    Attributes:
        crs: The CRS the coordinate is expressed in.
        coordinates: The point's values in ``xy`` order, in the CRS's axis
            units. Longitude before latitude for a geographic CRS.
        coordinate_epoch: Decimal year the coordinate was observed at.

    Example:
        >>> crs = CoordinateReferenceSystem.from_user_input("EPSG:4326")
        >>> Position(crs, (5.32, 60.39)).as_set().count
        1
    """

    crs: CoordinateReferenceSystem
    coordinates: tuple[float, ...]
    coordinate_epoch: float | None = None

    def as_set(self) -> PositionSet:
        """Express this point as a one-point :class:`PositionSet`."""
        return PositionSet.from_rows(
            self.crs, [self.coordinates], coordinate_epoch=self.coordinate_epoch
        )
