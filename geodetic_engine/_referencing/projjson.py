"""Reading the PROJJSON tree of a coordinate operation.

Shared by ``geodesy``, which checks what PROJ applies, and by
``persistablereference`` and the Helmert algebra, which must not export an
operation PROJ applies inverted.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

# PROJJSON object types that are coordinate operations. Anything else with an
# "id" (a CRS, a datum, an ellipsoid, a method, a parameter) is not one, and
# must not be allowed to satisfy a request for an operation code.
_OPERATION_TYPES = frozenset(
    {"Transformation", "Conversion", "ConcatenatedOperation", "PointMotionOperation"}
)

# Descending into these would reach the map projection conversion that a
# projected CRS carries, which is part of the CRS definition rather than part
# of the operation being applied.
_NESTED_CRS_KEYS = frozenset(
    {"source_crs", "target_crs", "interpolation_crs", "base_crs"}
)

# Only the INVERSE wrapper changes what the operation computes, and neither
# WKT2 nor PROJJSON can express it, so an export carrying one cannot be read
# back faithfully. DERIVED_FROM is unaffected.
_INVERSE_AUTHORITY = re.compile(r"INVERSE\(", re.IGNORECASE)


def has_inverted_step(definition: object) -> bool:
    """Whether a datum transformation in the tree is one PROJ applies inverted.

    PROJ marks such a step by wrapping its authority as ``INVERSE(EPSG)``,
    which is a PROJ-internal convention rather than anything WKT2 or PROJJSON
    define: neither format has a flag for "apply this operation backwards".
    Re-reading an export that contains one yields the *forward* operation
    instead, silently reversing the sign of a datum shift, so such an export
    must not be handed out. Reported as https://github.com/OSGeo/PROJ/issues/4866.

    Only datum-changing steps count. An inverted *conversion* -- a map
    projection or an axis-order reversal -- is analytically invertible from
    the same parameters, so PROJ reconstructs it correctly and its export is
    faithful. A Helmert's inverse is not recoverable that way, which is
    exactly the case this guards.

    The test is deliberately conservative: it asks whether an inversion is
    present, not whether that particular inversion happens to be harmless. A
    null transformation's inverse equals itself and would in fact survive the
    round trip, but relying on the parameters being zero is not a property
    worth betting coordinates on.

    Args:
        definition: PROJJSON of the operation, as a dict.

    Returns:
        True if a datum-changing step, or its method, carries an
        ``INVERSE(...)`` authority.

    Example:
        >>> has_inverted_step(
        ...     {"type": "Transformation",
        ...      "id": {"authority": "INVERSE(EPSG)", "code": 9607}}
        ... )
        True
        >>> has_inverted_step(
        ...     {"type": "Conversion",
        ...      "id": {"authority": "INVERSE(EPSG)", "code": 16031}}
        ... )
        False
    """
    return any(
        _INVERSE_AUTHORITY.search(str(identifier.get("authority", "")))
        for node in operation_nodes(definition)
        if node.get("type") in ("Transformation", "PointMotionOperation")
        for identifier in (node.get("id"), (node.get("method") or {}).get("id"))
        if isinstance(identifier, dict)
    )


def operation_nodes(definition: object) -> Iterator[dict[str, Any]]:
    """Yield every coordinate operation dict in a PROJJSON tree."""
    if isinstance(definition, dict):
        if definition.get("type") in _OPERATION_TYPES:
            yield definition
        for key, value in definition.items():
            if key not in _NESTED_CRS_KEYS:
                yield from operation_nodes(value)
    elif isinstance(definition, list):
        for item in definition:
            yield from operation_nodes(item)
