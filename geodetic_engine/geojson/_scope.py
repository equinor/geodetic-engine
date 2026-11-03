"""Where a document's positions are, and where they are to go.

Private to :mod:`geodetic_engine.geojson`. Reads the CRS and height unit a
document states, checks them against what the caller gives, and settles what
the converted document is written as.

An RFC 7946 document is in WGS 84 longitude and latitude, OGC:CRS84, and a
third value is a height in metres above the ellipsoid, OGC:CRS84h. Another CRS
can be agreed with whoever wrote it, which is what ``source_crs`` gives. A
GeoJSON 2008 ``crs`` member names the CRS by URN or URL; its positions are
read ``xy``, as GDAL and others wrote them, whatever the CRS declares.

An OGC JSON-FG document states the CRS of each ``place`` in the nearest
``coordRefSys``, with an optional coordinate epoch, and writes positions in
that CRS's declared axis order. A feature's ``geometry`` is always CRS84 or
CRS84h.

An OSDU AnyCrs document states its CRS on its collection, by record id in
``CoordinateReferenceSystemID`` or in full in ``persistableReferenceCrs``. The
record id takes precedence, as in the OSDU CRS conversion service, but gives
way to the persistableReference when the proj.db in use does not record it.
Heights are in ``VerticalUnitID``'s unit, or else
``persistableReferenceUnitZ``'s. A vertical CRS stated for them, in
``VerticalCoordinateReferenceSystemID`` or
``persistableReferenceVerticalCrs``, makes positions with heights compound:
the heights are transformed between vertical datums, where the service only
scales them.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from geodetic_engine._referencing.osdu_id import parse_osdu_id
from geodetic_engine.geodesy import CoordinateReferenceSystem, UnresolvableCRSError
from geodetic_engine.geodesy._crs_tools import compound, same_crs
from geodetic_engine.geodesy.errors import UnsupportedCRSError
from geodetic_engine.geojson._jsonfg import coord_ref_sys, crs_name, read_coord_ref_sys
from geodetic_engine.geojson._model import (
    ANYCRS_MEMBERS,
    LEGACY_CRS,
    Document,
    Flavour,
    Geometry,
)
from geodetic_engine.geojson.errors import (
    GeoJsonCrsError,
    InvalidGeoJsonError,
    UnsupportedGeoJsonError,
)
from geodetic_engine.persistablereference import (
    looks_like_reference,
    to_persistable_reference,
)
from geodetic_engine.units import Unit, UnitError, length_unit

type Output = Literal["auto", "geojson", "anycrs", "jsonfg", "jsonfg-plus"]
type AxisOrder = Literal["xy", "crs"]

CRS84: Final = "OGC:CRS84"
CRS84H: Final = "OGC:CRS84h"
OUTPUTS: Final = ("auto", "geojson", "anycrs", "jsonfg", "jsonfg-plus")

_VERTICAL_RECORD_ID: Final = "VerticalCoordinateReferenceSystemID"
_VERTICAL_PAYLOAD: Final = "persistableReferenceVerticalCrs"
_RECORD_ID: Final = "CoordinateReferenceSystemID"
_PAYLOAD: Final = "persistableReferenceCrs"
# WGS 84's ellipsoid: semi-major axis in metres and inverse flattening.
_WGS84_ELLIPSOID: Final = (6378137.0, 298.257223563)
_DEGREE: Final = math.pi / 180
# The axis directions of RFC 7946 positions, in value order.
_LONGITUDE_LATITUDE: Final = (("east", "north"), ("east", "north", "up"))

METRE: Final = Unit("m", "length", 1.0)


@dataclass(frozen=True, slots=True)
class Source:
    """Where a document's positions are.

    Attributes:
        crs: The CRS the document states or the caller gave. None for
            positions in WGS 84, whose CRS depends on whether a position has a
            height: see :meth:`crs_for`. A ``source_crs`` repeating CRS84 or
            CRS84h leaves it None.
        z_unit: The unit of a third value, or None when the document does not
            say.
        stated_by: Where the CRS comes from, for messages: a JSON path,
            ``"source_crs"`` or ``"RFC 7946"``.
        axis_order: ``"xy"``, or ``"crs"`` for positions in the CRS's
            declared order, as JSON-FG writes them.
        epoch: The coordinate epoch stated with the CRS, or None.
        vertical: The vertical CRS of a third value, which positions with
            heights are in together with :attr:`crs`, or None.
    """

    crs: CoordinateReferenceSystem | None
    z_unit: Unit | None
    stated_by: str
    axis_order: AxisOrder = "xy"
    epoch: float | None = None
    vertical: CoordinateReferenceSystem | None = None

    def crs_for(self, dimension: int) -> CoordinateReferenceSystem:
        """The CRS of positions with ``dimension`` values."""
        if self.crs is None:
            return CoordinateReferenceSystem.from_user_input(
                CRS84H if dimension == 3 else CRS84
            )
        if dimension == 3 and self.vertical is not None:
            try:
                return compound(self.crs, self.vertical)
            except UnsupportedCRSError as error:
                raise GeoJsonCrsError(self.stated_by, str(error)) from error
        if dimension == 2:
            # Positions without heights in a compound CRS are in its horizontal part.
            return crs_parts(self.crs)[0]
        return self.crs


def read_sources(document: Document, source_crs: object = None) -> tuple[Source, ...]:
    """Where each of a document's geometries is, in document order.

    One source for every geometry of an RFC 7946 or AnyCrs document. In a
    JSON-FG document, a ``place`` is in the CRS of its nearest
    ``coordRefSys``, or CRS84 without one, and a ``geometry`` in CRS84.

    Raises:
        As :func:`read_source`, and, for JSON-FG, :class:`GeoJsonCrsError` if
        ``source_crs`` differs from a ``coordRefSys``.
    """
    if document.flavour is not Flavour.JSONFG:
        source = read_source(document, source_crs)
        return (source,) * len(document.geometries)
    given = _given(source_crs)
    _refuse_members(document.root, Flavour.JSONFG)
    wgs84 = Source(None, METRE, "RFC 7946")
    unscoped = _unscoped(given)
    scopes: dict[str, Source] = {}
    sources: list[Source] = []
    for geometry in document.geometries:
        if not geometry.placed:
            sources.append(wgs84)
        elif geometry.scope is None:
            sources.append(unscoped)
        else:
            path = geometry.scope[0]
            if path not in scopes:
                scopes[path] = _scoped(geometry, given)
            sources.append(scopes[path])
    return tuple(sources)


def read_source(document: Document, source_crs: object = None) -> Source:
    """Where a document's positions are.

    For a JSON-FG document, where the positions of its root are: see
    :func:`read_sources` for each geometry's.

    Args:
        document: The document.
        source_crs: A CRS agreed with whoever wrote the document, or None. For
            a document that states one, it must be the same. For plain
            GeoJSON, CRS84 or CRS84h changes nothing: RFC 7946 already says
            so, heights included.

    Raises:
        InvalidGeoJsonError: If a CRS or unit member is not a string, a unit
            cannot be read, a plain document states an AnyCrs member, or a
            vertical CRS member names no vertical CRS.
        UnsupportedGeoJsonError: If a GeoJSON 2008 ``crs`` member is not a
            ``name``.
        GeoJsonCrsError: If an AnyCrs document states no CRS and none is
            given, or the one given differs from the one stated.
        UnresolvableCRSError: If a CRS stated or given cannot be resolved.
    """
    root = document.root
    given = _given(source_crs)
    if document.flavour is Flavour.GEOJSON:
        return _plain_source(root, given)
    if document.flavour is Flavour.JSONFG:
        _refuse_members(root, Flavour.JSONFG)
        scope = root.get("coordRefSys")
        if scope is None:
            return _unscoped(given)
        return _stated_scope("$.coordRefSys", scope, given)
    if LEGACY_CRS in root and root[LEGACY_CRS] is not None:
        raise UnsupportedGeoJsonError(
            f"$.{LEGACY_CRS}",
            "an AnyCrs document states its CRS in CoordinateReferenceSystemID or "
            "persistableReferenceCrs, not in a GeoJSON 2008 crs member",
        )
    stated = _stated_crs(root, _RECORD_ID, _PAYLOAD)
    if stated is not None:
        crs, stated_by = stated
        if given is not None and not same_crs(crs, given):
            raise GeoJsonCrsError(
                stated_by,
                f"the document's CRS, {_label(crs)}, is not source_crs, "
                f"{_label(given)}",
            )
    elif given is not None:
        crs, stated_by = given, "source_crs"
    else:
        raise GeoJsonCrsError(
            "$",
            "an AnyCrs document states its CRS in CoordinateReferenceSystemID or "
            "persistableReferenceCrs, and this one states neither; give "
            "source_crs if it was agreed",
        )
    vertical = None
    if (found := _stated_crs(root, _VERTICAL_RECORD_ID, _VERTICAL_PAYLOAD)) is not None:
        vertical, path = found
        if not vertical.crs.is_vertical:
            raise InvalidGeoJsonError(path, f"{_label(vertical)} is not a vertical CRS")
    z_unit = None
    for key in ("VerticalUnitID", "persistableReferenceUnitZ"):
        if (text := _text(root, key)) is not None:
            z_unit = _document_unit(text, f"$.{key}")
            break
    return Source(crs, z_unit, stated_by, vertical=vertical)


@dataclass(frozen=True, slots=True)
class Target:
    """Where a document's positions are to go, and what it is written as.

    Attributes:
        crs: The target CRS.
        flavour: What the converted document is written as.
        z_unit: The unit to write heights in, or None to keep the unit they
            come out of the transformation in.
        record_id: The OSDU record id the target was named by, if it was.
        given: The target as given, to write a payload back unchanged.
        plus: For JSON-FG, whether every feature with a ``place`` also gets a
            ``geometry`` in WGS 84, for readers that only know GeoJSON.
    """

    crs: CoordinateReferenceSystem
    flavour: Flavour
    z_unit: Unit | None
    record_id: str | None
    given: Any = field(compare=False, repr=False)
    plus: bool = False

    @property
    def lonlat(self) -> bool:
        """Whether the target is what RFC 7946 calls WGS 84."""
        return is_wgs84_lonlat(self.crs)

    def parts(
        self,
    ) -> tuple[CoordinateReferenceSystem, CoordinateReferenceSystem | None]:
        """The horizontal CRS, and the vertical CRS of a compound target."""
        return crs_parts(self.crs)

    def crs_reference(self) -> str:
        """The target CRS as a persistableReference, for an AnyCrs document.

        The payload the target was given as, if it was given as one; else the
        one the catalogue in proj.db publishes for it; else one written from
        its definition, with no authority code claimed for it. For a compound
        target, that of its horizontal part, unless it was named whole, by a
        payload or a record id.

        Raises:
            AmbiguousCatalogReferenceError: If catalogue records publishing
                different references name the target's authority code.
            PersistableReferenceError: If none is published and the
                definition cannot be written as one.
        """
        if self._given_payload():
            return str(self.given).strip()
        if self._named_whole():
            return crs_reference(self.crs)
        return crs_reference(self.parts()[0])

    def vertical_reference(self) -> str | None:
        """The vertical part of a compound target as a persistableReference.

        None for a compound target named whole: :meth:`crs_reference` states
        it all, and stating its vertical part again would make it a compound
        of a compound.
        """
        vertical = self.parts()[1]
        if vertical is None or self._named_whole():
            return None
        return crs_reference(vertical)

    def _given_payload(self) -> bool:
        return isinstance(self.given, str) and looks_like_reference(self.given.strip())

    def _named_whole(self) -> bool:
        return self._given_payload() or self.record_id is not None

    def coord_ref_sys(self, epoch: float | None) -> Any:
        """The target's JSON-FG ``coordRefSys``."""
        return coord_ref_sys(self.crs, epoch)


def read_target(
    target_crs: object,
    *,
    source: Flavour,
    output: Output = "auto",
    target_unit_z: str | None = None,
) -> Target:
    """Where a document's positions are to go, and what it is written as.

    ``output="auto"`` keeps the document's family: AnyCrs stays AnyCrs, and
    plain GeoJSON stays plain when the target is WGS 84 longitude and latitude
    and is written as JSON-FG otherwise, the open standard for other CRSs.
    JSON-FG stays JSON-FG.

    Args:
        target_crs: The CRS to convert to, as anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts.
        source: The flavour of the document being converted.
        output: ``"auto"``, ``"geojson"``, ``"anycrs"``, ``"jsonfg"`` or
            ``"jsonfg-plus"``.
        target_unit_z: The unit to write heights in, as
            :func:`~geodetic_engine.units.length_unit` reads it, or None. Plain
            GeoJSON heights are in metres, and JSON-FG's in the target CRS's.

    Raises:
        ValueError: If ``output`` is not one of the above, cannot be written
            in the target CRS, or ``target_unit_z`` is not a length unit, or
            not one the output can state.
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
            "write output='jsonfg' or output='anycrs'"
        )
    if output == "anycrs" or (output == "auto" and source is Flavour.ANYCRS):
        flavour = Flavour.ANYCRS
    elif output in ("jsonfg", "jsonfg-plus") or (
        output == "auto" and (source is Flavour.JSONFG or not lonlat)
    ):
        flavour = Flavour.JSONFG
    else:
        flavour = Flavour.GEOJSON
    if flavour is Flavour.JSONFG and not lonlat and coord_ref_sys(crs, None) is None:
        raise ValueError(
            f"JSON-FG states a CRS by its OGC URI, and {_label(crs)} has none: "
            "only EPSG and OGC CRSs do; write output='anycrs' to state it as an "
            "OSDU persistableReference"
        )
    z_unit = None
    if target_unit_z is not None:
        try:
            z_unit = length_unit(target_unit_z)
        except UnitError as error:
            raise UnitError(f"target_unit_z: {error}") from error
    if flavour is Flavour.JSONFG and not lonlat and z_unit is not None:
        raise ValueError(
            "JSON-FG heights are in the unit of the target CRS's height axis, "
            "which target_unit_z cannot change; give a target CRS with heights "
            "in that unit, or write output='anycrs'"
        )
    if flavour is Flavour.GEOJSON or (flavour is Flavour.JSONFG and lonlat):
        if z_unit is not None and z_unit.scale != 1.0:
            raise ValueError(
                f"WGS 84 heights in GeoJSON are in metres, not {z_unit.symbol!r} "
                "(RFC 7946, section 4); write output='anycrs' for other units"
            )
        z_unit = METRE
    record_id = None
    if isinstance(target_crs, str):
        osdu = parse_osdu_id(target_crs.strip())
        if osdu is not None and osdu.is_crs:
            record_id = target_crs.strip()
    return Target(
        crs, flavour, z_unit, record_id, target_crs, plus=output == "jsonfg-plus"
    )


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
    _refuse_members(root, Flavour.GEOJSON)
    legacy = root.get(LEGACY_CRS)
    if legacy is None:
        if given is None:
            return Source(None, METRE, "RFC 7946")
        if _is_rfc_7946(given):
            return Source(None, METRE, "source_crs")
        return Source(given, METRE, "source_crs")
    path = f"$.{LEGACY_CRS}"
    name = _legacy_name(legacy)
    if name is None:
        raise UnsupportedGeoJsonError(
            path,
            "a GeoJSON 2008 crs member is read when it names the CRS, as "
            '{"type": "name", "properties": {"name": ...}}, not '
            f"{_shown(legacy)}",
        )
    code = crs_name(name)
    if code is None:
        raise InvalidGeoJsonError(path, f"{name!r} is not a CRS URN or URL")
    crs = _resolve(code, path)
    rfc_7946 = _is_rfc_7946(crs)
    if given is not None and not (
        same_crs(crs, given) or (rfc_7946 and _is_rfc_7946(given))
    ):
        raise GeoJsonCrsError(
            path,
            f"the document's crs member names {'CRS84' if rfc_7946 else _label(crs)}"
            f", not source_crs, {_label(given)}",
        )
    if rfc_7946:
        return Source(None, METRE, path)
    return Source(crs, None, path)


def _refuse_members(root: Mapping[str, Any], flavour: Flavour) -> None:
    """Refuse AnyCrs members, and a legacy crs member in JSON-FG."""
    for key in ANYCRS_MEMBERS:
        if _text(root, key) is not None:
            raise InvalidGeoJsonError(
                f"$.{key}",
                "plain GeoJSON is in WGS 84 by RFC 7946 and states no CRS; a "
                "document stating one in AnyCrs members has AnyCrs types",
            )
    if flavour is Flavour.JSONFG and root.get(LEGACY_CRS) is not None:
        raise InvalidGeoJsonError(
            f"$.{LEGACY_CRS}",
            "a JSON-FG document states its CRS in coordRefSys, not in a GeoJSON "
            "2008 crs member",
        )


def _given(source_crs: object) -> CoordinateReferenceSystem | None:
    if source_crs is None:
        return None
    return CoordinateReferenceSystem.from_user_input(source_crs)


def _unscoped(given: CoordinateReferenceSystem | None) -> Source:
    """Where a JSON-FG place without a coordRefSys is: CRS84, or as agreed."""
    if given is None or _crs84_as_declared(given):
        return Source(None, METRE, "JSON-FG", axis_order="crs")
    return Source(given, None, "source_crs", axis_order="crs")


def _scoped(geometry: Geometry, given: CoordinateReferenceSystem | None) -> Source:
    assert geometry.scope is not None
    return _stated_scope(geometry.scope[0], geometry.scope[1], given)


def _stated_scope(
    path: str, value: object, given: CoordinateReferenceSystem | None
) -> Source:
    """Where the places a ``coordRefSys`` applies to are."""
    crs, epoch = read_coord_ref_sys(value, path)
    if given is not None and not same_crs(crs, given):
        raise GeoJsonCrsError(
            path,
            f"the document's CRS, {_label(crs)}, is not source_crs, {_label(given)}",
        )
    # CRS84 or CRS84h as declared; EPSG:4326 keeps its own, latitude first.
    if _crs84_as_declared(crs):
        return Source(None, METRE, path, axis_order="crs", epoch=epoch)
    return Source(crs, None, path, axis_order="crs", epoch=epoch)


def _crs84_as_declared(crs: CoordinateReferenceSystem) -> bool:
    """Whether ``crs`` is CRS84 or CRS84h with its axes in value order."""
    return _is_rfc_7946(crs) and crs.value_axis_order == tuple(range(crs.dimension))


def _is_rfc_7946(crs: CoordinateReferenceSystem) -> bool:
    """Whether ``crs`` is the one RFC 7946 puts positions in, CRS84 or CRS84h."""
    return any(
        same_crs(crs, CoordinateReferenceSystem.from_user_input(name))
        for name in (CRS84, CRS84H)
    )


def _stated_crs(
    root: Mapping[str, Any], record_key: str, payload_key: str
) -> tuple[CoordinateReferenceSystem, str] | None:
    """The CRS an AnyCrs document states, and the path of the member stating it.

    The record id takes precedence, as in the OSDU service. The service can
    always look it up; here it resolves only when the proj.db in use records
    it, so an id that does not resolve gives way to the persistableReference
    beside it, which is self-contained.
    """
    record = _text(root, record_key)
    payload = _text(root, payload_key)
    if record is not None:
        try:
            return _resolve(record, f"$.{record_key}"), f"$.{record_key}"
        except UnresolvableCRSError as unresolved:
            if payload is None:
                raise
            try:
                return _resolve(payload, f"$.{payload_key}"), f"$.{payload_key}"
            except UnresolvableCRSError as error:
                raise UnresolvableCRSError(f"{unresolved}; {error}") from error
    if payload is not None:
        return _resolve(payload, f"$.{payload_key}"), f"$.{payload_key}"
    return None


def crs_reference(crs: CoordinateReferenceSystem) -> str:
    """A CRS as a persistableReference: as its catalogue publishes it, if one does."""
    stored = crs.persistable_reference_from_catalog()
    if stored is not None:
        return stored.payload
    return to_persistable_reference(crs.crs)


def crs_parts(
    crs: CoordinateReferenceSystem,
) -> tuple[CoordinateReferenceSystem, CoordinateReferenceSystem | None]:
    """The horizontal and vertical parts of a compound CRS, else it and None."""
    definition = crs.crs
    if definition.is_compound and len(definition.sub_crs_list) == 2:
        horizontal, vertical = definition.sub_crs_list
        return (
            CoordinateReferenceSystem(horizontal, horizontal.name),
            CoordinateReferenceSystem(vertical, vertical.name),
        )
    return crs, None


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


def _document_unit(text: str, path: str) -> Unit:
    try:
        return length_unit(text)
    except UnitError as error:
        raise InvalidGeoJsonError(path, str(error)) from error


def _label(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or repr(crs.name)


def _shown(value: object) -> str:
    try:
        text = repr(value)
    except ValueError:
        # More than sys.get_int_max_str_digits() digits, possibly nested in value.
        if isinstance(value, int):
            return "a number too long to show"
        return "a value that cannot be shown"
    return text if len(text) <= 40 else f"{text[:37]}..."
