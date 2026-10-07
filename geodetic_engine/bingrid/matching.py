"""Find the stored bin grid a legacy seismic dataset is on.

An optional companion to the bin grid core, not re-exported by
:mod:`geodetic_engine.bingrid`: import it from here.

Legacy datasets often arrive with only their own four corners, and the SDU note
("How to detect if a dataset is on an already defined bin grid") asks that
they be assigned to a grid already in the store rather than stored as yet
another definition. Its algorithm, as implemented by :func:`match_bin_grid`:

1. Compute where each stored grid puts the dataset's corner numbers, and the
   largest distance from those points to the dataset's corner coordinates.
2. Keep the grids within half a bin: half the smaller "real" spacing between
   the dataset's loaded traces, which accounts for its node increments.
3. Prefer, in order: a grid in the dataset's own CRS, a grid stored at the
   dataset's node increments, and the smallest distance.

The note allows one exception to comparing grids in the dataset's own CRS:
NAD27 coordinates in US survey feet against a grid stored in metres
(EPSG:32065 against 26715, and 32066 against 26716). This module allows the
general form of it -- a stored grid in any projected CRS on the dataset's
datum, compared after an exact conversion -- and never compares across a
datum change, which would need a transformation that a match must not choose.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np

from geodetic_engine.bingrid.conversion import map_grid_crs, same_map_grid, unbound_crs
from geodetic_engine.bingrid.corners import BinGridCorner, BinGridCorners
from geodetic_engine.bingrid.p6 import P6Parameters
from geodetic_engine.bingrid.squaring import derive_p6
from geodetic_engine.geodesy import CoordinateReferenceSystem, Transformation

logger = logging.getLogger(__name__)

__all__ = ["BinGridMatch", "MatchResult", "StoredBinGrid", "match_bin_grid"]


@dataclass(frozen=True, slots=True)
class StoredBinGrid:
    """A grid in a store of technically assured bin grids.

    Attributes:
        key: Identifies the grid in its store, for example an OSDU record-id.
        parameters: The grid's P6 parameters.
        crs: The projected CRS the parameters are in. Give anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts; it is held as a
            :class:`~geodetic_engine.geodesy.CoordinateReferenceSystem` once
            constructed.

    Raises:
        geodetic_engine.bingrid.UnsupportedCRSError: On construction, if the
            CRS cannot carry a bin grid.
    """

    key: str
    parameters: P6Parameters
    crs: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "crs", map_grid_crs(self.crs, "stored grid's "))


@dataclass(frozen=True, slots=True)
class BinGridMatch:
    """A stored grid the dataset is on.

    Attributes:
        grid: The stored grid.
        distance: Largest distance between a corner of the dataset and where the
            grid puts that corner's numbers, in the dataset CRS's linear unit.
        same_crs: Whether the grid is on the dataset's own map grid: the same
            datum, projection and linear unit, whatever the axis order and
            whether or not either CRS is bound to WGS 84.
        same_increments: Whether the grid is stored at the dataset's increments.
    """

    grid: StoredBinGrid
    distance: float
    same_crs: bool
    same_increments: bool

    def to_json_dict(self) -> dict[str, Any]:
        """Render the match as plain data."""
        return {
            "key": self.grid.key,
            "crs": self.grid.crs.authority_code or self.grid.crs.name,
            "distance": self.distance,
            "same_crs": self.same_crs,
            "same_increments": self.same_increments,
            "parameters": self.grid.parameters.to_json_dict(),
        }


@dataclass(frozen=True, slots=True)
class MatchResult:
    """The stored grids a dataset is on, best first.

    Attributes:
        dataset: The dataset's corners.
        crs: The dataset's CRS.
        tolerance: Half the smaller real spacing between the dataset's loaded
            traces, in :attr:`linear_unit`: a grid matches if no corner is
            further than this from where the grid puts it.
        linear_unit: Unit of :attr:`crs`, of :attr:`tolerance` and of the
            distances.
        matches: The grids within :attr:`tolerance`, best first.
    """

    dataset: BinGridCorners
    crs: CoordinateReferenceSystem
    tolerance: float
    linear_unit: str
    matches: tuple[BinGridMatch, ...]

    @property
    def best(self) -> BinGridMatch | None:
        """The grid to assign to the dataset, or None to define a new one."""
        return self.matches[0] if self.matches else None

    def to_json_dict(self) -> dict[str, Any]:
        """Render the result as plain data."""
        return {
            "crs": self.crs.authority_code or self.crs.name,
            "dataset": self.dataset.to_json_dict(),
            "tolerance": self.tolerance,
            "linear_unit": self.linear_unit,
            "matches": [match.to_json_dict() for match in self.matches],
        }


def match_bin_grid(
    corners: BinGridCorners | Iterable[BinGridCorner | tuple[int, int, float, float]],
    crs: Any,
    grids: Iterable[StoredBinGrid],
    *,
    increment_i: int = 1,
    increment_j: int = 1,
) -> MatchResult:
    """Find the stored grids a dataset is on, by the SDU note's algorithm.

    Args:
        corners: The dataset's four corners, in any order, numbered as on the
            grid it was loaded from.
        crs: The dataset's projected CRS; preferably the one it was binned in.
        grids: The stored grids to search.
        increment_i: Inline increment at which the dataset's traces were loaded.
        increment_j: Crossline increment at which they were loaded.

    Returns:
        The matching grids, best first; :attr:`MatchResult.best` is the one to
        assign, or None when the dataset needs a grid of its own.

    Raises:
        InvalidCornersError: If the corners are not those of a bin grid, or
            their spans are not multiples of the increments.
        DegenerateBinGridError: If their coordinates cannot be.
        InvalidParameterError: If an increment is not a positive integer.
        geodetic_engine.bingrid.UnsupportedCRSError: If the dataset's CRS
            cannot carry a bin grid.

    Example:
        >>> from geodetic_engine.bingrid import Handedness, corners_from_p6
        >>> parameters = P6Parameters(
        ...     origin_i=1, origin_j=1,
        ...     origin_easting=423081.91, origin_northing=3227689.59,
        ...     bin_width_i=30.0, bin_width_j=25.0, bearing_j=2.48019694,
        ...     handedness=Handedness.RIGHT,
        ... )
        >>> store = [StoredBinGrid("survey-1", parameters, "EPSG:26715")]
        >>> corners = corners_from_p6(
        ...     parameters, inline_range=(101, 201), crossline_range=(51, 151)
        ... )
        >>> match_bin_grid(corners, "EPSG:26715", store).best.grid.key
        'survey-1'
    """
    dataset = (
        corners
        if isinstance(corners, BinGridCorners)
        else BinGridCorners.from_corners(corners)
    )
    working = map_grid_crs(crs, "dataset's ")
    spacing = derive_p6(dataset, increment_i=increment_i, increment_j=increment_j)
    tolerance = 0.5 * min(spacing.bin_width_i, spacing.bin_width_j)

    matches = []
    for grid in grids:
        same_crs = same_map_grid(grid.crs, working)
        if not same_crs and not _same_datum(grid.crs, working):
            logger.debug(
                "not comparing %s: %s is not on the datum of %s",
                grid.key,
                grid.crs.name,
                working.name,
            )
            continue
        modelled = grid.parameters.to_map(dataset.numbers)
        if not same_crs:
            converted = Transformation(unbound_crs(grid.crs), unbound_crs(working))
            modelled = converted.transform(modelled).coordinates.to_numpy()
        offsets = modelled - dataset.coordinates
        distance = float(np.hypot(offsets[:, 0], offsets[:, 1]).max())
        if distance <= tolerance:
            matches.append(
                BinGridMatch(
                    grid=grid,
                    distance=distance,
                    same_crs=same_crs,
                    same_increments=(
                        grid.parameters.increment_i == spacing.increment_i
                        and grid.parameters.increment_j == spacing.increment_j
                    ),
                )
            )
    matches.sort(key=lambda m: (not m.same_crs, not m.same_increments, m.distance))
    return MatchResult(
        dataset=dataset,
        crs=working,
        tolerance=tolerance,
        linear_unit=working.axes[working.value_axis_order[0]].unit_name,
        matches=tuple(matches),
    )


def _same_datum(
    first: CoordinateReferenceSystem, second: CoordinateReferenceSystem
) -> bool:
    """Whether coordinates in one convert to the other without a datum change."""
    a, b = unbound_crs(first).geodetic_crs, unbound_crs(second).geodetic_crs
    return a is not None and b is not None and a.equals(b, ignore_axis_order=True)
