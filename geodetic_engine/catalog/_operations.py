"""Listing coordinate operations: conversions, transformations and chains."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar, NamedTuple, cast

from pyproj import CRS
from pyproj.crs import CoordinateOperation

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import (
    LocatedEntry,
    ObjectRef,
    RegistryEntry,
    accuracy_json,
    authority_id,
    columns,
    order,
)
from geodetic_engine.catalog._filters import (
    AreaFilter,
    Folded,
    ReferenceFilter,
    TextFilter,
)
from geodetic_engine.catalog._listing import Listing
from geodetic_engine.catalog._registry import Key
from geodetic_engine.catalog.errors import InvalidFilterError

# For the annotations the dataclass inherits from LocatedEntry.
from geodetic_engine.geodesy import AreaOfUse as AreaOfUse
from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    GeodesyError,
    GridUsage,
)

# The words of a PROJ string. A value in double quotes may hold spaces.
_WORDS = re.compile(r'(?:[^\s"]|"[^"]*")+')
# The parameters PROJ takes grid files from: "file" is tinshift's.
_GRID_KEYS = frozenset({"grids", "file"})
# The grid PROJ makes up, shifting nothing anywhere. It is no file.
_NULL_GRID = "null"


def _stated(text: str) -> Iterator[str]:
    """The grid files a PROJ string names, as PROJ reads the string.

    A word is ``key=value``, with or without ``+`` before it. The value is a
    list such as ``a.tif,@b.tif``, in double quotes where a name has a space.
    """
    for word in _WORDS.findall(text):
        key, _, value = word.removeprefix("+").partition("=")
        if key in _GRID_KEYS:
            if len(value) > 1 and value[0] == value[-1] == '"':
                value = value[1:-1]
            yield from value.split(",")


class OperationKind(StrEnum):
    """The kind of a coordinate operation."""

    CONVERSION = "conversion"
    """A change of coordinates on one datum, such as a map projection."""

    TRANSFORMATION = "transformation"
    """A change of datum, by parameters or by grid."""

    CONCATENATED_OPERATION = "concatenated_operation"
    """A chain of operations the authority publishes as one."""

    POINT_MOTION_OPERATION = "point_motion_operation"
    """A change of coordinate epoch within one CRS, such as a velocity grid."""


_PROJJSON_TYPES = {
    OperationKind.CONVERSION: "Conversion",
    OperationKind.TRANSFORMATION: "Transformation",
    OperationKind.CONCATENATED_OPERATION: "ConcatenatedOperation",
    OperationKind.POINT_MOTION_OPERATION: "PointMotionOperation",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationEntry(LocatedEntry):
    """A coordinate operation the active database holds.

    Listed by :func:`~geodetic_engine.catalog.operations`. Its
    :attr:`~geodetic_engine.catalog.RegistryEntry.authority_code` is a valid
    ``operation=`` for :func:`~geodetic_engine.geodesy.transform`.

    This is the operation as the authority registered it, between the CRSs it
    was registered for. Between two other CRSs PROJ may apply it as one step
    of a chain; :func:`~geodetic_engine.geodesy.available_operations` lists
    what PROJ would assemble for a pair.

    Attributes:
        kind: What kind of operation it is.
        method: The operation method; None for a concatenated operation, whose
            steps each have their own.
        source_crs: The CRS it was registered from; None for a conversion,
            which is registered without one.
        target_crs: The CRS it was registered to; None for a conversion.
        accuracy: Stated accuracy in metres, or None when none is stated.
        version: The authority's version label, for example ``"EPSG-Nor N"``,
            which tells apart several operations between one pair.
        grid_names: The grid files it reads, as the database names them; for a
            concatenated operation, those of every step.
        optional_grid_names: Those of :attr:`grid_names` it can run without.
            A PROJ string marks such a grid with ``@``: PROJ reads it where
            it is installed, and goes without it otherwise.
        steps: The steps of a concatenated operation, in order; empty
            otherwise.
        superseded_by: ``"AUTH:CODE"`` of the operations the authority has
            superseded this one with. A superseded operation is not
            deprecated, and PROJ still offers it.
        osdu_ids: The ids of the OSDU catalogue records that publish this
            operation, when the database was built from an OSDU catalogue;
            empty otherwise.
        has_persistable_reference: Whether that build also recorded the
            ``persistableReference`` one of those records publishes.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = (
        "kind",
        "method",
        "source_crs",
        "target_crs",
        "accuracy",
        "version",
        "grid_names",
        "area_of_use",
        "superseded_by",
        "osdu_ids",
    )

    kind: OperationKind
    method: ObjectRef | None = None
    source_crs: ObjectRef | None = None
    target_crs: ObjectRef | None = None
    accuracy: float | None = None
    version: str | None = None
    grid_names: tuple[str, ...] = ()
    optional_grid_names: tuple[str, ...] = field(default=(), repr=False)
    steps: tuple[ObjectRef, ...] = field(default=(), repr=False)
    superseded_by: tuple[str, ...] = field(default=(), repr=False)
    osdu_ids: tuple[str, ...] = field(default=(), repr=False)
    has_persistable_reference: bool = field(default=False, repr=False)

    @property
    def projjson_type(self) -> str:
        """``"Transformation"``, ``"Conversion"`` and so on."""
        return _PROJJSON_TYPES[self.kind]

    @property
    def grid_based(self) -> bool:
        """Whether it reads any grid file."""
        return bool(self.grid_names)

    @property
    def superseded(self) -> bool:
        """Whether the authority has superseded it."""
        return bool(self.superseded_by)

    def grids(self) -> tuple[GridUsage, ...]:
        """The grid files PROJ reads for it, and whether each is available now.

        Named as PROJ names them, one per file: EPSG's ``conus.las`` and
        ``conus.los`` are PROJ's one ``us_noaa_conus.tif``. The optional
        grids, :attr:`optional_grid_names`, are among them. A file is
        available when PROJ finds it on this machine, as
        :attr:`GridEntry.installed <geodetic_engine.catalog.GridEntry.installed>`
        says, or, with PROJ's networking enabled, when the database states a
        direct download under an open licence for it, as it does for the
        grids PROJ's network serves.

        PROJ's own account of a registered operation is not used. It tests
        the names the authority published, so it calls a grid missing that
        PROJ reads under its own name.
        """
        return self._usages(self.grid_names)

    def grids_available(self) -> bool:
        """Whether every grid it cannot run without is available.

        True when it reads no grid, or only optional ones. See :meth:`grids`
        for what available means.
        """
        return all(grid.available for grid in self._usages(self._must_read()))

    def _must_read(self) -> tuple[str, ...]:
        optional = self.optional_grid_names
        return tuple(name for name in self.grid_names if name not in optional)

    def _usages(self, stated: tuple[str, ...]) -> tuple[GridUsage, ...]:
        if not stated:
            return ()
        # Imported here: the grid listing is built from this module's entries.
        from geodetic_engine.catalog._grids import usages

        return usages(stated, _registry.identity())

    def to_json_dict(self) -> dict[str, Any]:
        """The operation's full PROJJSON, exactly as PROJ exports it.

        Example:
            >>> from geodetic_engine import catalog
            >>> helmert = catalog.operations(code="EPSG:1612").one()
            >>> helmert.to_json_dict()["method"]["name"]
            'Position Vector transformation (geog2D domain)'
        """
        definition: dict[str, Any] = CoordinateOperation.from_authority(
            self.auth_name, self.code
        ).to_json_dict()
        return definition

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"kind": self.kind.value}
        summary.update(super()._summary())
        for key, ref in (
            ("method", self.method),
            ("source_crs", self.source_crs),
            ("target_crs", self.target_crs),
        ):
            if ref is not None:
                summary[key] = ref.to_json_dict()
        if self.accuracy is not None:
            summary["accuracy"] = accuracy_json(self.accuracy)
        if self.version:
            summary["version"] = self.version
        if self.grid_names:
            summary["grids"] = list(self.grid_names)
        if self.optional_grid_names:
            summary["optional_grids"] = list(self.optional_grid_names)
        if self.steps:
            summary["steps"] = [step.to_json_dict() for step in self.steps]
        if self.superseded_by:
            summary["superseded_by"] = [
                authority_id(code) for code in self.superseded_by
            ]
        if self.osdu_ids:
            summary["osdu_ids"] = list(self.osdu_ids)
            summary["has_persistable_reference"] = self.has_persistable_reference
        return summary


@dataclass(frozen=True, slots=True)
class Row:
    """One operation row, as proj.db states it."""

    table: str
    auth_name: str
    code: str
    name: str
    method: Key | None
    method_name: str | None
    source: Key | None
    target: Key | None
    accuracy: float | None
    version: str | None
    deprecated: bool


@lru_cache(maxsize=4)
def rows(databases: DatabaseIdentity) -> tuple[Row, ...]:
    """Every operation row of the database."""

    def pair(auth: object, code: object) -> Key | None:
        return None if code is None else (str(auth), str(code))

    selects = [
        "SELECT 'conversion', auth_name, code, name, method_auth_name, method_code, "
        "method_name, NULL, NULL, NULL, NULL, NULL, NULL, deprecated FROM conversion"
    ]
    selects += [
        f"SELECT '{table}', auth_name, code, name, method_auth_name, method_code, "
        "method_name, source_crs_auth_name, source_crs_code, target_crs_auth_name, "
        f"target_crs_code, accuracy, operation_version, deprecated FROM {table}"
        for table in _registry.TRANSFORMATION_TABLES
    ]
    selects.append(
        "SELECT 'concatenated_operation', auth_name, code, name, NULL, NULL, NULL, "
        "source_crs_auth_name, source_crs_code, target_crs_auth_name, "
        "target_crs_code, accuracy, operation_version, deprecated "
        "FROM concatenated_operation"
    )
    with _registry.reading(databases) as connection:
        found = connection.execute(" UNION ALL ".join(selects)).fetchall()
    return tuple(
        Row(
            table=str(table),
            auth_name=str(auth),
            code=str(code),
            name=str(name),
            method=pair(m_auth, m_code),
            method_name=_registry.text(m_name),
            source=pair(s_auth, s_code),
            target=pair(t_auth, t_code),
            accuracy=_registry.number(accuracy),
            version=_registry.text(version),
            deprecated=bool(deprecated),
        )
        for (
            table,
            auth,
            code,
            name,
            m_auth,
            m_code,
            m_name,
            s_auth,
            s_code,
            t_auth,
            t_code,
            accuracy,
            version,
            deprecated,
        ) in found
    )


class Reads(NamedTuple):
    """The grid files an operation reads, as the database names them."""

    names: tuple[str, ...]
    optional: tuple[str, ...]


@lru_cache(maxsize=4)
def grid_names(databases: DatabaseIdentity) -> Mapping[Key, Reads]:
    """The grid files each single operation reads, and which are optional."""
    names: dict[Key, list[str]] = {}
    required: dict[Key, set[str]] = {}

    def state(key: Key, grid: object, optional: bool = False) -> None:
        name = str(grid or "")
        if not name:
            return
        found = names.setdefault(key, [])
        if name not in found:
            found.append(name)
        if not optional:
            required.setdefault(key, set()).add(name)

    with _registry.reading(databases) as connection:
        for auth, code, first, second in connection.execute(
            "SELECT auth_name, code, grid_name, grid2_name FROM grid_transformation"
        ):
            state((str(auth), str(code)), first)
            state((str(auth), str(code)), second)
        # A method with other parameters besides its grid is registered here.
        for auth, code, grid in connection.execute(
            "SELECT auth_name, code, grid_name FROM other_transformation "
            "WHERE grid_name IS NOT NULL"
        ):
            state((str(auth), str(code)), grid)
        for auth, code, method in connection.execute(
            "SELECT auth_name, code, method_name FROM other_transformation "
            "WHERE method_auth_name = 'PROJ' AND method_code = 'PROJString'"
        ):
            for grid in _stated(str(method)):
                # "@" marks a grid PROJ goes without where it is not found.
                name = grid.removeprefix("@")
                if name != _NULL_GRID:
                    state((str(auth), str(code)), name, grid.startswith("@"))
    return {
        key: Reads(
            names=tuple(found),
            optional=tuple(n for n in found if n not in required.get(key, ())),
        )
        for key, found in names.items()
    }


@lru_cache(maxsize=4)
def steps(databases: DatabaseIdentity) -> Mapping[Key, tuple[Key, ...]]:
    """The steps of each concatenated operation, in order."""
    found: dict[Key, list[Key]] = {}
    with _registry.reading(databases) as connection:
        for auth, code, step_auth, step_code in connection.execute(
            "SELECT operation_auth_name, operation_code, step_auth_name, step_code "
            "FROM concatenated_operation_step "
            "ORDER BY operation_auth_name, operation_code, step_number"
        ):
            found.setdefault((str(auth), str(code)), []).append(
                (str(step_auth), str(step_code))
            )
    return {key: tuple(value) for key, value in found.items()}


def _kind(row: Row) -> OperationKind:
    if row.table == "conversion":
        return OperationKind.CONVERSION
    if row.table == "concatenated_operation":
        return OperationKind.CONCATENATED_OPERATION
    if (row.method_name or "").casefold().startswith("point motion"):
        return OperationKind.POINT_MOTION_OPERATION
    return OperationKind.TRANSFORMATION


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[OperationEntry, ...]:
    """Every operation in the database, built once per database."""
    from geodetic_engine.catalog._crs import structures

    names = _registry.names(databases)
    aliases = _registry.aliases(databases)
    replacements = _registry.replacements(databases)
    supersessions = _registry.supersessions(databases)
    usages = _registry.usages(databases)
    osdu = _registry.osdu_identifiers(databases)
    published = _registry.with_persistable_reference(databases)
    shapes = structures(databases)
    grids = grid_names(databases)
    chains = steps(databases)
    tables = {(row.auth_name, row.code): row.table for row in rows(databases)}

    def crs_ref(key: Key | None) -> ObjectRef | None:
        if key is None:
            return None
        shape = shapes.get(key)
        name = names.get((shape.table, *key), "") if shape else ""
        return ObjectRef(*key, name)

    def step_ref(key: Key) -> ObjectRef:
        return ObjectRef(*key, names.get((tables.get(key, ""), *key), ""))

    built = []
    for row in rows(databases):
        key = (row.auth_name, row.code)
        table_key = (row.table, *key)
        chain = chains.get(key, ())
        # A chain reads what its steps read, and needs what any of them needs.
        # No step is itself a chain: proj.db refuses one, and PROJ builds none.
        stated = [grids[step] for step in chain or (key,) if step in grids]
        reads = tuple(dict.fromkeys(name for one in stated for name in one.names))
        needed = {n for one in stated for n in one.names if n not in one.optional}
        method = None
        if row.method is not None:
            method = ObjectRef(*row.method, row.method_name or row.method[1])
        built.append(
            OperationEntry(
                auth_name=row.auth_name,
                code=row.code,
                name=row.name,
                deprecated=row.deprecated,
                aliases=aliases.get(table_key, ()),
                replaced_by=replacements.get(table_key, ()),
                areas_of_use=usages.get(table_key, ()),
                kind=_kind(row),
                method=method,
                source_crs=crs_ref(row.source),
                target_crs=crs_ref(row.target),
                accuracy=row.accuracy,
                version=row.version,
                grid_names=reads,
                optional_grid_names=tuple(n for n in reads if n not in needed),
                steps=tuple(step_ref(step) for step in chain),
                superseded_by=supersessions.get(table_key, ()),
                osdu_ids=osdu.get(table_key, ()),
                has_persistable_reference=table_key in published,
            )
        )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


@lru_cache(maxsize=4)
def methods_known(databases: DatabaseIdentity) -> Mapping[Folded, tuple[str, ...]]:
    """Every operation method by code, with its names, for the ``method`` filter."""
    from geodetic_engine.catalog._methods import index

    return {
        _filters.folded(*key): (method.name,)
        for key, method in index(databases).items()
    }


def crs_keys(
    value: Any, what: str, databases: DatabaseIdentity
) -> frozenset[Folded] | None:
    """The CRSs a ``source_crs`` or ``target_crs`` filter names.

    Anything the other reference filters take, and also anything
    :class:`~geodetic_engine.geodesy.CoordinateReferenceSystem` resolves, such
    as an OSDU record id or a WKT string, identified by its authority code.
    """
    if value is None:
        return None
    known = _filters.registered(_registry.CRS_TABLES, databases)
    single = (str, int, CRS, CoordinateReferenceSystem, ObjectRef, RegistryEntry)
    items = [value] if isinstance(value, single) else list(value)
    if not items:
        raise InvalidFilterError(f"{what} is empty")
    found: set[Folded] = set()
    for item in items:
        crs = item.crs if isinstance(item, CoordinateReferenceSystem) else item
        if isinstance(crs, CRS):
            found |= _identified(crs, what)
            continue
        try:
            found |= (
                _filters.references(
                    cast("ReferenceFilter", item), what, known, databases
                )
                or set()
            )
        except InvalidFilterError as refused:
            if not isinstance(item, str):
                raise
            try:
                resolved = CoordinateReferenceSystem.from_user_input(item)
            except GeodesyError:
                raise refused from None
            found |= _identified(resolved.crs, what)
    return frozenset(found)


def _identified(crs: CRS, what: str) -> set[Folded]:
    if crs.is_bound and crs.source_crs is not None:
        crs = crs.source_crs
    codes = crs.list_authority(min_confidence=100)
    if not codes:
        raise InvalidFilterError(
            f"{what} {crs.name!r} has no authority code in the active database"
        )
    return {_filters.folded(match.auth_name, match.code) for match in codes}


def operations(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    kind: str | OperationKind | Iterable[str | OperationKind] | None = None,
    source_crs: Any = None,
    target_crs: Any = None,
    either_direction: bool = False,
    method: ReferenceFilter | None = None,
    max_accuracy: float | None = None,
    grid_based: bool | None = None,
    grids_available: bool | None = None,
    superseded: bool | None = None,
    area_of_interest: AreaFilter | None = None,
    contains: bool = False,
    area_name: TextFilter | None = None,
    deprecated: bool | None = False,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[OperationEntry]:
    """List the coordinate operations the active database holds.

    What the registry publishes, as it publishes it: conversions,
    transformations and concatenated operations, including deprecated ones
    if asked. A transformation is found by the CRSs it was registered
    between; PROJ can also apply it between others, as a step of a chain.
    For what PROJ would apply between two given CRSs, use
    :func:`~geodetic_engine.geodesy.available_operations` instead.

    Every filter given must hold.

    Args:
        authority: Only these authorities, for example ``"EPSG"``.
        name: Text the name or an alias contains, without case, or a pattern.
        code: Only these codes, as for :func:`~geodetic_engine.catalog.crs`.
        kind: Only these kinds, for example ``"transformation"``.
        source_crs: Only operations registered from this CRS, or one of
            several: by code, entry, name, or anything
            :class:`~geodetic_engine.geodesy.CoordinateReferenceSystem`
            resolves, such as an OSDU record id.
        target_crs: Only operations registered to this CRS, likewise.
        either_direction: Also match an operation registered the other way
            round, from ``target_crs`` to ``source_crs``. PROJ applies
            transformations in either direction.
        method: Only operations applying these methods, by code, entry or
            name, for example ``"NTv2"`` or ``"EPSG:9606"``.
        max_accuracy: Only operations stating an accuracy, in metres, no
            worse than this. One that states none is left out.
        grid_based: True for only operations that read a grid file, False for
            none.
        grids_available: True for only operations whose every grid is
            available now, False for those missing one; see
            :meth:`OperationEntry.grids`. An operation reading no grid, or
            only optional ones, counts as available.
        superseded: True for only superseded operations, False for none.
        area_of_interest: ``(west, south, east, north)`` in degrees, as for
            :func:`~geodetic_engine.catalog.crs`.
        contains: With ``area_of_interest``, only operations whose area of
            use contains it entirely, rather than intersects it.
        area_name: Text the area of use's description contains, or a pattern.
        deprecated: False (default) leaves deprecated operations out, True
            lists only them, None lists both.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The operations, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed, or a CRS or method names
            nothing of its kind.

    Example:
        >>> from geodetic_engine import catalog
        >>> found = catalog.operations(
        ...     source_crs="EPSG:4230", target_crs="EPSG:4326",
        ...     max_accuracy=1.0, area_name="Norway",
        ... )
        >>> [(entry.authority_code, entry.accuracy) for entry in found]
        [('EPSG:1612', 1.0), ('EPSG:1613', 1.0)]
    """
    code, kind, method = (
        _filters.given(code),
        _filters.given(kind),
        _filters.given(method),
    )
    source_crs, target_crs = _filters.given(source_crs), _filters.given(target_crs)
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    kinds = _filters.choices(kind, OperationKind, "operation kind")
    sources = crs_keys(source_crs, "source_crs", databases)
    targets = crs_keys(target_crs, "target_crs", databases)
    reverse = _filters.flag(either_direction, "either_direction")
    methods = _filters.references(
        method, "operation method", methods_known(databases), databases
    )
    if max_accuracy is not None and (
        isinstance(max_accuracy, bool)
        or not isinstance(max_accuracy, (int, float))
        or not max_accuracy >= 0
    ):
        raise InvalidFilterError(
            f"max_accuracy must be a number of metres, not {max_accuracy!r}"
        )
    grid_based_ = _filters.flag(grid_based, "grid_based")
    available = _filters.flag(grids_available, "grids_available")
    superseded_ = _filters.flag(superseded, "superseded")
    bounds = _filters.area_of_interest(area_of_interest)
    whole = _filters.flag(contains, "contains")
    if whole is None or reverse is None:
        raise InvalidFilterError("contains and either_direction must be True or False")
    areas = _filters.text(area_name, "area_name")
    deprecated_ = _filters.flag(deprecated, "deprecated")
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)

    def key(ref: ObjectRef | None) -> Folded | None:
        return None if ref is None else _filters.folded(ref.auth_name, ref.code)

    def between(entry: OperationEntry) -> bool:
        if sources is None and targets is None:
            return True
        source, target = key(entry.source_crs), key(entry.target_crs)

        def matches(start: Folded | None, end: Folded | None) -> bool:
            return (sources is None or start in sources) and (
                targets is None or end in targets
            )

        return matches(source, target) or (reverse and matches(target, source))

    def admits(entry: OperationEntry) -> bool:
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if not _filters.admits_flag(deprecated_, entry.deprecated):
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if kinds is not None and entry.kind not in kinds:
            return False
        if not between(entry):
            return False
        if methods is not None and key(entry.method) not in methods:
            return False
        if max_accuracy is not None and (
            entry.accuracy is None or entry.accuracy > max_accuracy
        ):
            return False
        if not _filters.admits_flag(grid_based_, entry.grid_based):
            return False
        if not _filters.admits_flag(superseded_, entry.superseded):
            return False
        if names is not None and not names((entry.name, *entry.aliases)):
            return False
        if areas is not None and not areas(a.name for a in entry.areas_of_use):
            return False
        return _filters.located(entry.areas_of_use, bounds, whole)

    def produce() -> Iterator[OperationEntry]:
        reads = None
        if available is not None:
            from geodetic_engine.catalog._grids import availability

            reads = availability(databases)
        for entry in entries(databases):
            if not admits(entry):
                continue
            if reads is not None and reads(entry._must_read()) != available:
                continue
            yield entry

    filters = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        kind=kind,
        source_crs=_label(source_crs),
        target_crs=_label(target_crs),
        either_direction=reverse or None,
        method=method,
        max_accuracy=max_accuracy,
        grid_based=grid_based_,
        grids_available=available,
        superseded=superseded_,
        area_of_interest=bounds,
        contains=whole if bounds is not None else None,
        area_name=area_name,
        deprecated=deprecated_,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "operations",
        filters,
        produce,
        columns=columns(OperationEntry),
        databases=databases,
        limit=limit,
        offset=offset,
    )


def _label(value: Any) -> Any:
    """A CRS filter as its export records it."""
    if isinstance(value, CoordinateReferenceSystem):
        return value.authority_code or value.name
    if isinstance(value, CRS):
        return value.srs
    return value
