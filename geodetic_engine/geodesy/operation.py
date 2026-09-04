"""Identifying which EPSG coordinate operation is actually being applied.

The point of this module is to make the question "which operation did PROJ
really use?" answerable, rather than assumed. Asking PROJ for a transformation
and asking it for a *particular* transformation are different things, and the
gap between them is where wrong coordinates come from: PROJ will happily build
a working transformer using an operation other than the one that was asked for.

So an operation reference is parsed into a structured request, the operation
tree PROJ built is walked for the identifiers it actually contains, and the two
are compared. Nothing here trusts a name match or a substring.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pyproj.crs import CoordinateOperation
from pyproj.exceptions import CRSError

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

_URN_PREFIX = "urn:ogc:def:coordinateoperation:"

# Parameter names that mean the operation reads the coordinate epoch. EPSG
# names them consistently across the time-dependent Helmert variants.
_TIME_DEPENDENT_PARAMETERS = ("rate of change", "parameter reference epoch")

# PROJ wraps the authority of an operation it had to derive rather than look
# up directly. Normalising axis order re-issues an operation as
# DERIVED_FROM(DERIVED_FROM(EPSG)):3858, and building the inverse of a named
# operation re-issues it as INVERSE(EPSG):1612 -- both nestable, both still the
# same EPSG operation, and both must still satisfy a request for it.
_WRAPPED_AUTHORITY = re.compile(r"^(?:DERIVED_FROM|INVERSE)\((.*)\)$", re.IGNORECASE)


def base_authority(authority: str) -> str:
    """Strip PROJ's derived- and inverse-authority wrappers down to the issuer.

    Args:
        authority: An authority name, possibly wrapped, for example
            ``"DERIVED_FROM(DERIVED_FROM(EPSG))"`` or ``"INVERSE(EPSG)"``.

    Returns:
        The underlying authority name, for example ``"EPSG"``.

    Example:
        >>> base_authority("DERIVED_FROM(DERIVED_FROM(EPSG))")
        'EPSG'
        >>> base_authority("INVERSE(EPSG)")
        'EPSG'
    """
    name = authority.strip()
    while (match := _WRAPPED_AUTHORITY.match(name)) is not None:
        name = match.group(1).strip()
    return name


_INVERSE_NAME_PREFIX = re.compile(r"^Inverse of\s+", re.IGNORECASE)

# PROJ joins the names of operations it fuses into one unidentified step with
# this exact separator, each keeping its own "Inverse of" prefix independently.
_FUSED_NAME_SEPARATOR = " + "


def _base_operation_name(name: str) -> str:
    """Strip PROJ's "Inverse of" name prefix down to the underlying name.

    Mirrors :func:`base_authority`: building the inverse of a named operation
    renames it, but applying it in the reverse direction does not change which
    operation it is.

    Example:
        >>> _base_operation_name("Inverse of ED50 to WGS 84 (1)")
        'ED50 to WGS 84 (1)'
    """
    stripped = name.strip()
    while (match := _INVERSE_NAME_PREFIX.match(stripped)) is not None:
        stripped = stripped[match.end() :].strip()
    return stripped


def _operation_name_matches(node_name: str, expected_name: str) -> bool:
    """Whether a node's name is, or fuses in, the operation named by expected_name.

    A vertical shift folded into a compound target alongside a horizontal
    datum change (each touching only part of it) is fused by PROJ into one
    step whose name joins both operations' own names with " + ", each still
    carrying its own "Inverse of" prefix. So a name is checked whole first,
    and against each fused part in turn if that fails.

    Example:
        >>> _operation_name_matches(
        ...     "Inverse of A (1) + B (2)", "B (2)"
        ... )
        True
    """
    if _base_operation_name(node_name).casefold() == expected_name.casefold():
        return True
    parts = node_name.split(_FUSED_NAME_SEPARATOR)
    return len(parts) > 1 and any(
        _base_operation_name(part).casefold() == expected_name.casefold()
        for part in parts
    )


@lru_cache(maxsize=256)
def _registered_operation_name(auth_name: str, code: str) -> str | None:
    """The name an authority registers a coordinate operation's code under.

    Args:
        auth_name: Authority, for example ``"EPSG"``.
        code: Code within that authority.

    Returns:
        The registered name, or None if the authority does not know this code.
    """
    try:
        return CoordinateOperation.from_authority(auth_name, code).name
    except CRSError:
        return None


class OperationRoute(StrEnum):
    """How the transformer that will be used was arrived at."""

    TRANSFORMER_GROUP = "transformer_group"
    """Selected from the candidates PROJ offers for the CRS pair."""

    DIRECT = "direct"
    """Built from the requested operation, which spans the CRS pair itself."""

    CHAINED = "chained"
    """Built from the requested operation, wrapped in same-datum conversions."""

    BOUND = "bound"
    """Taken from the transformation a bound CRS carries in its own definition."""

    PROJ_DEFAULT = "proj_default"
    """No operation was requested; PROJ chose, and the choice is recorded."""

    ANY_OPERATION = "any_operation"
    """No operation was requested and the caller allowed PROJ to pick freely.

    Set only when ``allow_any_operation=True`` let a datum change through
    without a named operation, including a ballpark; the plain, no-datum-change
    default pick still reports :attr:`PROJ_DEFAULT`.
    """


@dataclass(frozen=True, slots=True)
class GridUsage:
    """A grid file a transformation depends on.

    Attributes:
        name: Short file name, for example ``"us_noaa_g2012bu0.tif"``.
        full_name: Absolute path if the grid is installed, otherwise empty.
        package_name: Name of the package that distributes the grid, if known.
        url: Where the grid can be obtained, if known.
        available: Whether PROJ can find the grid on this machine.
        open_license: Whether the grid is openly licensed.
        direct_download: Whether the grid can be downloaded without registration.
    """

    name: str
    full_name: str
    package_name: str
    url: str
    available: bool
    open_license: bool
    direct_download: bool


@dataclass(frozen=True, slots=True)
class AppliedOperation:
    """Which coordinate operation was applied, against which was requested.

    Attributes:
        requested: The operation reference the caller asked for, or None.
        auth_name: Authority of the operation applied, for example ``"EPSG"``.
        code: Code of the operation applied, for example ``"15670"``.
        name: Name of the operation applied.
        method_name: Name of the operation method, when it is a single step.
        accuracy: Stated accuracy in metres, or None when PROJ reports none.
        route: How the transformer was arrived at.
        ballpark: Whether the applied operation is a ballpark approximation.
        requires_epoch: Whether the applied operation reads the coordinate epoch.
        steps: Names of the individual steps, for a concatenated operation.
    """

    requested: str | None
    auth_name: str | None
    code: str | None
    name: str
    method_name: str | None
    accuracy: float | None
    route: OperationRoute
    ballpark: bool = False
    """Whether the applied operation is a ballpark approximation.

    Only ever True when the caller passed ``allow_any_operation=True``: absent
    that, a ballpark is refused outright rather than reaching a result.
    """
    requires_epoch: bool = False
    """Whether the applied operation reads the coordinate epoch.

    Meaningful for the ``allow_any_operation=True`` route, where the operation
    PROJ picks -- and so whether it is time-dependent -- is not known until a
    point has been transformed.
    """
    steps: tuple[str, ...] = ()
    projjson: str = field(default="", repr=False, compare=False)
    """PROJJSON of the operation applied, kept so it can be re-exported."""

    @property
    def authority_code(self) -> str | None:
        """``"AUTH:CODE"`` of the applied operation, or None if unidentified."""
        if self.auth_name is None or self.code is None:
            return None
        return f"{self.auth_name}:{self.code}"

    def to_wkt(self, *, pretty: bool = False) -> str | None:
        """Export the operation that was applied as WKT2.

        Rendered from what PROJ built rather than looked up by code, so it also
        works for an operation the EPSG dataset does not define, such as a
        concatenated chain collapsed into one step. The consequence is that the
        registry's descriptive metadata is not present: expect the method,
        parameters and ``ID`` of the operation, but no ``VERSION``, ``USAGE``
        or ``REMARK``. Read the parameters from here; read the scope and area
        of validity from the EPSG dataset via :attr:`authority_code`.

        Args:
            pretty: Whether to indent the output over several lines.

        Returns:
            The WKT2 of the applied operation, or None where PROJ built
            something that is not a coordinate operation in its own right.

        Example:
            >>> tfm = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1133")
            >>> tfm.operation.to_wkt()[:19]
            'COORDINATEOPERATION'
        """
        if not self.projjson:
            return None
        try:
            operation = CoordinateOperation.from_json(self.projjson)
        except CRSError:
            return None
        return str(operation.to_wkt(pretty=pretty))


@dataclass(frozen=True, slots=True)
class OperationCandidate:
    """One coordinate operation PROJ offers for a CRS pair, not yet applied.

    Listed by
    :func:`geodetic_engine.geodesy.transformation.available_operations`, this
    package's equivalent of inspecting a
    :class:`pyproj.transformer.TransformerGroup` directly. Nothing here has
    been checked against a request or applied to coordinates; it is
    information to choose an ``operation=`` argument from, not a result.

    Attributes:
        auth_name: Authority of the operation, for example ``"EPSG"``.
        code: Code of the operation.
        name: Name of the operation.
        method_name: Name of the operation method, when it is a single step.
        accuracy: Stated accuracy in metres, or None when PROJ reports none.
        area_of_use: Human-readable area the operation is valid for, or None.
        ballpark: Whether this candidate is a ballpark approximation.
        requires_epoch: Whether applying it would need a coordinate epoch.
        grids: Grid files it depends on. Not all need be installed.
        usable: Whether it could be applied right now: not a ballpark, and
            every grid it depends on is installed.
    """

    auth_name: str | None
    code: str | None
    name: str
    method_name: str | None
    accuracy: float | None
    area_of_use: str | None
    ballpark: bool
    requires_epoch: bool
    grids: tuple[GridUsage, ...]
    usable: bool

    @property
    def authority_code(self) -> str | None:
        """``"AUTH:CODE"``, usable as ``Transformation``'s ``operation=``."""
        if self.auth_name is None or self.code is None:
            return None
        return f"{self.auth_name}:{self.code}"


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """A caller's request for a particular coordinate operation.

    Either an authority code or a name, never a substring of either.

    Attributes:
        text: The reference as the caller wrote it.
        auth_name: Authority, when the reference is an authority code.
        code: Code, when the reference is an authority code.
        name: Operation name, when the reference is not an authority code.
    """

    text: str
    auth_name: str | None
    code: str | None
    name: str | None

    @classmethod
    def parse(cls, reference: str | int) -> OperationRequest:
        """Parse an operation reference.

        Args:
            reference: ``"EPSG:15670"``, a bare EPSG code such as ``15670``, an
                OGC URN such as
                ``"urn:ogc:def:coordinateOperation:EPSG::15670"``, or an
                operation name such as ``"ITRF2014 to ETRF2014 (1)"``.

        Returns:
            The parsed request.

        Example:
            >>> OperationRequest.parse("EPSG:15670").code
            '15670'
            >>> OperationRequest.parse("ITRF2014 to ETRF2014 (1)").name
            'ITRF2014 to ETRF2014 (1)'
        """
        if isinstance(reference, int):
            return cls(
                text=f"EPSG:{reference}",
                auth_name="EPSG",
                code=str(reference),
                name=None,
            )

        text = reference.strip()
        candidate = text
        if candidate.lower().startswith(_URN_PREFIX):
            candidate = candidate[len(_URN_PREFIX) :].replace("::", ":")
        if candidate.isdigit():
            return cls(text=text, auth_name="EPSG", code=candidate, name=None)
        authority, separator, code = candidate.partition(":")
        if separator and code and authority and " " not in authority:
            return cls(text=text, auth_name=authority.upper(), code=code, name=None)
        return cls(text=text, auth_name=None, code=None, name=text)

    @property
    def urn(self) -> str | None:
        """OGC URN for this operation, or None when the request is by name."""
        if self.auth_name is None or self.code is None:
            return None
        return f"urn:ogc:def:coordinateOperation:{self.auth_name}::{self.code}"

    def find_in(self, definition: dict[str, Any]) -> dict[str, Any] | None:
        """Locate this operation within a PROJJSON operation tree.

        Normalising axis order makes PROJ wrap the requested operation in a
        concatenated operation. That wrapper is often given the *same*
        identifier as the step it wraps, but PROJ also renames it, appending
        "(with axis order normalized for visualization)" to its name. Walking
        the tree in document order therefore meets the polluted wrapper name
        before the clean name on the step itself, so the last matching node
        is kept rather than the first: the wrapper can only repeat an
        identifier that a deeper, more specific node already carries.

        A step that needs to touch only part of a compound target CRS (a
        vertical shift folded into a horizontal+vertical compound, for
        example) cannot be looked up as the registered operation as-is, so
        PROJ rebuilds it as an unidentified "PROJ-based operation method" and
        drops its id. Its name survives that rebuild -- plain, or prefixed
        with "Inverse of" when applied in the reverse direction -- so a
        request by code also falls back to matching by that code's
        registered name.

        Args:
            definition: PROJJSON of the operation PROJ built.

        Returns:
            The matching operation node, or None if it is not present.
        """
        expected_name = self.name
        if (
            expected_name is None
            and self.auth_name is not None
            and self.code is not None
        ):
            expected_name = _registered_operation_name(self.auth_name, self.code)

        match: dict[str, Any] | None = None
        for node in _operation_nodes(definition):
            identifier_hit = (
                self.auth_name is not None
                and self.code is not None
                and _identifier_of(node) == (self.auth_name.upper(), str(self.code))
            )
            name_hit = expected_name is not None and _operation_name_matches(
                str(node.get("name", "")), expected_name
            )
            if identifier_hit or name_hit:
                match = node
        return match

    def is_satisfied_by(self, definition: dict[str, Any]) -> bool:
        """Whether a PROJJSON operation tree really contains this operation.

        Args:
            definition: PROJJSON of the operation PROJ built.

        Returns:
            True if the requested operation appears as the whole operation or
            as one of its steps.
        """
        return self.find_in(definition) is not None

    def __str__(self) -> str:
        return self.text


def parse_operations(
    reference: str | int | Iterable[str | int],
) -> tuple[OperationRequest, ...]:
    """Parse one operation reference, or several, into requests.

    More than one is for the case where a compound target CRS needs a
    horizontal and a vertical operation to both be pinned down: PROJ fuses
    such pairs into a single unidentified step, so naming just one would
    leave the other chosen silently.

    Several are a set, not a sequence: each request is later checked for
    independently against whatever pipeline PROJ built, so the order they
    are given in here does not affect which pipeline is accepted.

    Args:
        reference: A single operation reference, or an iterable of them (a
            plain string is never iterated as one, even though it is
            technically iterable).

    Returns:
        One parsed request per reference, in the order given.

    Example:
        >>> [r.text for r in parse_operations(["EPSG:11028", "EPSG:9484"])]
        ['EPSG:11028', 'EPSG:9484']
    """
    if isinstance(reference, (str, int)):
        return (OperationRequest.parse(reference),)
    return tuple(OperationRequest.parse(item) for item in reference)


def operation_ids(definition: object) -> set[tuple[str, str]]:
    """Collect the authority codes of every coordinate operation in a tree.

    Walks a PROJJSON operation, including the steps of a concatenated
    operation, and returns the identifier of each step. Identifiers belonging
    to nested CRS definitions are excluded, so a projected CRS's own map
    projection conversion cannot be mistaken for the operation being applied.

    Args:
        definition: PROJJSON of an operation, as a dict.

    Returns:
        Set of ``(authority, code)`` pairs, both upper-case strings.
    """
    return {
        identifier
        for node in _operation_nodes(definition)
        if (identifier := _identifier_of(node)) is not None
    }


def operation_names(definition: object) -> set[str]:
    """Collect the names of every coordinate operation in a PROJJSON tree."""
    return {name for node in _operation_nodes(definition) if (name := node.get("name"))}


def is_ballpark(definition: object) -> bool:
    """Whether any part of an operation tree is a ballpark approximation.

    PROJ names such a step "Ballpark geographic offset from X to Y". It appears
    where no datum shift is defined between two frames, and it silently assumes
    the datums coincide.

    Args:
        definition: PROJJSON of the operation PROJ built.

    Returns:
        True if a ballpark step is present anywhere in the operation.
    """
    return any(
        str(node.get("name", "")).lower().startswith("ballpark")
        for node in _operation_nodes(definition)
    )


def requires_epoch(
    definition: object, operations: Iterable[CoordinateOperation]
) -> bool:
    """Whether the transformation consumes a coordinate epoch.

    A dynamic CRS on its own does not mean the epoch enters the arithmetic. It
    does when the operation carries rates of change and a reference epoch, or
    when it is a point motion operation, and in exactly those cases omitting
    the epoch silently displaces the result. A plain Helmert between two frames
    produces the same numbers whatever epoch the coordinates were observed at,
    so demanding one there would block valid work without preventing any error.

    Args:
        definition: PROJJSON of the operation PROJ built.
        operations: The operations being applied, for their parameters.

    Returns:
        True if a coordinate epoch is needed to get the right answer.
    """
    if any(
        node.get("type") == "PointMotionOperation"
        for node in _operation_nodes(definition)
    ):
        return True
    return any(
        any(marker in parameter.name.lower() for marker in _TIME_DEPENDENT_PARAMETERS)
        for operation in operations
        for parameter in operation.params
    )


def _operation_nodes(definition: object) -> Iterator[dict[str, Any]]:
    """Yield every coordinate operation dict in a PROJJSON tree."""
    if isinstance(definition, dict):
        if definition.get("type") in _OPERATION_TYPES:
            yield definition
        for key, value in definition.items():
            if key not in _NESTED_CRS_KEYS:
                yield from _operation_nodes(value)
    elif isinstance(definition, list):
        for item in definition:
            yield from _operation_nodes(item)


def _identifier_of(node: dict[str, Any]) -> tuple[str, str] | None:
    """Read an ``(authority, code)`` pair from a PROJJSON object, if it has one."""
    identifier = node.get("id")
    if identifier is None:
        identifiers = node.get("ids")
        identifier = identifiers[0] if identifiers else None
    if not isinstance(identifier, dict):
        return None
    authority = identifier.get("authority")
    code = identifier.get("code")
    if authority is None or code is None:
        return None
    return base_authority(str(authority)).upper(), str(code)


def grid_usages(operations: Iterable[CoordinateOperation]) -> tuple[GridUsage, ...]:
    """Describe the grid files a set of operations depends on.

    Args:
        operations: The operations actually being applied.

    Returns:
        One entry per distinct grid, in the order first encountered.
    """
    seen: dict[str, GridUsage] = {}
    for operation in operations:
        for grid in operation.grids:
            if grid.short_name in seen:
                continue
            seen[grid.short_name] = GridUsage(
                name=grid.short_name,
                full_name=grid.full_name,
                package_name=grid.package_name,
                url=grid.url,
                available=bool(grid.available),
                open_license=bool(grid.open_license),
                direct_download=bool(grid.direct_download),
            )
    return tuple(seen.values())
