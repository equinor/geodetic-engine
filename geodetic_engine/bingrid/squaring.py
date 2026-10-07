"""Derive P6 parameters from four corners, and measure how square the corners are.

The method is the one of the SDU note "Geometric aspects of bin grids"
(B. Kampes), which the OSDU CRS conversion service documents for
``POST v3/convertBinGrid``:

* origin: the centre of the four corners, in bin grid numbers and coordinates;
* bin widths: the mean of the two opposite edge lengths, per node, divided by
  the scale factor -- the one change from the note, whose widths are only
  consistent with its own formulas when the scale factor is 1;
* bearing: the mean of the bearings of A to B and C to D, averaged as vectors
  so that 359 and 1 degrees give 0 rather than 180;
* handedness: which side of the J-axis C lies on.

The grid these parameters define is the note's rectangle through the corners:
a construction, not a least-squares fit, which would spread the residuals
differently. Converting each corner's coordinates back to bin grid numbers
with it and comparing them with the corner's own numbers gives the
mis-location: how far the corners are from forming a rectangle, in inline and
crossline numbers.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from dataclasses import dataclass
from typing import Any

from geodetic_engine.bingrid.corners import LABELS, BinGridCorners
from geodetic_engine.bingrid.errors import DegenerateBinGridError, InvalidCornersError
from geodetic_engine.bingrid.p6 import (
    Handedness,
    P6Parameters,
    node_increment,
    positive_number,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CornerResidual:
    """How far one input corner is from the fitted rectangle.

    Attributes:
        label: ``"A"``, ``"B"``, ``"C"`` or ``"D"``.
        inline: The corner's inline number minus the inline number its input
            coordinates convert to.
        crossline: Likewise for the crossline number.
        easting: The corner's input easting minus its squared easting.
        northing: Likewise for the northing.
    """

    label: str
    inline: float
    crossline: float
    easting: float
    northing: float


@dataclass(frozen=True, slots=True)
class MaxMislocation:
    """The largest mis-location over the four corners.

    Attributes:
        di: Largest absolute inline residual, in inline numbers: the ``dI``
            of ``POST v3/convertBinGrid``.
        dj: Largest absolute crossline residual, in crossline numbers: ``dJ``.
        distance: Largest distance between an input corner and its squared
            position, in the CRS's linear unit.
        increment_i: Inline node increment, to express ``di`` in bins.
        increment_j: Crossline node increment, to express ``dj`` in bins.
    """

    di: float
    dj: float
    distance: float
    increment_i: int = 1
    increment_j: int = 1

    @property
    def di_bins(self) -> float:
        """``di`` in bins: inline numbers divided by the node increment."""
        return self.di / self.increment_i

    @property
    def dj_bins(self) -> float:
        """``dj`` in bins: crossline numbers divided by the node increment."""
        return self.dj / self.increment_j


@dataclass(frozen=True, slots=True)
class SquaringResult:
    """The rectangle fitted through four corners, and how well it fits.

    Attributes:
        input_corners: The corners as given.
        parameters: The fitted grid, anchored at squared corner A.
        squared_corners: The corners of the fitted rectangle, at the same
            numbers as the input corners.
        residuals: Per corner, in order A, B, C, D.
        max_mislocation: The largest residuals.
    """

    input_corners: BinGridCorners
    parameters: P6Parameters
    squared_corners: BinGridCorners
    residuals: tuple[CornerResidual, ...]
    max_mislocation: MaxMislocation

    def to_json_dict(self) -> dict[str, Any]:
        """Render the result as plain data."""
        mislocation = self.max_mislocation
        return {
            "parameters": self.parameters.to_json_dict(),
            "input_corners": self.input_corners.to_json_dict(),
            "squared_corners": self.squared_corners.to_json_dict(),
            "residuals": [dataclasses.asdict(residual) for residual in self.residuals],
            "max_mislocation": {
                "di": mislocation.di,
                "dj": mislocation.dj,
                "di_bins": mislocation.di_bins,
                "dj_bins": mislocation.dj_bins,
                "distance": mislocation.distance,
            },
        }


def derive_p6(
    corners: BinGridCorners,
    *,
    scale_factor: float = 1.0,
    increment_i: int = 1,
    increment_j: int = 1,
) -> P6Parameters:
    """Derive the P6 parameters of the SDU note's rectangle through four corners.

    Args:
        corners: The corners.
        scale_factor: Bin grid scale factor of the grid; the derived bin widths
            are ground distances at this scale.
        increment_i: Change of inline number between adjacent bin nodes.
        increment_j: Change of crossline number between adjacent bin nodes.

    Returns:
        The parameters, with the centre of the corners as origin.

    Raises:
        InvalidCornersError: If the corners' inline or crossline span is not a
            multiple of the node increment, so that they cannot all be nodes.
        DegenerateBinGridError: If the corners coincide, or their outline
            A-B-D-C is not convex.
        InvalidParameterError: If the scale factor or an increment is out of
            range.
    """
    k = positive_number("scale_factor", scale_factor)
    increment_i = node_increment("increment_i", increment_i)
    increment_j = node_increment("increment_j", increment_j)
    (ax, ay), (bx, by), (cx, cy), (dx, dy) = _checked_coordinates(corners)
    span_i = corners.c.inline - corners.a.inline
    span_j = corners.b.crossline - corners.a.crossline
    _on_node_lattice("inline", span_i, increment_i)
    _on_node_lattice("crossline", span_j, increment_j)

    # The SDU note's widths are map grid distances; dividing by k makes them
    # the ground distances that EPSG 9666/1049 multiply by k again.
    mean_ac_bd = (math.hypot(cx - ax, cy - ay) + math.hypot(dx - bx, dy - by)) / 2
    mean_ab_cd = (math.hypot(bx - ax, by - ay) + math.hypot(dx - cx, dy - cy)) / 2
    bin_width_i = increment_i * mean_ac_bd / (span_i * k)
    bin_width_j = increment_j * mean_ab_cd / (span_j * k)

    bearing_ab = math.atan2(bx - ax, by - ay)
    bearing_cd = math.atan2(dx - cx, dy - cy)
    bearing = math.atan2(
        math.sin(bearing_ab) + math.sin(bearing_cd),
        math.cos(bearing_ab) + math.cos(bearing_cd),
    )
    right = (cx - ax) * math.cos(bearing) > (cy - ay) * math.sin(bearing)

    return P6Parameters(
        origin_i=sum(corner.inline for corner in corners) / 4,
        origin_j=sum(corner.crossline for corner in corners) / 4,
        origin_easting=(ax + bx + cx + dx) / 4,
        origin_northing=(ay + by + cy + dy) / 4,
        bin_width_i=bin_width_i,
        bin_width_j=bin_width_j,
        bearing_j=math.degrees(bearing),
        handedness=Handedness.RIGHT if right else Handedness.LEFT,
        scale_factor=k,
        increment_i=increment_i,
        increment_j=increment_j,
    )


def square_up(
    corners: BinGridCorners,
    *,
    scale_factor: float = 1.0,
    increment_i: int = 1,
    increment_j: int = 1,
) -> SquaringResult:
    """Fit the SDU note's rectangle through four corners and measure the misfit.

    Args:
        corners: The corners.
        scale_factor: Bin grid scale factor, as for :func:`derive_p6`.
        increment_i: Inline node increment.
        increment_j: Crossline node increment.

    Returns:
        The fitted grid, the squared corners and the residuals.

    Raises:
        InvalidCornersError: As for :func:`derive_p6`.
        DegenerateBinGridError: As for :func:`derive_p6`.
        InvalidParameterError: As for :func:`derive_p6`.
    """
    centred = derive_p6(
        corners,
        scale_factor=scale_factor,
        increment_i=increment_i,
        increment_j=increment_j,
    )
    squared = corners.with_coordinates(centred.to_map(corners.numbers).tolist())
    modelled = centred.to_bin(corners.coordinates).tolist()
    residuals = tuple(
        CornerResidual(
            label=label,
            inline=given.inline - model_i,
            crossline=given.crossline - model_j,
            easting=given.easting - fitted.easting,
            northing=given.northing - fitted.northing,
        )
        for label, given, fitted, (model_i, model_j) in zip(
            LABELS, corners, squared, modelled, strict=True
        )
    )
    anchored = dataclasses.replace(
        centred,
        origin_i=squared.a.inline,
        origin_j=squared.a.crossline,
        origin_easting=squared.a.easting,
        origin_northing=squared.a.northing,
    )
    return SquaringResult(
        input_corners=corners,
        parameters=anchored,
        squared_corners=squared,
        residuals=residuals,
        max_mislocation=MaxMislocation(
            di=max(abs(residual.inline) for residual in residuals),
            dj=max(abs(residual.crossline) for residual in residuals),
            distance=max(math.hypot(r.easting, r.northing) for r in residuals),
            increment_i=anchored.increment_i,
            increment_j=anchored.increment_j,
        ),
    )


def _on_node_lattice(axis: str, span: int, increment: int) -> None:
    """Check that corners ``span`` numbers apart can both be bin nodes.

    Raises:
        InvalidCornersError: If ``span`` is not a multiple of ``increment``.
    """
    if span % increment:
        raise InvalidCornersError(
            f"the corners' {axis} numbers are {span} apart, which is not a "
            f"multiple of the {axis} node increment {increment}: the corners "
            "cannot all be bin nodes of this grid"
        )


def _checked_coordinates(corners: BinGridCorners) -> list[tuple[float, float]]:
    """Coordinates of A, B, C, D, once they are known to outline a grid.

    Raises:
        DegenerateBinGridError: If two neighbouring corners coincide, or the
            outline A-B-D-C is not strictly convex.
    """
    points = dict(zip(LABELS, ((c.easting, c.northing) for c in corners), strict=True))
    ring = ("A", "B", "D", "C")
    for first, second in zip(ring, ring[1:] + ring[:1], strict=True):
        if points[first] == points[second]:
            raise DegenerateBinGridError(
                f"corners {first} and {second} are both at {points[first]}"
            )
    turns = []
    for index, label in enumerate(ring):
        (x0, y0), (x1, y1), (x2, y2) = (
            points[ring[index - 1]],
            points[label],
            points[ring[(index + 1) % 4]],
        )
        turns.append((x1 - x0) * (y2 - y1) - (y1 - y0) * (x2 - x1))
    if not (all(turn > 0 for turn in turns) or all(turn < 0 for turn in turns)):
        raise DegenerateBinGridError(
            "the outline A-B-D-C of the corners is not convex: their coordinates "
            "cannot belong to their inline and crossline numbers, as happens when "
            "corners are swapped or lie on one line"
        )
    return [points[label] for label in LABELS]
