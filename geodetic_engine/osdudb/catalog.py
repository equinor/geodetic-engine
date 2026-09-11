"""Reading an OSDU coordinate reference system catalogue.

An OSDU manifest is a single JSON document whose ``ReferenceData`` array holds
records of kind ``reference-data--CoordinateReferenceSystem`` and
``reference-data--CoordinateTransformation``. Unlike a register served over
HTTP there is nothing to page through and nothing to resolve over the network:
every record is present, and a reference from one record to another is an
``AuthorityCode`` pair that is looked up in this index.

The catalogue does not carry separate records for units, ellipsoids, prime
meridians, coordinate systems or datums. Those exist only inside each record's
WKT, which is why :mod:`geodetic_engine.osdudb.definition` has to take them
apart.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from geodetic_engine.osdudb import translate as tr
from geodetic_engine.osdudb.errors import OsduCatalogError

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]

# The array of records in an OSDU manifest.
REFERENCE_DATA = "ReferenceData"

# Record kinds, matched loosely because the manifest carries a full versioned
# kind such as ``osdu:wks:reference-data--CoordinateReferenceSystem:1.2.0`` and
# minor version changes must not silently drop half the catalogue.
CRS_KIND = "reference-data--CoordinateReferenceSystem"
TRANSFORMATION_KIND = "reference-data--CoordinateTransformation"

# ``CoordinateReferenceSystemType`` values.
GEODETIC_CRS = "GeodeticCRS"
PROJECTED_CRS = "ProjectedCRS"
VERTICAL_CRS = "VerticalCRS"
COMPOUND_CRS = "CompoundCRS"
ENGINEERING_CRS = "EngineeringCRS"
BOUND_CRS = "BoundCRS"

# ``CoordinateTransformationType`` values.
TRANSFORMATION = "Transformation"
CONCATENATED_OPERATION = "ConcatenatedOperation"


@dataclass(frozen=True, slots=True)
class Record:
    """One reference data record, identified the way proj.db identifies things.

    Attributes:
        auth_name: The record's ``CodeSpace``, which is the proj.db authority.
        code: The record's ``Code``, as a string.
        type: ``CoordinateReferenceSystemType`` or
            ``CoordinateTransformationType``.
        is_operation: Whether this is a coordinate operation rather than a CRS.
        data: The record's ``data`` object, verbatim.
    """

    auth_name: str
    code: str
    type: str
    is_operation: bool
    data: JsonObject

    @property
    def name(self) -> str:
        """The record's name, as the catalogue states it."""
        return tr.text(self.data, "Name") or "unknown"

    @property
    def described(self) -> str:
        """A short identity for log and error messages."""
        return f"{self.type} {self.auth_name}:{self.code} ({self.name})"


class OsduCatalog:
    """An indexed OSDU manifest.

    CRSs and coordinate operations are indexed separately. Their code spaces
    overlap in principle, and resolving a bound CRS's source CRS against an
    operation of the same code would silently produce a different object.

    Example:
        >>> catalog = OsduCatalog.from_file(Path("CRS_CT.json"))  # doctest: +SKIP
        >>> len(list(catalog.records(BOUND_CRS)))  # doctest: +SKIP
        1276
    """

    def __init__(self, records: Iterable[Record], *, path: Path | None = None) -> None:
        self.path = path
        self._records: list[Record] = list(records)
        self._crs: dict[tuple[str, str], Record] = {}
        self._operations: dict[tuple[str, str], Record] = {}
        for record in self._records:
            index = self._operations if record.is_operation else self._crs
            index.setdefault((record.auth_name, record.code), record)

    @classmethod
    def from_file(cls, path: Path) -> OsduCatalog:
        """Read and index a manifest.

        Args:
            path: The manifest file.

        Returns:
            The indexed catalogue.

        Raises:
            OsduCatalogError: If the file is unreadable, is not JSON, or has no
                ``ReferenceData`` array.
        """
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise OsduCatalogError(f"could not read {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise OsduCatalogError(f"{path} is not valid JSON: {exc}") from exc
        return cls.from_document(document, path=path)

    @classmethod
    def from_document(
        cls, document: JsonObject, *, path: Path | None = None
    ) -> OsduCatalog:
        """Index an already parsed manifest."""
        entries = document.get(REFERENCE_DATA)
        if not isinstance(entries, list):
            raise OsduCatalogError(
                f"{path or 'the catalogue'} has no {REFERENCE_DATA} array; it "
                "is not an OSDU manifest"
            )
        records = [
            record for entry in entries if (record := _record(entry)) is not None
        ]
        logger.info(
            "read %d record(s) from %s, %d of which are recognised",
            len(entries),
            path or "the catalogue",
            len(records),
        )
        return cls(records, path=path)

    def records(self, *types: str) -> Iterator[Record]:
        """Yield every record of the given types, in catalogue order.

        Args:
            *types: ``CoordinateReferenceSystemType`` or
                ``CoordinateTransformationType`` values. With none given, every
                record is yielded.
        """
        wanted = set(types)
        for record in self._records:
            if not wanted or record.type in wanted:
                yield record

    def crs(self, auth_name: str | None, code: str | None) -> Record | None:
        """Return the CRS with an authority and code, if the catalogue has it."""
        return self._lookup(self._crs, auth_name, code)

    def operation(self, auth_name: str | None, code: str | None) -> Record | None:
        """Return the operation with an authority and code, if it is present."""
        return self._lookup(self._operations, auth_name, code)

    def __len__(self) -> int:
        return len(self._records)

    @staticmethod
    def _lookup(
        index: dict[tuple[str, str], Record], auth_name: str | None, code: str | None
    ) -> Record | None:
        if not auth_name or code is None:
            return None
        return index.get((auth_name, str(code)))


def _record(entry: JsonObject) -> Record | None:
    """Turn one manifest entry into a record, or None if it is not one."""
    kind = str(entry.get("kind") or "")
    is_crs = CRS_KIND in kind
    is_operation = TRANSFORMATION_KIND in kind
    if not (is_crs or is_operation):
        return None

    data = entry.get("data")
    if not isinstance(data, dict):
        return None

    auth_name = tr.auth_name(data)
    code = tr.code(data)
    record_type = tr.text(
        data, "CoordinateReferenceSystemType", "CoordinateTransformationType"
    )
    if not auth_name or code is None or not record_type:
        logger.warning(
            "ignoring a %s record with no code space, code or type: %s",
            "transformation" if is_operation else "CRS",
            data.get("ID") or data.get("Name"),
        )
        return None

    return Record(
        auth_name=auth_name,
        code=code,
        type=record_type,
        is_operation=is_operation,
        data=data,
    )
