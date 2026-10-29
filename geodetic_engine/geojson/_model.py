"""Read a GeoJSON document: its structure, its geometries and their positions.

Private to :mod:`geodetic_engine.geojson`. A document is read once, into a copy
that a conversion then writes into, so the caller's input is never modified.
Reading collects everything a conversion needs: each geometry's positions as
one ``float64`` array, in document order, with the nesting needed to write
them back; the objects that carry a ``bbox``, and the geometries each covers;
and the objects whose ``type`` is renamed between flavours. Every other member
is copied unchanged and in its place, so ``id``, ``properties`` and foreign
members survive, and so does key order.

Two flavours are read: RFC 7946 GeoJSON, whose positions are WGS 84 longitude
and latitude, and OSDU AnyCrs GeoJSON, whose types are prefixed ``AnyCrs`` and
whose collection states the CRS. A document is one flavour throughout, as the
OSDU schema requires, because a plain object in an AnyCrs document, or the
reverse, leaves the CRS of its positions a guess.

The reading is strict where a conversion would otherwise produce something
wrong without saying so:

* a position is two or three finite numbers, and a boolean is not a number;
* a line has at least two positions, and a linear ring at least four, the last
  equal to the first (RFC 7946, section 3.1);
* all positions of one geometry have as many values;
* what this package cannot convert yet is refused rather than copied
  unconverted: JSON-FG ``coordRefSys``, ``place``, ``measures`` and geometry
  types, positions with a fourth value, and CRS members anywhere but the root.

It is lenient where nothing is at stake: a feature without ``properties`` or
``geometry`` is read as it is, and an empty ``coordinates`` array is an empty
geometry, which RFC 7946 allows.
"""

from __future__ import annotations

import copy
import json
import math
import numbers
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, cast

import numpy as np
from numpy.typing import NDArray

from geodetic_engine.geojson.errors import (
    InvalidGeoJsonError,
    UnsupportedGeoJsonError,
)

type FloatArray = NDArray[np.float64]
type Position = list[float]
# How a geometry's positions nest in its coordinates: None for a point's single
# position, the number of positions in an array of them, or a tuple of those
# for an array of arrays. An empty tuple is an empty coordinates array.
type Shape = int | tuple[Shape, ...] | None

ANYCRS_PREFIX: Final = "AnyCrs"
ANYCRS_MEMBERS: Final = (
    "CoordinateReferenceSystemID",
    "persistableReferenceCrs",
    "VerticalCoordinateReferenceSystemID",
    "persistableReferenceVerticalCrs",
    "VerticalUnitID",
    "persistableReferenceUnitZ",
)
"""The members an OSDU AnyCrs feature collection states its CRS and units in."""
LEGACY_CRS: Final = "crs"
"""The GeoJSON 2008 member that named a CRS, removed by RFC 7946."""

_JSONFG_CONFORMANCE: Final = (
    "http://www.opengis.net/spec/json-fg-1/",
    "https://www.opengis.net/spec/json-fg-1/",
)
_JSONFG_TYPES: Final = frozenset(
    {
        "CircularString",
        "CompoundCurve",
        "CurvePolygon",
        "MultiCurve",
        "MultiSurface",
        "Polyhedron",
        "MultiPolyhedron",
        "Prism",
        "MultiPrism",
    }
)
_EMPTY: Final = np.empty((0, 0), dtype=np.float64)
_SHOWN = 40


class Flavour(StrEnum):
    """Which family of ``type`` names a document is written in."""

    GEOJSON = "geojson"
    """RFC 7946: ``Point``, ``Feature``, ``FeatureCollection``, ..."""
    ANYCRS = "anycrs"
    """OSDU AnyCrs: ``AnyCrsPoint``, ``AnyCrsFeature``, ..."""


class Kind(StrEnum):
    """What a GeoJSON object is, whatever its flavour."""

    POINT = "Point"
    MULTI_POINT = "MultiPoint"
    LINE_STRING = "LineString"
    MULTI_LINE_STRING = "MultiLineString"
    POLYGON = "Polygon"
    MULTI_POLYGON = "MultiPolygon"
    GEOMETRY_COLLECTION = "GeometryCollection"
    FEATURE = "Feature"
    FEATURE_COLLECTION = "FeatureCollection"

    def name_in(self, flavour: Flavour) -> str:
        """The ``type`` this object has in ``flavour``."""
        return ANYCRS_PREFIX + self.value if flavour is Flavour.ANYCRS else self.value


_GEOMETRIES: Final = frozenset(Kind) - {Kind.FEATURE, Kind.FEATURE_COLLECTION}
_FLAVOUR_NAMES: Final = {
    Flavour.GEOJSON: "a plain GeoJSON document",
    Flavour.ANYCRS: "an AnyCrs GeoJSON document",
}
# Arrays of positions that coordinates nest down to, and what each array is.
_DEPTHS: Final = {
    Kind.POINT: 0,
    Kind.MULTI_POINT: 1,
    Kind.LINE_STRING: 1,
    Kind.MULTI_LINE_STRING: 2,
    Kind.POLYGON: 2,
    Kind.MULTI_POLYGON: 3,
}
_LINES: Final = frozenset({Kind.LINE_STRING, Kind.MULTI_LINE_STRING})
_RINGS: Final = frozenset({Kind.POLYGON, Kind.MULTI_POLYGON})


@dataclass(frozen=True, slots=True, eq=False)
class Geometry:
    """One geometry with coordinates, as it sits in the document being written.

    Attributes:
        path: JSON path of the geometry object.
        kind: What it is. Never :attr:`Kind.GEOMETRY_COLLECTION`, whose members
            are geometries of their own.
        target: The geometry object in the document being written.
        positions: Its positions, one row each in document order, with one
            column per value. Shape ``(0, 0)`` when it is empty.
        shape: How the positions nest in ``coordinates``.
        feature: Index into :attr:`Document.features` of the feature holding
            it, directly or in a geometry collection. None when the document
            is a geometry.
    """

    path: str
    kind: Kind
    target: dict[str, Any]
    positions: FloatArray
    shape: Shape
    feature: int | None

    @property
    def count(self) -> int:
        """Number of positions."""
        return int(self.positions.shape[0])

    @property
    def dimension(self) -> int:
        """Values per position: 2 or 3, or 0 for an empty geometry."""
        return int(self.positions.shape[1]) if self.count else 0

    def position_path(self, index: int) -> str:
        """JSON path of position ``index``, counted in document order."""
        if not 0 <= index < self.count:
            raise IndexError(f"{self.path} has no position {index}")
        nesting = "".join(f"[{number}]" for number in _locate(self.shape, index))
        return f"{self.path}.coordinates{nesting}"

    def write(self, rows: Sequence[Position]) -> None:
        """Replace the coordinates with ``rows``, one per position, nested as read.

        Every row is a position: GeoJSON has no null position. A feature whose
        positions cannot all be written is withdrawn instead, with
        :meth:`Document.withdraw`.
        """
        if len(rows) != self.count:
            raise ValueError(
                f"{self.path} has {self.count} positions, but {len(rows)} were given"
            )
        for index, row in enumerate(rows):
            if row is None:
                raise ValueError(f"{self.position_path(index)} cannot be null")
        self.target["coordinates"] = _nested(rows, self.shape, 0)[0]


@dataclass(frozen=True, slots=True, eq=False)
class Feature:
    """A feature, as it sits in the document being written.

    Attributes:
        path: JSON path of the feature.
        target: The feature in the document being written.
        geometries: Indices into :attr:`Document.geometries` of the geometries
            in it, consecutive as those of a :class:`BoundingBox` are.
    """

    path: str
    target: dict[str, Any]
    geometries: range


@dataclass(frozen=True, slots=True, eq=False)
class BoundingBox:
    """An object that carries a ``bbox``.

    Attributes:
        path: JSON path of the object.
        target: The object in the document being written.
        geometries: Indices into :attr:`Document.geometries` of the geometries
            it contains. They are consecutive, since geometries are collected
            in document order.
    """

    path: str
    target: dict[str, Any]
    geometries: range


@dataclass(frozen=True, slots=True, eq=False)
class Document:
    """A GeoJSON document, read into a copy to be written into.

    Attributes:
        root: The document being written. Until a conversion writes into it,
            it is the input with numbers as floats and arrays as lists.
        kind: What the root is.
        flavour: The flavour of every ``type`` in it.
        geometries: Every geometry with coordinates, in document order,
            including those inside geometry collections.
        features: Every feature, in document order.
        bounding_boxes: Every object carrying a ``bbox``, each listed after
            those it contains.
        typed: Every object with a ``type``, and what it is.
    """

    root: dict[str, Any]
    kind: Kind
    flavour: Flavour
    geometries: tuple[Geometry, ...]
    features: tuple[Feature, ...]
    bounding_boxes: tuple[BoundingBox, ...]
    typed: tuple[tuple[dict[str, Any], Kind], ...]
    _withdrawn: set[int] = field(default_factory=set, init=False, repr=False)

    @property
    def count(self) -> int:
        """Number of positions in the document."""
        return sum(geometry.count for geometry in self.geometries)

    @property
    def dimensions(self) -> frozenset[int]:
        """The numbers of values its positions have: 2, 3 or both."""
        return frozenset(g.dimension for g in self.geometries if g.count)

    @property
    def withdrawn(self) -> frozenset[int]:
        """Indices into :attr:`geometries` of those withdrawn with their feature."""
        return frozenset(self._withdrawn)

    def retype(self, flavour: Flavour) -> None:
        """Rename every ``type`` to its name in ``flavour``."""
        for target, kind in self.typed:
            target["type"] = kind.name_in(flavour)

    def withdraw(self, feature: Feature) -> None:
        """Write ``feature`` with a null geometry, as when a position fails.

        A position cannot be null in GeoJSON, and a geometry without one of its
        positions is another geometry, so the feature loses all of it: RFC 7946
        allows a feature without a geometry. Its ``bbox`` goes too, as there
        is nothing left to bound. A ``bbox`` around it is to be computed
        without the geometries :attr:`withdrawn` lists, and one bounding only
        those is not to be written.
        """
        feature.target["geometry"] = None
        feature.target.pop("bbox", None)
        self._withdrawn.update(feature.geometries)


def read_document(value: object) -> Document:
    """Read a GeoJSON document into a copy to be written into.

    Args:
        value: A mapping, JSON text as ``str`` or ``bytes``, or an object with
            a ``__geo_interface__``. Its root is a ``FeatureCollection``, a
            ``Feature`` or a geometry, plain or AnyCrs.

    Raises:
        TypeError: If ``value`` is none of these.
        InvalidGeoJsonError: If the document is not GeoJSON as described in
            the module docstring, or nests too deeply to be read.
        UnsupportedGeoJsonError: If it states something not converted yet.
    """
    try:
        return _read(value)
    except RecursionError:
        raise InvalidGeoJsonError("$", "nests too deeply to be read") from None


def _read(value: object) -> Document:
    data, owned = _load(value)
    kind, flavour = _type(data, "$")
    reader = _Reader(flavour, owned=owned)
    if kind is Kind.FEATURE_COLLECTION:
        root = reader.collection(data, "$")
    elif kind is Kind.FEATURE:
        root = reader.feature(data, "$")
    else:
        root = reader.geometry(data, "$", kind)
    return Document(
        root=root,
        kind=kind,
        flavour=flavour,
        geometries=tuple(reader.geometries),
        features=tuple(reader.features),
        bounding_boxes=tuple(reader.bounding_boxes),
        typed=tuple(reader.typed),
    )


class _Reader:
    """Reads one document, collecting its geometries as it goes."""

    def __init__(self, flavour: Flavour, *, owned: bool) -> None:
        self._flavour = flavour
        # Parsed from JSON text here, so its member values need no copy.
        self._owned = owned
        # The feature being read, which holds the geometries read meanwhile.
        self._feature: int | None = None
        self.geometries: list[Geometry] = []
        self.features: list[Feature] = []
        self.bounding_boxes: list[BoundingBox] = []
        self.typed: list[tuple[dict[str, Any], Kind]] = []

    def collection(self, data: Mapping[str, Any], path: str) -> dict[str, Any]:
        if "features" not in data:
            raise InvalidGeoJsonError(path, "a FeatureCollection has no features")
        first = len(self.geometries)
        out: dict[str, Any] = {}
        for key, value in data.items():
            if key == "features":
                if not _is_array(value):
                    raise InvalidGeoJsonError(
                        f"{path}.features", f"must be an array, not {_shown(value)}"
                    )
                out[key] = [
                    self._feature_in(item, f"{path}.features[{index}]")
                    for index, item in enumerate(value)
                ]
            else:
                out[key] = self._member(key, value, path, root=path == "$")
        self._record(out, Kind.FEATURE_COLLECTION, path, first)
        return out

    def feature(self, data: Mapping[str, Any], path: str) -> dict[str, Any]:
        first = len(self.geometries)
        self._feature = len(self.features)
        out: dict[str, Any] = {}
        for key, value in data.items():
            if key == "geometry":
                out[key] = (
                    None
                    if value is None
                    else self._geometry_in(value, f"{path}.geometry")
                )
            elif key == "place" and value is not None:
                raise UnsupportedGeoJsonError(
                    f"{path}.place", "JSON-FG place is not supported yet"
                )
            else:
                out[key] = self._member(key, value, path, root=path == "$")
        self._feature = None
        self.features.append(Feature(path, out, range(first, len(self.geometries))))
        self._record(out, Kind.FEATURE, path, first)
        return out

    def geometry(
        self, data: Mapping[str, Any], path: str, kind: Kind
    ) -> dict[str, Any]:
        if kind is Kind.GEOMETRY_COLLECTION:
            return self._geometry_collection(data, path)
        if "coordinates" not in data:
            raise InvalidGeoJsonError(path, f"a {kind.value} has no coordinates")
        positions, shape = _read_coordinates(
            data["coordinates"], f"{path}.coordinates", kind
        )
        first = len(self.geometries)
        out: dict[str, Any] = {}
        for key, value in data.items():
            if key == "coordinates":
                out[key] = None
            elif key == "measures" and value is not None:
                raise UnsupportedGeoJsonError(
                    f"{path}.measures", "JSON-FG measures are not supported yet"
                )
            else:
                out[key] = self._member(key, value, path, root=path == "$")
        geometry = Geometry(path, kind, out, positions, shape, self._feature)
        geometry.write(positions.tolist() if geometry.count else [])
        self.geometries.append(geometry)
        self._record(out, kind, path, first)
        return out

    def _geometry_collection(
        self, data: Mapping[str, Any], path: str
    ) -> dict[str, Any]:
        if "geometries" not in data:
            raise InvalidGeoJsonError(path, "a GeometryCollection has no geometries")
        first = len(self.geometries)
        out: dict[str, Any] = {}
        for key, value in data.items():
            if key == "geometries":
                if not _is_array(value):
                    raise InvalidGeoJsonError(
                        f"{path}.geometries", f"must be an array, not {_shown(value)}"
                    )
                out[key] = [
                    self._geometry_in(item, f"{path}.geometries[{index}]")
                    for index, item in enumerate(value)
                ]
            else:
                out[key] = self._member(key, value, path, root=path == "$")
        self._record(out, Kind.GEOMETRY_COLLECTION, path, first)
        return out

    def _feature_in(self, data: object, path: str) -> dict[str, Any]:
        self._expect(data, path, frozenset({Kind.FEATURE}), "a feature")
        return self.feature(cast("Mapping[str, Any]", data), path)

    def _geometry_in(self, data: object, path: str) -> dict[str, Any]:
        kind = self._expect(data, path, _GEOMETRIES, "a geometry")
        return self.geometry(cast("Mapping[str, Any]", data), path, kind)

    def _expect(
        self, data: object, path: str, allowed: frozenset[Kind], role: str
    ) -> Kind:
        """What ``data`` is, refusing anything but ``allowed`` in this flavour."""
        if not isinstance(data, Mapping):
            raise InvalidGeoJsonError(
                path, f"{role} must be a JSON object, not {_shown(data)}"
            )
        kind, flavour = _type(data, path)
        if kind not in allowed:
            raise InvalidGeoJsonError(
                f"{path}.type", f"{role} cannot be a {data['type']!r}"
            )
        if flavour is not self._flavour:
            raise InvalidGeoJsonError(
                f"{path}.type",
                f"{data['type']!r} does not belong in {_FLAVOUR_NAMES[self._flavour]}; "
                f"it would be {kind.name_in(self._flavour)!r}. A document mixing "
                "plain and AnyCrs types leaves the CRS of its positions a guess",
            )
        return kind

    def _member(self, key: str, value: object, path: str, *, root: bool) -> object:
        """Copy a member, refusing those that would be wrong left unconverted."""
        member = f"{path}.{key}"
        if key == "type":
            return value
        if key == "bbox":
            return _read_bbox(value, member)
        if key == "coordRefSys" and value is not None:
            raise UnsupportedGeoJsonError(
                member, "JSON-FG coordRefSys is not supported yet"
            )
        if key == "conformsTo" and _is_array(value):
            conformance = cast("Sequence[object]", value)
            if any(
                isinstance(uri, str) and uri.startswith(_JSONFG_CONFORMANCE)
                for uri in conformance
            ):
                raise UnsupportedGeoJsonError(
                    member, "JSON-FG documents are not supported yet"
                )
        stated = value is not None and value != ""
        if (key in ANYCRS_MEMBERS or key == LEGACY_CRS) and not root and stated:
            raise UnsupportedGeoJsonError(
                member,
                f"{key} is read only on the document's root, which states "
                "the CRS of every position in it",
            )
        return value if self._owned else copy.deepcopy(value)

    def _record(self, out: dict[str, Any], kind: Kind, path: str, first: int) -> None:
        self.typed.append((out, kind))
        if "bbox" in out:
            self.bounding_boxes.append(
                BoundingBox(path, out, range(first, len(self.geometries)))
            )


def _load(value: object) -> tuple[Mapping[str, Any], bool]:
    """The document as a mapping, and whether it was parsed here."""
    owned = False
    data: object
    if isinstance(value, str | bytes | bytearray):
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise InvalidGeoJsonError(
                "$", f"the document is not JSON: {error}"
            ) from error
        except ValueError as error:
            # An integer longer than sys.get_int_max_str_digits() digits.
            raise InvalidGeoJsonError(
                "$", f"the document cannot be read: {error}"
            ) from error
        owned = True
    elif isinstance(value, Mapping):
        data = value
    elif hasattr(value, "__geo_interface__"):
        data = value.__geo_interface__
    else:
        raise TypeError(
            "a GeoJSON document is a mapping, JSON text or an object with a "
            f"__geo_interface__, not {type(value).__name__}"
        )
    if not isinstance(data, Mapping):
        raise InvalidGeoJsonError("$", f"must be a JSON object, not {_shown(data)}")
    return data, owned


def _type(data: Mapping[str, Any], path: str) -> tuple[Kind, Flavour]:
    """What an object is, and the flavour its ``type`` is written in."""
    if "type" not in data:
        raise InvalidGeoJsonError(path, "has no type")
    name = data["type"]
    if not isinstance(name, str):
        raise InvalidGeoJsonError(
            f"{path}.type", f"must be a string, not {_shown(name)}"
        )
    flavour, plain = Flavour.GEOJSON, name
    if name.startswith(ANYCRS_PREFIX):
        flavour, plain = Flavour.ANYCRS, name.removeprefix(ANYCRS_PREFIX)
    try:
        return Kind(plain), flavour
    except ValueError:
        if plain in _JSONFG_TYPES:
            raise UnsupportedGeoJsonError(
                f"{path}.type",
                f"{name!r} is a JSON-FG geometry type, which is not supported yet",
            ) from None
        raise InvalidGeoJsonError(
            f"{path}.type", f"{name!r} is not a GeoJSON type"
        ) from None


def _read_coordinates(value: object, path: str, kind: Kind) -> tuple[FloatArray, Shape]:
    """A geometry's positions, one row each, and how they nest."""
    if not _is_array(value):
        raise InvalidGeoJsonError(path, f"must be an array, not {_shown(value)}")
    items = cast("Sequence[object]", value)
    if not len(items):
        return _EMPTY, ()
    flat: list[Position] = []
    shape: Shape
    depth = _DEPTHS[kind]
    if depth == 0:
        flat.append(_position(items, path))
        shape = None
    else:
        shape = _walk(items, path, depth, kind, flat)
    width = len(flat[0])
    for index, position in enumerate(flat):
        if len(position) != width:
            nesting = "".join(f"[{number}]" for number in _locate(shape, index))
            raise InvalidGeoJsonError(
                f"{path}{nesting}",
                f"has {len(position)} values, but the geometry's first position "
                f"has {width}; all positions of a geometry have as many",
            )
    return np.array(flat, dtype=np.float64), shape


def _walk(
    items: Sequence[object], path: str, depth: int, kind: Kind, flat: list[Position]
) -> Shape:
    """Collect the positions an array nests ``depth`` levels deep, checking it."""
    if depth == 1:
        start = len(flat)
        flat.extend(
            _position(item, f"{path}[{index}]") for index, item in enumerate(items)
        )
        count = len(items)
        if kind in _LINES and count < 2:
            raise InvalidGeoJsonError(
                path, f"a line has at least two positions, not {count}"
            )
        if kind in _RINGS:
            if count < 4:
                raise InvalidGeoJsonError(
                    path, f"a linear ring has at least four positions, not {count}"
                )
            if flat[start] != flat[-1]:
                raise InvalidGeoJsonError(
                    path, "a linear ring ends at the position it starts at"
                )
        return count
    parts: list[Shape] = []
    for index, item in enumerate(items):
        part = f"{path}[{index}]"
        if not _is_array(item):
            raise InvalidGeoJsonError(part, f"must be an array, not {_shown(item)}")
        members = cast("Sequence[object]", item)
        if depth == 3 and not len(members):
            raise InvalidGeoJsonError(part, "a polygon has at least one linear ring")
        parts.append(_walk(members, part, depth - 1, kind, flat))
    return tuple(parts)


def _position(value: object, path: str) -> Position:
    if not _is_array(value):
        raise InvalidGeoJsonError(
            path, f"a position is an array of numbers, not {_shown(value)}"
        )
    items = cast("Sequence[object]", value)
    count = len(items)
    if count < 2:
        raise InvalidGeoJsonError(
            path, f"a position has at least two numbers, not {count}"
        )
    if count > 3:
        raise UnsupportedGeoJsonError(
            path,
            f"positions of {count} numbers are not supported: RFC 7946 leaves "
            "what follows the third undefined",
        )
    return [_number(item, f"{path}[{index}]") for index, item in enumerate(items)]


def _read_bbox(value: object, path: str) -> list[float]:
    if not _is_array(value) or len(cast("Sequence[object]", value)) % 2:
        raise InvalidGeoJsonError(
            path, f"a bbox is an array of 2n numbers, not {_shown(value)}"
        )
    items = cast("Sequence[object]", value)
    if len(items) < 4:
        raise InvalidGeoJsonError(
            path, f"a bbox has at least four numbers, not {len(items)}"
        )
    return [_number(item, f"{path}[{index}]") for index, item in enumerate(items)]


def _number(value: object, path: str) -> float:
    """A finite number as a float. A boolean is not a number, though Python's is."""
    kind = type(value)
    if kind is float:
        number = cast("float", value)
    elif kind is int or (
        isinstance(value, numbers.Real | Decimal)
        and not isinstance(value, bool | np.bool_)
    ):
        try:
            number = float(cast("float", value))
        except (OverflowError, ValueError) as error:
            raise InvalidGeoJsonError(
                path, f"{_shown(value)} cannot be held as a 64-bit float"
            ) from error
    else:
        raise InvalidGeoJsonError(path, f"{_shown(value)} is not a number")
    if not math.isfinite(number):
        raise InvalidGeoJsonError(path, f"{number!r} is not a finite number")
    return number


def _is_array(value: object) -> bool:
    return isinstance(value, list | tuple) or (
        isinstance(value, np.ndarray) and value.ndim > 0
    )


def _nested(rows: Sequence[Position], shape: Shape, start: int) -> tuple[Any, int]:
    """``rows`` from ``start`` nested as ``shape``, and where they end."""
    if shape is None:
        return rows[start], start + 1
    if isinstance(shape, int):
        return list(rows[start : start + shape]), start + shape
    parts = []
    for part in shape:
        nested, start = _nested(rows, part, start)
        parts.append(nested)
    return parts, start


def _locate(shape: Shape, index: int) -> tuple[int, ...]:
    """The array indices of position ``index`` in coordinates nested as ``shape``."""
    if shape is None:
        return ()
    if isinstance(shape, int):
        return (index,)
    for number, part in enumerate(shape):
        size = _size(part)
        if index < size:
            return (number, *_locate(part, index))
        index -= size
    raise IndexError(index)


def _size(shape: Shape) -> int:
    if shape is None:
        return 1
    if isinstance(shape, int):
        return shape
    return sum(_size(part) for part in shape)


def _shown(value: object) -> str:
    try:
        text = repr(value)
    except ValueError:
        # More than sys.get_int_max_str_digits() digits, possibly nested in value.
        if isinstance(value, int):
            return "a number too long to show"
        return "a value that cannot be shown"
    return text if len(text) <= _SHOWN else f"{text[: _SHOWN - 3]}..."
