"""OGC JSON-FG: the CRS a ``coordRefSys`` names, and the one to write back.

Private to :mod:`geodetic_engine.geojson`. A ``coordRefSys`` is a CRS URI, a
``{"type": "Reference", "href": ..., "epoch": ...}`` object adding a
coordinate epoch, or an array of those naming the parts of an ad hoc compound
CRS (OGC 21-045r1, 7.3.4). A URI is read for the authority and code in it and
never fetched.

A converted document states its CRS as an OGC URI, which exists for EPSG and
OGC codes only; a CRS without one is refused rather than given a URI that
resolves to nothing.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any, Final

from geodetic_engine.geodesy import CoordinateReferenceSystem, UnresolvableCRSError
from geodetic_engine.geodesy._crs_tools import compound
from geodetic_engine.geodesy.errors import UnsupportedCRSError
from geodetic_engine.geojson.errors import InvalidGeoJsonError, UnsupportedGeoJsonError

_URI: Final = re.compile(
    r"https?://www\.opengis\.net/def/crs/([^/]+)/([^/]*)/([^/?#]+)/?\Z", re.IGNORECASE
)
_URN: Final = re.compile(r"urn:ogc:def:crs:([^:]+):([^:]*):(.+)\Z", re.IGNORECASE)
_CURIE: Final = re.compile(r"\[([A-Za-z][\w.-]*):([\w.-]+)\]\Z")
_CODE: Final = re.compile(r"([A-Za-z][\w.-]*)::?([\w.-]+)\Z")
_URI_AUTHORITIES: Final = frozenset({"EPSG", "OGC"})
_BASE: Final = "http://www.opengis.net/def/crs"


def crs_name(text: str) -> str | None:
    """``"AUTH:code"`` for a CRS URI, URN, safe CURIE or code, else None.

    Example:
        >>> crs_name("http://www.opengis.net/def/crs/EPSG/0/4258")
        'EPSG:4258'
        >>> crs_name("urn:ogc:def:crs:OGC:1.3:CRS84")
        'OGC:CRS84'
    """
    text = text.strip()
    for pattern in (_URI, _URN):
        if (match := pattern.match(text)) is not None:
            return f"{match.group(1).upper()}:{match.group(3)}"
    for pattern in (_CURIE, _CODE):
        if (match := pattern.match(text)) is not None:
            return f"{match.group(1).upper()}:{match.group(2)}"
    return None


def read_coord_ref_sys(
    value: object, path: str
) -> tuple[CoordinateReferenceSystem, float | None]:
    """The CRS and coordinate epoch a ``coordRefSys`` states.

    Raises:
        InvalidGeoJsonError: If ``value`` is not a CRS reference.
        UnsupportedGeoJsonError: If it is one by value, which JSON-FG leaves to
            extensions, or an ad hoc compound of other than two CRSs.
        UnresolvableCRSError: If the CRS it names cannot be resolved.
    """
    if isinstance(value, list | tuple):
        parts = [_single(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if len(parts) != 2:
            raise UnsupportedGeoJsonError(
                path,
                f"an ad hoc compound CRS of {len(parts)} CRSs is not supported; "
                "give a horizontal CRS and a vertical one",
            )
        epochs = {epoch for _, epoch in parts if epoch is not None}
        if len(epochs) > 1:
            raise InvalidGeoJsonError(path, f"states more than one epoch: {epochs}")
        try:
            joined = compound(parts[0][0], parts[1][0])
        except UnsupportedCRSError as error:
            raise InvalidGeoJsonError(path, str(error)) from error
        return joined, epochs.pop() if epochs else None
    return _single(value, path)


def uri(crs: CoordinateReferenceSystem) -> str | None:
    """The OGC URI of ``crs``, which EPSG and OGC CRSs have, else None."""
    identity = crs.crs.to_authority(min_confidence=100)
    if identity is None or identity[0].upper() not in _URI_AUTHORITIES:
        return None
    return f"{_BASE}/{identity[0].upper()}/0/{identity[1]}"


def coord_ref_sys(crs: CoordinateReferenceSystem, epoch: float | None) -> Any:
    """The ``coordRefSys`` to write for ``crs``, or None if it has no URI.

    A compound CRS without a URI of its own is written as the array of its
    parts' URIs. An epoch is written with the first.
    """
    references: list[str | None] = [uri(crs)]
    if references[0] is None and crs.crs.is_compound:
        references = [
            uri(CoordinateReferenceSystem(part, part.name))
            for part in crs.crs.sub_crs_list
        ]
    if any(reference is None for reference in references):
        return None
    written: list[Any] = list(references)
    if epoch is not None:
        written[0] = {"type": "Reference", "href": references[0], "epoch": epoch}
    return written[0] if len(written) == 1 else written


def _single(value: object, path: str) -> tuple[CoordinateReferenceSystem, float | None]:
    epoch = None
    if isinstance(value, Mapping):
        if value.get("type") != "Reference":
            raise UnsupportedGeoJsonError(
                path,
                f"a coordRefSys of type {value.get('type')!r} is not supported; "
                "give a CRS URI or a Reference",
            )
        epoch = _epoch(value.get("epoch"), f"{path}.epoch")
        value = value.get("href")
        path = f"{path}.href"
    if not isinstance(value, str):
        raise InvalidGeoJsonError(path, f"a CRS reference is a URI, not {value!r}")
    name = crs_name(value)
    if name is None:
        raise InvalidGeoJsonError(path, f"{value!r} is not a CRS URI")
    try:
        return CoordinateReferenceSystem.from_user_input(name), epoch
    except UnresolvableCRSError as error:
        raise UnresolvableCRSError(f"{path}: {error}") from error


def _epoch(value: object, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidGeoJsonError(path, f"an epoch is a decimal year, not {value!r}")
    if not math.isfinite(value):
        raise InvalidGeoJsonError(path, f"{value!r} is not a finite decimal year")
    return float(value)
