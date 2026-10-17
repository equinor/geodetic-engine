"""OSDU record ids for coordinate reference systems and transformations.

OSDU names a CRS or a coordinate transformation by the id of the reference data
record that publishes it::

    opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031
    opendes:reference-data--CoordinateReferenceSystem:BoundProjected:EPSG::23031_EPSG::1133
    opendes:reference-data--CoordinateTransformation:EPSG::1612

The first segment is the data partition holding the record. It differs between
operators. OSDU builds the rest of the id the same way in every partition so
that a standard CRS is recognised in all of them. So the partition is dropped,
and what remains, the *identifier*, is what is looked up.

A reference from one record to another ends with a version separator:
``...:EPSG::23031:`` for the latest version, ``...:EPSG::23031:17`` for a
particular one. A proj.db holds one definition per identifier, the one in the
catalogue it was built from, so the version is dropped as well.

Identifiers are looked up exactly, in the table that a build from an OSDU
catalogue writes (see :mod:`geodetic_engine.osdudb`). Two shortcuts are
deliberately not taken:

* An identifier is never decoded from its spelling. OSDU's convention for
  spelling ids is good practice, not a guarantee: operators curate these
  records themselves, and the schema allows any id, a UUID included.
* An identifier is never handed to PROJ's name search. That search matches
  approximately, and for an id an approximate match means a typo in a
  transformation code resolves to a different transformation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from geodetic_engine.geodesy.database import (
    IdentifiedObject,
    identified_object,
    identifier_scheme_present,
    skip_reason,
)

__all__ = [
    "CRS_ENTITY",
    "SCHEME",
    "TRANSFORMATION_ENTITY",
    "OsduId",
    "parse_osdu_id",
    "resolve_osdu_id",
    "unresolved_reason",
]

# The identifier scheme OSDU record ids are recorded under in proj.db.
SCHEME = "OSDU"

CRS_ENTITY = "reference-data--CoordinateReferenceSystem"
TRANSFORMATION_ENTITY = "reference-data--CoordinateTransformation"
_ENTITIES = (CRS_ENTITY, TRANSFORMATION_ENTITY)

# The characters OSDU's schemas allow in an id after the entity type.
_RECORD_ID = re.compile(r"[\w\-.:%]+")

# A data partition, as OSDU's schemas spell one, or the ``{{NAMESPACE}}``
# placeholder a published manifest carries instead. Strict enough that no
# JSON payload, quoted or percent-encoded, can be read as one.
_NAMESPACE = re.compile(r"[\w\-.]+|\{\{\w+\}\}")

# A version separator ending a reference. The character before the colon must
# not itself be a colon, since ``EPSG::1612`` ends in a code, not a version.
_VERSIONED = re.compile(r"(?P<id>.*[^:]):\d*")


@dataclass(frozen=True, slots=True)
class OsduId:
    """An OSDU record id for a CRS or a coordinate transformation.

    Attributes:
        entity: The record's entity type, :data:`CRS_ENTITY` or
            :data:`TRANSFORMATION_ENTITY`.
        record_id: Everything after the entity type, as given; OSDU's
            ``data.ID``, possibly followed by a version separator.
        namespace: The data partition, when one was given.
    """

    entity: str
    record_id: str
    namespace: str | None = None

    @property
    def identifier(self) -> str:
        """The id without its partition, as given otherwise."""
        return f"{self.entity}:{self.record_id}"

    @property
    def is_crs(self) -> bool:
        """Whether the id names a coordinate reference system record."""
        return self.entity == CRS_ENTITY

    @property
    def is_transformation(self) -> bool:
        """Whether the id names a coordinate transformation record."""
        return self.entity == TRANSFORMATION_ENTITY

    @property
    def described(self) -> str:
        """What kind of record the id names, in words."""
        return (
            "coordinate reference system"
            if self.is_crs
            else "coordinate transformation"
        )

    def candidates(self) -> tuple[str, ...]:
        """The identifiers to look up, in order.

        The id as given comes first, so an operator's id that genuinely ends
        in a colon and digits is never cut short. Then the id without a
        version separator, which is what a reference from another record
        carries.

        Example:
            >>> parse_osdu_id(
            ...     "ns:reference-data--CoordinateTransformation:EPSG::1612:"
            ... ).candidates()
            ('reference-data--CoordinateTransformation:EPSG::1612:',
             'reference-data--CoordinateTransformation:EPSG::1612')
            >>> parse_osdu_id(
            ...     "reference-data--CoordinateTransformation:EPSG::1612"
            ... ).candidates()
            ('reference-data--CoordinateTransformation:EPSG::1612',)
        """
        found = [self.record_id]
        if (versioned := _VERSIONED.fullmatch(self.record_id)) is not None:
            found.append(versioned["id"])
        return tuple(f"{self.entity}:{record}" for record in dict.fromkeys(found))


def parse_osdu_id(value: str, *, entity: str | None = None) -> OsduId | None:
    """Recognise an OSDU record id for a CRS or a coordinate transformation.

    Only a string naming one of the two entity types is recognised, with or
    without its partition. Anything else, an authority code, WKT, a URN or a
    payload, is not an OSDU id and yields None, so this can be asked of any
    input.

    Args:
        value: The candidate id.
        entity: The entity type the caller expects. When given, a bare
            ``data.ID`` such as ``"Projected:EPSG::23031"`` is accepted too,
            since the entity type is then implied.

    Returns:
        The parsed id, or None when ``value`` is not one.

    Raises:
        ValueError: If ``entity`` is neither :data:`CRS_ENTITY` nor
            :data:`TRANSFORMATION_ENTITY`.

    Example:
        >>> osdu = parse_osdu_id(
        ...     "opendes:reference-data--CoordinateReferenceSystem:Projected:"
        ...     "EPSG::23031"
        ... )
        >>> osdu.namespace, osdu.identifier
        ('opendes', 'reference-data--CoordinateReferenceSystem:Projected:EPSG::23031')
        >>> parse_osdu_id("EPSG:23031") is None
        True
    """
    if entity is not None and entity not in _ENTITIES:
        raise ValueError(f"{entity!r} is not an OSDU CRS or transformation entity")
    text = value.strip()
    for candidate in _ENTITIES:
        marker = f"{candidate}:"
        position = text.find(marker)
        if position < 0:
            continue
        prefix, record_id = text[:position], text[position + len(marker) :]
        namespace = prefix[:-1] if prefix.endswith(":") else prefix
        if prefix and (prefix == namespace or not _NAMESPACE.fullmatch(namespace)):
            return None
        if not _RECORD_ID.fullmatch(record_id):
            return None
        return OsduId(candidate, record_id, namespace or None)
    if entity is not None and _RECORD_ID.fullmatch(text):
        return OsduId(entity, text)
    return None


def resolve_osdu_id(osdu: OsduId) -> IdentifiedObject | None:
    """The object an OSDU id names in the active proj.db, if it names one.

    Args:
        osdu: The parsed id.

    Returns:
        The first of :meth:`OsduId.candidates` the database records, or None.
    """
    for identifier in osdu.candidates():
        if (found := identified_object(SCHEME, identifier)) is not None:
            return found
    return None


def unresolved_reason(osdu: OsduId) -> str:
    """Why an OSDU id names nothing in the active proj.db, in actionable words.

    Args:
        osdu: An id :func:`resolve_osdu_id` found nothing for.

    Returns:
        A sentence for an error message.
    """
    for identifier in osdu.candidates():
        if (reason := skip_reason(identifier)) is not None:
            return reason
    if not identifier_scheme_present(SCHEME):
        return (
            "the active proj.db records no OSDU ids; build one from the OSDU "
            "catalogue that publishes the record with geodetic-osdudb and put "
            "it on PROJ's data path"
        )
    return (
        f"the active proj.db records no OSDU id {osdu.identifier!r}, so the "
        "catalogue it was built from does not publish that record"
    )
