"""Listing coordinate operation methods."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar

from pyproj.crs import CoordinateOperation
from pyproj.exceptions import CRSError

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import (
    PROJJSON_SCHEMA,
    ObjectRef,
    RegistryEntry,
    columns,
    order,
    projjson_id,
)
from geodetic_engine.catalog._filters import Folded, ReferenceFilter, TextFilter
from geodetic_engine.catalog._listing import Listing
from geodetic_engine.catalog._operations import rows
from geodetic_engine.catalog._registry import Key

logger = logging.getLogger(__name__)


class MethodKind(StrEnum):
    """What a method's operations are."""

    CONVERSION = "conversion"
    """A method of conversions, such as a map projection."""

    TRANSFORMATION = "transformation"
    """A method of transformations, such as a Helmert or a grid shift."""


@dataclass(frozen=True, slots=True, kw_only=True)
class MethodEntry(RegistryEntry):
    """A coordinate operation method the active database holds.

    Listed by :func:`~geodetic_engine.catalog.methods`. proj.db records no
    deprecation of a method, so ``deprecated`` is always False.

    Attributes:
        kind: Whether its operations are conversions or transformations.
        parameters: The parameters of the method, as PROJ states them for an
            operation that applies it; empty for a method only PROJ strings
            use.
        operation_count: How many operations in the database apply it,
            deprecated ones included. The operations themselves are
            ``catalog.operations(method=entry)``.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = ("kind", "parameters", "operation_count")

    kind: MethodKind
    parameters: tuple[ObjectRef, ...] = field(default=(), repr=False)
    operation_count: int = 0

    @property
    def projjson_type(self) -> str:
        """Always ``"OperationMethod"``."""
        return "OperationMethod"

    def to_json_dict(self) -> dict[str, Any]:
        """The method as PROJJSON: its name and id.

        PROJ exports a method only inside an operation; this is the form it
        writes there, which PROJJSON's ``method`` definition describes.

        Example:
            >>> from geodetic_engine import catalog
            >>> catalog.methods(code="EPSG:9615").one().to_json_dict()["name"]
            'NTv2'
        """
        return {
            "$schema": PROJJSON_SCHEMA,
            "type": "OperationMethod",
            "name": self.name,
            "id": projjson_id(self.auth_name, self.code),
        }

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"kind": self.kind.value}
        if self.parameters:
            summary["parameters"] = [p.to_json_dict() for p in self.parameters]
        summary["operation_count"] = self.operation_count
        return summary


@dataclass(frozen=True, slots=True)
class _Method:
    name: str
    kind: MethodKind
    operations: tuple[Key, ...]


@lru_cache(maxsize=4)
def index(databases: DatabaseIdentity) -> Mapping[Key, _Method]:
    """Every method: those the database lists, and those its operations apply."""
    names: dict[Key, set[str]] = {}
    kinds: dict[Key, MethodKind] = {}
    using: dict[Key, list[Key]] = {}
    with _registry.reading(databases) as connection:
        for table, kind in (
            ("conversion_method", MethodKind.CONVERSION),
            ("coordinate_operation_method", MethodKind.TRANSFORMATION),
        ):
            if not _registry.has_table(connection, table):
                continue
            for auth, code, name in connection.execute(
                f"SELECT auth_name, code, name FROM {table}"
            ):
                key = (str(auth), str(code))
                names.setdefault(key, set()).add(str(name))
                kinds.setdefault(key, kind)
    for row in rows(databases):
        if row.method is None:
            continue
        names.setdefault(row.method, set()).add(row.method_name or row.method[1])
        kinds.setdefault(
            row.method,
            MethodKind.CONVERSION
            if row.table == "conversion"
            else MethodKind.TRANSFORMATION,
        )
        using.setdefault(row.method, []).append((row.auth_name, row.code))
    found = {}
    for key, stated in names.items():
        # PROJ's PROJString pseudo-method is "named" by each PROJ string.
        name = next(iter(stated)) if len(stated) == 1 else key[1]
        operations = sorted(using.get(key, ()), key=lambda k: order(*k))
        found[key] = _Method(name=name, kind=kinds[key], operations=tuple(operations))
    return found


def _parameters(
    operations: tuple[Key, ...], deprecated: set[Key]
) -> tuple[ObjectRef, ...]:
    """The parameters PROJ states for the first operation it can build."""
    ordered = [k for k in operations if k not in deprecated] + [
        k for k in operations if k in deprecated
    ]
    for key in ordered[:3]:
        try:
            operation = CoordinateOperation.from_authority(*key)
        except CRSError as error:
            logger.debug("could not build %s:%s: %s", *key, error)
            continue
        return tuple(
            ObjectRef(param.auth_name, param.code, param.name)
            for param in operation.params
            if param.auth_name and param.code
        )
    return ()


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[MethodEntry, ...]:
    """Every method in the database, with its parameters, built once."""
    aliases = _registry.aliases(databases)
    deprecated = {
        (row.auth_name, row.code) for row in rows(databases) if row.deprecated
    }
    built = []
    for (auth, code), method in index(databases).items():
        built.append(
            MethodEntry(
                auth_name=auth,
                code=code,
                name=method.name,
                deprecated=False,
                aliases=aliases.get(("coordinate_operation_method", auth, code), ()),
                kind=method.kind,
                parameters=_parameters(method.operations, deprecated),
                operation_count=len(method.operations),
            )
        )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


def methods(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    kind: str | MethodKind | Iterable[str | MethodKind] | None = None,
    parameter: ReferenceFilter | None = None,
    used: bool | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[MethodEntry]:
    """List the coordinate operation methods the active database holds.

    pyproj has no listing of methods, so they are read from ``proj.db``:
    those it lists, and those its operations apply. Each method's
    parameters are read through PROJ, from an operation that applies it.
    Every filter given must hold.

    Args:
        authority: Only these authorities, for example ``"EPSG"``.
        name: Text the name contains, without case, or a pattern.
        code: Only these codes, as for :func:`~geodetic_engine.catalog.crs`.
        kind: Only ``"conversion"`` or ``"transformation"`` methods.
        parameter: Only methods with one of these parameters, by code or name,
            for example ``"EPSG:8801"`` or ``"Latitude of natural origin"``.
        used: True for only methods some operation applies, False for none.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The methods, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed, or ``parameter`` names
            no parameter of any method.

    Example:
        >>> from geodetic_engine import catalog
        >>> helmerts = catalog.methods(name="Position Vector")
        >>> "EPSG:9606" in [method.authority_code for method in helmerts]
        True
    """
    code, kind, parameter = (
        _filters.given(code),
        _filters.given(kind),
        _filters.given(parameter),
    )
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    kinds = _filters.choices(kind, MethodKind, "method kind")
    used_ = _filters.flag(used, "used")
    parameters = None
    if parameter is not None:
        known: dict[Folded, tuple[str, ...]] = {}
        for entry in entries(databases):
            for param in entry.parameters:
                known[_filters.folded(param.auth_name, param.code)] = (param.name,)
        parameters = _filters.references(parameter, "parameter", known, databases)
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)

    def admits(entry: MethodEntry) -> bool:
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if kinds is not None and entry.kind not in kinds:
            return False
        if not _filters.admits_flag(used_, entry.operation_count > 0):
            return False
        if parameters is not None and not any(
            _filters.folded(p.auth_name, p.code) in parameters for p in entry.parameters
        ):
            return False
        return names is None or names((entry.name, *entry.aliases))

    def produce() -> Iterator[MethodEntry]:
        _registry.unchanged(databases)
        yield from filter(admits, entries(databases))

    filters = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        kind=kind,
        parameter=parameter,
        used=used_,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "methods",
        filters,
        produce,
        columns=columns(MethodEntry),
        limit=limit,
        offset=offset,
    )
