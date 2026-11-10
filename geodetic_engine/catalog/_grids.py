"""Listing grid files: the ones operations read, and the ones PROJ knows."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import datadir
from pyproj.sync import get_transform_grid_list

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _filters, _registry
from geodetic_engine.catalog._entry import (
    CatalogEntry,
    area_json,
    authority_id,
    table_row,
)
from geodetic_engine.catalog._filters import AreaFilter, ReferenceFilter, TextFilter
from geodetic_engine.catalog._listing import Listing
from geodetic_engine.catalog._operations import entries as operation_entries
from geodetic_engine.catalog.errors import GridIndexUnavailableError, InvalidFilterError
from geodetic_engine.geodesy import AreaOfUse


class GridKind(StrEnum):
    """What a grid file corrects."""

    HORIZONTAL = "horizontal"
    """Latitude and longitude shifts, such as NTv2 or NADCON."""

    VERTICAL = "vertical"
    """Height offsets, such as a geoid model."""

    GEOCENTRIC = "geocentric"
    """Geocentric translations, such as France's IGN grid."""

    VELOCITY = "velocity"
    """Velocities, for moving coordinates between epochs."""

    DEFORMATION = "deformation"
    """A deformation model, such as New Zealand's."""

    UNKNOWN = "unknown"
    """Neither PROJ nor the method using it says."""


# How PROJ reads the grid, from grid_alternatives.proj_method. "gridshift" and
# "tinshift" read several kinds, so the method of an operation using it decides.
_PROJ_METHODS = {
    "hgridshift": GridKind.HORIZONTAL,
    "vgridshift": GridKind.VERTICAL,
    "geoid_like": GridKind.VERTICAL,
    "geocentricoffset": GridKind.GEOCENTRIC,
    "velocity_grid": GridKind.VELOCITY,
    "defmodel": GridKind.DEFORMATION,
}

# Words in a method's name, checked in this order, that say what a grid is.
_METHOD_WORDS = (
    ("velocit", GridKind.VELOCITY),
    ("deformation", GridKind.DEFORMATION),
    ("height", GridKind.VERTICAL),
    ("depth", GridKind.VERTICAL),
    ("vertical", GridKind.VERTICAL),
    ("vgridshift", GridKind.VERTICAL),
    ("geoid", GridKind.VERTICAL),
    ("geocentric", GridKind.GEOCENTRIC),
    ("xyzgridshift", GridKind.GEOCENTRIC),
    ("ntv", GridKind.HORIZONTAL),
    ("nadcon", GridKind.HORIZONTAL),
    ("offset", GridKind.HORIZONTAL),
    ("hgridshift", GridKind.HORIZONTAL),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class GridEntry(CatalogEntry):
    """A grid file an operation in the active database reads, or PROJ knows.

    Listed by :func:`~geodetic_engine.catalog.grids`. One entry per file PROJ
    reads: an authority's name for a grid, such as EPSG's
    ``"A66 National (13.09.01).gsb"``, is listed under the file PROJ reads
    for it, ``"au_icsm_A66_National_13_09_01.tif"``, where PROJ's
    ``grid_alternatives`` table maps one to the other.

    A grid file has no authority code, so this is no
    :class:`RegistryEntry`.

    Attributes:
        name: The file PROJ reads, or the name the database states where PROJ
            knows no alternative for it.
        original_names: The names operations in the database use for it.
        old_proj_name: The name of PROJ's older release of the file, if any.
        format: The file format, for example ``"GTiff"``, where PROJ knows it.
        proj_method: How PROJ applies it, for example ``"hgridshift"``, where
            PROJ knows it.
        kind: What it corrects.
        url: Where PROJ downloads it from, if known.
        direct_download: Whether it can be downloaded without registering.
        open_license: Whether its licence is open, or None if unknown.
        installed: Whether a file of one of its names is in a directory on
            PROJ's search path or in PROJ's user data directory.
        remote_available: Whether PROJ's content delivery network lists it;
            None unless the listing was made with ``check_remote=True``.
        used_by: ``"AUTH:CODE"`` of the operations that read it, deprecated
            ones included.
        areas_of_use: The areas of use of those operations.
    """

    name: str
    original_names: tuple[str, ...] = field(default=(), repr=False)
    old_proj_name: str | None = field(default=None, repr=False)
    format: str | None = None
    proj_method: str | None = field(default=None, repr=False)
    kind: GridKind
    url: str | None = field(default=None, repr=False)
    direct_download: bool = field(default=False, repr=False)
    open_license: bool | None = field(default=None, repr=False)
    installed: bool = False
    remote_available: bool | None = None
    used_by: tuple[str, ...] = field(default=(), repr=False)
    areas_of_use: tuple[AreaOfUse, ...] = field(default=(), repr=False)

    @property
    def area_of_use(self) -> AreaOfUse | None:
        """The first of :attr:`areas_of_use`, or None."""
        return self.areas_of_use[0] if self.areas_of_use else None

    def to_json_dict(self) -> dict[str, Any]:
        """The grid as a listing's JSON states it.

        A grid file has no PROJJSON form, so this is the same as
        :meth:`to_summary_dict`.
        """
        return self.to_summary_dict()

    def to_summary_dict(self) -> dict[str, Any]:
        """The grid as one item of a listing's JSON."""
        summary: dict[str, Any] = {"name": self.name}
        if self.original_names:
            summary["original_names"] = list(self.original_names)
        if self.old_proj_name:
            summary["old_proj_name"] = self.old_proj_name
        if self.format:
            summary["format"] = self.format
        if self.proj_method:
            summary["proj_method"] = self.proj_method
        summary["kind"] = self.kind.value
        if self.url:
            summary["url"] = self.url
        summary["direct_download"] = self.direct_download
        if self.open_license is not None:
            summary["open_license"] = self.open_license
        summary["installed"] = self.installed
        if self.remote_available is not None:
            summary["remote_available"] = self.remote_available
        summary["used_by"] = [authority_id(code) for code in self.used_by]
        summary.update(area_json(self.areas_of_use))
        return summary

    def _row(self) -> dict[str, Any]:
        return table_row(self, _COLUMNS)

    def __str__(self) -> str:
        return self.name


_COLUMNS = (
    "name",
    "kind",
    "format",
    "proj_method",
    "installed",
    "remote_available",
    "url",
    "direct_download",
    "open_license",
    "original_names",
    "used_by",
    "area_of_use",
)


def _kind(proj_method: str | None, method_names: Iterable[str]) -> GridKind:
    if proj_method in _PROJ_METHODS:
        return _PROJ_METHODS[proj_method]
    for name in method_names:
        folded = name.casefold()
        for word, kind in _METHOD_WORDS:
            if word in folded:
                return kind
    return GridKind.UNKNOWN


@lru_cache(maxsize=4)
def entries(databases: DatabaseIdentity) -> tuple[GridEntry, ...]:
    """Every grid in the database, without what can change on disk."""
    alternatives: dict[str, tuple[Any, ...]] = {}
    with _registry.reading(databases) as connection:
        if _registry.has_table(connection, "grid_alternatives"):
            for row in connection.execute(
                "SELECT original_grid_name, proj_grid_name, old_proj_grid_name, "
                "proj_grid_format, proj_method, url, direct_download, open_license "
                "FROM grid_alternatives"
            ):
                alternatives[str(row[0])] = row
    files: dict[str, dict[str, Any]] = {}

    def file_of(stated: str) -> dict[str, Any]:
        row = alternatives.get(stated)
        name = str(row[1]) if row and row[1] else stated
        found = files.setdefault(
            name,
            {"originals": [], "row": row, "operations": [], "methods": [], "areas": []},
        )
        if found["row"] is None and row is not None:
            found["row"] = row
        if stated != name and stated not in found["originals"]:
            found["originals"].append(stated)
        return found

    for operation in operation_entries(databases):
        # A NADCON pair (".las", ".los") is one GeoTIFF in PROJ, which
        # grid_alternatives records against the first name only.
        mapped = next((g for g in operation.grid_names if g in alternatives), None)
        for stated in operation.grid_names:
            if mapped is not None and stated not in alternatives:
                found = file_of(mapped)
                if stated not in found["originals"]:
                    found["originals"].append(stated)
            else:
                found = file_of(stated)
            if operation.authority_code not in found["operations"]:
                found["operations"].append(operation.authority_code)
            if operation.method is not None:
                found["methods"].append(operation.method.name)
            for area in operation.areas_of_use:
                if area not in found["areas"]:
                    found["areas"].append(area)
    for stated in alternatives:
        file_of(stated)

    built = []
    for name, found in files.items():
        row = found["row"]
        built.append(
            GridEntry(
                name=name,
                original_names=tuple(found["originals"]),
                old_proj_name=_registry.text(row[2]) if row else None,
                format=_registry.text(row[3]) if row else None,
                proj_method=_registry.text(row[4]) if row else None,
                kind=_kind(row[4] if row else None, found["methods"]),
                url=_registry.text(row[5]) if row else None,
                direct_download=bool(row[6]) if row else False,
                open_license=None if not row or row[7] is None else bool(row[7]),
                used_by=tuple(found["operations"]),
                areas_of_use=tuple(found["areas"]),
            )
        )
    return tuple(sorted(built, key=lambda e: e.name.casefold()))


def _directories() -> tuple[Path, ...]:
    """Where PROJ looks for a grid file on this machine."""
    found = [Path(d) for d in datadir.get_data_dir().split(os.pathsep) if d]
    found.append(Path(datadir.get_user_data_dir()))
    return tuple(found)


def _installed(entry: GridEntry, directories: tuple[Path, ...]) -> bool:
    names = {entry.name, *entry.original_names}
    if entry.old_proj_name:
        names.add(entry.old_proj_name)
    return any(
        (directory / name).is_file() for directory in directories for name in names
    )


@lru_cache(maxsize=1)
def remote_names() -> frozenset[str]:
    """The files PROJ's content delivery network lists. Uses the network."""
    try:
        features = get_transform_grid_list(include_already_downloaded=True)
    except Exception as error:  # urllib, ssl and JSON errors all end up here
        raise GridIndexUnavailableError(
            f"could not read the list of grids PROJ's CDN publishes: {error}"
        ) from error
    return frozenset(
        str(feature["properties"]["name"])
        for feature in features
        if isinstance(feature, Mapping) and "name" in feature.get("properties", {})
    )


def grids(
    *,
    authority: str | Iterable[str] | None = None,
    name: TextFilter | None = None,
    kind: str | GridKind | Iterable[str | GridKind] | None = None,
    installed: bool | None = None,
    check_remote: bool = False,
    remote_available: bool | None = None,
    format: str | Iterable[str] | None = None,
    direct_download: bool | None = None,
    open_license: bool | None = None,
    used: bool | None = None,
    operation: ReferenceFilter | None = None,
    area_of_interest: AreaFilter | None = None,
    contains: bool = False,
    area_name: TextFilter | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Listing[GridEntry]:
    """List the grid files the operations in the active database read.

    Also those PROJ's ``grid_alternatives`` table knows that no operation
    reads. Read from ``proj.db``; nothing is downloaded. Every filter given
    must hold.

    Args:
        authority: Only grids read by an operation of these authorities.
        name: Text the PROJ file name or an original name contains, without
            case, or a pattern.
        kind: Only these kinds, for example ``"vertical"``.
        installed: True for only grids found on this machine, False for only
            those missing.
        check_remote: Ask PROJ's content delivery network which grids it
            publishes, to fill in :attr:`GridEntry.remote_available`. The only
            listing that uses the network; read once per process.
        remote_available: True for only grids the network publishes, False
            for those it does not. Implies ``check_remote``.
        format: Only these file formats, by name without case, for example
            ``"GTiff"``.
        direct_download: True for only grids downloadable without
            registering, False for none.
        open_license: True for only openly licensed grids, False for those
            that are not; a grid of unknown licence matches neither.
        used: True for only grids some operation reads, False for those none
            does.
        operation: Only grids these operations read, by code or entry.
        area_of_interest: ``(west, south, east, north)`` in degrees, tested
            against the areas of use of the operations reading the grid.
        contains: With ``area_of_interest``, only grids with an area of use
            containing it entirely, rather than intersecting it.
        area_name: Text an area of use's description contains, or a pattern.
        limit: At most this many entries.
        offset: Skip this many entries first.

    Returns:
        The grids, ordered by file name.

    Raises:
        UnknownAuthorityError: If ``authority`` names an authority the
            database does not hold.
        InvalidFilterError: If a filter is malformed.
        GridIndexUnavailableError: If ``check_remote`` and the network's list
            of grids cannot be read.

    Example:
        >>> from geodetic_engine import catalog
        >>> found = catalog.grids(operation="EPSG:15851")
        >>> sorted(grid.name for grid in found)
        ['us_noaa_conus.tif']
    """
    kind, format, operation = (
        _filters.given(kind),
        _filters.given(format),
        _filters.given(operation),
    )
    databases = _registry.identity()
    authorities = _filters.authorities(authority, databases)
    names = _filters.text(name, "name")
    kinds = _filters.choices(kind, GridKind, "grid kind")
    installed_ = _filters.flag(installed, "installed")
    remote = _filters.flag(check_remote, "check_remote")
    remote_ = _filters.flag(remote_available, "remote_available")
    formats = _filters.labels(format, "format")
    downloadable = _filters.flag(direct_download, "direct_download")
    licensed = _filters.flag(open_license, "open_license")
    used_ = _filters.flag(used, "used")
    readers = None
    if operation is not None:
        known = {
            _filters.folded(entry.auth_name, entry.code): (entry.name,)
            for entry in operation_entries(databases)
        }
        readers = _filters.references(operation, "operation", known, databases)
    bounds = _filters.area_of_interest(area_of_interest)
    whole = _filters.flag(contains, "contains")
    if whole is None or remote is None:
        raise InvalidFilterError("contains and check_remote must be True or False")
    areas = _filters.text(area_name, "area_name")
    limit, offset = _filters.paging(limit, offset)
    remote = remote or remote_ is not None
    prefixes = None if authorities is None else {f"{a}:" for a in authorities}

    def admits(entry: GridEntry) -> bool:
        if prefixes is not None and not any(
            code.startswith(prefix) for code in entry.used_by for prefix in prefixes
        ):
            return False
        if kinds is not None and entry.kind not in kinds:
            return False
        if formats is not None and (entry.format or "").casefold() not in formats:
            return False
        if not _filters.admits_flag(downloadable, entry.direct_download):
            return False
        if licensed is not None and entry.open_license is not licensed:
            return False
        if not _filters.admits_flag(used_, bool(entry.used_by)):
            return False
        if readers is not None and not any(
            _filters.folded(*code.split(":", 1)) in readers for code in entry.used_by
        ):
            return False
        if names is not None and not names((entry.name, *entry.original_names)):
            return False
        if areas is not None and not areas(a.name for a in entry.areas_of_use):
            return False
        return _filters.located(entry.areas_of_use, bounds, whole)

    def produce() -> Iterator[GridEntry]:
        _registry.unchanged(databases)
        published = remote_names() if remote else None
        directories = _directories()
        for entry in entries(databases):
            if not admits(entry):
                continue
            found = replace(
                entry,
                installed=_installed(entry, directories),
                remote_available=None if published is None else entry.name in published,
            )
            if not _filters.admits_flag(installed_, found.installed):
                continue
            if remote_ is not None and found.remote_available is not remote_:
                continue
            yield found

    filters = _filters.echo(
        authority=authorities,
        name=name,
        kind=kind,
        installed=installed_,
        check_remote=remote or None,
        remote_available=remote_,
        format=format,
        direct_download=downloadable,
        open_license=licensed,
        used=used_,
        operation=operation,
        area_of_interest=bounds,
        contains=whole if bounds is not None else None,
        area_name=area_name,
        limit=limit,
        offset=offset or None,
    )
    columns = tuple(
        c
        for column in _COLUMNS
        for c in (
            ("area_of_use", "west", "south", "east", "north")
            if column == "area_of_use"
            else (column,)
        )
    )
    return Listing(
        "grids", filters, produce, columns=columns, limit=limit, offset=offset
    )
