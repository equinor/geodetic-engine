"""Listing coordinate reference systems."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar

from pyproj.aoi import AreaOfInterest
from pyproj.database import query_crs_info

from geodetic_engine._referencing.database import DatabaseIdentity, bound_codes
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._coordinate_systems import CoordinateSystemKind
from geodetic_engine.catalog._entry import LocatedEntry, ObjectRef, columns, order
from geodetic_engine.catalog._filters import AreaFilter, ReferenceFilter, TextFilter
from geodetic_engine.catalog._listing import Listing
from geodetic_engine.catalog._registry import Key, TableKey
from geodetic_engine.catalog.errors import InvalidFilterError
from geodetic_engine.geodesy import AreaOfUse, CoordinateReferenceSystem


class CrsKind(StrEnum):
    """The kind of a coordinate reference system."""

    GEOGRAPHIC_2D = "geographic_2d"
    """Latitude and longitude on an ellipsoid."""

    GEOGRAPHIC_3D = "geographic_3d"
    """Latitude, longitude and ellipsoidal height."""

    GEOCENTRIC = "geocentric"
    """Earth-centred Cartesian X, Y, Z."""

    GEODETIC = "geodetic"
    """A geodetic CRS that is neither geographic nor geocentric, such as the
    planetocentric CRSs of ``IAU_2015``."""

    PROJECTED = "projected"
    """Easting and northing on a map projection of a geodetic CRS."""

    DERIVED_PROJECTED = "derived_projected"
    """A CRS derived from a projected CRS by a further conversion."""

    VERTICAL = "vertical"
    """Gravity-related height or depth."""

    COMPOUND = "compound"
    """A horizontal CRS and a vertical CRS together."""

    ENGINEERING = "engineering"
    """A local CRS, such as a plant or site grid, not tied to the Earth's shape."""


_PROJJSON_TYPES = {
    CrsKind.GEOGRAPHIC_2D: "GeographicCRS",
    CrsKind.GEOGRAPHIC_3D: "GeographicCRS",
    CrsKind.GEOCENTRIC: "GeodeticCRS",
    CrsKind.GEODETIC: "GeodeticCRS",
    CrsKind.PROJECTED: "ProjectedCRS",
    CrsKind.DERIVED_PROJECTED: "DerivedProjectedCRS",
    CrsKind.VERTICAL: "VerticalCRS",
    CrsKind.COMPOUND: "CompoundCRS",
    CrsKind.ENGINEERING: "EngineeringCRS",
}

# crs_view's (table_name, type) to the kind. pyproj's own type cannot be used:
# it reports an engineering CRS as plain "CRS", and filtering by its engineering
# type finds none.
_KINDS = {
    ("geodetic_crs", "geographic 2D"): CrsKind.GEOGRAPHIC_2D,
    ("geodetic_crs", "geographic 3D"): CrsKind.GEOGRAPHIC_3D,
    ("geodetic_crs", "geocentric"): CrsKind.GEOCENTRIC,
    ("geodetic_crs", "other"): CrsKind.GEODETIC,
    ("projected_crs", "projected"): CrsKind.PROJECTED,
    ("derived_projected_crs", "derived projected"): CrsKind.DERIVED_PROJECTED,
    ("vertical_crs", "vertical"): CrsKind.VERTICAL,
    ("compound_crs", "compound"): CrsKind.COMPOUND,
    ("engineering_crs", "engineering"): CrsKind.ENGINEERING,
}

# For a row pyproj lists that crs_view does not, by pyproj's type name.
_FROM_PJTYPE = {
    "GEOGRAPHIC_2D_CRS": CrsKind.GEOGRAPHIC_2D,
    "GEOGRAPHIC_3D_CRS": CrsKind.GEOGRAPHIC_3D,
    "GEOCENTRIC_CRS": CrsKind.GEOCENTRIC,
    "GEODETIC_CRS": CrsKind.GEODETIC,
    "PROJECTED_CRS": CrsKind.PROJECTED,
    "DERIVED_PROJECTED_CRS": CrsKind.DERIVED_PROJECTED,
    "VERTICAL_CRS": CrsKind.VERTICAL,
    "COMPOUND_CRS": CrsKind.COMPOUND,
    "ENGINEERING_CRS": CrsKind.ENGINEERING,
    "CRS": CrsKind.ENGINEERING,
}

_TABLES = {
    CrsKind.GEOGRAPHIC_2D: "geodetic_crs",
    CrsKind.GEOGRAPHIC_3D: "geodetic_crs",
    CrsKind.GEOCENTRIC: "geodetic_crs",
    CrsKind.GEODETIC: "geodetic_crs",
    CrsKind.PROJECTED: "projected_crs",
    CrsKind.DERIVED_PROJECTED: "derived_projected_crs",
    CrsKind.VERTICAL: "vertical_crs",
    CrsKind.COMPOUND: "compound_crs",
    CrsKind.ENGINEERING: "engineering_crs",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class CrsEntry(LocatedEntry):
    """A coordinate reference system the active database holds.

    Listed by :func:`~geodetic_engine.catalog.crs`. Everything here is read
    from the database without building the CRS;
    :meth:`resolve` builds it, the way
    :class:`~geodetic_engine.geodesy.CoordinateReferenceSystem` resolves the
    same code for a transformation.

    Attributes:
        kind: What kind of CRS it is.
        projection_method: The projection method of a projected CRS, as PROJ
            names it, for example ``"Transverse Mercator"``; None for any
            other kind.
        datums: The datum or datum ensemble the CRS is on. A compound CRS has
            two, horizontal then vertical, and a projected CRS reports its
            base CRS's. Empty for a CRS the database stores only as WKT text,
            as it does some ESRI CRSs, since no datum code is stored for it.
        coordinate_system: The coordinate system, or None for a compound CRS
            and for a CRS stored only as WKT text.
        axis_units: The unit of each axis in declared axis order: for a
            compound CRS, the horizontal axes then the vertical one.
        bound: Whether the database stores the CRS as a bound CRS, carrying
            the transformation to its hub. Only a database built by this
            package does (see :mod:`geodetic_engine.osdudb`); PROJ's own
            holds none.
        osdu_ids: The ids of the OSDU catalogue records that publish this CRS,
            when the database was built from an OSDU catalogue; empty
            otherwise.
        has_persistable_reference: Whether that build also recorded the
            ``persistableReference`` one of those records publishes.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = (
        "kind",
        "projection_method",
        "datums",
        "coordinate_system",
        "axis_units",
        "area_of_use",
        "bound",
        "osdu_ids",
    )

    kind: CrsKind
    projection_method: str | None = None
    datums: tuple[ObjectRef, ...] = ()
    coordinate_system: ObjectRef | None = field(default=None, repr=False)
    axis_units: tuple[ObjectRef, ...] = field(default=(), repr=False)
    bound: bool = False
    osdu_ids: tuple[str, ...] = field(default=(), repr=False)
    has_persistable_reference: bool = field(default=False, repr=False)

    @property
    def projjson_type(self) -> str:
        """``"ProjectedCRS"``, ``"GeographicCRS"``, ``"BoundCRS"`` and so on."""
        return "BoundCRS" if self.bound else _PROJJSON_TYPES[self.kind]

    def resolve(self) -> CoordinateReferenceSystem:
        """Build the CRS, ready to transform with.

        Resolved the way :mod:`geodetic_engine.geodesy` resolves the code, so
        a bound CRS keeps the transformation the database stores with it.

        Raises:
            UnresolvableCRSError: If PROJ cannot build it.
        """
        return CoordinateReferenceSystem.from_user_input(self.authority_code)

    def to_json_dict(self) -> dict[str, Any]:
        """The CRS's full PROJJSON, exactly as PROJ exports it.

        Example:
            >>> from geodetic_engine import catalog
            >>> catalog.crs(code="EPSG:25832").one().to_json_dict()["type"]
            'ProjectedCRS'
        """
        definition: dict[str, Any] = self.resolve().crs.to_json_dict()
        return definition

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"kind": self.kind.value}
        summary.update(super()._summary())
        if self.projection_method:
            summary["projection_method"] = self.projection_method
        if self.datums:
            summary["datums"] = [datum.to_json_dict() for datum in self.datums]
        if self.coordinate_system is not None:
            summary["coordinate_system"] = self.coordinate_system.to_json_dict()
        if self.axis_units:
            summary["axis_units"] = [unit.to_json_dict() for unit in self.axis_units]
        summary["bound"] = self.bound
        if self.osdu_ids:
            summary["osdu_ids"] = list(self.osdu_ids)
            summary["has_persistable_reference"] = self.has_persistable_reference
        return summary


@dataclass(frozen=True, slots=True)
class Structure:
    """What a CRS is built from, as proj.db states it."""

    table: str
    kind: CrsKind
    datums: tuple[TableKey, ...]
    coordinate_system: Key | None
    axis_units: tuple[Key, ...]


@lru_cache(maxsize=4)
def structures(databases: DatabaseIdentity) -> Mapping[Key, Structure]:
    """The kind, datums and coordinate system of every CRS in the database."""
    with _registry.reading(databases) as connection:
        kinds = {
            (str(auth), str(code)): (str(table), str(kind))
            for table, auth, code, kind in connection.execute(
                "SELECT table_name, auth_name, code, type FROM crs_view"
            )
        }
        datums: dict[Key, TableKey | None] = {}
        bases: dict[Key, Key | None] = {}
        components: dict[Key, tuple[Key | None, Key | None]] = {}
        systems: dict[Key, Key | None] = {}

        def pair(auth: object, code: object) -> Key | None:
            return None if code is None else (str(auth), str(code))

        for table, datum_table in (
            ("geodetic_crs", "geodetic_datum"),
            ("vertical_crs", "vertical_datum"),
            ("engineering_crs", "engineering_datum"),
        ):
            for auth, code, cs_auth, cs_code, d_auth, d_code in connection.execute(
                "SELECT auth_name, code, coordinate_system_auth_name, "
                f"coordinate_system_code, datum_auth_name, datum_code FROM {table}"
            ):
                key = (str(auth), str(code))
                systems[key] = pair(cs_auth, cs_code)
                datum = pair(d_auth, d_code)
                datums[key] = None if datum is None else (datum_table, *datum)
        for table, base_column in (
            ("projected_crs", "geodetic_crs"),
            ("derived_projected_crs", "base_crs"),
        ):
            if not _registry.has_table(connection, table):
                continue
            for auth, code, cs_auth, cs_code, b_auth, b_code in connection.execute(
                "SELECT auth_name, code, coordinate_system_auth_name, "
                f"coordinate_system_code, {base_column}_auth_name, "
                f"{base_column}_code FROM {table}"
            ):
                key = (str(auth), str(code))
                systems[key] = pair(cs_auth, cs_code)
                bases[key] = pair(b_auth, b_code)
        for auth, code, h_auth, h_code, v_auth, v_code in connection.execute(
            "SELECT auth_name, code, horiz_crs_auth_name, horiz_crs_code, "
            "vertical_crs_auth_name, vertical_crs_code FROM compound_crs"
        ):
            components[(str(auth), str(code))] = (
                pair(h_auth, h_code),
                pair(v_auth, v_code),
            )

    coordinate_systems = _registry.coordinate_system_rows(databases)

    def datums_of(key: Key | None, depth: int = 0) -> tuple[TableKey, ...]:
        if key is None or depth > 4:
            return ()
        if key in components:
            return tuple(
                datum
                for part in components[key]
                for datum in datums_of(part, depth + 1)
            )
        if key in bases:
            return datums_of(bases[key], depth + 1)
        datum = datums.get(key)
        return () if datum is None else (datum,)

    def units_of(key: Key | None) -> tuple[Key, ...]:
        if key is None:
            return ()
        if key in components:
            return tuple(unit for part in components[key] for unit in units_of(part))
        system = coordinate_systems.get(systems.get(key) or ("", ""))
        if system is None:
            return ()
        return tuple(axis.unit for axis in system.axes if axis.unit is not None)

    found = {}
    for key, (table, kind) in kinds.items():
        found[key] = Structure(
            table=table,
            kind=_KINDS.get((table, kind), CrsKind.ENGINEERING),
            datums=datums_of(key),
            coordinate_system=systems.get(key),
            axis_units=units_of(key),
        )
    return found


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[CrsEntry, ...]:
    """Every CRS in the database, built once per database."""
    shapes = structures(databases)
    names = _registry.names(databases)
    aliases = _registry.aliases(databases)
    replacements = _registry.replacements(databases)
    units = _registry.unit_rows(databases)
    osdu = _registry.osdu_identifiers(databases)
    published = _registry.with_persistable_reference(databases)
    bound = bound_codes()
    system_refs = {
        key: ObjectRef(*key, _registry.coordinate_system_name(row, units))
        for key, row in _registry.coordinate_system_rows(databases).items()
    }
    unit_refs = {key: ObjectRef(*key, row.name) for key, row in units.items()}
    refs: dict[TableKey, ObjectRef] = {}

    def datum_ref(datum: TableKey) -> ObjectRef:
        if datum not in refs:
            refs[datum] = ObjectRef(datum[1], datum[2], names.get(datum, ""))
        return refs[datum]

    rows: dict[Key, list[Any]] = {}
    for info in query_crs_info(allow_deprecated=True):
        key = (info.auth_name, info.code)
        found = rows.setdefault(key, [info, []])
        # PROJ lists a CRS once for each of its usages.
        if info.area_of_use is not None:
            area = AreaOfUse(
                west=info.area_of_use.west,
                south=info.area_of_use.south,
                east=info.area_of_use.east,
                north=info.area_of_use.north,
                name=info.area_of_use.name or "",
            )
            if area not in found[1]:
                found[1].append(area)

    built = []
    for key, (info, areas) in rows.items():
        shape = shapes.get(key)
        if shape is None:
            kind = _FROM_PJTYPE.get(info.type.name, CrsKind.ENGINEERING)
            shape = Structure(_TABLES[kind], kind, (), None, ())
        object_key = (shape.table, *key)
        system = shape.coordinate_system
        built.append(
            CrsEntry(
                auth_name=info.auth_name,
                code=info.code,
                name=info.name,
                deprecated=bool(info.deprecated),
                aliases=aliases.get(object_key, ()),
                replaced_by=replacements.get(object_key, ()),
                areas_of_use=tuple(areas),
                kind=shape.kind,
                projection_method=info.projection_method_name or None,
                datums=tuple(datum_ref(datum) for datum in shape.datums),
                coordinate_system=None if system is None else system_refs.get(system),
                axis_units=tuple(
                    unit_refs.get(unit) or ObjectRef(*unit, "")
                    for unit in shape.axis_units
                ),
                bound=(info.auth_name.casefold(), info.code) in bound,
                osdu_ids=osdu.get(object_key, ()),
                has_persistable_reference=object_key in published,
            )
        )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


def _within(
    bounds: _filters.Bounds, whole: bool, authorities: tuple[str, ...] | None
) -> frozenset[Key]:
    """The CRSs PROJ itself finds for an area of interest."""
    area = AreaOfInterest(*bounds)
    found: set[Key] = set()
    for authority in authorities or (None,):
        found |= {
            (info.auth_name, info.code)
            for info in query_crs_info(
                auth_name=authority,
                area_of_interest=area,
                contains=whole,
                allow_deprecated=True,
            )
        }
    return frozenset(found)


def crs(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    kind: str | CrsKind | Iterable[str | CrsKind] | None = None,
    datum: ReferenceFilter | None = None,
    unit: ReferenceFilter | None = None,
    coordinate_system_kind: (
        str | CoordinateSystemKind | Iterable[str | CoordinateSystemKind] | None
    ) = None,
    projection_method: TextFilter | None = None,
    area_of_interest: AreaFilter | None = None,
    contains: bool = False,
    area_name: TextFilter | None = None,
    bound: bool | None = None,
    deprecated: bool | None = False,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[CrsEntry]:
    """List the coordinate reference systems the active database holds.

    Every filter given must hold. Rows, names, kinds, deprecation and areas
    come from :func:`pyproj.database.query_crs_info`, and an
    ``area_of_interest`` is tested by PROJ itself; the datum, coordinate
    system and units of each CRS are read from ``proj.db``.

    Args:
        authority: Only these authorities, for example ``"EPSG"``; see
            :func:`~geodetic_engine.catalog.authorities`.
        name: Text the name or an alias contains, without case, or a
            :class:`re.Pattern` to search them with.
        code: Only these codes: ``"EPSG:25832"``, a bare code such as
            ``25832`` in any authority, an entry or an :class:`ObjectRef`, or
            several.
        kind: Only these kinds, by :class:`CrsKind` or its value, for example
            ``"projected"``.
        datum: Only CRSs on these datums or datum ensembles, by code, entry or
            name, for example ``"EPSG:6258"`` or ``"ETRS89"``. Matches the
            datum exactly: ETRS89 does not match a CRS on one of its member
            frames, such as ETRF2000.
        unit: Only CRSs with an axis in one of these units, by code, entry or
            name, for example ``"US survey foot"``.
        coordinate_system_kind: Only CRSs whose coordinate system is of these
            kinds, for example ``"Cartesian"``.
        projection_method: Text the projection method's name contains, or a
            pattern, for example ``"Transverse Mercator"``.
        area_of_interest: ``(west, south, east, north)`` in degrees, or a
            pyproj or :class:`~geodetic_engine.geodesy.AreaOfUse` box. West
            greater than east crosses the antimeridian.
        contains: With ``area_of_interest``, only CRSs whose area of use
            contains it entirely, rather than intersects it.
        area_name: Text the area of use's description contains, or a pattern,
            for example ``"Norway"``.
        bound: True for only bound CRSs, False for none.
        deprecated: False (default) leaves deprecated CRSs out, True lists
            only them, None lists both.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The CRSs, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed, or ``datum`` or
            ``unit`` names nothing of that kind.

    Example:
        >>> from geodetic_engine import catalog
        >>> found = catalog.crs(
        ...     authority="EPSG", kind="projected", datum="ETRS89",
        ...     name="UTM zone 32N", area_of_interest=(10.0, 59.0, 11.0, 60.0),
        ... )
        >>> [entry.name for entry in found]
        ['ETRS89 / UTM zone 32N (N-E)', 'ETRS89 / UTM zone 32N']
    """
    code, kind, datum = (
        _filters.given(code),
        _filters.given(kind),
        _filters.given(datum),
    )
    unit = _filters.given(unit)
    coordinate_system_kind = _filters.given(coordinate_system_kind)
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    kinds = _filters.choices(kind, CrsKind, "CRS kind")
    datums = _filters.references(
        datum,
        "datum",
        _filters.registered(_registry.DATUM_TABLES, databases),
        databases,
    )
    units = _filters.references(
        unit,
        "unit",
        _filters.registered(frozenset({"unit_of_measure"}), databases),
        databases,
    )
    systems = _filters.choices(
        coordinate_system_kind, CoordinateSystemKind, "coordinate system kind"
    )
    methods = _filters.text(projection_method, "projection_method")
    bounds = _filters.area_of_interest(area_of_interest)
    whole = _filters.flag(contains, "contains")
    if whole is None:
        raise InvalidFilterError("contains must be True or False")
    areas = _filters.text(area_name, "area_name")
    bound_ = _filters.flag(bound, "bound")
    deprecated_ = _filters.flag(deprecated, "deprecated")
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)
    system_rows = _registry.coordinate_system_rows(databases) if systems else {}

    def admits(entry: CrsEntry, inside: frozenset[Key] | None) -> bool:
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if not _filters.admits_flag(deprecated_, entry.deprecated):
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if kinds is not None and entry.kind not in kinds:
            return False
        if not _filters.admits_flag(bound_, entry.bound):
            return False
        if names is not None and not names((entry.name, *entry.aliases)):
            return False
        if datums is not None and not any(
            _filters.folded(d.auth_name, d.code) in datums for d in entry.datums
        ):
            return False
        if units is not None and not any(
            _filters.folded(u.auth_name, u.code) in units for u in entry.axis_units
        ):
            return False
        if systems is not None:
            cs = entry.coordinate_system
            row = None if cs is None else system_rows.get((cs.auth_name, cs.code))
            if row is None or row.kind.casefold() not in {
                s.value.casefold() for s in systems
            }:
                return False
        if methods is not None and not methods((entry.projection_method or "",)):
            return False
        if areas is not None and not areas(a.name for a in entry.areas_of_use):
            return False
        return inside is None or (entry.auth_name, entry.code) in inside

    def produce() -> Iterator[CrsEntry]:
        inside = None if bounds is None else _within(bounds, whole, authorities)
        for entry in entries(databases):
            if admits(entry, inside):
                yield entry

    filters: Mapping[str, Any] = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        kind=kind,
        datum=datum,
        unit=unit,
        coordinate_system_kind=coordinate_system_kind,
        projection_method=projection_method,
        area_of_interest=bounds,
        contains=whole if bounds is not None else None,
        area_name=area_name,
        bound=bound_,
        deprecated=deprecated_,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "crs",
        filters,
        produce,
        columns=columns(CrsEntry),
        databases=databases,
        limit=limit,
        offset=offset,
    )
