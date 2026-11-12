"""Listing ellipsoids."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, ClassVar

from pyproj.crs import Ellipsoid

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import RegistryEntry, columns, order
from geodetic_engine.catalog._filters import RangeFilter, ReferenceFilter, TextFilter
from geodetic_engine.catalog._listing import Listing


@dataclass(frozen=True, slots=True, kw_only=True)
class EllipsoidEntry(RegistryEntry):
    """An ellipsoid or sphere the active database holds.

    Listed by :func:`~geodetic_engine.catalog.ellipsoids`. The axes are in
    metres whatever unit the database states them in, and whichever of the
    semi-minor axis and the inverse flattening it states, the other is
    derived from it.

    Attributes:
        semi_major_axis: Semi-major axis, in metres.
        semi_minor_axis: Semi-minor axis, in metres.
        inverse_flattening: Inverse flattening, or None for a sphere.
        celestial_body: The body the ellipsoid models, for example
            ``"Earth"``, as the database names it; None when it states none.
    """

    _COLUMNS: ClassVar[tuple[str, ...]] = (
        "semi_major_axis",
        "semi_minor_axis",
        "inverse_flattening",
        "celestial_body",
    )

    semi_major_axis: float
    semi_minor_axis: float
    inverse_flattening: float | None
    celestial_body: str | None = None

    @property
    def is_sphere(self) -> bool:
        """Whether the ellipsoid is a sphere."""
        return self.inverse_flattening is None

    @property
    def projjson_type(self) -> str:
        """Always ``"Ellipsoid"``."""
        return "Ellipsoid"

    def to_json_dict(self) -> dict[str, Any]:
        """The ellipsoid's PROJJSON, exactly as PROJ exports it.

        Example:
            >>> from geodetic_engine import catalog
            >>> grs80 = catalog.ellipsoids(code="EPSG:7019").one()
            >>> grs80.to_json_dict()["inverse_flattening"]
            298.257222101
        """
        definition: dict[str, Any] = Ellipsoid.from_authority(
            self.auth_name, self.code
        ).to_json_dict()
        return definition

    def _summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "semi_major_axis": self.semi_major_axis,
            "semi_minor_axis": self.semi_minor_axis,
        }
        if self.inverse_flattening is not None:
            summary["inverse_flattening"] = self.inverse_flattening
        if self.celestial_body:
            summary["celestial_body"] = self.celestial_body
        return summary


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[EllipsoidEntry, ...]:
    """Every ellipsoid in the database, built once per database."""
    aliases = _registry.aliases(databases)
    replacements = _registry.replacements(databases)
    units = _registry.unit_rows(databases)
    with _registry.reading(databases) as connection:
        rows = connection.execute(
            "SELECT e.auth_name, e.code, e.name, e.semi_major_axis, e.uom_auth_name, "
            "e.uom_code, e.inv_flattening, e.semi_minor_axis, b.name, e.deprecated "
            "FROM ellipsoid e LEFT JOIN celestial_body b "
            "ON b.auth_name = e.celestial_body_auth_name "
            "AND b.code = e.celestial_body_code"
        ).fetchall()
    built = []
    for auth, code, name, a, u_auth, u_code, rf, b, body, deprecated in rows:
        unit = units.get((str(u_auth), str(u_code)))
        factor = unit.conversion_factor if unit and unit.conversion_factor else 1.0
        semi_major = float(a) * factor
        if rf is not None and float(rf) != 0:
            inverse_flattening: float | None = float(rf)
            semi_minor = semi_major * (1 - 1 / float(rf))
        elif b is not None and float(b) * factor != semi_major:
            semi_minor = float(b) * factor
            inverse_flattening = semi_major / (semi_major - semi_minor)
        else:
            semi_minor, inverse_flattening = semi_major, None
        key = ("ellipsoid", str(auth), str(code))
        built.append(
            EllipsoidEntry(
                auth_name=str(auth),
                code=str(code),
                name=str(name),
                deprecated=bool(deprecated),
                aliases=aliases.get(key, ()),
                replaced_by=replacements.get(key, ()),
                semi_major_axis=semi_major,
                semi_minor_axis=semi_minor,
                inverse_flattening=inverse_flattening,
                celestial_body=_registry.text(body),
            )
        )
    return tuple(sorted(built, key=lambda e: order(e.auth_name, e.code)))


def ellipsoids(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    code: ReferenceFilter | None = None,
    semi_major_axis: RangeFilter | None = None,
    semi_minor_axis: RangeFilter | None = None,
    inverse_flattening: RangeFilter | None = None,
    sphere: bool | None = None,
    celestial_body: str | Iterable[str] | None = None,
    deprecated: bool | None = False,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[EllipsoidEntry]:
    """List the ellipsoids the active database holds.

    Every filter given must hold. The datums on an ellipsoid are
    ``catalog.datums(ellipsoid=entry)``.

    Args:
        authority: Only these authorities, for example ``"EPSG"``.
        name: Text the name or an alias contains, without case, or a pattern.
        code: Only these codes, as for :func:`~geodetic_engine.catalog.crs`.
        semi_major_axis: In metres, exactly or as ``(low, high)`` with None
            for an open end.
        semi_minor_axis: In metres, likewise.
        inverse_flattening: Likewise. A sphere never matches.
        sphere: True for only spheres, False for none.
        celestial_body: Only ellipsoids of these bodies, by name without
            case, for example ``"Earth"``. ``IAU_2015`` models many.
        deprecated: False (default) leaves deprecated ellipsoids out, True
            lists only them, None lists both.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The ellipsoids, ordered by authority and code.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed.

    Example:
        >>> from geodetic_engine import catalog
        >>> found = catalog.ellipsoids(authority="EPSG", semi_major_axis=6378137)
        >>> [entry.name for entry in found]
        ['CGCS2000', 'GRS 1980', 'WGS 84', 'GEM 10C']
    """
    code, celestial_body = _filters.given(code), _filters.given(celestial_body)
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    codes = _filters.codes(code, databases, authorities)
    majors = _filters.value_range(semi_major_axis, "semi_major_axis")
    minors = _filters.value_range(semi_minor_axis, "semi_minor_axis")
    flattenings = _filters.value_range(inverse_flattening, "inverse_flattening")
    sphere_ = _filters.flag(sphere, "sphere")
    bodies = _filters.labels(celestial_body, "celestial_body")
    deprecated_ = _filters.flag(deprecated, "deprecated")
    limit, offset = _filters.paging(limit, offset)
    wanted = None if authorities is None else frozenset(authorities)

    def admits(entry: EllipsoidEntry) -> bool:
        if wanted is not None and entry.auth_name not in wanted:
            return False
        if not _filters.admits_flag(deprecated_, entry.deprecated):
            return False
        if codes is not None and not codes.admits(entry.auth_name, entry.code):
            return False
        if not _filters.in_range(majors, entry.semi_major_axis):
            return False
        if not _filters.in_range(minors, entry.semi_minor_axis):
            return False
        if not _filters.in_range(flattenings, entry.inverse_flattening):
            return False
        if not _filters.admits_flag(sphere_, entry.is_sphere):
            return False
        if bodies is not None and (entry.celestial_body or "").casefold() not in bodies:
            return False
        return names is None or names((entry.name, *entry.aliases))

    def produce() -> Iterator[EllipsoidEntry]:
        yield from filter(admits, entries(databases))

    filters = _filters.echo(
        authority=authorities,
        name=name,
        code=code,
        semi_major_axis=semi_major_axis,
        semi_minor_axis=semi_minor_axis,
        inverse_flattening=inverse_flattening,
        sphere=sphere_,
        celestial_body=celestial_body,
        deprecated=deprecated_,
        limit=limit,
        offset=offset or None,
    )
    return Listing(
        "ellipsoids",
        filters,
        produce,
        columns=columns(EllipsoidEntry),
        databases=databases,
        limit=limit,
        offset=offset,
    )
