"""Check, convert and square up a bin grid defined by its four corners.

:func:`convert_bin_grid` is the computation behind the OSDU CRS conversion
service's ``POST v3/convertBinGrid``, without any of its JSON: optionally
convert the corners to another CRS, fit the SDU note's rectangle through them
there, report how far they were from it, and give the squared corners in WGS
84. The bin grid scale factor is that CRS's point scale factor at the grid
centre, so that the P6 bin widths are the ground spacing of the bins there. A
projection that is not conformal there has a scale per direction rather than
one, so the scale factor must then be stated.

Coordinate conversions go through :class:`~geodetic_engine.geodesy.Transformation`
and keep its guarantees: a datum change needs a named operation or a bound CRS,
ballpark results and missing grids are refused, and the result records what was
applied.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pyproj import CRS, Proj

from geodetic_engine.bingrid.corners import LABELS, BinGridCorner, BinGridCorners
from geodetic_engine.bingrid.errors import UnsupportedCRSError
from geodetic_engine.bingrid.outline import BinGridOutline, outline
from geodetic_engine.bingrid.p6 import P6Parameters
from geodetic_engine.bingrid.squaring import MaxMislocation, SquaringResult, square_up
from geodetic_engine.geodesy import (
    AppliedOperation,
    AxisSpec,
    CoordinateReferenceSystem,
    Transformation,
    TransformationResult,
)

logger = logging.getLogger(__name__)

WGS84 = "EPSG:4326"

# PROJ's Tissot axes of a conformal projection differ by up to ~2e-8 of noise.
_CONFORMAL_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class BinGridResult:
    """A bin grid checked, optionally converted, and squared up.

    Attributes:
        source_crs: CRS the input corners are in.
        crs: CRS of the converted and squared grid: the target CRS if one was
            given on another map grid, or a bound one on the same map grid;
            else the source CRS.
        input_corners: The corners as given.
        converted_corners: The input corners converted to :attr:`crs`, before
            squaring; None when no conversion was applied.
        squaring: The rectangle fitted in :attr:`crs`, and the mis-location.
        outline: Outline of the squared corners in :attr:`crs`.
        wgs84_corners: Longitude and latitude of the squared corners, in order
            A, B, C, D; None when WGS 84 coordinates were not asked for.
        wgs84_outline: The ring of :attr:`outline` through
            :attr:`wgs84_corners`, or None. Each edge runs the short way round
            in longitude: across the antimeridian the longitudes continue past
            180 (-179.9 becomes 180.1), and a ring round a pole closes along
            the pole's latitude through two points labelled ``"pole"``.
        conversion: The conversion to :attr:`crs`, or None.
        wgs84_conversion: The conversion to WGS 84, or None.
    """

    source_crs: CoordinateReferenceSystem
    crs: CoordinateReferenceSystem
    input_corners: BinGridCorners
    converted_corners: BinGridCorners | None
    squaring: SquaringResult
    outline: BinGridOutline
    wgs84_corners: tuple[tuple[float, float], ...] | None
    wgs84_outline: BinGridOutline | None
    conversion: TransformationResult | None
    wgs84_conversion: TransformationResult | None

    @property
    def parameters(self) -> P6Parameters:
        """The P6 parameters of the squared grid, anchored at corner A."""
        return self.squaring.parameters

    @property
    def squared_corners(self) -> BinGridCorners:
        """Corners of the squared grid in :attr:`crs`."""
        return self.squaring.squared_corners

    @property
    def max_mislocation(self) -> MaxMislocation:
        """How far the corners were from a rectangle, in :attr:`crs`."""
        return self.squaring.max_mislocation

    @property
    def converted(self) -> bool:
        """Whether the corners were converted to another CRS."""
        return self.conversion is not None

    @property
    def linear_unit(self) -> str:
        """Unit of :attr:`crs`'s coordinates and of the bin widths."""
        return self.crs.axes[self.crs.value_axis_order[0]].unit_name

    def applied_operations(self) -> tuple[str, ...]:
        """What was done to the corners, one sentence per step, in order."""
        steps = []
        if self.conversion is not None:
            steps.append(
                f"Converted the corners from {self.source_crs.name} to "
                f"{self.crs.name} using {_label(self.conversion.operation)}"
            )
        mislocation = self.max_mislocation
        steps.append(
            f"Squared up the bin grid in {self.crs.name}: "
            f"dI={mislocation.di:.2f} inline numbers, "
            f"dJ={mislocation.dj:.2f} crossline numbers"
        )
        if self.wgs84_conversion is not None:
            steps.append(
                "Computed WGS 84 coordinates of the squared corners from "
                f"{self.crs.name} using {_label(self.wgs84_conversion.operation)}"
            )
        return tuple(steps)

    def to_json_dict(self) -> dict[str, Any]:
        """Render the result as plain data, for logging or serialisation."""
        squaring = self.squaring.to_json_dict()
        return {
            "source_crs": _crs_label(self.source_crs),
            "crs": _crs_label(self.crs),
            "converted": self.converted,
            "linear_unit": self.linear_unit,
            "parameters": squaring["parameters"],
            "input_corners": self.input_corners.to_json_dict(),
            "converted_corners": None
            if self.converted_corners is None
            else self.converted_corners.to_json_dict(),
            "squared_corners": squaring["squared_corners"],
            "residuals": squaring["residuals"],
            "max_mislocation": squaring["max_mislocation"],
            "outline": self.outline.to_json_dict(),
            "wgs84_corners": None
            if self.wgs84_corners is None
            else [list(point) for point in self.wgs84_corners],
            "wgs84_outline": None
            if self.wgs84_outline is None
            else self.wgs84_outline.to_json_dict(),
            "conversion": None
            if self.conversion is None
            else self.conversion.to_json_dict(),
            "wgs84_conversion": None
            if self.wgs84_conversion is None
            else self.wgs84_conversion.to_json_dict(),
            "applied_operations": list(self.applied_operations()),
        }


def convert_bin_grid(
    corners: BinGridCorners | Iterable[BinGridCorner | tuple[int, int, float, float]],
    crs: Any,
    *,
    target_crs: Any = None,
    operation: Any = None,
    scale_factor: float | None = None,
    increment_i: int = 1,
    increment_j: int = 1,
    wgs84: bool = True,
    wgs84_operation: Any = None,
    coordinate_epoch: float | None = None,
) -> BinGridResult:
    """Check a four-corner bin grid, optionally convert it, and square it up.

    Args:
        corners: The four corners, in any order.
        crs: CRS the corners' coordinates are in; anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts. Must be projected, optionally bound.
        target_crs: CRS to convert the grid to. Omitted, or on the same map
            grid as ``crs`` -- the same projected CRS, whether or not either
            is bound -- the grid is squared up where it is; a bound
            ``target_crs`` then names the operation to WGS 84, an unbound one
            leaves the grid's own CRS in place.
        operation: Coordinate operation for the conversion to ``target_crs``,
            as :class:`~geodetic_engine.geodesy.Transformation` takes it.
            Needed when the conversion changes datum and neither CRS is bound.
        scale_factor: Bin grid scale factor of the squared grid. Omitted, it
            is the point scale factor of the CRS the grid is squared in, at
            the grid centre (``point_scale_factor``), so that the bin widths
            are the ground spacing of the bins there. Required where that
            CRS's projection is not conformal at the grid, or its geographic
            CRS states its axes in different units.
        increment_i: Inline node increment.
        increment_j: Crossline node increment.
        wgs84: Whether to also give the squared corners in WGS 84.
        wgs84_operation: Coordinate operation to WGS 84, when the grid's CRS
            does not state one.
        coordinate_epoch: Decimal year the coordinates were observed at, for
            a conversion whose operation reads it, as
            :meth:`~geodetic_engine.geodesy.Transformation.transform` takes it.

    Returns:
        The squared grid with its provenance.

    Raises:
        InvalidCornersError: If the corners are not those of a bin grid, or
            their spans are not multiples of the node increments.
        DegenerateBinGridError: If their coordinates cannot be.
        InvalidParameterError: If a parameter is out of range.
        UnsupportedCRSError: If a CRS cannot carry a bin grid, or
            ``scale_factor`` is omitted and cannot be derived: the projection
            is not conformal at the grid, or its geographic CRS states its
            axes in different units.
        ValueError: If ``operation`` is given without a conversion to apply it
            to, or ``wgs84_operation`` with ``wgs84=False``.
        geodetic_engine.geodesy.GeodesyError: If a conversion cannot be resolved
            or applied, for example a datum change that names no operation.
    """
    labelled = (
        corners
        if isinstance(corners, BinGridCorners)
        else BinGridCorners.from_corners(corners)
    )
    source = map_grid_crs(crs, "")
    target = None if target_crs is None else map_grid_crs(target_crs, "target ")
    working = source
    if target is not None and same_map_grid(target, source):
        # Nothing to convert; the target's binding to WGS 84, if it has one, is
        # what was asked for, else the grid's own CRS stays, bound or not.
        working = target if target.crs.is_bound else source
        target = None
    if operation is not None and target is None:
        raise ValueError(
            "an operation was given, but no target_crs on another map grid than "
            "the grid's own to convert the corners to"
        )
    if wgs84_operation is not None and not wgs84:
        raise ValueError(
            "a wgs84_operation was given, but wgs84=False asks for no WGS 84 "
            "coordinates to apply it to"
        )

    conversion, converted_corners = None, None
    if target is not None:
        conversion = Transformation(source, target, operation).transform(
            labelled.coordinates, coordinate_epoch=coordinate_epoch
        )
        working = target
        converted_corners = labelled.with_coordinates(conversion.coordinates)
        logger.debug(
            "converted bin grid corners from %s to %s", source.name, target.name
        )

    in_crs = labelled if converted_corners is None else converted_corners
    squaring = square_up(
        in_crs,
        scale_factor=1.0 if scale_factor is None else scale_factor,
        increment_i=increment_i,
        increment_j=increment_j,
    )
    if scale_factor is None:
        # Squared at k = 1 first to refuse bad corners; k moves no fitted position.
        squaring = square_up(
            in_crs,
            scale_factor=point_scale_factor(in_crs, working),
            increment_i=increment_i,
            increment_j=increment_j,
        )

    squared_outline = outline(squaring.squared_corners)
    wgs84_conversion, wgs84_corners, wgs84_outline = None, None, None
    if wgs84:
        wgs84_conversion = Transformation(working, WGS84, wgs84_operation).transform(
            squaring.squared_corners.coordinates, coordinate_epoch=coordinate_epoch
        )
        wgs84_corners = tuple(
            (point[0], point[1]) for point in wgs84_conversion.coordinates
        )
        wgs84_outline = _geographic_ring(wgs84_corners, squared_outline.labels)

    return BinGridResult(
        source_crs=source,
        crs=working,
        input_corners=labelled,
        converted_corners=converted_corners,
        squaring=squaring,
        outline=squared_outline,
        wgs84_corners=wgs84_corners,
        wgs84_outline=wgs84_outline,
        conversion=conversion,
        wgs84_conversion=wgs84_conversion,
    )


def map_grid_crs(value: Any, role: str) -> CoordinateReferenceSystem:
    """Resolve a CRS a bin grid can be defined in: easting and northing.

    Raises:
        UnsupportedCRSError: If the CRS is not a 2D projected CRS, optionally
            bound, with an easting and a northing axis in one linear unit.
    """
    crs = CoordinateReferenceSystem.from_user_input(value)
    if not crs.crs.is_projected or crs.dimension != 2:
        raise UnsupportedCRSError(
            f"the {role}CRS {crs.name} is not a 2D projected CRS: a bin grid is "
            "laid out on a map grid, in its linear units"
        )
    easting, northing = (crs.axes[index] for index in crs.value_axis_order)
    if not (_points(easting, "east", "E") and _points(northing, "north", "N")):
        raise UnsupportedCRSError(
            f"the {role}CRS {crs.name} has axes pointing "
            f"{easting.direction} and {northing.direction}, not east and north, "
            "which the bin grid formulas need"
        )
    if easting.unit_conversion_factor != northing.unit_conversion_factor:
        raise UnsupportedCRSError(
            f"the {role}CRS {crs.name} states its easting in {easting.unit_name} "
            f"and its northing in {northing.unit_name}: the bin grid formulas "
            "need one linear unit for both"
        )
    return crs


def point_scale_factor(
    corners: BinGridCorners, crs: CoordinateReferenceSystem
) -> float:
    """EPSG's bin grid scale factor: the map grid's point scale at the grid centre.

    Args:
        corners: The corners, in ``crs``.
        crs: A CRS that :func:`map_grid_crs` accepts.

    Returns:
        Map grid distance per ellipsoidal distance at the centre of the corners.

    Raises:
        UnsupportedCRSError: If the CRS states no geographic CRS, or one with
            its axes in different units, or its projection is not conformal at
            the centre: where the scale there differs by more than 1e-6
            between directions, no one scale factor gives the ground spacing
            along both bin grid axes.
    """
    base = unbound_crs(crs)
    geographic = base.geodetic_crs
    if geographic is None:
        raise UnsupportedCRSError(
            f"the CRS {crs.name} states no geographic CRS to measure the grid on"
        )
    # PROJ gives both axes of a geographic CRS in the unit of one of them.
    first, second = geographic.axis_info[:2]
    if first.unit_conversion_factor != second.unit_conversion_factor:
        raise UnsupportedCRSError(
            f"the geographic CRS of {crs.name} states its axes in "
            f"{first.unit_name} and {second.unit_name}, not in one unit, so the "
            "grid centre cannot be placed on it; pass scale_factor to state the "
            "scale factor"
        )
    centre = (
        Transformation(base, geographic)
        .transform(corners.coordinates.mean(axis=0, keepdims=True))
        .coordinates.to_numpy()[0]
    )
    # Proj's factors take degrees from the CRS's own prime meridian, on its datum.
    longitude, latitude = centre * math.degrees(
        geographic.axis_info[0].unit_conversion_factor
    )
    factors = Proj(base).get_factors(longitude, latitude, errcheck=True)
    smallest, largest = factors.tissot_semiminor, factors.tissot_semimajor
    if largest > smallest * (1 + _CONFORMAL_TOLERANCE):
        raise UnsupportedCRSError(
            f"the projection of {crs.name} is not conformal at the grid: its "
            f"scale there ranges from {smallest:.9f} to {largest:.9f} by "
            "direction, so no one bin grid scale factor makes both bin widths "
            "ground distances; pass scale_factor to state one"
        )
    return math.sqrt(factors.areal_scale)


def unbound_crs(crs: CoordinateReferenceSystem) -> CRS:
    """The CRS itself, without any transformation to WGS 84 it is bound with."""
    base = crs.crs.source_crs if crs.crs.is_bound else None
    return crs.crs if base is None else base


def same_map_grid(
    first: CoordinateReferenceSystem, second: CoordinateReferenceSystem
) -> bool:
    """Whether two CRSs are the same projected CRS, bound to WGS 84 or not.

    Coordinates in one are coordinates in the other: a bin grid defined in
    either is the same grid.
    """
    return bool(unbound_crs(first) == unbound_crs(second))


def _geographic_ring(
    lonlat: tuple[tuple[float, float], ...], labels: tuple[str, ...]
) -> BinGridOutline:
    """The map grid's ring of corners, ``labels``, in longitude and latitude.

    Each edge runs the short way round, continuing past 180 east rather than
    -180 west; a ring that goes round a pole closes along the pole's latitude.
    """
    by_label = dict(zip(LABELS, lonlat, strict=True))
    points: list[tuple[float, float]] = []
    for label in labels:
        lon, lat = by_label[label]
        if points:
            lon += 360.0 * round((points[-1][0] - lon) / 360.0)
        points.append((lon, lat))
    if min(lon for lon, _ in points) < -180.0:
        points = [(lon + 360.0, lat) for lon, lat in points]
    laps = round((points[-1][0] - points[0][0]) / 360.0)
    if laps:
        # A counterclockwise ring goes east round the North Pole, west round the South.
        pole = 90.0 * laps
        points += [(points[-1][0], pole), (points[0][0], pole), points[0]]
        labels += ("pole", "pole", labels[0])
    return BinGridOutline(labels=labels, coordinates=tuple(points))


def _points(axis: AxisSpec, direction: str, abbreviation: str) -> bool:
    # A polar CRS's easting and northing both point "south" (along different
    # meridians), so its axes are known from their abbreviations instead.
    return axis.direction.lower() == direction or axis.abbrev.upper() == abbreviation


def _label(operation: AppliedOperation) -> str:
    codes = (
        (operation.authority_code,)
        if operation.authority_code
        else operation.bound_operations
    )
    return f"{operation.name} [{', '.join(codes)}]" if codes else operation.name


def _crs_label(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or crs.name
