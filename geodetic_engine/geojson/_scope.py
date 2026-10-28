"""Where a document's positions are, and where they are to go.

Private to :mod:`geodetic_engine.geojson`. Reads the CRS and height unit a
document states, checks them against what the caller gives, and settles what
the converted document is written as.

An RFC 7946 document is in WGS 84 longitude and latitude, OGC:CRS84, and a
third value is a height in metres above the ellipsoid, OGC:CRS84h. Another CRS
can be agreed with whoever wrote it, which is what ``source_crs`` gives. A
GeoJSON 2008 ``crs`` member is read only when it names CRS84, as GDAL writes
it: any other CRS is refused for now, since reading it needs the axis-order
rules RFC 7946 dropped it over.

An OSDU AnyCrs document states its CRS on its collection, by record id in
``CoordinateReferenceSystemID`` or in full in ``persistableReferenceCrs``. The
record id takes precedence, as in the OSDU CRS conversion service. Heights are
in ``VerticalUnitID``'s unit, or else ``persistableReferenceUnitZ``'s. A
vertical CRS stated for them is refused for now: the service ignores it and
only scales the heights, which is not a conversion between vertical CRSs.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import unquote

from pyproj import CRS
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy import CoordinateReferenceSystem, UnresolvableCRSError
from geodetic_engine.geodesy.osdu_id import parse_osdu_id
from geodetic_engine.geojson._model import (
    ANYCRS_MEMBERS,
    LEGACY_CRS,
    Document,
    Flavour,
)
from geodetic_engine.geojson.errors import (
    GeoJsonCrsError,
    InvalidGeoJsonError,
    UnsupportedGeoJsonError,
)
from geodetic_engine.persistablereference import (
    PersistableReferenceError,
    UnitReference,
    looks_like_reference,
    parse_persistable_reference,
    to_persistable_reference,
)
from geodetic_engine.welltrajectory.errors import UnitError
from geodetic_engine.welltrajectory.survey import length_factor

type Output = Literal["auto", "geojson", "anycrs"]

CRS84: Final = "OGC:CRS84"
CRS84H: Final = "OGC:CRS84h"
OUTPUTS: Final = ("auto", "geojson", "anycrs")

# Names GDAL and others write a GeoJSON 2008 crs member with for CRS84, which
# RFC 7946 assumes anyway.
_CRS84_NAMES: Final = frozenset(
    {
        "urn:ogc:def:crs:OGC:1.3:CRS84",
        "urn:ogc:def:crs:OGC::CRS84",
        "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "https://www.opengis.net/def/crs/OGC/1.3/CRS84",
        "OGC:CRS84",
    }
)
_VERTICAL_CRS: Final = (
    "VerticalCoordinateReferenceSystemID",
    "persistableReferenceVerticalCrs",
)
_UNIT_OF_MEASURE: Final = "UnitOfMeasure:"
# WGS 84's ellipsoid: semi-major axis in metres and inverse flattening.
_WGS84_ELLIPSOID: Final = (6378137.0, 298.257223563)
_DEGREE: Final = math.pi / 180
# The axis directions of RFC 7946 positions, in value order.
_LONGITUDE_LATITUDE: Final = (("east", "north"), ("east", "north", "up"))


@dataclass(frozen=True, slots=True)
class LengthUnit:
    """A length unit, as metres = ``offset + scale * value``.

    Attributes:
        symbol: Its symbol, for messages and for a payload written for it.
        scale: Metres per unit.
        offset: Metres at a value of zero, which is zero for every ordinary
            length unit.
        payload: The persistableReference it was read from, to be written
            back unchanged.
    """

    symbol: str
    scale: float
    offset: float = 0.0
    payload: str | None = field(default=None, compare=False)

    def to_metres(self, value: float) -> float:
        """A value in this unit, in metres."""
        return self.offset + self.scale * value

    def from_metres(self, value: float) -> float:
        """A value in metres, in this unit."""
        return (value - self.offset) / self.scale

    def reference(self) -> str:
        """The unit as a persistableReference.

        The payload it was read from, if any. Otherwise a ``USO`` payload in
        the form the OSDU CRS conversion service writes: for metres, exactly
        the service's own.
        """
        if self.payload is not None:
            return self.payload
        stated = {
            "scaleOffset": {"scale": self.scale, "offset": self.offset},
            "symbol": self.symbol,
            "baseMeasurement": {"ancestry": "Length", "type": "UM"},
            "type": "USO",
        }
        return json.dumps(stated, separators=(",", ":"))


METRE: Final = LengthUnit("m", 1.0)


def length_unit(value: object) -> LengthUnit:
    """Read a length unit.

    Args:
        value: A symbol or name such as ``"ft"``, an OSDU ``UnitOfMeasure``
            record id, or an OSDU unit persistableReference. Symbols and ids are
            read as :func:`~geodetic_engine.welltrajectory.length_factor` reads
            them. A payload's offset is kept, not refused.

    Raises:
        ValueError: If ``value`` is none of these, or not a length unit
            converted by a scale and an offset.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"a unit is a non-empty string, not {value!r}")
    text = value.strip()
    if not looks_like_reference(text):
        try:
            return LengthUnit(_symbol(text), length_factor(text))
        except UnitError as error:
            raise ValueError(str(error)) from error
    try:
        reference = parse_persistable_reference(text)
        if not isinstance(reference, UnitReference):
            raise ValueError("the persistableReference given as a unit is not a unit")
        scale, offset = reference.scale, reference.offset
    except PersistableReferenceError as error:
        raise ValueError(f"cannot read the unit: {error}") from error
    if reference.measurement != "length":
        raise ValueError(
            f"{reference.symbol!r} ({reference.measurement or 'no measurement'}) "
            "is not a length unit"
        )
    if not (math.isfinite(scale) and scale > 0 and math.isfinite(offset)):
        raise ValueError(f"{reference.symbol!r} converts by a scale of {scale!r}")
    return LengthUnit(reference.symbol or text, scale, offset, payload=text)


@dataclass(frozen=True, slots=True)
class Source:
    """Where a document's positions are.

    Attributes:
        crs: The CRS the document states or the caller gave. None for an RFC
            7946 document, whose CRS depends on whether a position has a
            height: see :meth:`crs_for`.
        z_unit: The unit of a third value that a transformation carries
            through rather than converts, or None when the document does not
            say.
        stated_by: Where the CRS comes from, for messages: a JSON path,
            ``"source_crs"`` or ``"RFC 7946"``.
    """

    crs: CoordinateReferenceSystem | None
    z_unit: LengthUnit | None
    stated_by: str

    def crs_for(self, dimension: int) -> CoordinateReferenceSystem:
        """The CRS of positions with ``dimension`` values."""
        if self.crs is not None:
            return self.crs
        return CoordinateReferenceSystem.from_user_input(
            CRS84H if dimension == 3 else CRS84
        )


def read_source(document: Document, source_crs: object = None) -> Source:
    """Where a document's positions are.

    Args:
        document: The document.
        source_crs: A CRS agreed with whoever wrote the document, or None. For
            an AnyCrs document that states one, it must be the same.

    Raises:
        InvalidGeoJsonError: If a CRS or unit member is not a string, a unit
            cannot be read, or a plain document states an AnyCrs member.
        UnsupportedGeoJsonError: If a GeoJSON 2008 ``crs`` member names a CRS
            other than CRS84, or an AnyCrs document with heights states a
            vertical CRS.
        GeoJsonCrsError: If an AnyCrs document states no CRS and none is
            given, or the one given differs from the one stated.
        UnresolvableCRSError: If a CRS stated or given cannot be resolved.
    """
    root = document.root
    given = (
        None
        if source_crs is None
        else CoordinateReferenceSystem.from_user_input(source_crs)
    )
    if document.flavour is Flavour.GEOJSON:
        return _plain_source(root, given)
    if LEGACY_CRS in root and root[LEGACY_CRS] is not None:
        raise UnsupportedGeoJsonError(
            f"$.{LEGACY_CRS}",
            "an AnyCrs document states its CRS in CoordinateReferenceSystemID or "
            "persistableReferenceCrs, not in a GeoJSON 2008 crs member",
        )
    stated: CoordinateReferenceSystem | None = None
    stated_by = "source_crs"
    for key in ("CoordinateReferenceSystemID", "persistableReferenceCrs"):
        if (text := _text(root, key)) is not None:
            stated, stated_by = _resolve(text, f"$.{key}"), f"$.{key}"
            break
    if stated is not None and given is not None and not _same_crs(stated, given):
        raise GeoJsonCrsError(
            stated_by,
            f"the document's CRS, {_label(stated)}, is not source_crs, {_label(given)}",
        )
    crs = stated or given
    if crs is None:
        raise GeoJsonCrsError(
            "$",
            "an AnyCrs document states its CRS in CoordinateReferenceSystemID or "
            "persistableReferenceCrs, and this one states neither; give "
            "source_crs if it was agreed",
        )
    if 3 in document.dimensions:
        for key in _VERTICAL_CRS:
            if _text(root, key) is not None:
                raise UnsupportedGeoJsonError(
                    f"$.{key}",
                    "heights in a vertical CRS are not supported yet: converting "
                    "them only by their unit would leave them in a vertical "
                    "datum the target does not state",
                )
    z_unit = None
    for key in ("VerticalUnitID", "persistableReferenceUnitZ"):
        if (text := _text(root, key)) is not None:
            z_unit = _document_unit(text, f"$.{key}")
            break
    return Source(crs, z_unit, stated_by)


@dataclass(frozen=True, slots=True)
class Target:
    """Where a document's positions are to go, and what it is written as.

    Attributes:
        crs: The target CRS.
        flavour: What the converted document is written as.
        z_unit: The unit to write heights in that a transformation carries
            through, or None to keep the document's.
        record_id: The OSDU record id the target was named by, if it was.
        given: The target as given, to write a payload back unchanged.
    """

    crs: CoordinateReferenceSystem
    flavour: Flavour
    z_unit: LengthUnit | None
    record_id: str | None
    given: Any = field(compare=False, repr=False)

    def crs_reference(self) -> str:
        """The target CRS as a persistableReference, for an AnyCrs document.

        The payload the target was given as, if it was given as one; else the
        one the catalogue in proj.db publishes for it; else one written from
        its definition, with no authority code claimed for it.

        Raises:
            AmbiguousCatalogReferenceError: If catalogue records publishing
                different references name the target's authority code.
            PersistableReferenceError: If none is published and the
                definition cannot be written as one.
        """
        if isinstance(self.given, str) and looks_like_reference(self.given.strip()):
            return self.given.strip()
        stored = self.crs.persistable_reference_from_catalog()
        if stored is not None:
            return stored.payload
        return to_persistable_reference(self.crs.crs)


def read_target(
    target_crs: object,
    *,
    source: Flavour,
    output: Output = "auto",
    target_unit_z: object = None,
) -> Target:
    """Where a document's positions are to go, and what it is written as.

    ``output="auto"`` keeps the document's flavour: AnyCrs stays AnyCrs, and
    plain GeoJSON stays plain when the target is WGS 84 longitude and latitude.
    Plain GeoJSON in another CRS would be JSON-FG, which is not written yet,
    so that is refused rather than written as something else.

    Args:
        target_crs: The CRS to convert to, as anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts.
        source: The flavour of the document being converted.
        output: ``"auto"``, ``"geojson"`` or ``"anycrs"``.
        target_unit_z: The unit to write heights in, as :func:`length_unit`
            reads it, or None. Plain GeoJSON heights are in metres.

    Raises:
        ValueError: If ``output`` is not one of the above, cannot be written
            in the target CRS, or ``target_unit_z`` is not a length unit, or
            is not metres for plain GeoJSON.
        UnresolvableCRSError: If the target cannot be resolved.
    """
    if output not in OUTPUTS:
        raise ValueError(f"output must be one of {OUTPUTS}, not {output!r}")
    crs = CoordinateReferenceSystem.from_user_input(target_crs)
    lonlat = is_wgs84_lonlat(crs)
    if output == "geojson" and not lonlat:
        raise ValueError(
            f"output='geojson' writes RFC 7946 GeoJSON, whose positions are WGS 84 "
            f"longitude and latitude in degrees, which {_label(crs)} is not; "
            "write output='anycrs'"
        )
    if output == "auto" and source is Flavour.GEOJSON and not lonlat:
        raise ValueError(
            f"plain GeoJSON converted to {_label(crs)}, which is not WGS 84 "
            "longitude and latitude, would be written as JSON-FG, which is not "
            "supported yet; pass output='anycrs' for OSDU AnyCrs GeoJSON"
        )
    if output == "anycrs" or (output == "auto" and source is Flavour.ANYCRS):
        flavour = Flavour.ANYCRS
    else:
        flavour = Flavour.GEOJSON
    z_unit = None
    if target_unit_z is not None:
        try:
            z_unit = length_unit(target_unit_z)
        except ValueError as error:
            raise ValueError(f"target_unit_z: {error}") from error
    if flavour is Flavour.GEOJSON:
        if z_unit is not None and (z_unit.scale, z_unit.offset) != (1.0, 0.0):
            raise ValueError(
                f"plain GeoJSON heights are in metres, not {z_unit.symbol!r} "
                "(RFC 7946, section 4); write output='anycrs' for other units"
            )
        z_unit = METRE
    record_id = None
    if isinstance(target_crs, str):
        osdu = parse_osdu_id(target_crs.strip())
        if osdu is not None and osdu.is_crs:
            record_id = target_crs.strip()
    return Target(crs, flavour, z_unit, record_id, target_crs)


def is_wgs84_lonlat(crs: CoordinateReferenceSystem) -> bool:
    """Whether positions in ``crs`` are what RFC 7946 calls WGS 84.

    A geographic CRS on WGS 84 or one of its realizations, with the Greenwich
    meridian, neither bound to a transformation nor compound, whose values
    are longitude east and latitude north in degrees and any height up in
    metres. Values are always longitude first in this package, so EPSG:4326
    qualifies although it declares latitude first. A CRS counting longitude
    west or latitude south does not: its values would have the wrong signs,
    and, as this package orders only east and north axes, possibly the wrong
    order too.
    """
    definition = crs.crs
    if not crs.is_geographic or definition.is_bound or definition.is_compound:
        return False
    datum = definition.datum
    ellipsoid = definition.ellipsoid
    if datum is None or ellipsoid is None:
        return False
    if not datum.name.startswith("World Geodetic System 1984"):
        return False
    if not (
        math.isclose(ellipsoid.semi_major_metre, _WGS84_ELLIPSOID[0], rel_tol=1e-12)
        and math.isclose(
            ellipsoid.inverse_flattening, _WGS84_ELLIPSOID[1], rel_tol=1e-12
        )
    ):
        return False
    meridian = definition.prime_meridian
    if meridian is None or meridian.longitude != 0.0:
        return False
    axes = [crs.axes[index] for index in crs.value_axis_order]
    directions = tuple(axis.direction.lower() for axis in axes)
    if directions not in _LONGITUDE_LATITUDE:
        return False
    return all(
        math.isclose(
            axis.unit_conversion_factor,
            1.0 if direction == "up" else _DEGREE,
            rel_tol=1e-12,
        )
        for axis, direction in zip(axes, directions, strict=True)
    )


def _plain_source(
    root: Mapping[str, Any], given: CoordinateReferenceSystem | None
) -> Source:
    for key in ANYCRS_MEMBERS:
        if _text(root, key) is not None:
            raise InvalidGeoJsonError(
                f"$.{key}",
                "plain GeoJSON is in WGS 84 by RFC 7946 and states no CRS; a "
                "document stating one in AnyCrs members has AnyCrs types",
            )
    legacy = root.get(LEGACY_CRS)
    if legacy is None:
        return Source(given, METRE, "source_crs" if given else "RFC 7946")
    name = _legacy_name(legacy)
    if name not in _CRS84_NAMES:
        raise UnsupportedGeoJsonError(
            f"$.{LEGACY_CRS}",
            f"a GeoJSON 2008 crs member naming {name or _shown(legacy)} is not "
            "supported yet; only CRS84, which RFC 7946 assumes, is read",
        )
    crs84 = CoordinateReferenceSystem.from_user_input(CRS84)
    if given is not None and not _same_crs(given, crs84):
        raise GeoJsonCrsError(
            f"$.{LEGACY_CRS}",
            f"the document's crs member names CRS84, not source_crs, {_label(given)}",
        )
    return Source(given, METRE, f"$.{LEGACY_CRS}")


def _legacy_name(member: object) -> str | None:
    """The name a GeoJSON 2008 ``{"type": "name", ...}`` crs member gives."""
    if isinstance(member, Mapping) and member.get("type") == "name":
        properties = member.get("properties")
        if isinstance(properties, Mapping) and isinstance(properties.get("name"), str):
            return str(properties["name"]).strip()
    return None


def _text(root: Mapping[str, Any], key: str) -> str | None:
    """A string member of the root, or None when it is absent, null or empty."""
    value = root.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise InvalidGeoJsonError(f"$.{key}", f"must be a string, not {_shown(value)}")
    return value.strip()


def _resolve(text: str, path: str) -> CoordinateReferenceSystem:
    try:
        return CoordinateReferenceSystem.from_user_input(text)
    except UnresolvableCRSError as error:
        raise UnresolvableCRSError(f"{path}: {error}") from error


def _document_unit(text: str, path: str) -> LengthUnit:
    try:
        return length_unit(text)
    except ValueError as error:
        raise InvalidGeoJsonError(path, str(error)) from error


def _symbol(text: str) -> str:
    """The symbol a unit symbol or ``UnitOfMeasure`` record id names."""
    text = unquote(text)
    if _UNIT_OF_MEASURE in text:
        return text.split(_UNIT_OF_MEASURE, 1)[1].split(":", 1)[0]
    return text


def _same_crs(
    first: CoordinateReferenceSystem, second: CoordinateReferenceSystem
) -> bool:
    """Whether a CRS repeats another: the same positions at the same values.

    Equivalent by PROJ once both declare their axes in the order this package
    gives values in: longitude or easting first, then latitude or northing,
    then any height. So EPSG:4326 repeats OGC:CRS84, and EPSG:2193, declared
    northing first, repeats an OSDU persistableReference for it, declared
    easting first. PROJ's own ``ignore_axis_order`` reaches geographic CRSs
    only.

    Everything else counts: axis directions, units, the number of axes, and a
    transformation a CRS is bound with. A CRS counting longitude west does not
    repeat CRS84, and neither does a CRS that only states the same authority
    code: a persistableReference is defined by its parameters, not its code.
    """
    return _in_value_order(first.crs).equals(
        _in_value_order(second.crs), ignore_axis_order=True
    )


def _in_value_order(crs: CRS) -> CRS:
    """``crs`` with every coordinate system in it declared in value order."""
    definition = crs.to_json_dict()
    if not _declare_in_value_order(definition):
        return crs
    try:
        return CRS.from_json_dict(definition)
    except CRSError:
        return crs


def _declare_in_value_order(node: object) -> bool:
    """Reorder each CRS's axes in a PROJJSON node into value order, in place.

    Every node is visited, whatever an earlier one did.

    Returns:
        Whether any axis moved.
    """
    moved = False
    if isinstance(node, list):
        for item in node:
            moved = _declare_in_value_order(item) or moved
        return moved
    if not isinstance(node, dict):
        return False
    system = node.get("coordinate_system")
    if isinstance(system, dict) and isinstance(system.get("axis"), list):
        order = _value_order(node)
        if order is not None and order != tuple(range(len(order))):
            axes = system["axis"]
            system["axis"] = [axes[index] for index in order]
            # An identifier would claim the authority's own axis order.
            node.pop("id", None)
            node.pop("ids", None)
            moved = True
    for key, child in node.items():
        if key != "coordinate_system":
            moved = _declare_in_value_order(child) or moved
    return moved


def _value_order(node: dict[str, Any]) -> tuple[int, ...] | None:
    try:
        crs = CRS.from_json_dict(node)
    except CRSError:
        return None
    return CoordinateReferenceSystem(crs, crs.name).value_axis_order


def _label(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or repr(crs.name)


def _shown(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 40 else f"{text[:37]}..."
