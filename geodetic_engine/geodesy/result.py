"""The structured outcome of a transformation, with its provenance.

A transformation returns this rather than bare numbers, so that a result can
still answer, long after the call, which CRSs it went between, which EPSG
operation produced it, which grids that consumed, and at which epoch. Once the
numbers are separated from those facts they cannot be checked, only trusted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from geodetic_engine.geodesy.crs import CoordinateReferenceSystem
from geodetic_engine.geodesy.operation import AppliedOperation, GridUsage


class Coordinates(tuple[tuple[float, ...], ...]):
    """Transformed coordinate values, one tuple per point.

    A plain tuple of tuples in every way that matters for existing code:
    indexing (``coordinates[0]``), iteration, ``len()``, and equality against
    a plain tuple all behave exactly as they would for the tuple it wraps.
    The only addition is knowing the target CRS well enough to export itself
    correctly -- in particular, to name a pandas DataFrame's columns.

    Example:
        >>> coordinates[0]
        (10.7522, 59.9139)
        >>> coordinates == ((10.7522, 59.9139),)
        True
    """

    _target_crs: CoordinateReferenceSystem

    def __new__(
        cls,
        rows: Iterable[Iterable[float]],
        target_crs: CoordinateReferenceSystem,
    ) -> Coordinates:
        self = super().__new__(cls, (tuple(row) for row in rows))
        self._target_crs = target_crs
        return self

    def to_list(self) -> list[list[float]]:
        """Coordinates as plain nested Python lists, one list per point.

        No dependency beyond the standard library; prefer this over the
        tuples themselves only when a caller specifically needs lists, for
        example to hand to a JSON encoder that does not accept tuples.

        Returns:
            One list of values per point, in the same order, units and axis
            count as the wrapped tuples.

        Example:
            >>> coordinates.to_list()
            [[10.7522, 59.9139]]
        """
        return [list(row) for row in self]

    def to_numpy(self) -> np.ndarray:
        """Coordinates as a 2D NumPy array, one row per point.

        Returns:
            A ``float64`` array of shape ``(n_points, n_axes)``.

        Example:
            >>> coordinates.to_numpy()  # doctest: +SKIP
            array([[10.7522, 59.9139]])
        """
        return np.array(self, dtype=np.float64)

    def to_dataframe(self) -> pd.DataFrame:
        """Coordinates as a pandas DataFrame, one row per point.

        Columns are named after the target CRS's declared axes, for example
        ``["E", "N"]`` for a projected target. A row carrying one value more
        than the target CRS declares -- a height passed through unchanged
        alongside a 2D horizontal target -- gets one extra column, named
        ``"h"`` for a geographic target or ``"Z"`` for a Cartesian one
        (projected, geocentric, engineering).

        Returns:
            A DataFrame with one row per point and one column per value.

        Example:
            >>> coordinates.to_dataframe()  # doctest: +SKIP
                    Lat      Lon
            0  10.7522  59.9139
        """
        axes = self._target_crs.axis_abbreviations
        width = len(self[0]) if self else len(axes)
        columns = list(axes[:width])
        if width > len(columns):
            # _require_width allows at most one value beyond the declared
            # axes, so there is never more than one such column to name.
            columns.append("h" if self._target_crs.crs.is_geographic else "Z")
        return pd.DataFrame(self, columns=columns)


@dataclass(frozen=True, slots=True)
class TransformationResult:
    """Transformed coordinates together with everything that produced them.

    Attributes:
        coordinates: One tuple per point, holding that point's values in ``xy``
            order in the units of the target CRS's axes. There is one value per
            axis the target CRS declares, so a vertical target yields one value
            per point even though PROJ computes three. If the input carried one
            value more than the source CRS declares -- a height alongside a 2D
            horizontal CRS -- that value is carried through unchanged as one
            extra trailing component, so :attr:`coordinates` can then hold one
            more value per point than :attr:`target_axes` lists. Behaves as a
            plain tuple of tuples (indexing, iteration, equality), with
            :meth:`Coordinates.to_list`, :meth:`~Coordinates.to_numpy` and
            :meth:`~Coordinates.to_dataframe` for exporting it in a specific
            format.
        source_crs: CRS the input was expressed in.
        target_crs: CRS the output is expressed in.
        operation: Which coordinate operation was applied, and how it was
            arrived at.
        grids: Grid files the operation depended on.
        coordinate_epoch: Decimal year supplied with the input, if any.
        coordinate_order: Order the values in :attr:`coordinates` are in. Always
            ``"xy"``; present so that a caller reading
            :attr:`target_axes` as ``("Lat", "Lon")`` cannot mistake the
            declared axis order for the value order.
        pipeline: The PROJ pipeline definition that was executed.

    Example:
        >>> result.target_axes
        ('Lat', 'Lon')
        >>> result.coordinate_order
        'xy'
        >>> result.coordinates[0]
        (10.7522, 59.9139)

        The axes are declared latitude first, the values are longitude first.
    """

    coordinates: Coordinates
    source_crs: CoordinateReferenceSystem
    target_crs: CoordinateReferenceSystem
    operation: AppliedOperation
    grids: tuple[GridUsage, ...]
    coordinate_epoch: float | None
    coordinate_order: str = "xy"
    pipeline: str | None = None

    @property
    def count(self) -> int:
        """How many points the result holds."""
        return len(self.coordinates)

    @property
    def source_axes(self) -> tuple[str, ...]:
        """Source CRS axis abbreviations, in EPSG-declared order."""
        return self.source_crs.axis_abbreviations

    @property
    def target_axes(self) -> tuple[str, ...]:
        """Target CRS axis abbreviations, in EPSG-declared order."""
        return self.target_crs.axis_abbreviations

    @property
    def source_units(self) -> tuple[str, ...]:
        """Source CRS axis units, in EPSG-declared order."""
        return self.source_crs.axis_units

    @property
    def target_units(self) -> tuple[str, ...]:
        """Target CRS axis units, in EPSG-declared order."""
        return self.target_crs.axis_units

    @property
    def missing_grids(self) -> tuple[str, ...]:
        """Names of grids the operation needs that are not installed.

        Always empty on a returned result, since a missing grid raises. Kept so
        that a caller logging a result does not have to special-case it.
        """
        return tuple(grid.name for grid in self.grids if not grid.available)

    def to_json_dict(self) -> dict[str, Any]:
        """Render the result as plain data, for logging or serialisation.

        Returns:
            A dict carrying the coordinates and every provenance field.

        Example:
            >>> result.to_json_dict()["coordinates"]  # doctest: +SKIP
            [[10.7522, 59.9139]]
        """
        return {
            "coordinates": [list(row) for row in self.coordinates],
            "coordinate_order": self.coordinate_order,
            "coordinate_epoch": self.coordinate_epoch,
            "source_crs": self.source_crs.authority_code or self.source_crs.name,
            "target_crs": self.target_crs.authority_code or self.target_crs.name,
            "source_axes": list(self.source_axes),
            "source_units": list(self.source_units),
            "target_axes": list(self.target_axes),
            "target_units": list(self.target_units),
            "operation": {
                "requested": self.operation.requested,
                "applied": self.operation.authority_code,
                "name": self.operation.name,
                "method": self.operation.method_name,
                "accuracy_m": self.operation.accuracy,
                "route": str(self.operation.route),
                "steps": list(self.operation.steps),
            },
            "grids": [
                {
                    "name": grid.name,
                    "available": grid.available,
                    "package": grid.package_name,
                    "url": grid.url,
                }
                for grid in self.grids
            ],
            "pipeline": self.pipeline,
        }

    def to_json(self, *, pretty: bool = True) -> str:
        """Serialise the result as JSON.

        Args:
            pretty: Whether to indent the output over several lines. True by
                default, since this is meant for a human to read; pass False
                for a compact form to log or send over the wire.

        Returns:
            The same fields as :meth:`to_json_dict`, as a JSON string.

        Example:
            >>> print(result.to_json(pretty=False))  # doctest: +SKIP
            {"coordinates": [[10.7522, 59.9139]], ...}
        """
        return json.dumps(self.to_json_dict(), indent=2 if pretty else None)
