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

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pyproj.crs import CoordinateOperation
from pyproj.database import get_authorities
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

# Only the INVERSE wrapper changes what the operation computes, and neither
# WKT2 nor PROJJSON can express it, so an export carrying one cannot be read
# back faithfully. DERIVED_FROM is unaffected.
_INVERSE_AUTHORITY = re.compile(r"INVERSE\(", re.IGNORECASE)


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

    ``expected_name`` is normalised the same way ``node_name`` is: a request
    built directly from a candidate's own name (for example from
    :func:`~geodetic_engine.geodesy.transformation.available_operations`) can
    itself start with "Inverse of" when that candidate was found in the
    reverse direction, and that prefix must not make a fused part with the
    same base name, but not the same direction, fail to match.

    Example:
        >>> _operation_name_matches(
        ...     "Inverse of A (1) + B (2)", "B (2)"
        ... )
        True
    """
    expected = _base_operation_name(expected_name)
    if _base_operation_name(node_name).casefold() == expected.casefold():
        return True
    parts = node_name.split(_FUSED_NAME_SEPARATOR)
    return len(parts) > 1 and any(
        _base_operation_name(part).casefold() == expected.casefold() for part in parts
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
    """PROJJSON of the operation applied.

    Raw, so it is present even when :meth:`to_wkt` returns None because a step
    is applied inverted. Feeding it back to PROJ in that case silently applies
    that step forwards; prefer
    :attr:`~geodetic_engine.geodesy.result.TransformationResult.pipeline`,
    which keeps the inversion explicit.
    """

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
            The WKT2 of the applied operation, or None where it cannot be
            exported faithfully: either PROJ built something that is not a
            coordinate operation in its own right, or a step is applied
            inverted and WKT2 cannot say so (see :func:`has_inverted_step`).
            Use :attr:`TransformationResult.pipeline` in the latter case,
            which keeps the inversion explicit.

        Example:
            >>> from geodetic_engine.geodesy import Transformation
            >>> tfm = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1133")
            >>> tfm.operation.to_wkt()[:19]
            'COORDINATEOPERATION'
        """
        if not self.projjson or has_inverted_step(json.loads(self.projjson)):
            return None
        try:
            operation = CoordinateOperation.from_json(self.projjson)
        except CRSError:
            return None
        return str(operation.to_wkt(pretty=pretty))


@dataclass(frozen=True, slots=True)
class AreaOfUse:
    """The bounding box an operation or CRS is stated to be valid within.

    Mirrors :class:`pyproj.aoi.AreaOfUse`, so that the bounding box PROJ
    already computed does not have to be re-derived from the name string.

    Attributes:
        west: Western bound, in decimal degrees of longitude.
        south: Southern bound, in decimal degrees of latitude.
        east: Eastern bound, in decimal degrees of longitude.
        north: Northern bound, in decimal degrees of latitude.
        name: Human-readable description of the area, for example
            ``"Norway - onshore."``.
    """

    west: float
    south: float
    east: float
    north: float
    name: str

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """``(west, south, east, north)``, as accepted by most GIS tooling."""
        return (self.west, self.south, self.east, self.north)

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class OperationStep:
    """One substantive coordinate operation within a candidate.

    A step is a datum transformation in its own right, not the axis-order
    bookkeeping PROJ inserts around them. A candidate has more than one
    whenever PROJ had to chain operations to span the pair, which a bound CRS
    on either side reliably causes: the bound CRS contributes its own datum
    transformation to the hub, and a second one carries on from there.

    Attributes:
        auth_name: Authority of this step, for example ``"EPSG"``. PROJ's
            ``INVERSE(...)`` wrapper is stripped: a step applied in reverse is
            still the same registered operation.
        code: Code of this step within that authority.
        name: Name of this step, as PROJ reports it, including any
            ``"Inverse of"`` prefix.
        method_name: Name of the operation method this step applies.
    """

    auth_name: str | None
    code: str | None
    name: str
    method_name: str | None

    @property
    def authority_code(self) -> str | None:
        """``"AUTH:CODE"`` of this step, or None if PROJ gave it no id."""
        if self.auth_name is None or self.code is None:
            return None
        return f"{self.auth_name}:{self.code}"

    @property
    def reference(self) -> str:
        """This step as an operation reference: its code, or else its name."""
        return self.authority_code or _base_operation_name(self.name)

    def __str__(self) -> str:
        return f"{self.name} ({self.authority_code or 'unidentified'})"


@dataclass(frozen=True, slots=True)
class OperationCandidate:
    """One coordinate operation PROJ offers for a CRS pair, not yet applied.

    Listed by
    :func:`geodetic_engine.geodesy.transformation.available_operations`, this
    package's equivalent of inspecting a
    :class:`pyproj.transformer.TransformerGroup` directly. Nothing here has
    been checked against a request or applied to coordinates; it is
    information to choose an ``operation=`` argument from, not a result.

    A candidate is not always a single registered operation. A registered
    concatenated operation such as ``EPSG:8047`` applies two Helmerts and
    still has its own code, because the authority publishes the chain itself.
    But when PROJ has to assemble a chain of its own to span the pair -- which
    a bound CRS on either side reliably causes -- no authority code names the
    result, so :attr:`auth_name`, :attr:`code` and :attr:`method_name` are all
    None. Either way :attr:`steps` lists the operations actually applied, and
    passing the candidate as ``operation=`` pins down every one of them; see
    :attr:`references`.

    Attributes:
        auth_name: Authority of the operation, for example ``"EPSG"``, or None
            when no authority publishes this chain as one operation.
        code: Code of the operation, or None in that same case.
        name: Name of the operation. For an unregistered chain, the step names
            joined with ``" + "``.
        method_name: Name of the operation method, or None when the candidate
            applies more than one operation.
        accuracy: Stated accuracy in metres, or None when PROJ reports none.
        area_of_use: The area the operation is valid for, with its bounding
            box, or None.
        ballpark: Whether this candidate is a ballpark approximation.
        requires_epoch: Whether applying it would need a coordinate epoch.
        grids: Grid files it depends on. Not all need be installed.
        usable: Whether it could be applied right now: not a ballpark, and
            every grid it depends on is installed.
        steps: The substantive operations this candidate applies, in pipeline
            order, excluding the axis-order bookkeeping PROJ inserts around
            them. Always at least one.
        projjson: PROJJSON of the pipeline PROJ would build. Read it through
            :meth:`to_json_dict` or :meth:`to_wkt` rather than directly.
    """

    auth_name: str | None
    code: str | None
    name: str
    method_name: str | None
    accuracy: float | None
    area_of_use: AreaOfUse | None
    ballpark: bool
    requires_epoch: bool
    grids: tuple[GridUsage, ...]
    usable: bool
    steps: tuple[OperationStep, ...] = ()
    projjson: str = field(default="", repr=False, compare=False)
    """PROJJSON of the pipeline PROJ would build.

    Raw, so it is present even when :meth:`to_json_dict` returns None because a
    step is applied inverted. Feeding it back to PROJ in that case silently
    applies that step forwards; prefer the transformation's PROJ pipeline
    string, which keeps the inversion explicit.
    """

    @property
    def authority_code(self) -> str | None:
        """``"AUTH:CODE"`` of the operation as a whole, or None if it has none.

        A registered concatenated operation such as ``EPSG:8047`` has one even
        though it applies two Helmerts, because the authority publishes the
        chain itself under that code. A chain PROJ assembled on its own, which
        a bound CRS on either side of the pair causes, has none: reporting the
        first step's code for it would understate what is being applied by a
        whole datum shift. Use :attr:`references` in that case.
        """
        if self.auth_name is None or self.code is None:
            return None
        return f"{self.auth_name}:{self.code}"

    @property
    def is_chained(self) -> bool:
        """Whether this candidate applies more than one substantive operation.

        True for a registered concatenated operation as well as for an
        unregistered chain, so it does not by itself say whether the candidate
        has an :attr:`authority_code`.
        """
        return len(self.steps) > 1

    @property
    def references(self) -> tuple[str, ...]:
        """This candidate as ``operation=`` references.

        A single entry -- the :attr:`authority_code`, or the :attr:`name` if
        there is none -- whenever one reference names the whole candidate.
        Otherwise one entry per step, since an unregistered chain can only be
        pinned down by naming every operation in it. Passing the candidate
        object as ``operation=`` expands to exactly this.

        Example:
            >>> candidate = OperationCandidate(
            ...     auth_name="EPSG", code="1133", name="ED50 to WGS 84 (1)",
            ...     method_name=None, accuracy=10.0, area_of_use=None,
            ...     ballpark=False, requires_epoch=False, grids=(), usable=True,
            ...     steps=(OperationStep("EPSG", "1133", "ED50 to WGS 84 (1)", None),),
            ... )
            >>> candidate.references
            ('EPSG:1133',)
        """
        if (code := self.authority_code) is not None:
            return (code,)
        if self.steps:
            return tuple(step.reference for step in self.steps)
        return (self.name,)

    def to_json_dict(self) -> dict[str, Any] | None:
        """Export the pipeline PROJ would build for this candidate as PROJJSON.

        This is the whole pipeline as PROJ assembled it, not the registry's
        entry for :attr:`authority_code`: it includes the source and target
        CRS, every step, and the axis-order bookkeeping PROJ inserts to meet
        this package's ``xy`` value order. That last part is why the name
        inside carries PROJ's "(with axis order normalized for visualization)"
        annotation while :attr:`name` does not.

        Returns:
            The PROJJSON as a dict, or None where PROJ gave the candidate none
            or the pipeline cannot be exported faithfully because a step is
            applied inverted (see :func:`has_inverted_step`). The raw text is
            still on :attr:`projjson` for anyone who needs to inspect it
            knowing that caveat.

        Example:
            >>> from geodetic_engine.geodesy import available_operations
            >>> candidate = available_operations("EPSG:4230", "EPSG:4326")[0]
            >>> candidate.to_json_dict()["type"]
            'ConcatenatedOperation'
        """
        if not self.projjson:
            return None
        definition: dict[str, Any] = json.loads(self.projjson)
        return None if has_inverted_step(definition) else definition

    def to_wkt(self, *, pretty: bool = False) -> str | None:
        """Export the pipeline PROJ would build for this candidate as WKT2.

        Rendered from what PROJ assembled rather than looked up by code, so it
        also works for a candidate the EPSG dataset does not define, such as a
        chain across two bound CRSs. The consequence is that the registry's
        descriptive metadata is not present: expect the method, parameters and
        ``ID``, but no ``VERSION``, ``USAGE`` or ``REMARK``. Read the
        parameters from here; read the scope and area of validity from the
        EPSG dataset via :attr:`authority_code`, or from :attr:`area_of_use`.

        Args:
            pretty: Whether to indent the output over several lines.

        Returns:
            The WKT2 of the candidate, or None where it cannot be exported
            faithfully: either PROJ built something that is not a coordinate
            operation in its own right, or a step is applied inverted and
            WKT2 cannot say so (see :func:`has_inverted_step`). Transform a
            point and read
            :attr:`~geodetic_engine.geodesy.result.TransformationResult.pipeline`
            in the latter case, which keeps the inversion explicit.

        Example:
            >>> from geodetic_engine.geodesy import available_operations
            >>> candidate = available_operations("EPSG:4230", "EPSG:4326")[0]
            >>> candidate.to_wkt()[:21]
            'CONCATENATEDOPERATION'
        """
        if not self.projjson or has_inverted_step(json.loads(self.projjson)):
            return None
        try:
            operation = CoordinateOperation.from_json(self.projjson)
        except CRSError:
            return None
        return str(operation.to_wkt(pretty=pretty))


# A candidate from available_operations(), usable as an operation reference in
# its own right -- the only way to name a candidate PROJ built with no EPSG id
# of its own. Kept distinct from the plain str | int forms so a signature such
# as str | int | OperationReference reads as three genuinely different things
# to pass, not one alias quietly standing in for all of them.
type OperationReference = OperationCandidate


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
    def parse(cls, reference: str | int | OperationReference) -> OperationRequest:
        """Parse an operation reference.

        Args:
            reference: ``"EPSG:15670"``, a bare EPSG code such as ``15670``, an
                OGC URN such as
                ``"urn:ogc:def:coordinateOperation:EPSG::15670"``, an
                operation name such as ``"ITRF2014 to ETRF2014 (1)"``, or an
                :class:`OperationCandidate` from
                :func:`~geodetic_engine.geodesy.transformation.available_operations`
                (its :attr:`~OperationCandidate.authority_code` is used when it
                has one, its name otherwise -- the latter is the only way to
                pin down a candidate PROJ built with no EPSG id of its own).

        Returns:
            The parsed request.

        Raises:
            ValueError: If given an :class:`OperationCandidate` that no single
                reference can name, because PROJ assembled it from operations
                no authority publishes as one. Use :func:`parse_operations`,
                which expands it.

        Example:
            >>> OperationRequest.parse("EPSG:15670").code
            '15670'
            >>> OperationRequest.parse("ITRF2014 to ETRF2014 (1)").name
            'ITRF2014 to ETRF2014 (1)'
        """
        if isinstance(reference, OperationCandidate):
            references = reference.references
            if len(references) > 1:
                raise ValueError(
                    f"{reference.name!r} applies {len(references)} operations "
                    "that no single code names, so it cannot be parsed as one "
                    "request; pass it to parse_operations() instead"
                )
            reference = references[0]
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
            return cls(
                text=text,
                auth_name=_canonical_authority(authority),
                code=code,
                name=None,
            )
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
    reference: (
        str | int | OperationReference | Iterable[str | int | OperationReference]
    ),
) -> tuple[OperationRequest, ...]:
    """Parse one operation reference, or several, into requests.

    More than one is for the case where a single request cannot name the whole
    operation: a compound target CRS needing a horizontal and a vertical
    operation both pinned down, or a candidate that chains two datum
    transformations to span the pair. PROJ fuses such pipelines into one
    unidentified step, so naming just one part would leave the rest chosen
    silently.

    Several are a set, not a sequence: each request is later checked for
    independently against whatever pipeline PROJ built, so the order they
    are given in here does not affect which pipeline is accepted.

    Args:
        reference: A single operation reference, or an iterable of them (a
            plain string is never iterated as one, even though it is
            technically iterable). A reference may be an
            :class:`OperationCandidate`, which expands to one request per
            step it chains.

    Returns:
        One parsed request per reference, in the order given, with a chained
        candidate expanded into one request per step.

    Example:
        >>> [r.text for r in parse_operations(["EPSG:11028", "EPSG:9484"])]
        ['EPSG:11028', 'EPSG:9484']
    """
    if isinstance(reference, (str, int, OperationCandidate)):
        references: Iterable[str | int | OperationReference] = (reference,)
    else:
        references = reference
    return tuple(
        OperationRequest.parse(text)
        for item in references
        for text in (
            item.references if isinstance(item, OperationCandidate) else (item,)
        )
    )


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


def datum_operation_count(definition: dict[str, Any]) -> int:
    """How many datum transformations an operation tree applies.

    Counts only steps that move between reference frames, so a map projection
    or an axis-order reversal -- both conversions, and both part of expressing
    a CRS rather than changing its datum -- are excluded. The tree's own root
    is excluded too, since a concatenated wrapper is not itself a step.

    Args:
        definition: PROJJSON of the operation PROJ built.

    Returns:
        The number of datum-changing steps, which is more than one exactly
        when no single published operation spans the pair.

    Example:
        >>> datum_operation_count(
        ...     {"type": "ConcatenatedOperation",
        ...      "steps": [{"type": "Transformation"}, {"type": "Conversion"}]}
        ... )
        1
    """
    return sum(
        1
        for node in _operation_nodes(definition)
        if node is not definition
        and node.get("type") in ("Transformation", "PointMotionOperation")
    )


def has_inverted_step(definition: object) -> bool:
    """Whether a datum transformation in the tree is one PROJ applies inverted.

    PROJ marks such a step by wrapping its authority as ``INVERSE(EPSG)``,
    which is a PROJ-internal convention rather than anything WKT2 or PROJJSON
    define: neither format has a flag for "apply this operation backwards".
    Re-reading an export that contains one yields the *forward* operation
    instead, silently reversing the sign of a datum shift, so such an export
    must not be handed out.

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
        for node in _operation_nodes(definition)
        if node.get("type") in ("Transformation", "PointMotionOperation")
        for identifier in (node.get("id"), (node.get("method") or {}).get("id"))
        if isinstance(identifier, dict)
    )


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


@lru_cache(maxsize=32)
def _canonical_authority(authority: str) -> str:
    """Spell an authority the way the PROJ database does.

    PROJ matches authority names case-sensitively, so a request written as
    ``"EQUINOR:3000034"`` or ``"equinor:3000034"`` has to be resolved against
    the spelling ``proj.db`` actually stores (``"Equinor"``) before it can be
    looked up or turned into a URN. Uppercasing instead would leave every
    authority whose registered name is not uppercase unreachable.

    Args:
        authority: Authority as the caller wrote it.

    Returns:
        The registered spelling, or the authority unchanged when PROJ knows
        no authority by that name.
    """
    folded = authority.casefold()
    for registered in get_authorities():
        if registered.casefold() == folded:
            return registered
    return authority


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
