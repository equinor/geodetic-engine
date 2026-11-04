"""Read, write, round and transform vector geometries.

Geometries are read from, and written to, the encodings databases, GDAL and
web services exchange: WKT, WKB and hex WKB in their ISO flavour, PostGIS
EWKT and EWKB with an SRID, GeoJSON geometry objects, Esri JSON geometries,
and Shapely geometries or anything with a ``__geo_interface__``.
:meth:`~geodetic_engine.geodesy.Transformation.transform_geometry` and
:meth:`~geodetic_engine.geodesy.Transformation.transform_geometries` transform
them with the guarantees points have, since every coordinate goes through
:meth:`~geodetic_engine.geodesy.Transformation.transform_arrays`.

"WKT" here is geometry WKT (OGC Simple Features), such as ``POINT (10 60)``,
not the CRS WKT
:meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input` reads.

Coordinates are ``xy``, longitude or easting first, as WKT and WKB are in
practice. ``axis_order="crs"`` reads and writes a CRS's declared order
instead, for data written latitude first. An SRID is a bare integer, so
``srid_authority`` says which authority it is a code of; SRID 0 is unknown.

Rounding works on the coordinate values, not in the writer, so every
encoding holds the same numbers: ``decimals`` rounds half up by each value's
shortest decimal form, and ``grid_size`` snaps to a grid keeping polygons
valid.

Shapely refuses curved geometries, and so does this module. M values are
read, carried through a transformation unchanged and written, except to
GeoJSON, which has no place for them.

Example:
    >>> from geodetic_engine.geodesy import Transformation
    >>> tfm = Transformation("EPSG:4230", "EPSG:32631", operation="EPSG:1612")
    >>> result = tfm.transform_geometry("SRID=4230;POINT (2.5 63.5)")
    >>> result.wkt(decimals=2)
    'POINT (475017.58 7041354.6)'
    >>> result.ewkt(decimals=2)
    'SRID=32631;POINT (475017.58 7041354.6)'
"""

from __future__ import annotations

import json
import math
import os
import re
import warnings
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Final, Literal, cast

import numpy as np
import pandas as pd
import shapely
import shapely.geometry
from numpy.typing import NDArray
from shapely.errors import ShapelyError
from shapely.geometry.base import BaseGeometry, BaseMultipartGeometry

from geodetic_engine.geodesy._antimeridian import (
    ANTIMERIDIANS,
    Antimeridian,
    crossings,
    cut,
    turns,
)
from geodetic_engine.geodesy._crs_tools import (
    compound,
    integer_code,
    longitude,
    same_crs,
)
from geodetic_engine.geodesy._failures import (
    failing_rows,
    first_failure,
    probe,
    relocated,
)
from geodetic_engine.geodesy._rounding import axis_decimals, round_half_up
from geodetic_engine.geodesy._segments import (
    SEGMENTS,
    Segments,
    densify_rows,
    require_length,
)
from geodetic_engine.geodesy._wkb import replace_coordinates
from geodetic_engine.geodesy.crs import CoordinateReferenceSystem
from geodetic_engine.geodesy.errors import (
    GeodesyError,
    GeometryError,
    GeometryValidityWarning,
    TransformationFailedError,
    UnresolvableCRSError,
)
from geodetic_engine.geodesy.result import ArrayTransformationResult

if TYPE_CHECKING:
    from geodetic_engine.geodesy.transformation import Transformation

__all__ = [
    "FORMATS",
    "GeometryError",
    "GeometryResult",
    "GeometryResults",
    "GeometryValidityWarning",
    "read_geometry",
    "round_geometry",
    "write_geometry",
]

type Format = Literal[
    "wkt", "wkb", "wkb_hex", "ewkt", "ewkb", "ewkb_hex", "geojson", "esrijson"
]
type AxisOrder = Literal["xy", "crs"]
type Orientation = Literal["ccw", "cw"]
type Validate = Literal["off", "warn", "raise"]
type ByteOrder = Literal["little", "big"]
type Decimals = int | Sequence[int | None] | None
type ObjectArray = NDArray[np.object_]
type FloatArray = NDArray[np.float64]

FORMATS: Final = (
    "wkt",
    "wkb",
    "wkb_hex",
    "ewkt",
    "ewkb",
    "ewkb_hex",
    "geojson",
    "esrijson",
)
"""The encodings :func:`write_geometry` writes and :func:`read_geometry` reads."""

_AXIS_ORDERS: Final = ("xy", "crs")
_ORIENTATIONS: Final = ("ccw", "cw")
_VALIDATIONS: Final = ("off", "warn", "raise")
_BYTE_ORDERS: Final = {"little": 1, "big": 0}
_EWKT: Final = re.compile(r"\s*SRID\s*=\s*(-?\d+)\s*;(.*)\Z", re.IGNORECASE | re.DOTALL)
_HEX: Final = re.compile(r"[0-9A-Fa-f]+\Z")
# What repr writes and WKT here does not: a whole number's ".0", and an
# exponent's sign and leading zeros, "1e+20" and "1.5e-05".
_WHOLE: Final = re.compile(r"\.0(?=[ ,]|\Z)")
_EXPONENT: Final = re.compile(r"e\+?(-?)0*(\d)")
_GEOJSON_TYPES: Final = frozenset(
    {
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    }
)
_ESRI_KEYS: Final = (
    "x",
    "points",
    "paths",
    "rings",
    "xmin",
    "curvePaths",
    "curveRings",
)
_AXIS_NAMES: Final = ("x", "y", "z", "m")
# Rows transformed per call to transform_batches, bounding PROJ's buffers.
_CHUNK: Final = 1 << 20
# The types Shapely reads into ragged arrays and builds back from them, whose
# lines and rings are runs of coordinates: LineString, Polygon,
# MultiLineString and MultiPolygon.
_RAGGED: Final = (1, 3, 5, 6)
# Warnings name the caller's line, outside the package.
_PACKAGE: Final = os.path.join(os.path.dirname(os.path.dirname(__file__)), "")


def read_geometry(
    value: object,
    *,
    format: Format | None = None,
    srid_authority: str = "EPSG",
    axis_order: AxisOrder = "xy",
    crs: object = None,
) -> tuple[shapely.Geometry | None, CoordinateReferenceSystem | None]:
    """Read a geometry, and the CRS it states, from any encoding in :data:`FORMATS`.

    Args:
        value: WKT or EWKT text; WKB or EWKB as bytes or hex text in either
            case; a GeoJSON geometry object or Esri JSON geometry, as a
            mapping or JSON text; a Shapely geometry; or an object with a
            ``__geo_interface__``. None reads as None.
        format: The encoding ``value`` is in, or None to tell from ``value``.
        srid_authority: The authority an SRID is a code of: ``"EPSG"``, or
            any other in the active proj.db, such as ``"ESRI"``.
        axis_order: ``"xy"`` when the coordinates are longitude or easting
            first, ``"crs"`` when they are in the CRS's declared order.
        crs: The CRS ``value`` is in, when it does not say. Needed for
            ``axis_order="crs"`` then.

    Returns:
        The geometry, ``xy``, and the CRS it is in: the one it states (an
        SRID, an Esri ``spatialReference``), else ``crs``, else None.

    Raises:
        TypeError: If ``value`` is none of the above.
        GeometryError: If it cannot be read, is curved, or states a CRS other
            than ``crs``.
        UnresolvableCRSError: If the CRS it states cannot be resolved.
        ValueError: If ``axis_order="crs"`` and no CRS is known.

    Example:
        >>> geometry, crs = read_geometry(
        ...     "0101000020E6100000C7726EBA353F24405B39F0746B7E4E40"
        ... )
        >>> geometry.wkt, crs.authority_code
        ('POINT (10.123456789 60.987654321)', 'EPSG:4326')
    """
    _require_choice("axis_order", axis_order, _AXIS_ORDERS)
    geometry, stated = _read(value, format, _Srids(srid_authority))
    given = None if crs is None else CoordinateReferenceSystem.from_user_input(crs)
    if stated is not None and given is not None and not same_crs(stated, given):
        raise GeometryError(
            f"the geometry states {_label(stated)}, not crs, {_label(given)}"
        )
    known = stated if stated is not None else given
    if axis_order == "crs" and geometry is not None:
        geometry = _permute(_objects([geometry]), _require_crs(known), True)[0]
    return geometry, known


def write_geometry(
    geometry: shapely.Geometry | None,
    format: Format,
    *,
    crs: object = None,
    decimals: Decimals = None,
    grid_size: float | None = None,
    axis_order: AxisOrder = "xy",
    byte_order: ByteOrder = "little",
    validate: Validate = "warn",
) -> Any:
    """Write a geometry in an encoding from :data:`FORMATS`.

    WKT is written at full precision, trimmed, so ``1.50`` is ``1.5`` and
    nothing is cut. ``"wkb"`` is ISO WKB, the OGC standard; ``"ewkb"`` is
    PostGIS's extended WKB, the only flavour with an SRID.

    Args:
        geometry: The geometry, ``xy``, or None, which is written as None.
        format: The encoding: ``"wkt"``, ``"wkb"`` (bytes), ``"wkb_hex"``,
            ``"ewkt"``, ``"ewkb"``, ``"ewkb_hex"``, ``"geojson"`` (a GeoJSON
            geometry object) or ``"esrijson"`` (an Esri JSON geometry).
        crs: The CRS the geometry is in. Needed for the SRID of EWKT and EWKB,
            for ``axis_order="crs"``, and for Esri's ``spatialReference``.
        decimals: Round to this many decimals first, half up: one number for
            every axis, or one per axis in the order x, y, z, m (None keeps
            that axis).
        grid_size: Snap to a grid of this size first instead, keeping
            polygons valid.
        axis_order: ``"xy"``, or ``"crs"`` to write the CRS's declared order.
        byte_order: ``"little"`` or ``"big"``, for WKB.
        validate: What to do when rounding to ``decimals`` makes a valid
            geometry invalid: ``"off"``, ``"warn"`` or ``"raise"``.

    Returns:
        The encoding: text, bytes or a mapping, or None for None.

    Raises:
        GeometryError: If the geometry cannot be written in ``format``: an
            SRID or ``spatialReference`` without a code to write, M values in
            GeoJSON, a geometry collection in Esri JSON; or if
            ``validate="raise"`` and rounding made it invalid.
        ValueError: If an option is not one of those above.

    Example:
        >>> point = shapely.Point(10.123456789, 60.987654321)
        >>> write_geometry(point, "ewkb_hex", crs="EPSG:4326")
        '0101000020E6100000C7726EBA353F24405B39F0746B7E4E40'
        >>> write_geometry(point, "wkt", decimals=3)
        'POINT (10.123 60.988)'
    """
    return _encode(
        _objects([geometry]),
        format,
        crs=None if crs is None else CoordinateReferenceSystem.from_user_input(crs),
        decimals=decimals,
        grid_size=grid_size,
        axis_order=axis_order,
        byte_order=byte_order,
        validate=validate,
    )[0]


def round_geometry(
    geometry: Any,
    decimals: Decimals = None,
    *,
    grid_size: float | None = None,
    validate: Validate = "warn",
) -> Any:
    """Round a geometry's coordinates, or those of an array of geometries.

    ``decimals`` rounds every coordinate half up, by its shortest decimal
    form: with 2 decimals, ``2.675`` is ``2.68`` and ``-0.125`` is ``-0.13``.
    ``-0.0`` becomes ``0.0``. Every vertex is kept, which can make a polygon
    invalid. ``grid_size`` snaps to a grid instead, with
    ``shapely.set_precision``, which keeps polygons valid but can remove
    vertices. Rounding twice gives what rounding once does.

    Args:
        geometry: A Shapely geometry or None, or a sequence or array of them.
        decimals: One number for every axis, or one per axis in the order x,
            y, z, m, None keeping that axis.
        grid_size: The grid to snap to, instead of ``decimals``.
        validate: What to do when rounding to ``decimals`` makes a valid
            geometry invalid: ``"off"``, ``"warn"`` (a
            :class:`~geodetic_engine.geodesy.GeometryValidityWarning`) or ``"raise"``.

    Returns:
        The rounded geometry, or a numpy array of them.

    Raises:
        GeometryError: If ``validate="raise"`` and a geometry became invalid,
            or ``grid_size`` is asked for geometries with M values.
        ValueError: If both ``decimals`` and ``grid_size`` are given, or
            either is not valid.

    Example:
        >>> round_geometry(shapely.Point(2.675, -0.125), 2).wkt
        'POINT (2.68 -0.13)'
    """
    single = geometry is None or isinstance(geometry, shapely.Geometry)
    array = _objects([geometry] if single else list(geometry))
    rounded = _round(array, decimals, grid_size, validate)
    return rounded[0] if single else rounded


@dataclass(frozen=True, slots=True, eq=False)
class GeometryResult:
    """A transformed geometry, its provenance, and its encodings.

    Attributes:
        geometry: The geometry in the target CRS, ``xy``, at full precision;
            None when None was transformed.
        source_crs: The CRS it was transformed from.
        target_crs: The CRS it is in.
        transformation: The provenance: CRSs, operation, grids, epoch and
            pipeline, as
            :meth:`~geodetic_engine.geodesy.ArrayTransformationResult.metadata_json_dict`
            gives it, written out each time it is read.
        axis_order: The axis order the encodings below are written in.
    """

    geometry: shapely.Geometry | None
    source_crs: CoordinateReferenceSystem
    target_crs: CoordinateReferenceSystem
    _provenance: ArrayTransformationResult = field(repr=False)
    axis_order: AxisOrder = "xy"

    @property
    def transformation(self) -> dict[str, Any]:
        """The provenance, written out now: it takes longer than a point does."""
        return self._provenance.metadata_json_dict()

    def wkt(self, decimals: Decimals = None, **options: Any) -> str | None:
        """WKT, as :func:`write_geometry` writes it."""
        return cast("str | None", self._write("wkt", decimals, options))

    def wkb(self, decimals: Decimals = None, **options: Any) -> bytes | None:
        """ISO WKB, as :func:`write_geometry` writes it."""
        return cast("bytes | None", self._write("wkb", decimals, options))

    def wkb_hex(self, decimals: Decimals = None, **options: Any) -> str | None:
        """ISO WKB as upper-case hex, as :func:`write_geometry` writes it."""
        return cast("str | None", self._write("wkb_hex", decimals, options))

    def ewkt(self, decimals: Decimals = None, **options: Any) -> str | None:
        """EWKT with the target CRS's SRID, as :func:`write_geometry` writes it."""
        return cast("str | None", self._write("ewkt", decimals, options))

    def ewkb(self, decimals: Decimals = None, **options: Any) -> bytes | None:
        """Extended WKB with the target CRS's SRID."""
        return cast("bytes | None", self._write("ewkb", decimals, options))

    def ewkb_hex(self, decimals: Decimals = None, **options: Any) -> str | None:
        """Extended WKB with the target CRS's SRID, as hex, as PostGIS prints it."""
        return cast("str | None", self._write("ewkb_hex", decimals, options))

    def geojson(self, decimals: Decimals = None, **options: Any) -> Any:
        """A GeoJSON geometry object."""
        return self._write("geojson", decimals, options)

    def esrijson(self, decimals: Decimals = None, **options: Any) -> Any:
        """An Esri JSON geometry with the target CRS as its ``spatialReference``."""
        return self._write("esrijson", decimals, options)

    def _write(self, format: Format, decimals: Decimals, options: Any) -> Any:
        return _encode(
            _objects([self.geometry]),
            format,
            crs=self.target_crs,
            decimals=decimals,
            axis_order=self.axis_order,
            **options,
        )[0]


@dataclass(frozen=True, slots=True, eq=False)
class GeometryResults:
    """Transformed geometries, their provenance, and their encodings.

    Indexing or iterating gives a :class:`GeometryResult` per geometry. The
    encodings are returned in the container the geometries came in: a list
    for a sequence, a numpy array for an array, and a pandas Series with the
    same index for a Series or GeoSeries.

    Attributes:
        geometries: The geometries in the target CRS, ``xy``, in the input's
            container. A GeoSeries comes back as one, with the target CRS set.
        source_crs: The CRS they were transformed from.
        target_crs: The CRS they are in.
        transformation: The provenance, shared by every geometry, written out
            each time it is read.
        axis_order: The axis order the encodings are written in.
    """

    geometries: Any
    source_crs: CoordinateReferenceSystem
    target_crs: CoordinateReferenceSystem
    _provenance: ArrayTransformationResult = field(repr=False)
    axis_order: AxisOrder = "xy"
    _array: ObjectArray = field(default_factory=lambda: _objects([]), repr=False)
    _container: _Container | None = field(default=None, repr=False)

    @property
    def transformation(self) -> dict[str, Any]:
        """The provenance, written out now: it takes longer than a point does."""
        return self._provenance.metadata_json_dict()

    def __len__(self) -> int:
        return len(self._array)

    def __iter__(self) -> Iterator[GeometryResult]:
        return (self[index] for index in range(len(self)))

    def __getitem__(self, index: int) -> GeometryResult:
        return GeometryResult(
            self._array[index],
            self.source_crs,
            self.target_crs,
            self._provenance,
            self.axis_order,
        )

    def wkt(self, decimals: Decimals = None, **options: Any) -> Any:
        """WKT for each geometry."""
        return self._write("wkt", decimals, options)

    def wkb(self, decimals: Decimals = None, **options: Any) -> Any:
        """ISO WKB for each geometry."""
        return self._write("wkb", decimals, options)

    def wkb_hex(self, decimals: Decimals = None, **options: Any) -> Any:
        """ISO WKB as hex for each geometry."""
        return self._write("wkb_hex", decimals, options)

    def ewkt(self, decimals: Decimals = None, **options: Any) -> Any:
        """EWKT with the target CRS's SRID for each geometry."""
        return self._write("ewkt", decimals, options)

    def ewkb(self, decimals: Decimals = None, **options: Any) -> Any:
        """Extended WKB with the target CRS's SRID for each geometry."""
        return self._write("ewkb", decimals, options)

    def ewkb_hex(self, decimals: Decimals = None, **options: Any) -> Any:
        """Extended WKB as hex with the target CRS's SRID for each geometry."""
        return self._write("ewkb_hex", decimals, options)

    def geojson(self, decimals: Decimals = None, **options: Any) -> Any:
        """A GeoJSON geometry object for each geometry."""
        return self._write("geojson", decimals, options)

    def esrijson(self, decimals: Decimals = None, **options: Any) -> Any:
        """An Esri JSON geometry for each geometry."""
        return self._write("esrijson", decimals, options)

    def _write(self, format: Format, decimals: Decimals, options: Any) -> Any:
        written = _encode(
            self._array,
            format,
            crs=self.target_crs,
            decimals=decimals,
            axis_order=self.axis_order,
            **options,
        )
        if self._container is None:
            return written
        return self._container.wrap(written, geometries=False, crs=None)


@dataclass(frozen=True, slots=True)
class _Container:
    """What a batch of geometries came in, to give results back in it."""

    kind: Literal["list", "array", "series", "geoseries"]
    index: Any = None
    name: Any = None
    series_type: Any = None

    def wrap(self, values: Sequence[Any], *, geometries: bool, crs: Any) -> Any:
        if self.kind == "list":
            return list(values)
        array = np.empty(len(values), dtype=object)
        for index, value in enumerate(values):
            array[index] = value
        if self.kind == "array":
            return array
        if self.kind == "geoseries" and geometries:
            return self.series_type(array, index=self.index, crs=crs, name=self.name)
        return pd.Series(array, index=self.index, name=self.name, dtype=object)


def _transform(
    transformation: Transformation,
    values: object,
    *,
    single: bool,
    coordinate_epoch: float | None,
    axis_order: AxisOrder,
    densify: float | None,
    segments: Segments,
    antimeridian: Antimeridian | None,
    orient: Orientation | None,
    srid_authority: str,
) -> GeometryResult | GeometryResults:
    """Transform one geometry or a batch, for the methods of ``Transformation``."""
    _require_choice("axis_order", axis_order, _AXIS_ORDERS)
    _require_choice("segments", segments, SEGMENTS)
    if antimeridian is not None:
        _require_choice("antimeridian", antimeridian, ANTIMERIDIANS)
    if orient is not None:
        _require_choice("orient", orient, _ORIENTATIONS)
    if segments == "geodesic" and densify is None:
        raise ValueError(
            "segments='geodesic' draws geodesics with densify, the longest "
            "segment in metres along the ellipsoid"
        )
    source = transformation.source_crs
    target = transformation.target_crs
    container: _Container | None = None
    stated_all: CoordinateReferenceSystem | None = None
    if single:
        items: list[object] = [values]
    else:
        items, container, stated_all = _items(values)
    geometries, stated = _read_all(items, srid_authority)
    _require_source(stated, source)
    if stated_all is not None and not same_crs(stated_all, source):
        raise GeometryError(
            f"the GeoSeries is in {_label(stated_all)}, but the transformation "
            f"is from {_label(source)}"
        )
    if axis_order == "crs":
        geometries = _permute(geometries, source, True)
    if densify is not None:
        geometries = _densify(geometries, densify, segments, source)
    moved, provenance = _apply(transformation, geometries, coordinate_epoch)
    if antimeridian is not None:
        moved = _wrap(moved, geometries, transformation, coordinate_epoch, antimeridian)
    if orient is not None:
        moved = shapely.orient_polygons(moved, exterior_cw=orient == "cw")
    # Kept for what it says of the transformation, not for its positions,
    # which the geometries hold.
    provenance = replace(
        provenance, columns=tuple(np.empty(0) for _ in provenance.columns)
    )
    if single:
        return GeometryResult(moved[0], source, target, provenance, axis_order)
    assert container is not None
    return GeometryResults(
        container.wrap(list(moved), geometries=True, crs=target.crs),
        source,
        target,
        provenance,
        axis_order,
        moved,
        container,
    )


def _items(
    values: object,
) -> tuple[list[object], _Container, CoordinateReferenceSystem | None]:
    """The values of a batch, the container they came in, and a CRS it states."""
    if isinstance(values, str | bytes | bytearray | Mapping | shapely.Geometry):
        raise TypeError(
            "transform_geometries takes a sequence, array or Series of "
            "geometries; transform one geometry with transform_geometry"
        )
    if isinstance(values, pd.Series):
        stated = None
        kind: Literal["series", "geoseries"] = "series"
        if type(values).__module__.split(".")[0] == "geopandas":
            kind = "geoseries"
            crs = getattr(values, "crs", None)
            if crs is not None:
                stated = CoordinateReferenceSystem.from_user_input(crs)
        container = _Container(kind, values.index, values.name, type(values))
        items = [
            None if _missing(item) else item for item in values.to_numpy(dtype=object)
        ]
        return items, container, stated
    if isinstance(values, np.ndarray):
        if values.ndim != 1:
            raise ValueError(
                f"an array of geometries is one-dimensional, not {values.ndim}"
            )
        return list(values), _Container("array"), None
    try:
        items = list(cast("Sequence[object]", values))
    except TypeError:
        raise TypeError(
            "transform_geometries takes a sequence, array or Series of "
            f"geometries, not {type(values).__name__}"
        ) from None
    return items, _Container("list"), None


def _missing(value: object) -> bool:
    """Whether a Series holds no geometry here: None, NaN or ``pd.NA``."""
    if value is None or value is pd.NA:
        return True
    return isinstance(value, float) and math.isnan(value)


def _read_all(
    items: Sequence[object], srid_authority: str
) -> tuple[ObjectArray, list[CoordinateReferenceSystem | None]]:
    """Read a batch, each encoding in one Shapely call.

    A batch that fails is read again item by item, which names the first item
    that fails, and how, as reading it alone does.
    """
    srids = _Srids(srid_authority)
    try:
        batch = _read_batch(items, srids)
    except Exception:
        # Whatever failed, the item-by-item read below raises it, named.
        batch = None
    if batch is not None:
        return batch
    geometries: list[shapely.Geometry | None] = []
    stated: list[CoordinateReferenceSystem | None] = []
    for index, item in enumerate(items):
        try:
            geometry, crs = _read(item, None, srids)
        except (GeometryError, TypeError) as error:
            raise type(error)(f"geometry {index}: {error}") from error
        geometries.append(geometry)
        stated.append(crs)
    return _objects(geometries), stated


def _read_batch(
    items: Sequence[object], srids: _Srids
) -> tuple[ObjectArray, list[CoordinateReferenceSystem | None]]:
    """:func:`_read_all` for a batch: one Shapely call per encoding."""
    count = len(items)
    geometries = np.empty(count, dtype=object)
    # The SRID each item states, by its encoding or its geometry; 0 for none.
    srid = np.zeros(count, dtype=np.int64)
    from_geometry: list[int] = []
    wkb_at: list[int] = []
    wkb: list[bytes | str] = []
    wkt_at: list[int] = []
    wkt: list[str] = []
    stated: list[CoordinateReferenceSystem | None] = [None] * count
    for index, item in enumerate(items):
        if item is None:
            continue
        if isinstance(item, shapely.Geometry):
            geometries[index] = item
            from_geometry.append(index)
        elif isinstance(item, bytes | bytearray | memoryview):
            wkb_at.append(index)
            wkb.append(bytes(item))
        elif isinstance(item, str):
            text = item.strip()
            if (match := _EWKT.match(text)) is not None:
                srid[index] = int(match.group(1))
                if srid[index] < 0:
                    raise GeometryError(f"an SRID is not negative: {srid[index]}")
                wkt_at.append(index)
                wkt.append(match.group(2))
            elif text.startswith("{"):
                geometries[index], stated[index] = _detect(item, srids)
            elif _HEX.match(text) and not len(text) % 2:
                wkb_at.append(index)
                wkb.append(text)
            else:
                wkt_at.append(index)
                wkt.append(text)
        else:
            geometries[index], stated[index] = _detect(item, srids)
    if wkb:
        geometries[wkb_at] = shapely.from_wkb(np.array(wkb, dtype=object))
        from_geometry.extend(wkb_at)
    if wkt:
        geometries[wkt_at] = shapely.from_wkt(np.array(wkt, dtype=object))
    if from_geometry:
        srid[from_geometry] = shapely.get_srid(geometries[from_geometry])
    for index in np.flatnonzero(srid).tolist():
        stated[index] = srids(int(srid[index]))
    return _objects(list(geometries)), stated


def _require_source(
    stated: Sequence[CoordinateReferenceSystem | None],
    source: CoordinateReferenceSystem,
) -> None:
    """Refuse a geometry stating a CRS other than the transformation's source."""
    checked: dict[int, bool] = {}
    for index, crs in enumerate(stated):
        if crs is None:
            continue
        same = checked.get(id(crs))
        if same is None:
            same = checked[id(crs)] = same_crs(crs, source)
        if not same:
            raise GeometryError(
                f"geometry {index} is in {_label(crs)}, but the transformation "
                f"is from {_label(source)}"
            )


def _apply(
    transformation: Transformation,
    geometries: ObjectArray,
    coordinate_epoch: float | None,
) -> tuple[ObjectArray, ArrayTransformationResult]:
    """Transform every coordinate, one call per number of values per position."""
    units, origins = _split(geometries)
    moved = units.copy()
    provenance: ArrayTransformationResult | None = None
    for has_z, has_m, members in _groups(units):
        coordinates, owners = shapely.get_coordinates(
            units[members], include_z=has_z, include_m=has_m, return_index=True
        )
        if not len(coordinates):
            continue
        width = 3 if has_z else 2
        columns, result = _transform_rows(
            transformation,
            np.ascontiguousarray(coordinates[:, :width]),
            coordinate_epoch,
            units,
            members[owners],
            origins,
        )
        if len(columns) != width:
            raise GeometryError(
                f"{_label(transformation.target_crs)} gives {len(columns)} values "
                f"per position for {width} given, which is not a geometry"
            )
        replaced = coordinates.copy()
        replaced[:, :width] = np.column_stack(columns)
        moved[members] = _replace(units[members], replaced, has_z, has_m)
        provenance = provenance or result
    if provenance is None:
        provenance = _provenance(transformation, coordinate_epoch)
    return shapely.set_srid(_join(geometries, moved, origins), 0), provenance


def _provenance(
    transformation: Transformation, coordinate_epoch: float | None
) -> ArrayTransformationResult:
    """The provenance of geometries without a position to transform.

    That of an empty batch of positions of two values or of three, whichever
    the transformation takes, the source CRS's own number first: a target with
    a height refuses positions of two values, but there are none to refuse.

    Raises:
        What an empty batch of the source CRS's own number of values raises,
        if neither is taken; a missing epoch, say, at once.
    """
    dimension = transformation.source_crs.dimension
    natural = 3 if dimension == 1 else dimension
    errors: list[Exception] = []
    for width in (natural, 2 if natural == 3 else 3):
        try:
            return probe(transformation, width, coordinate_epoch)
        except (TransformationFailedError, ValueError) as error:
            errors.append(error)
    raise errors[0]


def _transform_rows(
    transformation: Transformation,
    rows: FloatArray,
    coordinate_epoch: float | None,
    geometries: ObjectArray,
    owners: NDArray[np.intp],
    origins: _Origins | None = None,
) -> tuple[tuple[FloatArray, ...], ArrayTransformationResult]:
    """Transform positions, naming the geometry, part and vertex that fails."""
    try:
        if len(rows) <= _CHUNK:
            result = transformation.transform_arrays(
                rows, coordinate_epoch=coordinate_epoch
            )
            return result.columns, result
        results = list(
            transformation.transform_batches(
                (rows[start : start + _CHUNK] for start in range(0, len(rows), _CHUNK)),
                coordinate_epoch=coordinate_epoch,
            )
        )
        columns = tuple(
            np.concatenate([part.columns[axis] for part in results])
            for axis in range(len(results[0].columns))
        )
        return columns, results[0]
    except TransformationFailedError as error:
        row, failed = error.point, error
        if row is None or not 0 <= row < len(rows):
            failure = first_failure(transformation, rows, coordinate_epoch)
            if failure is None:
                raise
            row, failed = failure.index, failure.error
        raise relocated(failed, _locate(geometries, owners, row, origins)) from error
    except ValueError as error:
        if isinstance(error, GeodesyError):
            raise
        first = int(owners[0]) if origins is None else origins[int(owners[0])][0]
        raise ValueError(f"geometry {first}: {error}") from error


def _locate(
    geometries: ObjectArray,
    owners: NDArray[np.intp],
    row: int,
    origins: _Origins | None = None,
) -> str:
    """Where position ``row`` is: its geometry, part, ring and vertex."""
    owner = int(owners[row])
    local = row - int(np.searchsorted(owners, owner))
    steps = _steps(geometries[owner], local)
    index, path = (owner, ()) if origins is None else origins[owner]
    return ", ".join(
        [
            f"geometry {index}",
            *(f"part {number}" for number in path),
            *(f"{name} {n}" for name, n in steps),
        ]
    )


def _steps(geometry: shapely.Geometry, index: int) -> list[tuple[str, int]]:
    """The part, ring and vertex position ``index`` of ``geometry`` is."""
    if isinstance(geometry, shapely.Polygon):
        rings = [geometry.exterior, *geometry.interiors]
        for number, ring in enumerate(rings):
            count = shapely.get_num_coordinates(ring)
            if index < count:
                return [("ring", number), ("vertex", index)]
            index -= count
    elif isinstance(geometry, BaseMultipartGeometry):
        for number, part in enumerate(geometry.geoms):
            count = shapely.get_num_coordinates(part)
            if index < count:
                return [("part", number), *_steps(part, index)]
            index -= count
    return [("vertex", index)]


def _groups(
    geometries: ObjectArray,
) -> Iterator[tuple[bool, bool, NDArray[np.intp]]]:
    """Indices of the geometries with and without z and m, group by group."""
    present = np.array([geometry is not None for geometry in geometries], dtype=bool)
    with_z = shapely.has_z(geometries)
    with_m = shapely.has_m(geometries)
    for has_z in (False, True):
        for has_m in (False, True):
            members = np.flatnonzero(present & (with_z == has_z) & (with_m == has_m))
            if len(members):
                yield has_z, has_m, members


def _replace(
    geometries: ObjectArray, coordinates: FloatArray, has_z: bool, has_m: bool
) -> ObjectArray:
    """``geometries`` with new coordinates: x, y, then z and m as they have them."""
    if not has_m:
        replaced = geometries.copy()
        shapely.set_coordinates(replaced, coordinates)
        return replaced
    out: list[shapely.Geometry] = []
    start = 0
    for geometry in geometries:
        count = int(shapely.get_num_coordinates(geometry))
        wkb = shapely.to_wkb(geometry, flavor="iso", output_dimension=4, byte_order=1)
        rewritten = replace_coordinates(
            wkb, coordinates[start : start + count], has_z=has_z, has_m=has_m
        )
        out.append(shapely.from_wkb(rewritten))
        start += count
    return shapely.set_srid(_objects(out), shapely.get_srid(geometries))


def _map_coordinates(geometries: ObjectArray, change: Any) -> ObjectArray:
    """``geometries`` with ``change(coordinates, has_z, has_m)`` applied."""
    units, origins = _split(geometries)
    out = units.copy()
    for has_z, has_m, members in _groups(units):
        coordinates = shapely.get_coordinates(
            units[members], include_z=has_z, include_m=has_m
        )
        if len(coordinates):
            out[members] = _replace(
                units[members], change(coordinates, has_z, has_m), has_z, has_m
            )
    return _join(geometries, out, origins)


# For each unit _split gives: the geometry it belongs to, and its part path there.
type _Origins = list[tuple[int, tuple[int, ...]]]


def _uniform(geometry: shapely.Geometry | None) -> bool:
    """Whether every member of a collection has z and m alike, at any depth."""
    if not isinstance(geometry, BaseMultipartGeometry):
        return True
    parts = [part for part in geometry.geoms if not part.is_empty]
    kinds = {(bool(shapely.has_z(part)), bool(shapely.has_m(part))) for part in parts}
    return len(kinds) <= 1 and all(_uniform(part) for part in parts)


def _all_uniform(multipart: ObjectArray) -> bool:
    """:func:`_uniform` for every geometry of ``multipart``, all of them multipart.

    The members of a multipoint, multilinestring or multipolygon are not
    multipart, so theirs are compared in one pass; a geometry collection's
    members may be, and are walked.
    """
    collections = shapely.get_type_id(multipart) == 7
    parts, owners = shapely.get_parts(multipart[~collections], return_index=True)
    filled = ~shapely.is_empty(parts)
    parts, owners = parts[filled], owners[filled]
    key = 2 * shapely.has_z(parts).astype(np.intp) + shapely.has_m(parts)
    # A geometry's parts are consecutive, so alike when each is like the next.
    if (key[1:] != key[:-1])[owners[1:] == owners[:-1]].any():
        return False
    return all(_uniform(geometry) for geometry in multipart[collections])


def _units(
    geometry: shapely.Geometry, path: tuple[int, ...] = ()
) -> Iterator[tuple[tuple[int, ...], shapely.Geometry]]:
    if not isinstance(geometry, BaseMultipartGeometry) or _uniform(geometry):
        yield path, geometry
        return
    for number, part in enumerate(geometry.geoms):
        yield from _units(part, (*path, number))


def _split(geometries: ObjectArray) -> tuple[ObjectArray, _Origins | None]:
    """Geometries whose members differ in z or m, split into members that do not.

    GEOS lets a collection hold, say, a 2D point beside a 3D one, but a
    geometry's coordinates are read and written with one width throughout.
    """
    multipart = np.flatnonzero(shapely.get_type_id(geometries) >= 4)
    if _all_uniform(geometries[multipart]):
        return geometries, None
    units: list[shapely.Geometry | None] = []
    origins: _Origins = []
    for index, geometry in enumerate(geometries):
        for path, unit in _units(geometry) if geometry is not None else [((), None)]:
            units.append(unit)
            origins.append((index, path))
    return _objects(units), origins


def _join(
    geometries: ObjectArray, units: ObjectArray, origins: _Origins | None
) -> ObjectArray:
    """The geometries :func:`_split` split, rebuilt from their changed units."""
    if origins is None:
        return units
    parts = iter(units)

    def rebuilt(geometry: shapely.Geometry | None) -> shapely.Geometry | None:
        if not isinstance(geometry, BaseMultipartGeometry) or _uniform(geometry):
            return cast("shapely.Geometry | None", next(parts))
        members = [rebuilt(part) for part in geometry.geoms]
        return cast("shapely.Geometry", type(geometry)(members))  # type: ignore[call-arg]

    out = [rebuilt(geometry) for geometry in geometries]
    return shapely.set_srid(_objects(out), shapely.get_srid(geometries))


def _permute(
    geometries: ObjectArray, crs: CoordinateReferenceSystem, to_values: bool
) -> ObjectArray:
    """Reorder coordinates between a CRS's declared order and value order."""
    order = crs.value_axis_order

    def change(coordinates: FloatArray, has_z: bool, has_m: bool) -> FloatArray:
        width = 3 if has_z else 2
        count = min(width, len(order))
        moved = tuple(order[:count])
        if sorted(moved) != list(range(count)):
            raise ValueError(
                f"positions of {width} values cannot be put in the declared order "
                f"of {_label(crs)}, which has {len(order)} axes"
            )
        if not to_values:
            moved = tuple(int(axis) for axis in np.argsort(moved))
        columns = [*moved, *range(count, coordinates.shape[1])]
        return np.ascontiguousarray(coordinates[:, columns])

    return _map_coordinates(geometries, change)


def _densify(
    geometries: ObjectArray,
    length: float,
    segments: Segments,
    crs: CoordinateReferenceSystem,
) -> ObjectArray:
    """``geometries`` with vertices inserted along their segments.

    Straight segments too go through :func:`densify_rows`, as a GeoJSON
    document's do, so either gets the same vertices. GEOS's own segmentize
    buffers an invalid polygon into another geometry, and crashes on a
    multipart geometry with an empty part.
    """
    require_length(length)
    _require_choice("segments", segments, SEGMENTS)
    if shapely.has_m(geometries).any():
        raise GeometryError("geometries with M values cannot be densified")
    try:
        return _with_lines(
            geometries,
            lambda rows, parts: densify_rows(rows, parts, crs, length, segments)[:2],
        )
    except ValueError as error:
        raise GeometryError(str(error)) from error


def _with_lines(
    geometries: ObjectArray,
    change: Callable[[FloatArray, NDArray[np.intp]], tuple[FloatArray, Any]],
) -> ObjectArray:
    """``geometries`` with the positions of each line and ring changed by ``change``.

    ``change`` takes the positions of lines and rings, x, y and any z, and the
    part each is on, and gives them back, with positions inserted or not. The
    geometries of one type and dimension go through it together, as Shapely's
    ragged arrays; a linear ring, a geometry collection and a multipart
    geometry with an empty part, which Shapely cannot build back from one,
    one by one.
    """
    kinds = shapely.get_type_id(geometries)
    has_z = shapely.has_z(geometries)
    # Not None, which is -1, a point or a multipoint, or empty.
    lined = (kinds > 0) & (kinds != 4) & ~shapely.is_empty(geometries)
    ragged = lined & np.isin(kinds, _RAGGED)
    multipart = np.flatnonzero(ragged & (kinds >= 5))
    if len(multipart):
        parts, owners = shapely.get_parts(geometries[multipart], return_index=True)
        ragged[multipart[np.unique(owners[shapely.is_empty(parts)])]] = False
    out = geometries.copy()
    for kind in _RAGGED:
        for z in (False, True):
            members = np.flatnonzero(ragged & (kinds == kind) & (has_z == z))
            if len(members):
                out[members] = _changed_lines(geometries[members], z, change)
    rest = np.flatnonzero(lined & ~ragged)
    if len(rest):
        out[rest] = _changed_one_by_one(geometries[rest], change)
    return shapely.set_srid(out, shapely.get_srid(geometries))


def _changed_lines(
    geometries: ObjectArray,
    has_z: bool,
    change: Callable[[FloatArray, NDArray[np.intp]], tuple[FloatArray, Any]],
) -> ObjectArray:
    """:func:`_with_lines` for geometries of one ragged type and dimension."""
    kind, coordinates, offsets = shapely.to_ragged_array(geometries, include_z=has_z)
    lines = offsets[0]
    parts = np.repeat(np.arange(len(lines) - 1, dtype=np.intp), np.diff(lines))
    rows, changed_parts = change(np.ascontiguousarray(coordinates), parts)
    sizes = np.bincount(changed_parts, minlength=len(lines) - 1)
    starts = np.concatenate([[0], np.cumsum(sizes)])
    return shapely.from_ragged_array(kind, rows, (starts, *offsets[1:]))


def _changed_one_by_one(
    geometries: ObjectArray,
    change: Callable[[FloatArray, NDArray[np.intp]], tuple[FloatArray, Any]],
) -> ObjectArray:
    """:func:`_with_lines` taking each geometry apart and building it again."""
    lines = [line for geometry in geometries for line in _lines(geometry)]
    if not lines:
        return geometries
    array = _objects(lines)
    has_z = shapely.has_z(array)
    rows, parts = shapely.get_coordinates(array, include_z=True, return_index=True)
    changed, changed_parts = change(rows, parts)
    bounds = np.searchsorted(changed_parts, np.arange(len(lines) + 1))
    pieces = iter(
        [
            changed[bounds[n] : bounds[n + 1], : 3 if has_z[n] else 2]
            for n in range(len(lines))
        ]
    )
    return _objects([_rebuilt(geometry, pieces) for geometry in geometries])


def _lines(geometry: shapely.Geometry | None) -> Iterator[shapely.Geometry]:
    """The lines and rings of a geometry, in order, but for empty ones."""
    if geometry is None or shapely.is_empty(geometry):
        return
    if isinstance(geometry, shapely.LineString):
        yield geometry
    elif isinstance(geometry, shapely.Polygon):
        yield geometry.exterior
        yield from geometry.interiors
    elif isinstance(geometry, BaseMultipartGeometry):
        for part in geometry.geoms:
            yield from _lines(part)


def _rebuilt(
    geometry: shapely.Geometry | None, pieces: Iterator[FloatArray]
) -> shapely.Geometry | None:
    """``geometry`` with its lines and rings, in order, taken from ``pieces``."""
    if geometry is None or shapely.is_empty(geometry):
        return geometry
    if isinstance(geometry, shapely.LinearRing):
        return shapely.LinearRing(next(pieces))
    if isinstance(geometry, shapely.LineString):
        return shapely.LineString(next(pieces))
    if isinstance(geometry, shapely.Polygon):
        shell = next(pieces)
        return shapely.Polygon(shell, [next(pieces) for _ in geometry.interiors])
    if isinstance(geometry, BaseMultipartGeometry) and not isinstance(
        geometry, shapely.MultiPoint
    ):
        parts = [_rebuilt(part, pieces) for part in geometry.geoms]
        # Shapely's constructor functions take an empty part, as WKT has one;
        # its classes refuse it.
        if isinstance(geometry, shapely.MultiPolygon):
            return cast("shapely.Geometry", shapely.multipolygons(parts))
        if isinstance(geometry, shapely.MultiLineString):
            return cast("shapely.Geometry", shapely.multilinestrings(parts))
        return shapely.geometrycollections(parts)
    return geometry


def _wrap(
    moved: ObjectArray,
    geometries: ObjectArray,
    transformation: Transformation,
    coordinate_epoch: float | None,
    mode: Antimeridian,
) -> ObjectArray:
    """``moved`` cut at the target CRS's antimeridian where it crosses it, or unwrapped.

    ``geometries`` are the same geometries in the source CRS, which tell a
    segment that crosses the antimeridian from one going the long way round.
    """
    kinds = shapely.get_type_id(moved)
    lined = np.flatnonzero((kinds > 0) & (kinds != 4))
    if not len(lined):
        return moved
    bounds = longitude(transformation.target_crs)
    if bounds is None:
        return moved
    half, pole = bounds
    # Only a geometry that jumps half a turn somewhere can cross the antimeridian.
    rows, owner = shapely.get_coordinates(moved[lined], return_index=True)
    jumps = (owner[1:] == owner[:-1]) & (np.abs(np.diff(rows[:, 0])) > half)
    suspects = lined[np.unique(owner[1:][jumps])]
    if not len(suspects):
        return moved
    out = moved.copy()
    out[suspects] = _wrapped(
        moved[suspects],
        geometries[suspects],
        suspects,
        transformation,
        coordinate_epoch,
        mode,
        (half, pole),
    )
    return out


def _wrapped(
    moved: ObjectArray,
    geometries: ObjectArray,
    indices: NDArray[np.intp],
    transformation: Transformation,
    coordinate_epoch: float | None,
    mode: Antimeridian,
    extent: tuple[float, float],
) -> ObjectArray:
    """:func:`_wrap` for the geometries at ``indices``, which jump half a turn."""
    half, pole = extent
    lines, owners = [], []
    for index, geometry in enumerate(moved):
        for line in _lines(geometry):
            lines.append(line)
            owners.append(index)
    array = _objects(lines)
    rows, parts = shapely.get_coordinates(array, include_z=True, return_index=True)
    source = _objects([line for geometry in geometries for line in _lines(geometry)])
    source_rows = shapely.get_coordinates(source, include_z=True)
    crossing = crossings(
        rows[:, 0],
        parts,
        half,
        lambda at: _middles(transformation, source_rows, at, coordinate_epoch),
    )
    if not crossing.any():
        return moved
    turned = turns(rows[:, 0], parts, crossing)
    bounds = np.searchsorted(parts, np.arange(len(lines) + 1))
    has_z = shapely.has_z(array)
    per_line = [
        (
            rows[bounds[n] : bounds[n + 1], : 3 if has_z[n] else 2],
            turned[bounds[n] : bounds[n + 1]],
        )
        for n in range(len(lines))
    ]
    counts = np.bincount(np.array(owners), minlength=len(moved))
    out = moved.copy()
    start = 0
    for index, count in enumerate(counts.tolist()):
        mine = per_line[start : start + count]
        start += count
        if not any(line_turns.any() for _, line_turns in mine):
            continue
        if shapely.has_m(moved[index]):
            raise GeometryError(
                f"geometry {int(indices[index])} crosses the antimeridian of "
                f"{_label(transformation.target_crs)}, and has M values, which "
                "cutting or unwrapping it would lose; give antimeridian=None to "
                "keep it as transformed"
            )
        out[index] = cut(moved[index], iter(mine), mode, half, pole)
    return out


def _middles(
    transformation: Transformation,
    rows: FloatArray,
    at: NDArray[np.intp],
    coordinate_epoch: float | None,
) -> FloatArray:
    """The longitude the middle of each source segment from ``at`` goes to, or NaN."""
    middle = (rows[at] + rows[at + 1]) / 2
    lon = np.full(len(at), np.nan)
    flat = np.isnan(middle[:, 2])
    for mask, width in ((flat, 2), (~flat, 3)):
        if not mask.any():
            continue
        try:
            columns, _ = failing_rows(
                transformation,
                np.ascontiguousarray(middle[mask, :width]),
                coordinate_epoch,
            )
        except (GeodesyError, ValueError):
            continue
        lon[mask] = columns[0]
    return lon


def _round(
    geometries: ObjectArray,
    decimals: Decimals,
    grid_size: float | None,
    validate: Validate,
) -> ObjectArray:
    _require_choice("validate", validate, _VALIDATIONS)
    if decimals is None and grid_size is None:
        return geometries
    if decimals is not None and grid_size is not None:
        raise ValueError("give decimals or grid_size, not both")
    if grid_size is not None:
        if (
            not isinstance(grid_size, int | float)
            or not math.isfinite(grid_size)
            or grid_size <= 0
        ):
            raise ValueError(f"grid_size is a positive number, not {grid_size!r}")
        if shapely.has_m(geometries).any():
            raise GeometryError("geometries with M values cannot be snapped to a grid")
        return shapely.set_precision(geometries, grid_size, mode="valid_output")

    def change(coordinates: FloatArray, has_z: bool, has_m: bool) -> FloatArray:
        places = _geometry_decimals(decimals, has_z, has_m)
        rounded = coordinates.copy()
        for column, number in enumerate(places):
            if number is not None:
                rounded[:, column] = round_half_up(coordinates[:, column], number)
        return rounded

    before = shapely.is_valid(geometries)
    rounded = _map_coordinates(geometries, change)
    broken = np.flatnonzero(before & ~shapely.is_valid(rounded))
    if len(broken) and validate != "off":
        index = int(broken[0])
        reason = shapely.is_valid_reason(rounded[index])
        message = (
            f"rounding to {decimals!r} decimals made {len(broken)} geometr"
            f"{'y' if len(broken) == 1 else 'ies'} invalid, the first at index "
            f"{index}: {reason}; snap with grid_size to keep them valid"
        )
        if validate == "raise":
            raise GeometryError(message)
        warnings.warn(message, GeometryValidityWarning, skip_file_prefixes=(_PACKAGE,))
    return rounded


def _geometry_decimals(
    decimals: Decimals, has_z: bool, has_m: bool
) -> tuple[int | None, ...]:
    """Decimals per coordinate column: x, y, then z and m where present."""
    axes = [0, 1] + ([2] if has_z else []) + ([3] if has_m else [])
    if decimals is None or isinstance(decimals, int):
        return axis_decimals(decimals, len(axes))
    given = axis_decimals(decimals, min(len(decimals), 4)) if len(decimals) else ()
    places = []
    for axis in axes:
        if axis >= len(given):
            raise ValueError(
                f"decimals gives {len(given)} numbers, but the geometry has an "
                f"{_AXIS_NAMES[axis]} value; give one for it, or None to keep it"
            )
        places.append(given[axis])
    return tuple(places)


def _encode(
    geometries: ObjectArray,
    format: Format,
    *,
    crs: CoordinateReferenceSystem | None,
    decimals: Decimals = None,
    grid_size: float | None = None,
    axis_order: AxisOrder = "xy",
    byte_order: ByteOrder = "little",
    validate: Validate = "warn",
) -> list[Any]:
    _require_choice("format", format, FORMATS)
    _require_choice("axis_order", axis_order, _AXIS_ORDERS)
    _require_choice("byte_order", byte_order, tuple(_BYTE_ORDERS))
    geometries = _round(geometries, decimals, grid_size, validate)
    if axis_order == "crs":
        geometries = _permute(geometries, _require_crs(crs), False)
    order = _BYTE_ORDERS[byte_order]
    if format == "wkt":
        return list(_wkt(geometries))
    if format == "ewkt":
        srid = _srid(crs, format)
        return [
            None if text is None else f"SRID={srid};{text}" for text in _wkt(geometries)
        ]
    if format in ("wkb", "wkb_hex"):
        return list(
            shapely.to_wkb(
                geometries,
                hex=format == "wkb_hex",
                output_dimension=4,
                byte_order=order,
                flavor="iso",
            )
        )
    if format in ("ewkb", "ewkb_hex"):
        srid = _srid(crs, format)
        return list(
            shapely.to_wkb(
                shapely.set_srid(geometries, srid),
                hex=format == "ewkb_hex",
                output_dimension=4,
                byte_order=order,
                flavor="extended",
                include_srid=True,
            )
        )
    if format == "geojson":
        return [_geojson(geometry) for geometry in geometries]
    reference = None if crs is None else _esri_reference(crs)
    return [_esri(geometry, reference) for geometry in geometries]


def _wkt(geometries: ObjectArray) -> list[str | None]:
    """WKT with each number as its shortest round-trip form.

    GEOS's own writer gives at most 16 significant digits, so a value such as
    0.017453292519943295 would not read back as written.
    """
    has_z = shapely.has_z(geometries).tolist()
    has_m = shapely.has_m(geometries).tolist()
    return [
        None if geometry is None else _wkt_text(geometry, z, m)
        for geometry, z, m in zip(geometries, has_z, has_m, strict=True)
    ]


def _wkt_text(geometry: BaseGeometry, has_z: bool, has_m: bool) -> str:
    name = type(geometry).__name__.upper()
    tag = _tag(has_z, has_m)
    if _without_parts(geometry):
        return f"{name}{tag} EMPTY"
    return f"{name}{tag} {_wkt_body(geometry, has_z, has_m)}"


def _without_parts(geometry: BaseGeometry) -> bool:
    """Whether WKT writes ``geometry`` as EMPTY: a collection only without members."""
    if isinstance(geometry, BaseMultipartGeometry):
        return len(geometry.geoms) == 0
    return bool(geometry.is_empty)


def _wkt_body(geometry: BaseGeometry, has_z: bool, has_m: bool) -> str:
    if _without_parts(geometry):
        return "EMPTY"
    if isinstance(geometry, shapely.GeometryCollection) and not isinstance(
        geometry, shapely.MultiPoint | shapely.MultiLineString | shapely.MultiPolygon
    ):
        members = (
            _wkt_text(part, bool(shapely.has_z(part)), bool(shapely.has_m(part)))
            for part in geometry.geoms
        )
        return f"({', '.join(members)})"
    if isinstance(geometry, BaseMultipartGeometry):
        parts = (_wkt_body(part, has_z, has_m) for part in geometry.geoms)
        return f"({', '.join(parts)})"
    if isinstance(geometry, shapely.Polygon):
        rings = [geometry.exterior, *geometry.interiors]
        return f"({', '.join(_wkt_body(ring, has_z, has_m) for ring in rings)})"
    coordinates = shapely.get_coordinates(geometry, include_z=has_z, include_m=has_m)
    return f"({_positions_text(coordinates)})"


def _positions_text(coordinates: FloatArray) -> str:
    """Positions as WKT writes them, each number as :func:`repr` gives it.

    Formatted all at once and trimmed after: ``1.0`` as ``1``, ``1e+20`` as
    ``1e20`` and ``1.5e-05`` as ``1.5e-5``.
    """
    values = iter(map(repr, coordinates.ravel().tolist()))
    text = ", ".join(map(" ".join, zip(*[values] * coordinates.shape[1], strict=True)))
    if "e" in text:
        text = _EXPONENT.sub(r"e\1\2", text)
    return _WHOLE.sub("", text)


def _srid(crs: CoordinateReferenceSystem | None, format: str) -> int:
    """The SRID to write: the CRS's integer code in its own authority."""
    if crs is None:
        raise GeometryError(f"{format} states an SRID; give the crs to take it from")
    code = integer_code(crs)
    if code is None:
        raise GeometryError(
            f"{_label(crs)} has no integer code to write as an SRID; write wkt "
            "or wkb, which state none"
        )
    return code[1]


def _read(
    value: object, format: Format | None, srids: _Srids
) -> tuple[shapely.Geometry | None, CoordinateReferenceSystem | None]:
    """A geometry and the CRS it states, read from ``value``."""
    if value is None:
        return None, None
    if format is None:
        return _detect(value, srids)
    _require_choice("format", format, FORMATS)
    if format in ("wkt", "ewkt"):
        text = _text(value, format)
        match = _EWKT.match(text)
        if match is not None:
            if format == "wkt":
                raise GeometryError(
                    "the text starts with an SRID, which makes it EWKT; read "
                    "it with format='ewkt'"
                )
            return _from_ewkt(match, srids)
        if format == "ewkt":
            raise GeometryError("EWKT starts with SRID=<code>;")
        return _from_wkt(text), None
    if format in ("wkb", "ewkb"):
        if not isinstance(value, bytes | bytearray | memoryview):
            raise TypeError(f"{format} is bytes, not {type(value).__name__}")
        return _from_wkb(bytes(value), srids)
    if format in ("wkb_hex", "ewkb_hex"):
        text = _text(value, format)
        if not _HEX.match(text) or len(text) % 2:
            raise GeometryError(f"{format} is an even number of hex digits")
        return _from_wkb(text, srids)
    data = _json(value) if isinstance(value, str | bytes | bytearray) else value
    if not isinstance(data, Mapping):
        raise TypeError(f"{format} is a JSON object, not {type(data).__name__}")
    if format == "geojson":
        return _from_geojson(data), None
    return _from_esri(data)


def _detect(
    value: object, srids: _Srids
) -> tuple[shapely.Geometry | None, CoordinateReferenceSystem | None]:
    if isinstance(value, shapely.Geometry):
        return value, srids(int(shapely.get_srid(value)))
    if isinstance(value, bytes | bytearray | memoryview):
        return _from_wkb(bytes(value), srids)
    if isinstance(value, str):
        text = value.strip()
        if (match := _EWKT.match(text)) is not None:
            return _from_ewkt(match, srids)
        if text.startswith("{"):
            return _from_json(_json(text))
        if _HEX.match(text) and not len(text) % 2:
            return _from_wkb(text, srids)
        return _from_wkt(text), None
    if isinstance(value, Mapping):
        return _from_json(value)
    interface = getattr(value, "__geo_interface__", None)
    if isinstance(interface, Mapping):
        return _from_geojson(interface), None
    raise TypeError(
        "a geometry is WKT, WKB, GeoJSON, Esri JSON, a Shapely geometry or "
        f"has a __geo_interface__, not {type(value).__name__}"
    )


def _from_json(
    data: Mapping[str, Any],
) -> tuple[shapely.Geometry | None, CoordinateReferenceSystem | None]:
    if "type" in data:
        return _from_geojson(data), None
    if any(key in data for key in _ESRI_KEYS):
        return _from_esri(data)
    raise GeometryError(
        "a JSON geometry is a GeoJSON geometry object, with a type, or an Esri "
        "JSON geometry, with x, points, paths or rings"
    )


def _from_wkt(text: str) -> shapely.Geometry:
    try:
        return shapely.from_wkt(text)
    except NotImplementedError as error:
        raise GeometryError(f"curved geometries are not supported: {error}") from error
    except ShapelyError as error:
        raise GeometryError(f"not WKT: {error}") from error


def _from_wkb(
    data: bytes | str, srids: _Srids
) -> tuple[shapely.Geometry, CoordinateReferenceSystem | None]:
    try:
        geometry = shapely.from_wkb(data)
    except NotImplementedError as error:
        raise GeometryError(f"curved geometries are not supported: {error}") from error
    except ShapelyError as error:
        raise GeometryError(f"not WKB: {error}") from error
    return geometry, srids(int(shapely.get_srid(geometry)))


def _from_ewkt(
    match: re.Match[str], srids: _Srids
) -> tuple[shapely.Geometry, CoordinateReferenceSystem | None]:
    srid = int(match.group(1))
    if srid < 0:
        raise GeometryError(f"an SRID is not negative: {srid}")
    return _from_wkt(match.group(2)), srids(srid)


class _Srids:
    """The CRS each SRID names in an authority, each SRID read once.

    The geometries of a batch commonly all state the same SRID, and reading
    it costs a look at the files of the active database each time.
    """

    def __init__(self, authority: str) -> None:
        self._authority = authority
        self._crss: dict[int, CoordinateReferenceSystem | None] = {}

    def __call__(self, srid: int) -> CoordinateReferenceSystem | None:
        if srid not in self._crss:
            self._crss[srid] = _srid_crs(srid, self._authority)
        return self._crss[srid]


def _srid_crs(srid: int, authority: str) -> CoordinateReferenceSystem | None:
    """The CRS an SRID names in ``authority``; None for 0, which is unknown."""
    if srid == 0:
        return None
    if not isinstance(authority, str) or not authority.strip():
        raise ValueError(f"srid_authority is an authority name, not {authority!r}")
    return CoordinateReferenceSystem.from_user_input(f"{authority.strip()}:{srid}")


def _from_geojson(data: Mapping[str, Any]) -> shapely.Geometry:
    kind = data.get("type")
    if kind not in _GEOJSON_TYPES:
        hint = ""
        if isinstance(kind, str) and (
            kind.startswith("AnyCrs") or kind in ("Feature", "FeatureCollection")
        ):
            hint = "; convert documents with geodetic_engine.geojson.convert_geojson"
        raise GeometryError(f"{kind!r} is not a GeoJSON geometry type{hint}")
    try:
        return shapely.geometry.shape(dict(data))
    except (ShapelyError, ValueError, TypeError, KeyError, IndexError) as error:
        raise GeometryError(f"not a GeoJSON geometry: {error}") from error


def _geojson(geometry: shapely.Geometry | None) -> dict[str, Any] | None:
    if geometry is None:
        return None
    if shapely.has_m(geometry):
        raise GeometryError(
            "GeoJSON positions have no place for M values; write wkt or wkb"
        )
    interface = cast("BaseGeometry", geometry).__geo_interface__
    return cast("dict[str, Any]", _lists(interface))


def _lists(value: Any) -> Any:
    """``value`` with tuples as lists and a LinearRing as a LineString."""
    kind = type(value)
    if kind is tuple or kind is list:
        # A coordinate is a float, read as it is: most values are.
        return [item if type(item) is float else _lists(item) for item in value]
    if isinstance(value, Mapping):
        out = {key: _lists(item) for key, item in value.items()}
        if out.get("type") == "LinearRing":
            out["type"] = "LineString"
        return out
    if isinstance(value, tuple | list):
        return [_lists(item) for item in value]
    return value


def _from_esri(
    data: Mapping[str, Any],
) -> tuple[shapely.Geometry, CoordinateReferenceSystem | None]:
    crs = _spatial_reference(data.get("spatialReference"))
    if "curvePaths" in data or "curveRings" in data:
        raise GeometryError("curved geometries are not supported")
    has_z = bool(data.get("hasZ"))
    has_m = bool(data.get("hasM"))
    if "x" in data:
        return _esri_point(data), crs
    tag = _tag(has_z, has_m)
    width = 2 + has_z + has_m
    if "points" in data:
        points = [_esri_position(item, width) for item in _array(data["points"])]
        body = ", ".join(f"({position})" for position in points)
        return _from_wkt(f"MULTIPOINT{tag} {_body(body)}"), crs
    if "paths" in data:
        paths = [_esri_path(path, width) for path in _array(data["paths"])]
        if len(paths) <= 1:
            body = paths[0] if paths else "EMPTY"
            return _from_wkt(f"LINESTRING{tag} {body}"), crs
        return _from_wkt(f"MULTILINESTRING{tag} {_body(', '.join(paths))}"), crs
    if "rings" in data:
        return _esri_polygon(_array(data["rings"]), width, tag), crs
    if "xmin" in data:
        xmin, ymin, xmax, ymax = (
            _number(data.get(key)) for key in ("xmin", "ymin", "xmax", "ymax")
        )
        return shapely.box(xmin, ymin, xmax, ymax), crs
    raise GeometryError("an Esri JSON geometry has x, points, paths or rings")


def _esri_point(data: Mapping[str, Any]) -> shapely.Geometry:
    has_z = data.get("z") is not None
    has_m = data.get("m") is not None
    tag = _tag(has_z, has_m)
    x = data.get("x")
    if x is None or x == "NaN":
        return _from_wkt(f"POINT{tag} EMPTY")
    values = [x, data.get("y")]
    if has_z:
        values.append(data["z"])
    if has_m:
        values.append(data["m"])
    return _from_wkt(f"POINT{tag} ({' '.join(repr(_number(v)) for v in values)})")


def _esri_polygon(rings: list[Any], width: int, tag: str) -> shapely.Geometry:
    """Polygons from Esri rings: clockwise exteriors, holes inside one of them."""
    texts = [_esri_path(ring, width) for ring in rings]
    if not texts:
        return _from_wkt(f"POLYGON{tag} EMPTY")
    shapes = []
    for number, text in enumerate(texts):
        line = _from_wkt(f"LINESTRING{tag} {text}")
        try:
            shapes.append(shapely.Polygon(shapely.get_coordinates(line)))
        except (ShapelyError, ValueError) as error:
            raise GeometryError(f"ring {number} is not a ring: {error}") from error
    exteriors = [n for n, shape in enumerate(shapes) if not shape.exterior.is_ccw]
    holes: dict[int, list[int]] = {n: [] for n in exteriors}
    for number, shape in enumerate(shapes):
        if number in holes:
            continue
        inside = [
            outer
            for outer in exteriors
            if shapes[outer].contains(shapely.Point(shape.exterior.coords[0]))
            or shapes[outer].covers(shape)
        ]
        if not inside:
            raise GeometryError(
                f"ring {number} runs counterclockwise, which makes it a hole, but "
                "it is inside no clockwise ring"
            )
        holes[min(inside, key=lambda outer: shapes[outer].area)].append(number)
    polygons = [
        "(" + ", ".join(texts[n] for n in (outer, *holes[outer])) + ")"
        for outer in exteriors
    ]
    if len(polygons) == 1:
        return _from_wkt(f"POLYGON{tag} {polygons[0]}")
    return _from_wkt(f"MULTIPOLYGON{tag} ({', '.join(polygons)})")


def _esri_path(path: object, width: int) -> str:
    positions = [_esri_position(item, width) for item in _array(path)]
    return _body(", ".join(positions))


def _esri_position(item: object, width: int) -> str:
    values = _array(item)
    if len(values) != width:
        raise GeometryError(
            f"a position of this Esri JSON geometry has {width} values, as its "
            f"hasZ and hasM say, not {len(values)}"
        )
    return " ".join(repr(_number(value)) for value in values)


def _spatial_reference(value: object) -> CoordinateReferenceSystem | None:
    """The CRS an Esri ``spatialReference`` names, by WKT or by WKID."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise GeometryError(f"a spatialReference is a JSON object, not {value!r}")
    if isinstance(value.get("wkt"), str):
        horizontal = CoordinateReferenceSystem.from_user_input(value["wkt"])
    else:
        wkid = value.get("latestWkid", value.get("wkid"))
        if wkid is None:
            return None
        horizontal = _wkid(wkid)
    vertical = value.get("latestVcsWkid", value.get("vcsWkid"))
    if vertical is None:
        return horizontal
    return compound(horizontal, _wkid(vertical))


def _wkid(wkid: object) -> CoordinateReferenceSystem:
    """The CRS of an Esri WKID, which counts EPSG codes and Esri's own."""
    if not isinstance(wkid, int) or isinstance(wkid, bool):
        raise GeometryError(f"a WKID is an integer, not {wkid!r}")
    try:
        return CoordinateReferenceSystem.from_user_input(f"EPSG:{wkid}")
    except UnresolvableCRSError:
        try:
            return CoordinateReferenceSystem.from_user_input(f"ESRI:{wkid}")
        except UnresolvableCRSError as error:
            raise UnresolvableCRSError(
                f"WKID {wkid} is neither EPSG:{wkid} nor ESRI:{wkid} in the "
                "active proj.db"
            ) from error


def _esri_reference(crs: CoordinateReferenceSystem) -> dict[str, Any]:
    """An Esri ``spatialReference`` for ``crs``: its WKID, else its WKT."""
    code = integer_code(crs)
    if code is not None and code[0] in ("EPSG", "ESRI"):
        return {"wkid": code[1]}
    if crs.crs.is_compound and len(crs.crs.sub_crs_list) == 2:
        parts = [
            integer_code(CoordinateReferenceSystem(part, part.name))
            for part in crs.crs.sub_crs_list
        ]
        if all(part is not None and part[0] in ("EPSG", "ESRI") for part in parts):
            horizontal, vertical = cast("list[tuple[str, int]]", parts)
            return {"wkid": horizontal[1], "vcsWkid": vertical[1]}
    text = crs.crs.to_wkt("WKT1_ESRI")
    if not text:
        raise GeometryError(
            f"{_label(crs)} has neither a WKID nor an Esri WKT to write as a "
            "spatialReference"
        )
    return {"wkt": text}


def _esri(
    geometry: shapely.Geometry | None, reference: dict[str, Any] | None
) -> dict[str, Any] | None:
    if geometry is None:
        return None
    has_z = bool(shapely.has_z(geometry))
    has_m = bool(shapely.has_m(geometry))
    out: dict[str, Any]
    if isinstance(geometry, shapely.Point):
        if geometry.is_empty:
            out = {"x": None}
        else:
            values = _positions(geometry, has_z, has_m)[0]
            out = {"x": values[0], "y": values[1]}
            if has_z:
                out["z"] = values[2]
            if has_m:
                out["m"] = values[-1]
    else:
        out = _flags(has_z, has_m)
        if isinstance(geometry, shapely.MultiPoint):
            out["points"] = _positions(geometry, has_z, has_m)
        elif isinstance(geometry, shapely.LineString | shapely.LinearRing):
            out["paths"] = (
                [] if geometry.is_empty else [_positions(geometry, has_z, has_m)]
            )
        elif isinstance(geometry, shapely.MultiLineString):
            out["paths"] = [_positions(part, has_z, has_m) for part in geometry.geoms]
        elif isinstance(geometry, shapely.Polygon | shapely.MultiPolygon):
            polygons = (
                geometry.geoms
                if isinstance(geometry, shapely.MultiPolygon)
                else [geometry]
            )
            out["rings"] = [
                ring
                for polygon in polygons
                if not polygon.is_empty
                for ring in _esri_rings(polygon, has_z, has_m)
            ]
        else:
            raise GeometryError(
                f"Esri JSON has no {type(geometry).__name__}; write its parts "
                "one by one"
            )
    if reference is not None:
        out["spatialReference"] = dict(reference)
    return out


def _esri_rings(
    polygon: shapely.Polygon, has_z: bool, has_m: bool
) -> list[list[list[float]]]:
    """A polygon's rings as Esri JSON requires: exterior clockwise, holes not."""
    rings = []
    for number, ring in enumerate([polygon.exterior, *polygon.interiors]):
        positions = _positions(ring, has_z, has_m)
        if ring.is_ccw == (number == 0):
            positions.reverse()
        rings.append(positions)
    return rings


def _positions(
    geometry: shapely.Geometry, has_z: bool, has_m: bool
) -> list[list[float]]:
    coordinates = shapely.get_coordinates(geometry, include_z=has_z, include_m=has_m)
    return cast("list[list[float]]", coordinates.tolist())


def _flags(has_z: bool, has_m: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if has_z:
        out["hasZ"] = True
    if has_m:
        out["hasM"] = True
    return out


def _tag(has_z: bool, has_m: bool) -> str:
    return {(False, False): "", (True, False): " Z", (False, True): " M"}.get(
        (has_z, has_m), " ZM"
    )


def _body(text: str) -> str:
    return f"({text})" if text else "EMPTY"


def _array(value: object) -> list[Any]:
    if not isinstance(value, list | tuple):
        raise GeometryError(f"expected a JSON array, not {value!r}")
    return list(value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise GeometryError(f"{value!r} is not a number")
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"{number!r} is not a finite number")
    return number


def _text(value: object, format: str) -> str:
    if isinstance(value, bytes | bytearray):
        value = bytes(value).decode("ascii", errors="strict")
    if not isinstance(value, str):
        raise TypeError(f"{format} is text, not {type(value).__name__}")
    return value.strip()


def _json(value: str | bytes | bytearray) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GeometryError(f"not JSON: {error}") from error


def _objects(items: Sequence[Any]) -> ObjectArray:
    """A one-dimensional object array holding ``items`` as they are."""
    array = np.empty(len(items), dtype=object)
    for index, item in enumerate(items):
        if item is not None and not isinstance(item, shapely.Geometry):
            raise TypeError(
                f"a geometry is a Shapely geometry, not {type(item).__name__}"
            )
        array[index] = item
    return array


def _require_crs(crs: CoordinateReferenceSystem | None) -> CoordinateReferenceSystem:
    if crs is None:
        raise ValueError("axis_order='crs' needs the CRS whose declared order it is")
    return crs


def _require_choice(name: str, value: object, choices: Sequence[str]) -> None:
    if value not in choices:
        raise ValueError(f"{name} must be one of {tuple(choices)}, not {value!r}")


def _label(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or repr(crs.name)
