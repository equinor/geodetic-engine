"""Listing datums and datum ensembles."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any, ClassVar

from pyproj.crs import Datum

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import (
    LocatedEntry,
    ObjectRef,
    accuracy_json,
    columns,
    order,
)
from geodetic_engine.catalog._filters import (
    AreaFilter,
    RangeFilter,
    ReferenceFilter,
    TextFilter,
)
from geodetic_engine.catalog._listing import Listing
from geodetic_engine.catalog.errors import InvalidFilterError

# For the annotations the dataclass inherits from LocatedEntry.
from geodetic_engine.geodesy import AreaOfUse as AreaOfUse


class DatumKind(StrEnum):
    """What a datum, or a datum ensemble, is a datum for."""

    GEODETIC = "geodetic"
    """A geodetic reference frame, which places an ellipsoid on the Earth."""

    VERTICAL = "vertical"
    """A vertical reference frame, for heights or depths."""

    ENGINEERING = "engineering"
    """A local datum, such as a site or plant origin."""


_TABLES = {
    DatumKind.GEODETIC: "geodetic_datum",
    DatumKind.VERTICAL: "vertical_datum",
    DatumKind.ENGINEERING: "engineering_datum",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class DatumEntry(LocatedEntry):
    """A datum or datum ensemble the active database holds.

    Listed by :func:`~geodetic_engine.catalog.datums`.

    Attributes:
        kind: Geodetic, vertical or engineering.
        ellipsoid: The ellipsoid of a geodetic datum; None otherwise.
        prime_meridian: The prime meridian of a geodetic datum; None
            otherwise.
        frame_reference_epoch: The reference epoch of a dynamic reference
            frame, as a decimal year; None for a static one.
        ensemble_accuracy: The accuracy, in metres, of a datum ensemble; None
            for a single datum.
        members: The member datums of an ensemble, in order; empty for a
            single datum.
        publication_date: When the datum was published, as the database states
            it, or None.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = (
        "kind",
        "is_ensemble",
        "is_dynamic",
        "ellipsoid",
        "prime_meridian",
        "frame_reference_epoch",
        "ensemble_accuracy",
        "area_of_use",
    )

    kind: DatumKind
    ellipsoid: ObjectRef | None = None
    prime_meridian: ObjectRef | None = field(default=None, repr=False)
    frame_reference_epoch: float | None = None
    ensemble_accuracy: float | None = field(default=None, repr=False)
    members: tuple[ObjectRef, ...] = field(default=(), repr=False)
    publication_date: str | None = field(default=None, repr=False)

    @property
    def is_ensemble(self) -> bool:
        """Whether this is a datum ensemble, such as WGS 84 or ETRS89."""
        return self.ensemble_accuracy is not None or bool(self.members)

    @property
    def is_dynamic(self) -> bool:
        """Whether this is a dynamic reference frame, with a reference epoch."""
        return self.frame_reference_epoch is not None

    @property
    def projjson_type(self) -> str:
        """``"GeodeticReferenceFrame"``, ``"DatumEnsemble"`` and so on."""
        if self.is_ensemble:
            return "DatumEnsemble"
        if self.kind is DatumKind.ENGINEERING:
            return "EngineeringDatum"
        dynamic = "Dynamic" if self.is_dynamic else ""
        if self.kind is DatumKind.VERTICAL:
            return f"{dynamic}VerticalReferenceFrame"
        return f"{dynamic}GeodeticReferenceFrame"

    def to_json_dict(self) -> dict[str, Any]:
        """The datum's full PROJJSON, exactly as PROJ exports it.

        Example:
            >>> from geodetic_engine import catalog
            >>> catalog.datums(code="EPSG:6258").one().to_json_dict()["type"]
            'DatumEnsemble'
        """
        definition: dict[str, Any] = Datum.from_authority(
            self.auth_name, self.code
        ).to_json_dict()
        return definition

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"kind": self.kind.value}
        summary.update(super()._summary())
        if self.ellipsoid is not None:
            summary["ellipsoid"] = self.ellipsoid.to_json_dict()
        if self.prime_meridian is not None:
            summary["prime_meridian"] = self.prime_meridian.to_json_dict()
        if self.frame_reference_epoch is not None:
            summary["frame_reference_epoch"] = self.frame_reference_epoch
        if self.members:
            summary["members"] = [member.to_json_dict() for member in self.members]
        if self.ensemble_accuracy is not None:
            summary["accuracy"] = accuracy_json(self.ensemble_accuracy)
        if self.publication_date:
            summary["publication_date"] = self.publication_date
        return summary


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[DatumEntry, ...]:
    """Every datum and ensemble in the database, built once per database."""
    names = _registry.names(databases)
    aliases = _registry.aliases(databases)
    replacements = _registry.replacements(databases)
    usages = _registry.usages(databases)

    def ref(table: str, auth: object, code: object) -> ObjectRef | None:
        if code is None:
            return None
        return ObjectRef(
            str(auth), str(code), names.get((table, str(auth), str(code)), "")
        )

    built = []
    with _registry.reading(databases) as connection:
        members: dict[tuple[str, str, str], list[ObjectRef]] = {}
        for table in ("geodetic_datum", "vertical_datum"):
            member_table = f"{table}_ensemble_member"
            if not _registry.has_table(connection, member_table):
                continue
            for auth, code, m_auth, m_code in connection.execute(
                "SELECT ensemble_auth_name, ensemble_code, member_auth_name, "
                f"member_code FROM {member_table} ORDER BY ensemble_auth_name, "
                "ensemble_code, sequence"
            ):
                member = ref(table, m_auth, m_code)
                if member is not None:
                    members.setdefault((table, str(auth), str(code)), []).append(member)
        rows = connection.execute(
            "SELECT 'geodetic_datum', auth_name, code, name, ellipsoid_auth_name, "
            "ellipsoid_code, prime_meridian_auth_name, prime_meridian_code, "
            "frame_reference_epoch, ensemble_accuracy, publication_date, deprecated "
            "FROM geodetic_datum UNION ALL "
            "SELECT 'vertical_datum', auth_name, code, name, NULL, NULL, NULL, NULL, "
            "frame_reference_epoch, ensemble_accuracy, publication_date, deprecated "
            "FROM vertical_datum"
        ).fetchall()
        if _registry.has_table(connection, "engineering_datum"):
            rows += connection.execute(
                "SELECT 'engineering_datum', auth_name, code, name, NULL, NULL, "
                "NULL, NULL, NULL, NULL, publication_date, deprecated "
                "FROM engineering_datum"
            ).fetchall()
    kinds = {table: kind for kind, table in _TABLES.items()}
    for (
        table,
        auth,
        code,
        name,
        e_auth,
        e_code,
        p_auth,
        p_code,
        epoch,
        accuracy,
        published,
        deprecated,
    ) in rows:
        key = (str(table), str(auth), str(code))
        built.append(
            DatumEntry(
                auth_name=str(auth),
                code=str(code),
                name=str(name),
                deprecated=bool(deprecated),
                aliases=aliases.get(key, ()),
                replaced_by=replacements.get(key, ()),
                areas_of_use=usages.get(key, ()),
                kind=kinds[str(table)],
                ellipsoid=ref("ellipsoid", e_auth, e_code),
                prime_meridian=ref("prime_meridian", p_auth, p_code),
                frame_reference_epoch=_registry.number(epoch),
                ensemble_accuracy=_registry.number(accuracy),
                members=tuple(members.get(key, ())),
                publication_date=_registry.text(published),
            )
        )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


def datums(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    kind: str | DatumKind | Iterable[str | DatumKind] | None = None,
    ensemble: bool | None = None,
    dynamic: bool | None = None,
    ellipsoid: ReferenceFilter | None = None,
    prime_meridian: ReferenceFilter | None = None,
    frame_reference_epoch: RangeFilter | None = None,
    member_of: ReferenceFilter | None = None,
    area_of_interest: AreaFilter | None = None,
    contains: bool = False,
    area_name: TextFilter | None = None,
    deprecated: bool | None = False,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[DatumEntry]:
    """List the datums and datum ensembles the active database holds.

    Every filter given must hold. The datums are read from ``proj.db``;
    :meth:`DatumEntry.to_json_dict` builds each through pyproj.

    Args:
        authority: Only these authorities, for example ``"EPSG"``.
        name: Text the name or an alias contains, without case, or a pattern.
        code: Only these codes, as for :func:`~geodetic_engine.catalog.crs`.
        kind: Only these kinds: ``"geodetic"``, ``"vertical"`` or
            ``"engineering"``.
        ensemble: True for only datum ensembles, False for none.
        dynamic: True for only dynamic reference frames, False for none.
        ellipsoid: Only datums on these ellipsoids, by code, entry or name,
            for example ``"GRS 1980"``.
        prime_meridian: Only datums with these prime meridians, by code,
            entry or name, for example ``"Greenwich"``.
        frame_reference_epoch: The reference epoch, exactly or as
            ``(low, high)`` with None for an open end. A static datum never
            matches.
        member_of: Only the members of these datum ensembles, by code, entry
            or name, for example ``"ETRS89"``.
        area_of_interest: ``(west, south, east, north)`` in degrees, as for
            :func:`~geodetic_engine.catalog.crs`.
        contains: With ``area_of_interest``, only datums whose area of use
            contains it entirely, rather than intersects it.
        area_name: Text the area of use's description contains, or a pattern.
        deprecated: False (default) leaves deprecated datums out, True lists
            only them, None lists both.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The datums, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed, or a reference names
            nothing of its kind.

    Example:
        >>> from geodetic_engine import catalog
        >>> ensembles = catalog.datums(authority="EPSG", kind="geodetic", ensemble=True)
        >>> "World Geodetic System 1984 ensemble" in [d.name for d in ensembles]
        True
    """
    code, kind = _filters.given(code), _filters.given(kind)
    ellipsoid, prime_meridian = (
        _filters.given(ellipsoid),
        _filters.given(prime_meridian),
    )
    member_of = _filters.given(member_of)
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    kinds = _filters.choices(kind, DatumKind, "datum kind")
    ensemble_ = _filters.flag(ensemble, "ensemble")
    dynamic_ = _filters.flag(dynamic, "dynamic")
    ellipsoids = _filters.references(
        ellipsoid,
        "ellipsoid",
        _filters.registered(frozenset({"ellipsoid"}), databases),
        databases,
    )
    meridians = _filters.references(
        prime_meridian,
        "prime meridian",
        _filters.registered(frozenset({"prime_meridian"}), databases),
        databases,
    )
    epochs = _filters.value_range(frame_reference_epoch, "frame_reference_epoch")
    groups = _filters.references(
        member_of,
        "datum ensemble",
        _filters.registered(_registry.DATUM_TABLES, databases),
        databases,
    )
    bounds = _filters.area_of_interest(area_of_interest)
    whole = _filters.flag(contains, "contains")
    if whole is None:
        raise InvalidFilterError("contains must be True or False")
    areas = _filters.text(area_name, "area_name")
    deprecated_ = _filters.flag(deprecated, "deprecated")
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)
    in_groups: frozenset[_filters.Folded] | None = None
    if groups is not None:
        in_groups = frozenset(
            _filters.folded(member.auth_name, member.code)
            for entry in entries(databases)
            if _filters.folded(entry.auth_name, entry.code) in groups
            for member in entry.members
        )

    def admits(entry: DatumEntry) -> bool:
        key = _filters.folded(entry.auth_name, entry.code)
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if not _filters.admits_flag(deprecated_, entry.deprecated):
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if kinds is not None and entry.kind not in kinds:
            return False
        if not _filters.admits_flag(ensemble_, entry.is_ensemble):
            return False
        if not _filters.admits_flag(dynamic_, entry.is_dynamic):
            return False
        if not _references(ellipsoids, entry.ellipsoid):
            return False
        if not _references(meridians, entry.prime_meridian):
            return False
        if epochs is not None and not _filters.in_range(
            epochs, entry.frame_reference_epoch
        ):
            return False
        if in_groups is not None and key not in in_groups:
            return False
        if names is not None and not names((entry.name, *entry.aliases)):
            return False
        if areas is not None and not areas(a.name for a in entry.areas_of_use):
            return False
        return _filters.located(entry.areas_of_use, bounds, whole)

    def produce() -> Iterator[DatumEntry]:
        _registry.unchanged(databases)
        yield from filter(admits, entries(databases))

    filters = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        kind=kind,
        ensemble=ensemble_,
        dynamic=dynamic_,
        ellipsoid=ellipsoid,
        prime_meridian=prime_meridian,
        frame_reference_epoch=frame_reference_epoch,
        member_of=member_of,
        area_of_interest=bounds,
        contains=whole if bounds is not None else None,
        area_name=area_name,
        deprecated=deprecated_,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "datums",
        filters,
        produce,
        columns=columns(DatumEntry),
        limit=limit,
        offset=offset,
    )


def _references(
    wanted: frozenset[_filters.Folded] | None, found: ObjectRef | None
) -> bool:
    if wanted is None:
        return True
    return found is not None and _filters.folded(found.auth_name, found.code) in wanted
