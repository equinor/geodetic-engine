"""The three things a persistableReference can state, as PROJ objects.

A payload states a coordinate reference system, a coordinate transformation, or
a unit. Each is read here into a frozen record that keeps what the payload said
-- including the authority code it stamped on itself -- and can hand back the
pyproj object it describes.

The authority code is kept and never followed. A payload is self-contained by
design: it carries the full definition precisely so that the receiving system
does not have to agree with the sender about what a code means. Resolving
``EPSG:1612`` from the register instead of reading the parameters beside it
would quietly substitute a different transformation whenever the two disagree,
and the codes in these payloads are not always real. So the parameters are
built from, and the code is provenance the caller can read off
:attr:`Reference.authority_code`.

Nothing here reaches for ``geodetic_engine.geodesy``. These are pyproj objects,
and it is :class:`~geodetic_engine.geodesy.crs.CoordinateReferenceSystem` that
wraps them.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite
from typing import Any

from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy.utils import (
    collapse_concatenated,
    scale_in_parts_per_million,
)
from geodetic_engine.persistablereference import esriwkt, methods
from geodetic_engine.persistablereference.envelope import (
    AuthorityCode,
    Envelope,
    JsonObject,
    Kind,
    decode,
    field,
    object_field,
    text_field,
)
from geodetic_engine.persistablereference.errors import (
    MalformedReferenceError,
    PersistableReferenceError,
    UnsupportedReferenceError,
)
from geodetic_engine.persistablereference.esriwkt import Node

_TRANSFORMATION_KEYWORD = "GEOGTRAN"
_VERTICAL_KEYWORD = "VERTTRAN"


@dataclass(frozen=True, slots=True)
class Reference:
    """What every persistableReference states about itself.

    Attributes:
        raw: The payload exactly as it arrived.
        kind: Which of the six kinds it states.
        name: Name the producer gave the definition, or ``""``.
        authority_code: Authority code the producer stamped on it, if any.
            Provenance only; nothing here is looked up by it.
        version: Producer version string, for example ``"PE_10_9_1"``.
    """

    raw: str
    kind: Kind
    name: str
    authority_code: AuthorityCode | None
    version: str


@dataclass(frozen=True, slots=True)
class OperationReference(Reference):
    """A coordinate transformation, single or concatenated.

    Attributes:
        steps: One ``GEOGTRAN`` per step, in the order they are applied. A
            single transformation has exactly one.
    """

    steps: tuple[Node, ...]

    @property
    def is_concatenated(self) -> bool:
        """Whether the transformation is applied through intermediate frames."""
        return len(self.steps) > 1

    @property
    def method_names(self) -> tuple[str, ...]:
        """The ESRI method name of each step, in order."""
        return tuple(
            node.name if (node := step.node("METHOD")) else "" for step in self.steps
        )

    def to_operation(self) -> CoordinateOperation:
        """Build the coordinate operation this reference states.

        Returns:
            A single step transformation, or a concatenated operation when the
            reference states more than one step.

        Raises:
            MalformedReferenceError: If a step is not a readable ``GEOGTRAN``.
            UnsupportedMethodError: If a step states a method or parameter this
                package will not translate.
            UnresolvableGridError: If a grid-based step names a dataset that
                resolves to no single grid in PROJ's database.
        """
        built = [_transformation(step) for step in self.steps]
        definition = built[0] if len(built) == 1 else _concatenation(built, self.name)
        return _operation_from(definition, self.name)


@dataclass(frozen=True, slots=True)
class CrsReference(Reference):
    """A coordinate reference system, on its own or bound to a hub.

    Attributes:
        wkt: The ESRI WKT of the CRS itself. For a bound reference this is the
            nested late bound CRS's WKT when supplied, regardless of any
            top-level WKT.
        late_bound: The CRS being bound, when this reference binds one.
        operation: The transformation it is bound by, when it binds one.
    """

    wkt: str
    late_bound: CrsReference | None
    operation: OperationReference | None

    @property
    def is_bound(self) -> bool:
        """Whether the reference carries the transformation to its hub."""
        return self.operation is not None

    def to_crs(self) -> CRS:
        """Build the CRS this reference states.

        Returns:
            The CRS, as a :class:`~pyproj.crs.BoundCRS` when the reference
            binds a transformation to it.

        Raises:
            MalformedReferenceError: If the reference states no WKT, or PROJ
                will not read it, or the bound operation's source CRS does not
                match the base geodetic CRS.
            UnsupportedMethodError: If the bound transformation states a method
                this package will not translate.
            UnembeddableOperationError: If the bound transformation cannot be
                stated as the single step a bound CRS carries.
        """
        base = _crs(self.wkt, self.name)
        if self.operation is None:
            return base
        return _bound(base, self.operation, self.name)

    def to_bound_crs(self, operation: OperationReference) -> CRS:
        """Build this CRS bound to a transformation stated by a separate payload.

        An OSDU catalogue publishes a CRS and a coordinate transformation as
        records of their own, and a record that uses them names both. Binding
        them here gives the same thing an early bound payload states in one
        piece: a CRS that carries its own datum shift, leaving nothing for a
        caller to disambiguate later.

        Args:
            operation: The transformation to bind, as
                :func:`parse_persistable_reference` returns for an ``ST`` or
                ``CT`` payload. A parsed reference is the only
                thing accepted. An authority code, a CRS name or PROJJSON would
                each have to be resolved against a register, and this package
                reads the parameters a payload carries rather than looking up
                what a code is meant to stand for.

        Returns:
            The bound CRS.

        Raises:
            ValueError: If this reference already states a transformation of
                its own. Binding a second would discard the first in silence.
            MalformedReferenceError: If the operation's source CRS does not
                match this CRS's geodetic base (ignoring axis order), or PROJ
                will not assemble the two. Reversed operations must be
                explicitly inverted before binding.
            UnsupportedMethodError: If the transformation states a method this
                package will not translate.
            UnembeddableOperationError: If it cannot be stated as the single
                step a bound CRS carries.

        Example:
            >>> crs = parse_persistable_reference(crs_payload)  # doctest: +SKIP
            >>> shift = parse_persistable_reference(ct_payload)  # doctest: +SKIP
            >>> crs.to_bound_crs(shift).is_bound  # doctest: +SKIP
            True
        """
        if self.operation is not None:
            raise ValueError(
                f"{_described(self.name)} already states the transformation "
                f"{self.operation.name or '<unnamed>'!r}, and binding "
                f"{operation.name or '<unnamed>'!r} would discard it without "
                f"saying so. Use to_crs() to keep the stated one, or call this "
                f"on the lateBoundCRS to choose deliberately"
            )
        return _bound(_crs(self.wkt, self.name), operation, self.name)


@dataclass(frozen=True, slots=True)
class UnitReference(Reference):
    """A unit, stated by how it converts to its SI base.

    ``USO`` states a scale and an offset, ``UAD`` a rational polynomial. The
    polynomial reduces to a scale and an offset whenever its ``d`` term is
    zero, which is the usual case.

    Attributes:
        symbol: The unit's symbol, for example ``"ft"``.
        measurement: What it measures, for example ``"length"``, or ``""``.
        coefficients: The polynomial ``(a, b, c, d)`` in
            ``si = (a + b * value) / (c + d * value)``. A scale and offset is
            held as ``(offset, scale, 1, 0)``, which is the same conversion.
    """

    symbol: str
    measurement: str
    coefficients: tuple[float, float, float, float]

    @property
    def scale(self) -> float:
        """The conversion's scale factor.

        Raises:
            UnsupportedReferenceError: If the conversion is not linear, and so
                has no single scale.
        """
        _, b, c, d = self.coefficients
        if d != 0.0 or c == 0.0:
            raise UnsupportedReferenceError(
                f"{_described(self.name)} converts by a rational polynomial, "
                f"which has no single scale factor"
            )
        return b / c

    @property
    def offset(self) -> float:
        """The conversion's offset, in the SI base unit.

        Raises:
            UnsupportedReferenceError: If the conversion is not linear.
        """
        a, _, c, d = self.coefficients
        if d != 0.0 or c == 0.0:
            raise UnsupportedReferenceError(
                f"{_described(self.name)} converts by a rational polynomial, "
                f"which has no single offset"
            )
        return a / c

    def to_si(self, value: float) -> float:
        """Convert a value in this unit to the SI base unit.

        Args:
            value: The value to convert.

        Returns:
            The value in the SI base unit.

        Raises:
            MalformedReferenceError: If the conversion is undefined at this
                value.
        """
        a, b, c, d = self.coefficients
        if (denominator := c + d * value) == 0.0:
            raise MalformedReferenceError(
                f"{_described(self.name)} states a conversion that is undefined "
                f"at {value}"
            )
        return (a + b * value) / denominator

    def from_si(self, value: float) -> float:
        """Convert a value in the SI base unit to this unit.

        Args:
            value: The value in the SI base unit.

        Returns:
            The value in this unit.

        Raises:
            MalformedReferenceError: If the conversion cannot be inverted at
                this value.
        """
        a, b, c, d = self.coefficients
        if (denominator := b - d * value) == 0.0:
            raise MalformedReferenceError(
                f"{_described(self.name)} states a conversion that cannot be "
                f"inverted at {value}"
            )
        return (c * value - a) / denominator

    def convert_to(self, other: UnitReference, value: float) -> float:
        """Convert a value from this unit to another.

        Args:
            other: The unit to convert to, which must measure the same thing.
            value: The value in this unit.

        Returns:
            The value in ``other``.

        Raises:
            MalformedReferenceError: If either conversion is undefined at this
                value.
            UnsupportedReferenceError: If the two units measure different
                things, where a conversion would be meaningless.
        """
        if (
            self.measurement
            and other.measurement
            and self.measurement != other.measurement
        ):
            raise UnsupportedReferenceError(
                f"{self.symbol or self.name} measures {self.measurement} and "
                f"{other.symbol or other.name} measures {other.measurement}; "
                f"there is no conversion between them"
            )
        return other.from_si(self.to_si(value))


type AnyReference = CrsReference | OperationReference | UnitReference


def parse_persistable_reference(raw: str) -> AnyReference:
    """Read a persistableReference payload.

    Args:
        raw: The payload, plain or URL-encoded JSON.

    Returns:
        The reference it states.

    Raises:
        MalformedReferenceError: If the payload is not a readable
            persistableReference.
        UnsupportedReferenceError: If it states a kind this package does not
            model.

    Example:
        >>> payload = '{"type":"LBC","wkt":"GEOGCS[...]"}'
        >>> parse_persistable_reference(payload).to_crs().name  # doctest: +SKIP
        'WGS 84'
    """
    envelope = decode(raw)
    match envelope.kind:
        case Kind.LATE_BOUND_CRS | Kind.EARLY_BOUND_CRS:
            return _crs_reference(envelope)
        case Kind.TRANSFORMATION | Kind.CONCATENATED_TRANSFORMATION:
            return _operation_reference(envelope)
        case Kind.UNIT_SCALE_OFFSET | Kind.UNIT_ABCD:
            return _unit_reference(envelope)


def _crs_reference(envelope: Envelope) -> CrsReference:
    """Read an ``LBC`` or ``EBC`` payload."""
    if envelope.kind is Kind.LATE_BOUND_CRS and any(
        field(envelope.data, key) is not None
        for key in ("lateBoundCRS", "singleCT", "compoundCT")
    ):
        raise MalformedReferenceError(
            f"{_described(envelope.name)} is a late bound CRS but carries "
            "early-bound members"
        )
    late_bound: CrsReference | None = None
    operation: OperationReference | None = None
    wkt = text_field(envelope.data, "wkt")

    if (nested := object_field(envelope.data, "lateBoundCRS")) is not None:
        late_bound = _crs_reference(_nested(envelope, nested, Kind.LATE_BOUND_CRS))
        wkt = late_bound.wkt
    for key, kind in (
        ("singleCT", Kind.TRANSFORMATION),
        ("compoundCT", Kind.CONCATENATED_TRANSFORMATION),
    ):
        if (stated := object_field(envelope.data, key)) is not None:
            if operation is not None:
                raise MalformedReferenceError(
                    f"{_described(envelope.name)} states more than one "
                    f"transformation to bind to"
                )
            operation = _operation_reference(_nested(envelope, stated, kind))

    if envelope.kind is Kind.EARLY_BOUND_CRS and operation is None:
        raise MalformedReferenceError(
            f"{_described(envelope.name)} is an early bound CRS that states no "
            f"transformation to bind"
        )
    if not wkt:
        raise MalformedReferenceError(
            f"{_described(envelope.name)} states no WKT; a persistableReference "
            f"carries its own definition and this one carries none"
        )
    return CrsReference(
        raw=envelope.raw,
        kind=envelope.kind,
        name=envelope.name,
        authority_code=envelope.authority_code,
        version=envelope.version,
        wkt=wkt,
        late_bound=late_bound,
        operation=operation,
    )


def _operation_reference(envelope: Envelope) -> OperationReference:
    """Read an ``ST`` or ``CT`` payload."""
    if envelope.kind is Kind.CONCATENATED_TRANSFORMATION:
        stated = field(envelope.data, "cts")
        if not isinstance(stated, list) or not stated:
            raise MalformedReferenceError(
                f"{_described(envelope.name)} is a concatenated transformation "
                f"that states no steps"
            )
        steps = tuple(
            _step(step, envelope.name)
            for step in stated
            if isinstance(step, dict | str)
        )
        if len(steps) != len(stated):
            raise MalformedReferenceError(
                f"{_described(envelope.name)} states a step that is not an object"
            )
    else:
        steps = (_step(envelope.data, envelope.name),)
    return OperationReference(
        raw=envelope.raw,
        kind=envelope.kind,
        name=envelope.name,
        authority_code=envelope.authority_code,
        version=envelope.version,
        steps=steps,
    )


def _unit_reference(envelope: Envelope) -> UnitReference:
    """Read a ``USO`` or ``UAD`` payload."""
    member = "scaleOffset" if envelope.kind is Kind.UNIT_SCALE_OFFSET else "abcd"
    conversions = [
        key.casefold()
        for key in envelope.data
        if key.casefold() in {"scaleoffset", "abcd"}
    ]
    if conversions != [member.casefold()]:
        raise MalformedReferenceError(
            f"{_described(envelope.name)} states unit type {envelope.kind.value}, "
            f"which requires exactly one {member} conversion member and no "
            "other conversion member"
        )
    stated = object_field(envelope.data, member)
    if stated is None:
        raise MalformedReferenceError(
            f"{_described(envelope.name)} states {member} as something other "
            "than a conversion object"
        )
    if envelope.kind is Kind.UNIT_SCALE_OFFSET:
        scale = _number(stated, envelope.name, "scale")
        offset = _number(stated, envelope.name, "offset")
        coefficients = (offset, scale, 1.0, 0.0)
    else:
        coefficients = (
            _number(stated, envelope.name, "a"),
            _number(stated, envelope.name, "b"),
            _number(stated, envelope.name, "c"),
            _number(stated, envelope.name, "d"),
        )
    base = object_field(envelope.data, "baseMeasurement") or {}
    return UnitReference(
        raw=envelope.raw,
        kind=envelope.kind,
        name=envelope.name,
        authority_code=envelope.authority_code,
        version=envelope.version,
        symbol=text_field(envelope.data, "symbol"),
        measurement=text_field(base, "ancestry").casefold(),
        coefficients=coefficients,
    )


def _nested(envelope: Envelope, data: JsonObject, default: Kind) -> Envelope:
    """Wrap a nested member as an envelope of its own."""
    from geodetic_engine.persistablereference.envelope import authority_code, kind_of

    try:
        kind = kind_of(data)
    except UnsupportedReferenceError:
        raise
    except PersistableReferenceError:
        kind = default
    if kind is not default:
        raise MalformedReferenceError(
            f"{_described(envelope.name)} states nested type {kind.value!r}, "
            f"where {default.value!r} is required"
        )
    return Envelope(
        raw=envelope.raw,
        data=data,
        kind=kind,
        name=text_field(data, "name"),
        authority_code=authority_code(field(data, "authCode")),
        version=text_field(data, "ver", "version"),
    )


def _step(stated: JsonObject | str, described: str) -> Node:
    """Read one transformation step's ESRI WKT."""
    if isinstance(stated, str):
        raise MalformedReferenceError(
            f"{_described(described)} states a transformation step as text "
            f"rather than an object"
        )
    wkt = text_field(stated, "wkt")
    if not wkt:
        raise MalformedReferenceError(
            f"{_described(described)} states a transformation step with no WKT"
        )
    return _transformation_node(wkt, described)


def _transformation_node(wkt: str, described: str) -> Node:
    """Read ESRI WKT as a ``GEOGTRAN``, refusing anything else."""
    node = esriwkt.read(wkt)
    if node.keyword.casefold() == _VERTICAL_KEYWORD.casefold():
        raise UnsupportedReferenceError(
            f"{_described(described)} states a vertical transformation, which "
            f"this package does not model"
        )
    if node.keyword.casefold() != _TRANSFORMATION_KEYWORD.casefold():
        raise MalformedReferenceError(
            f"{_described(described)} states a transformation as {node.keyword}, "
            f"not {_TRANSFORMATION_KEYWORD}"
        )
    return node


def _number(data: JsonObject, described: str, *names: str) -> float:
    """Read a numeric member."""
    value = field(data, *names)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MalformedReferenceError(
            f"{_described(described)} states {names[0]} as "
            f"{type(value).__name__}, not a number"
        )
    try:
        converted = float(value)
    except OverflowError:
        converted = float("inf")
    if not isfinite(converted):
        raise MalformedReferenceError(
            f"{_described(described)} states {names[0]} as a nonfinite number"
        )
    return converted


def _crs(wkt: str, described: str) -> CRS:
    """Read ESRI WKT as a CRS, which PROJ parses directly."""
    try:
        return CRS.from_wkt(wkt)
    except CRSError as error:
        raise MalformedReferenceError(
            f"{_described(described)} states WKT PROJ will not read: {error}"
        ) from error


def _bound(base: CRS, operation: OperationReference, described: str) -> CRS:
    """Package a CRS with the transformation that ties it to its hub."""
    built = operation.to_operation()
    source = built.to_json_dict().get("source_crs")
    geographic_base = base.geodetic_crs
    if (
        not isinstance(source, dict)
        or geographic_base is None
        or not geographic_base.equals(
            CRS.from_json_dict(source), ignore_axis_order=True
        )
    ):
        raise MalformedReferenceError(
            f"{_described(described)} cannot bind {operation.name!r}: the "
            "operation's source CRS does not match the base geodetic CRS"
        )
    if operation.is_concatenated:
        # A bound CRS carries one transformation, so a chain has to become one
        # equivalent step or be refused. collapse_concatenated proves the
        # rewrite against PROJ's own rendering of the chain.
        built = collapse_concatenated(built)
    try:
        return BoundCRS(
            base, _target_of(built, described), scale_in_parts_per_million(built)
        )
    except CRSError as error:
        raise MalformedReferenceError(
            f"{_described(described)} does not assemble into a bound CRS: {error}"
        ) from error


def _frame(node: Node, described: str) -> JsonObject:
    """State one of the two frames a ``GEOGTRAN`` goes between.

    ESRI writes no ``AUTHORITY`` and no ``AXIS`` inside a ``GEOGTRAN``, while
    the late bound CRS beside it does carry an authority code and so comes back
    from PROJ with the register's axis order. The two are then one datum
    described two ways, which PROJ reconciles by inserting a null offset
    between them: an operation the bound CRS never declared, and one this
    package refuses to apply unasked, leaving the datum change looking
    ambiguous when the payload had in fact settled it.

    So a frame PROJ identifies in a register is taken from that register. Only
    an exact identification counts -- at that confidence the definitions are
    equivalent and the only thing gained is the axis order ESRI does not state.
    A fuzzy match could name a frame that differs, which is the substitution
    this module exists to avoid.
    """
    crs = _crs(esriwkt.write(node), described)
    if found := crs.to_authority(min_confidence=100):
        try:
            return dict(CRS.from_authority(*found).to_json_dict())
        except CRSError:
            pass
    definition: JsonObject = crs.to_json_dict()
    return definition


def _transformation(node: Node) -> JsonObject:
    """State one ``GEOGTRAN`` as a PROJJSON transformation."""
    described = node.name or _TRANSFORMATION_KEYWORD
    frames = node.nodes("GEOGCS")
    if len(frames) != 2:
        raise MalformedReferenceError(
            f"{_described(described)} states {len(frames)} geographic CRSs; a "
            f"{_TRANSFORMATION_KEYWORD} states the two it goes between"
        )
    if (stated := node.node("METHOD")) is None or not stated.name:
        raise MalformedReferenceError(f"{_described(described)} states no method")

    definition: JsonObject = {
        "type": "Transformation",
        "name": described,
        "source_crs": _frame(frames[0], described),
        "target_crs": _frame(frames[1], described),
    }
    if methods.is_grid_method(stated.name):
        definition |= _grid_method(node, stated.name, described)
    else:
        definition |= _parameter_method(node, stated.name, described)
    if accuracies := node.nodes("OPERATIONACCURACY"):
        values = accuracies[0].children
        if (
            len(accuracies) > 1
            or len(values) != 1
            or not isinstance(values[0], int | float)
            or values[0] < 0
        ):
            raise MalformedReferenceError(
                f"{_described(described)} states OPERATIONACCURACY other than "
                "once, as one non-negative number of metres"
            )
        definition["accuracy"] = str(float(values[0]))
    return definition


def _parameter_method(node: Node, name: str, described: str) -> JsonObject:
    """State a parameter-based method and its parameters."""
    found = methods.method(name)
    stated: dict[str, float] = {}
    for parameter in node.nodes("PARAMETER"):
        if parameter.name.casefold() in stated:
            raise MalformedReferenceError(
                f"{_described(described)} states duplicate parameter {parameter.name!r}"
            )
        if (
            not parameter.name
            or len(parameter.children) != 2
            or not isinstance(parameter.children[1], int | float)
        ):
            raise MalformedReferenceError(
                f"{_described(described)} states parameter {parameter.name!r} "
                "without exactly a quoted name and one numeric value"
            )
        stated[parameter.name.casefold()] = float(parameter.children[1])

    expected = {esri.casefold() for esri in found.parameters}
    if not stated:
        raise UnsupportedReferenceError(
            f"{_described(described)} states method {name} with no parameters "
            f"at all; {found.name} takes {sorted(found.parameters)}, and "
            f"recovering them from the CRSs either side would be inference "
            f"rather than reading"
        )
    if set(stated) != expected:
        raise MalformedReferenceError(
            f"{_described(described)} states method {name} with parameters "
            f"{sorted(stated)}, but {found.name} takes "
            f"{sorted(found.parameters)}"
        )
    return {
        "method": {
            "name": found.name,
            "id": {"authority": "EPSG", "code": found.code},
        },
        "parameters": [
            _parameter(methods.parameter(esri), stated[esri.casefold()])
            for esri in found.parameters
        ],
    }


def _grid_method(node: Node, name: str, described: str) -> JsonObject:
    """State a grid-based method, resolved against PROJ's own database."""
    datasets = [
        parameter.name
        for parameter in node.nodes("PARAMETER")
        if parameter.name.casefold().startswith(
            methods.GRID_PARAMETER_PREFIX.casefold()
        )
    ]
    if len(datasets) != 1 or len(node.nodes("PARAMETER")) != 1:
        raise MalformedReferenceError(
            f"{_described(described)} states a grid-based method with "
            f"{len(datasets)} dataset parameters; it states exactly one"
        )
    found = methods.grid(datasets[0])
    expected_code = "9615" if name.casefold() == "ntv2" else "9613"
    if found.method_code != expected_code:
        raise MalformedReferenceError(
            f"{_described(described)} states method {name}, but dataset "
            f"{datasets[0]!r} requires {found.method_name}"
        )
    return {
        "method": {
            "name": found.method_name,
            "id": {"authority": "EPSG", "code": int(found.method_code)},
        },
        "parameters": [
            {
                "name": parameter,
                "value": file_name,
                "id": {"authority": "EPSG", "code": int(code)},
            }
            for code, parameter, file_name in found.files
        ],
    }


def _parameter(parameter: methods.Parameter, value: float) -> JsonObject:
    """State one numeric parameter, with the unit ESRI leaves implicit."""
    return {
        "name": parameter.name,
        "value": value,
        "unit": parameter.unit,
        "id": {"authority": "EPSG", "code": parameter.code},
    }


def _concatenation(steps: list[JsonObject], described: str) -> JsonObject:
    """State a chain of transformations as one concatenated operation.

    Adjacent CRSs must be equivalent apart from axis order. When their axes
    differ, restate the next step's source axes to match the preceding output
    so PROJ can join them; the method and its parameters remain unchanged.

    Raises:
        UnsupportedReferenceError: If a step does not begin where the one
            before it ended. A reversal or an additional transformation may
            be required; neither is inferred. Swapping a step's source and
            target alone leaves PROJ applying its parameters forwards.
    """
    for before, after in pairwise(steps):
        ending = CRS.from_json_dict(before["target_crs"])
        beginning = CRS.from_json_dict(after["source_crs"])
        if not ending.equals(beginning, ignore_axis_order=True):
            raise UnsupportedReferenceError(
                f"{_described(described)} chains a step from {beginning.name!r} "
                f"after one ending at {ending.name!r}, but their CRS definitions "
                "are not equivalent; an additional transformation or a reversed "
                "step would be required, which this package does not infer"
            )
        if not ending.equals(beginning):
            after["source_crs"] = {
                **after["source_crs"],
                "coordinate_system": before["target_crs"]["coordinate_system"],
            }
    return {
        "type": "ConcatenatedOperation",
        "name": described or "concatenated transformation",
        "source_crs": steps[0]["source_crs"],
        "target_crs": steps[-1]["target_crs"],
        "steps": steps,
    }


def operation_from_geogtran(wkt: str) -> CoordinateOperation:
    """Build the coordinate operation an ESRI ``GEOGTRAN`` states.

    ESRI's ``GEOGTRAN`` is not standard WKT and PROJ will not read one, so its
    parameters are translated here exactly as they are inside a
    persistableReference, which is this same definition in a JSON envelope.
    Use it when a register hands over the transformation on its own.

    Args:
        wkt: The ``GEOGTRAN``, without an envelope around it.

    Returns:
        The operation, ready to be applied or bound to a CRS.

    Raises:
        MalformedReferenceError: If the WKT cannot be read, or states
            something other than a ``GEOGTRAN``.
        UnsupportedReferenceError: If it states a vertical transformation.
        UnsupportedMethodError: If it states a method or parameter this
            package will not translate.
        UnresolvableGridError: If a grid-based step names a dataset that
            resolves to no single grid in PROJ's database.
    """
    node = _transformation_node(wkt, _TRANSFORMATION_KEYWORD)
    return _operation_from(_transformation(node), node.name or _TRANSFORMATION_KEYWORD)


def _operation_from(definition: JsonObject, described: str) -> CoordinateOperation:
    """Hand a PROJJSON operation to PROJ."""
    try:
        return CoordinateOperation.from_json_dict(definition)
    except CRSError as error:
        raise MalformedReferenceError(
            f"{_described(described)} does not assemble into a coordinate "
            f"operation: {error}"
        ) from error


def _target_of(operation: CoordinateOperation, described: str) -> CRS:
    """The CRS a transformation ends at, which a bound CRS binds to."""
    target: Any = operation.to_json_dict().get("target_crs")
    if not isinstance(target, dict):
        raise MalformedReferenceError(
            f"{_described(described)} states a transformation with no target CRS"
        )
    try:
        return CRS.from_json_dict(target)
    except CRSError as error:
        raise MalformedReferenceError(
            f"{_described(described)} states a target CRS PROJ will not read: {error}"
        ) from error


def _described(name: str) -> str:
    """Name a reference for an error message."""
    return f"persistableReference {name!r}" if name else "persistableReference"
