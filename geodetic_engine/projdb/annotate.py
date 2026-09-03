"""Annotations this authority adds to objects it does not own.

A register curates more than its own objects. It also records what an
organisation calls ``EPSG:32632`` and what that CRS is used for here, as an
alias and as a usage whose scope belongs to the custom authority rather than to
EPSG. Those annotations are the reason a lookup by a local name succeeds, so
losing them loses the point of building a custom database at all.

Nothing here rewrites the annotated object. The EPSG row stays exactly as PROJ
shipped it; only ``alias_name`` and ``usage`` gain rows pointing at it. An
object that is not already in the database is skipped rather than annotated,
because a usage row referencing a CRS that does not exist is a dangling
reference that PROJ's own foreign key check would reject.
"""

from __future__ import annotations

import logging
from typing import Any

from geodetic_engine.projdb import translate as tr
from geodetic_engine.projdb.context import BuildContext
from geodetic_engine.projdb.records import ObjectKey

logger = logging.getLogger(__name__)

# Collection endpoints that carry annotatable objects, and the proj.db table
# each one lands in. Enumerated per kind so the table is known without having
# to read every object's Kind.
_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("GeodeticCoordRefSystem", "geodetic_crs"),
    ("ProjectedCoordRefSystem", "projected_crs"),
    ("VerticalCoordRefSystem", "vertical_crs"),
    ("EngineeringCoordRefSystem", "engineering_crs"),
    ("CompoundCoordRefSystem", "compound_crs"),
)


def collect_foreign_annotations(context: BuildContext) -> None:
    """Attach this authority's aliases and usages to other authorities' objects.

    Every collection is enumerated in full, because the API has no server-side
    filter for "objects annotated by this authority": the annotation is on the
    object, so the object has to be read to find it.
    """
    if not context.config.annotate_foreign_objects:
        logger.info("foreign object annotation disabled by configuration")
        return

    custom = {name.casefold() for name in context.config.authorities}
    annotated = 0
    for endpoint, table in _ENDPOINTS:
        for summary in context.client.iter_collection(endpoint):
            auth, code = tr.auth_name(summary), tr.code(summary)
            if code is None or auth.casefold() in custom:
                continue
            # Only annotate what the database already holds; PROJ's foreign key
            # check would reject a usage pointing at an absent object.
            if context.is_new(table, auth, code):
                continue
            if _annotate(context, ObjectKey(table, auth, str(code)), summary, custom):
                annotated += 1
    logger.info("foreign objects annotated: %d", annotated)


def _annotate(
    context: BuildContext,
    key: ObjectKey,
    summary: dict[str, Any],
    custom: set[str],
) -> bool:
    """Record one foreign object's custom aliases and usages.

    Returns:
        True if the object carried anything belonging to this authority.
    """
    obj = context.client.detail(summary)
    added = sum(
        context.alias.add(
            key,
            alias=tr.text(record, "Alias"),
            source=str((record.get("NamingSystem") or {}).get("Name") or ""),
        )
        for record in context.client.aliases(obj)
    )

    for usage in obj.get("Usage") or []:
        scope = context.client.resolve(usage.get("Scope"))
        extent = context.client.resolve(usage.get("Extent"))
        # An EPSG object with an EPSG scope is already described by the base
        # database; only an annotation this authority added is new information.
        if not _owned(scope, custom) and not _owned(extent, custom):
            continue
        context.usage.add(key, scope=tr.scope_of(scope), extent=tr.extent_of(extent))
        added += 1

    if added:
        logger.debug("annotated %s %s:%s", key.table, key.auth_name, key.code)
    return added > 0


def _owned(obj: dict[str, Any], custom: set[str]) -> bool:
    """Whether a scope or extent belongs to one of the custom authorities."""
    return bool(obj) and tr.auth_name(obj).casefold() in custom
