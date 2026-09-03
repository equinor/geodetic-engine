"""Taking an OSDU record's WKT apart into proj.db building blocks.

An OSDU catalogue names a CRS's coordinate system, datum and projection by
authority code, but it defines none of them: the only place an ellipsoid's
axis, a prime meridian's longitude or an operation's parameters are stated is
the record's own WKT. So every structural detail is read back out of the WKT
through PROJ, which is the same parser that will later read the database.

Two things PROJ does not carry through its PROJJSON export have to be recovered
elsewhere:

* **Nested identifiers.** ``CRS.to_json_dict()`` drops the identifiers of the
  datum, ellipsoid and prime meridian, so those are read from the pyproj
  sub-objects, which keep them.
* **Unit identifiers.** A unit is exported by name and conversion factor with
  no code at all, so units are resolved against the ``unit_of_measure`` table
  already in the database. A unit that cannot be resolved is refused rather
  than guessed at, because an operation with the wrong rotation unit produces
  coordinates that are wrong by a plausible-looking amount.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from pyproj import CRS
from pyproj.crs import CoordinateOperation
from pyproj.exceptions import CRSError

from geodetic_engine.osdudb.errors import UnreadableDefinitionError
from geodetic_engine.projdb import parameters as pm

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]

# PROJJSON unit types mapped onto proj.db's unit_of_measure.type vocabulary.
UNIT_TYPES: dict[str, str] = {
    "LinearUnit": "length",
    "AngularUnit": "angle",
    "ScaleUnit": "scale",
    "TimeUnit": "time",
    "ParametricUnit": "parametric",
}

# PROJJSON writes these three units as a bare string rather than an object.
SHORTHAND_UNITS: dict[str, str] = {
    "metre": "length",
    "degree": "angle",
    "unity": "scale",
}

# PROJJSON datum types mapped onto the proj.db table that stores them.
DATUM_TABLES: dict[str, str] = {
    "GeodeticReferenceFrame": "geodetic_datum",
    "DynamicGeodeticReferenceFrame": "geodetic_datum",
    "DatumEnsemble": "geodetic_datum",
    "VerticalReferenceFrame": "vertical_datum",
    "DynamicVerticalReferenceFrame": "vertical_datum",
    "EngineeringDatum": "engineering_datum",
}

# proj.db's coordinate_system.type vocabulary, keyed by PROJJSON subtype.
COORDINATE_SYSTEM_TYPES: dict[str, str] = {
    "Cartesian": "Cartesian",
    "ellipsoidal": "ellipsoidal",
    "vertical": "vertical",
    "spherical": "spherical",
    "ordinal": "ordinal",
    "parametric": "parametric",
    "temporal": "temporal",
    "temporalCount": "temporalCount",
    "temporalMeasure": "temporalMeasure",
    "DateTimeTemporal": "DateTimeTemporal",
}

# Every ellipsoid in a terrestrial catalogue is an ellipsoid of the Earth.
CELESTIAL_BODY = ("PROJ", "EARTH")

# Two conversion factors are the same unit when they agree to this many
# significant figures. Tighter than any real difference between EPSG units and
# looser than the rounding PROJ applies when it writes a factor out.
_FACTOR_TOLERANCE = 1e-10


@dataclass(frozen=True, slots=True)
class Identifier:
    """An authority and code naming one object."""

    auth_name: str
    code: str


def parse_crs(wkt: str | None, described: str) -> CRS:
    """Parse an OSDU record's WKT as a CRS.

    Args:
        wkt: The record's ``OGCWellKnownText2``.
        described: Identity of the record, for the error message.

    Returns:
        The parsed CRS.

    Raises:
        UnreadableDefinitionError: If there is no WKT, or PROJ will not parse
            it. Both mean the catalogue states no definition this build can
            use.
    """
    if not wkt:
        raise UnreadableDefinitionError(f"{described} carries no WKT definition")
    try:
        return CRS.from_wkt(wkt)
    except CRSError as exc:
        raise UnreadableDefinitionError(
            f"{described} has WKT PROJ rejects: {exc}"
        ) from exc


def parse_operation(wkt: str | None, described: str) -> CoordinateOperation:
    """Parse an OSDU record's WKT as a coordinate operation.

    Args:
        wkt: The record's ``OGCWellKnownText2``.
        described: Identity of the record, for the error message.

    Returns:
        The parsed operation.

    Raises:
        UnreadableDefinitionError: If there is no WKT, or PROJ will not parse it.
    """
    if not wkt:
        raise UnreadableDefinitionError(f"{described} carries no WKT definition")
    try:
        return CoordinateOperation.from_string(wkt)
    except CRSError as exc:
        raise UnreadableDefinitionError(
            f"{described} has WKT PROJ rejects: {exc}"
        ) from exc


def identifier_of(obj: Any) -> Identifier | None:
    """Return the identifier a pyproj object or PROJJSON fragment carries."""
    document = obj.to_json_dict() if hasattr(obj, "to_json_dict") else obj
    if not isinstance(document, dict):
        return None
    id_dict = document.get("id") or {}
    authority = str(id_dict.get("authority") or "").strip()
    raw = id_dict.get("code")
    if not authority or raw in (None, ""):
        return None
    return Identifier(auth_name=authority, code=str(raw))


class UnitResolver:
    """Resolves a PROJJSON unit onto a unit already defined in the database.

    PROJ exports a unit by name and conversion factor without its code, so the
    code is recovered by matching against ``unit_of_measure``. Matching is by
    name first and by conversion factor second, because a factor is what
    actually determines the arithmetic while a name is only a label.

    Example:
        >>> resolver = UnitResolver(connection)  # doctest: +SKIP
        >>> resolver.resolve("metre", described="ellipsoid EPSG:7030")  # doctest: +SKIP
        Identifier(auth_name='EPSG', code='9001')
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._by_name: dict[tuple[str, str], Identifier] = {}
        self._by_factor: list[tuple[str, float, Identifier]] = []
        statement = (
            "SELECT auth_name, code, name, type, conv_factor FROM unit_of_measure "
            # EPSG first so a unit that several authorities define resolves to
            # the EPSG one, which is the authority for units.
            "ORDER BY CASE auth_name WHEN 'EPSG' THEN 0 ELSE 1 END, auth_name, code"
        )
        for auth_name, code, name, unit_type, factor in connection.execute(statement):
            identifier = Identifier(str(auth_name), str(code))
            self._by_name.setdefault((str(unit_type), str(name).casefold()), identifier)
            if factor is not None:
                self._by_factor.append((str(unit_type), float(factor), identifier))

    def resolve(self, unit: Any, *, described: str) -> Identifier:
        """Resolve one PROJJSON unit.

        Args:
            unit: A unit as PROJJSON states it: the string ``"metre"``,
                ``"degree"`` or ``"unity"``, or an object with a type, a name
                and a conversion factor.
            described: What the unit belongs to, for the error message.

        Returns:
            The unit's authority and code.

        Raises:
            UnreadableDefinitionError: If the unit is absent or matches nothing
                in the database. Falling back to a default unit here would
                silently rescale every value expressed in it.
        """
        resolved = self.resolve_optional(unit)
        if resolved is None:
            raise UnreadableDefinitionError(
                f"the unit of {described} is {unit!r}, which is not a unit of "
                "measure this database defines. Its code cannot be guessed "
                "without changing what the values mean."
            )
        return resolved

    def resolve_optional(self, unit: Any) -> Identifier | None:
        """Resolve a unit, returning None instead of raising when it is unknown."""
        if isinstance(unit, str):
            unit_type = SHORTHAND_UNITS.get(unit)
            return None if unit_type is None else self._by_name.get((unit_type, unit))
        if not isinstance(unit, dict):
            return None

        if (identifier := identifier_of(unit)) is not None:
            return identifier

        unit_type = UNIT_TYPES.get(str(unit.get("type") or ""))
        if unit_type is None:
            return None
        name = str(unit.get("name") or "").casefold()
        if (by_name := self._by_name.get((unit_type, name))) is not None:
            return by_name

        factor = unit.get("conversion_factor")
        if not isinstance(factor, int | float):
            return None
        for candidate_type, candidate_factor, identifier in self._by_factor:
            if candidate_type == unit_type and math.isclose(
                candidate_factor, float(factor), rel_tol=_FACTOR_TOLERANCE
            ):
                return identifier
        return None


def measure(raw: Any, fallback: Any) -> tuple[float | None, Any]:
    """Split a PROJJSON measure into its value and its unit.

    PROJJSON writes a measure either as a bare number, meaning the type's
    default unit, or as an object carrying its own unit.

    Args:
        raw: The measure.
        fallback: Unit to assume when the measure is a bare number.

    Returns:
        The value and the unit it is expressed in.
    """
    if isinstance(raw, dict):
        value = raw.get("value")
        return (
            float(value) if isinstance(value, int | float) else None,
            raw.get("unit", fallback),
        )
    if isinstance(raw, int | float):
        return float(raw), fallback
    return None, fallback


def ellipsoid_row(
    ellipsoid: Any, units: UnitResolver
) -> tuple[Identifier, dict[str, Any]] | None:
    """Build an ``ellipsoid`` row from a pyproj ellipsoid.

    The semi-minor axis and the inverse flattening are kept apart: an ellipsoid
    is defined by one or the other, and whichever the authority states is
    stored while the other is left NULL rather than derived, so PROJ applies
    its own conversion at the precision it needs.

    Returns:
        The ellipsoid's identifier and its row, or None if the WKT gave it no
        identifier and it therefore cannot be referenced.

    Raises:
        UnreadableDefinitionError: If its unit cannot be resolved.
    """
    identifier = identifier_of(ellipsoid)
    if identifier is None:
        return None
    document = ellipsoid.to_json_dict()
    described = f"ellipsoid {identifier.auth_name}:{identifier.code}"

    radius, radius_unit = measure(document.get("radius"), "metre")
    semi_major, unit = measure(document.get("semi_major_axis"), "metre")
    semi_minor, _ = measure(document.get("semi_minor_axis"), unit)
    inv_flattening = document.get("inverse_flattening")

    if radius is not None:
        # A sphere has no flattening; PROJ stores it with equal axes.
        semi_major, semi_minor, inv_flattening, unit = radius, radius, None, radius_unit
    if semi_major is None or semi_major <= 0:
        raise UnreadableDefinitionError(f"{described} has no positive semi-major axis")

    uom = units.resolve(unit, described=described)
    return identifier, {
        "auth_name": identifier.auth_name,
        "code": identifier.code,
        "name": str(document.get("name") or "unknown"),
        "description": None,
        "celestial_body_auth_name": CELESTIAL_BODY[0],
        "celestial_body_code": CELESTIAL_BODY[1],
        "semi_major_axis": semi_major,
        "uom_auth_name": uom.auth_name,
        "uom_code": uom.code,
        "inv_flattening": inv_flattening,
        "semi_minor_axis": None if inv_flattening else semi_minor,
        "deprecated": 0,
    }


def prime_meridian_row(
    prime_meridian: Any, units: UnitResolver
) -> tuple[Identifier, dict[str, Any]] | None:
    """Build a ``prime_meridian`` row, keeping the longitude in its own unit.

    Returns:
        The prime meridian's identifier and its row, or None when the WKT gave
        it no identifier.

    Raises:
        UnreadableDefinitionError: If its unit cannot be resolved.
    """
    identifier = identifier_of(prime_meridian)
    if identifier is None:
        return None
    document = prime_meridian.to_json_dict()
    described = f"prime meridian {identifier.auth_name}:{identifier.code}"
    longitude, unit = measure(document.get("longitude"), "degree")
    uom = units.resolve(unit, described=described)
    return identifier, {
        "auth_name": identifier.auth_name,
        "code": identifier.code,
        "name": str(document.get("name") or "unknown"),
        "longitude": longitude,
        "uom_auth_name": uom.auth_name,
        "uom_code": uom.code,
        "deprecated": 0,
    }


def datum_table(datum: Any) -> str | None:
    """Return the proj.db table a datum belongs in, or None if it is unknown."""
    document = datum.to_json_dict() if hasattr(datum, "to_json_dict") else datum
    return DATUM_TABLES.get(str((document or {}).get("type") or ""))


def datum_row(
    datum: Any,
    table: str,
    *,
    ellipsoid: Identifier | None = None,
    prime_meridian: Identifier | None = None,
) -> tuple[Identifier, dict[str, Any]] | None:
    """Build a datum row for one of the three datum tables.

    A dynamic datum's frame reference epoch and an ensemble's accuracy are
    carried through; dropping either turns a time-dependent or approximate
    datum into a static, exact one that answers with the wrong coordinates.

    Args:
        datum: The pyproj datum.
        table: ``geodetic_datum``, ``vertical_datum`` or ``engineering_datum``.
        ellipsoid: Identifier of its ellipsoid, required for a geodetic datum.
        prime_meridian: Identifier of its prime meridian, required for a
            geodetic datum.

    Returns:
        The datum's identifier and its row, or None when the WKT gave it no
        identifier.

    Raises:
        UnreadableDefinitionError: If a geodetic datum has no ellipsoid or no
            prime meridian to reference.
    """
    identifier = identifier_of(datum)
    if identifier is None:
        return None
    document = datum.to_json_dict()
    row: dict[str, Any] = {
        "auth_name": identifier.auth_name,
        "code": identifier.code,
        "name": str(document.get("name") or "unknown"),
        "publication_date": document.get("publication_date"),
        "anchor": document.get("anchor"),
        "anchor_epoch": _epoch(document.get("anchor_epoch")),
        "deprecated": 0,
    }
    if table == "engineering_datum":
        # engineering_datum has no description column.
        return identifier, row

    row |= {
        "description": None,
        "frame_reference_epoch": _epoch(document.get("frame_reference_epoch")),
        "ensemble_accuracy": document.get("accuracy"),
    }
    if table == "vertical_datum":
        return identifier, row

    if ellipsoid is None or prime_meridian is None:
        raise UnreadableDefinitionError(
            f"geodetic datum {identifier.auth_name}:{identifier.code} has no "
            "identified ellipsoid or prime meridian"
        )
    return identifier, row | {
        "ellipsoid_auth_name": ellipsoid.auth_name,
        "ellipsoid_code": ellipsoid.code,
        "prime_meridian_auth_name": prime_meridian.auth_name,
        "prime_meridian_code": prime_meridian.code,
    }


def coordinate_system_rows(
    crs: CRS, identifier: Identifier, units: UnitResolver
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build the ``coordinate_system`` row and its ``axis`` rows.

    Axes are written in the order the WKT declares them and are never
    re-sorted, because axis order is part of the CRS definition. The identifier
    comes from the OSDU record rather than from the WKT: PROJ's PROJJSON export
    does not carry a coordinate system's code.

    Args:
        crs: The parsed CRS.
        identifier: Authority and code the OSDU record gives its coordinate
            system.
        units: Resolver for the axis units.

    Returns:
        The coordinate system row and its axis rows.

    Raises:
        UnreadableDefinitionError: If the CRS has no coordinate system, no
            axes, or an axis whose unit cannot be resolved.
    """
    described = f"coordinate system {identifier.auth_name}:{identifier.code}"
    if crs.coordinate_system is None:
        raise UnreadableDefinitionError(f"{described} is not stated by the WKT")
    document = crs.coordinate_system.to_json_dict()
    axes = document.get("axis") or []
    if not axes:
        raise UnreadableDefinitionError(f"{described} has no axes")

    subtype = str(document.get("subtype") or "")
    system = {
        "auth_name": identifier.auth_name,
        "code": identifier.code,
        "type": COORDINATE_SYSTEM_TYPES.get(subtype, subtype),
        "dimension": len(axes),
    }
    axis_rows = []
    for order, axis in enumerate(axes, start=1):
        uom = units.resolve(axis.get("unit"), described=f"axis {order} of {described}")
        axis_rows.append(
            {
                "auth_name": identifier.auth_name,
                "code": f"{identifier.code}_{order}",
                "name": str(axis.get("name") or "unknown"),
                "abbrev": str(axis.get("abbreviation") or ""),
                "orientation": str(axis.get("direction") or ""),
                "coordinate_system_auth_name": identifier.auth_name,
                "coordinate_system_code": identifier.code,
                "coordinate_system_order": order,
                "uom_auth_name": uom.auth_name,
                "uom_code": uom.code,
            }
        )
    return system, axis_rows


def parameters_of(
    operation: Any, units: UnitResolver, described: str
) -> list[pm.Parameter]:
    """Read an operation's parameters, in the order the WKT declares them.

    A parameter whose value is a string names a grid file rather than stating a
    number, and is recorded as such so that
    :func:`geodetic_engine.projdb.parameters.classify` can route the operation
    to ``grid_transformation``.

    Args:
        operation: A pyproj coordinate operation, or a PROJJSON conversion.
        units: Resolver for the parameter units.
        described: Identity of the operation, for error messages.

    Returns:
        The parameters, values unconverted and units named.

    Raises:
        UnreadableDefinitionError: If a numeric parameter's unit cannot be
            resolved.
    """
    document = (
        operation.to_json_dict() if hasattr(operation, "to_json_dict") else operation
    )
    parameters: list[pm.Parameter] = []
    for entry in (document or {}).get("parameters") or []:
        identifier = identifier_of(entry)
        if identifier is None:
            raise UnreadableDefinitionError(
                f"{described} has a parameter {entry.get('name')!r} with no "
                "EPSG code, which proj.db has no column to record"
            )
        name = str(entry.get("name") or "")
        raw = entry.get("value")
        if isinstance(raw, str):
            parameters.append(
                pm.Parameter(
                    code=identifier.code,
                    name=name,
                    file=raw,
                    auth_name=identifier.auth_name,
                )
            )
            continue
        uom = units.resolve(entry.get("unit"), described=f"{name} of {described}")
        parameters.append(
            pm.Parameter(
                code=identifier.code,
                name=name,
                value=float(raw) if isinstance(raw, int | float) else None,
                uom_auth_name=uom.auth_name,
                uom_code=uom.code,
                auth_name=identifier.auth_name,
            )
        )
    return parameters


def method_of(operation: Any) -> Identifier | None:
    """Return the EPSG method identifier an operation states."""
    document = (
        operation.to_json_dict() if hasattr(operation, "to_json_dict") else operation
    )
    return identifier_of((document or {}).get("method") or {})


def geodetic_type(crs: CRS) -> str:
    """Return the ``geodetic_crs.type`` term matching a parsed CRS."""
    if crs.is_geocentric:
        return "geocentric"
    return "geographic 3D" if len(crs.axis_info) > 2 else "geographic 2D"


def _epoch(value: Any) -> str | None:
    """Return an epoch as recorded, without rounding.

    A dynamic datum's reference epoch is part of the datum's identity;
    reformatting it risks losing precision.
    """
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("value")
    return None if value in (None, "") else str(value)
