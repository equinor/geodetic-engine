"""The result of every listing function: entries, read when first used."""

from __future__ import annotations

import copy
import itertools
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, overload

from geodetic_engine._referencing.database import DatabaseIdentity
from geodetic_engine.catalog import _registry
from geodetic_engine.catalog._entry import CatalogEntry
from geodetic_engine.catalog.errors import NotFoundError, NotUniqueError

if TYPE_CHECKING:
    import pandas as pd

# Where the schema of a listing's JSON is published; also shipped in the
# package, see listing_schema().
LISTING_SCHEMA_URL = (
    "https://equinor.github.io/geodetic-engine/schemas/catalog-listing-v1.schema.json"
)


class Listing[E: CatalogEntry](Sequence[E]):
    """The entries one listing function found.

    A listing is lazy: the database is read, and the filters that need PROJ
    for each entry are applied, the first time it is iterated, indexed,
    measured or exported. The entries are then kept, so iterating again
    reads nothing. ``limit`` and ``offset`` are applied after every filter,
    so pages are consistent, and iteration stops once ``limit`` entries are
    found.

    Entries are ordered by authority, then by code, numerically where the code
    is a number.

    A listing belongs to the database it was made for. Once PROJ's database
    has been switched, a listing not yet read refuses to be read, and any
    listing refuses to be exported as JSON, since the export names the
    database that answered. Entries already read stay as they were read.

    Example:
        >>> from geodetic_engine import catalog
        >>> found = catalog.crs(authority="EPSG", name="UTM zone 32N", kind="projected")
        >>> found.first().name
        'ELD79 / UTM zone 32N'
        >>> "EPSG:25832" in [entry.authority_code for entry in found]
        True
    """

    __slots__ = (
        "_columns",
        "_databases",
        "_entries",
        "_filters",
        "_kind",
        "_produce",
    )

    def __init__(
        self,
        kind: str,
        filters: Mapping[str, Any],
        produce: Callable[[], Iterator[E]],
        *,
        columns: tuple[str, ...],
        databases: DatabaseIdentity,
        limit: int | None = None,
        offset: int = 0,
    ) -> None:
        """Wrap a listing; the listing functions build these.

        Args:
            kind: What is listed, for example ``"crs"``.
            filters: The filters asked for, as JSON values.
            produce: Yields every entry that passes the filters, in order.
            columns: The columns of :meth:`to_dataframe`.
            databases: The databases PROJ read when the filters were checked.
            limit: At most this many entries.
            offset: Skip this many entries first.
        """
        self._kind = kind
        self._filters = dict(filters)
        self._columns = columns
        self._databases = databases
        stop = None if limit is None else offset + limit
        self._produce: Callable[[], Iterator[E]] = lambda: itertools.islice(
            produce(), offset, stop
        )
        self._entries: tuple[E, ...] | None = None

    @property
    def kind(self) -> str:
        """What is listed, as ``"crs"``, ``"operations"``, ``"datums"`` and so on."""
        return self._kind

    @property
    def filters(self) -> Mapping[str, Any]:
        """The filters this listing was asked for, as JSON values.

        A copy, lists and all: changing it does not change the listing.
        """
        return copy.deepcopy(self._filters)

    def _all(self) -> tuple[E, ...]:
        if self._entries is None:
            _registry.unchanged(self._databases)
            self._entries = tuple(self._produce())
        return self._entries

    def __iter__(self) -> Iterator[E]:
        return iter(self._all())

    def __len__(self) -> int:
        return len(self._all())

    @overload
    def __getitem__(self, index: int) -> E: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[E, ...]: ...

    def __getitem__(self, index: int | slice) -> E | tuple[E, ...]:
        return self._all()[index]

    def __bool__(self) -> bool:
        return bool(self._all())

    def __repr__(self) -> str:
        count = "unread" if self._entries is None else f"{len(self._entries)} entries"
        return f"<Listing {self._kind} {self._filters!r}: {count}>"

    def first(self) -> E | None:
        """The first entry, or None when there is none."""
        entries = self._all()
        return entries[0] if entries else None

    def one(self) -> E:
        """The only entry.

        Raises:
            NotFoundError: If there is none.
            NotUniqueError: If there is more than one.
        """
        entries = self._all()
        if not entries:
            raise NotFoundError(f"no {self._kind} entry matches {self._filters!r}")
        if len(entries) > 1:
            shown = ", ".join(str(entry) for entry in entries[:5])
            more = f" and {len(entries) - 5} more" if len(entries) > 5 else ""
            raise NotUniqueError(
                f"{len(entries)} {self._kind} entries match {self._filters!r}: "
                f"{shown}{more}"
            )
        return entries[0]

    def to_list(self) -> list[E]:
        """The entries as a list."""
        return list(self._all())

    def to_dataframe(self) -> pd.DataFrame:
        """The entries as a table, one row per entry.

        References are given as ``"name (AUTH:CODE)"``, enumerations by
        value, and the first area of use as ``area_of_use`` with its
        ``west``, ``south``, ``east`` and ``north`` bounds. The columns are
        the same when the listing is empty.
        """
        import pandas as pd

        rows = [entry._row() for entry in self._all()]
        return pd.DataFrame(rows, columns=list(self._columns))

    def to_json_dict(self, *, include_definitions: bool = False) -> dict[str, Any]:
        """The listing as JSON, with the database that answered it.

        Every item is the entry's
        :meth:`~geodetic_engine.catalog.RegistryEntry.to_summary_dict`. The
        whole validates against :func:`~geodetic_engine.catalog.listing_schema`.

        Args:
            include_definitions: Add each entry's full PROJJSON as
                ``definition``. Builds every object through PROJ, so it takes
                longer on a large listing.

        Raises:
            CatalogError: If PROJ's database was switched after the listing
                was made, whether or not its entries were read before. The
                export would name a database that did not answer.
        """
        from geodetic_engine.catalog._database import database

        _registry.unchanged(self._databases)
        items = []
        for entry in self._all():
            item = entry.to_summary_dict()
            if include_definitions:
                item["definition"] = entry.to_json_dict()
            items.append(item)
        return {
            "$schema": LISTING_SCHEMA_URL,
            "type": "CatalogListing",
            "kind": self._kind,
            "database": database().to_json_dict(),
            "filters": self.filters,
            "count": len(items),
            "items": items,
        }

    def to_json(
        self, *, include_definitions: bool = False, indent: int | None = 2
    ) -> str:
        """:meth:`to_json_dict` as text.

        Args:
            include_definitions: See :meth:`to_json_dict`.
            indent: Indentation, or None for one line.
        """
        return json.dumps(
            self.to_json_dict(include_definitions=include_definitions),
            indent=indent,
            ensure_ascii=False,
        )
