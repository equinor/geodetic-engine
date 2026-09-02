"""Reducing a chain of Helmert steps to a single equivalent Helmert.

A concatenated operation such as ``EPSG:8047`` (ED50 to WGS 84 (15)) is defined
as two Helmert transformations through an intermediate frame. PROJ can apply
such a chain, but it cannot embed one in a ``BoundCRS``, which requires a single
transformation. Collapsing the chain is what makes those definitions usable.

Two Helmert transformations compose exactly, because each is an affine map on
geocentric coordinates::

    X1 = T1 + (1 + s1) * R1 * X0
    X2 = T2 + (1 + s2) * R2 * X1
       = [T2 + (1 + s2) * R2 * T1] + (1 + s1) * (1 + s2) * R2 * R1 * X0

so the composition is again a Helmert with ``T = T2 + (1 + s2) * R2 * T1``,
``R = R2 * R1`` and ``1 + s = (1 + s1) * (1 + s2)``. The intermediate
geographic-to-geocentric conversions cancel because the frame between the two
steps is one CRS with one ellipsoid.

The algebra is only the proposal. Because EPSG's rotation matrix is linearised
for small angles, ``R2 * R1`` is not exactly a linearised matrix again, so every
collapse is checked against PROJ's own rendering of the original chain over the
operation's area of use, and refused if it does not agree. See
:func:`collapse_concatenated`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pyproj import CRS, Transformer
from pyproj.crs import CoordinateOperation
from pyproj.exceptions import CRSError, ProjError

from geodetic_engine.geodesy.errors import NotCollapsibleError

# Radians per arc-second, the unit EPSG states Helmert rotations in and the unit
# PROJ's "+proj=helmert +rx=" expects. Parameter values are never assumed to be
# in this unit; they are converted using each parameter's own conversion factor,
# because EPSG states some rotations in microradians instead.
_ARC_SECOND = math.pi / (180.0 * 3600.0)

# EPSG parameter codes of a Helmert transformation.
_TRANSLATION_CODES = (8605, 8606, 8607)
_ROTATION_CODES = (8608, 8609, 8610)
_SCALE_CODE = 8611

# Parameter codes that make a Helmert something this module must not collapse:
# rates of change and their reference epoch (the composition would need a common
# epoch), a pivot point (Molodensky-Badekas is not a plain affine map about the
# geocentre), and a transformation epoch.
_DISQUALIFYING_CODES = frozenset(
    {1040, 1041, 1042, 1043, 1044, 1045, 1046, 1047, 1049, 8617, 8618, 8667}
)


class Rotation(StrEnum):
    """Sign convention a method states its rotation parameters in."""

    POSITION_VECTOR = "position_vector"
    """Rotations act on the position vector; PROJ's ``+convention=position_vector``."""

    COORDINATE_FRAME = "coordinate_frame"
    """Rotations act on the axes; the transpose of position vector."""


class _Domain(StrEnum):
    """Coordinate space a Helmert method is defined to act on."""

    GEOG_2D = "geog2D"
    GEOG_3D = "geog3D"
    GEOCENTRIC = "geocentric"


# EPSG method code -> (rotation convention, domain). Only plain Helmert methods
# appear here. Molodensky-Badekas, time-dependent, time-specific and
# "full matrix" variants are deliberately absent: they either are not a plain
# affine map about the geocentre or do not use the linearised rotation, so
# composing them as one would silently change the transformation.
_METHODS: dict[int, tuple[Rotation, _Domain]] = {
    9603: (Rotation.POSITION_VECTOR, _Domain.GEOG_2D),  # Geocentric translations
    1031: (Rotation.POSITION_VECTOR, _Domain.GEOCENTRIC),  # Geocentric translations
    1035: (Rotation.POSITION_VECTOR, _Domain.GEOG_3D),  # Geocentric translations
    9606: (Rotation.POSITION_VECTOR, _Domain.GEOG_2D),  # Position Vector
    1033: (Rotation.POSITION_VECTOR, _Domain.GEOCENTRIC),  # Position Vector
    1037: (Rotation.POSITION_VECTOR, _Domain.GEOG_3D),  # Position Vector
    9607: (Rotation.COORDINATE_FRAME, _Domain.GEOG_2D),  # Coordinate Frame
    1032: (Rotation.COORDINATE_FRAME, _Domain.GEOCENTRIC),  # Coordinate Frame
    1038: (Rotation.COORDINATE_FRAME, _Domain.GEOG_3D),  # Coordinate Frame
}

# Method to state a collapsed operation under, by domain. Always the seven
# parameter Position Vector form: it represents a translations-only chain
# exactly as well, with zero rotations and zero scale.
_COLLAPSED_METHOD: dict[_Domain, tuple[int, str]] = {
    _Domain.GEOG_2D: (9606, "Position Vector transformation (geog2D domain)"),
    _Domain.GEOG_3D: (1037, "Position Vector transformation (geog3D domain)"),
    _Domain.GEOCENTRIC: (1033, "Position Vector transformation (geocentric domain)"),
}

_PARAMETER_NAMES = {
    8605: "X-axis translation",
    8606: "Y-axis translation",
    8607: "Z-axis translation",
    8608: "X-axis rotation",
    8609: "Y-axis rotation",
    8610: "Z-axis rotation",
    8611: "Scale difference",
}


@dataclass(frozen=True, slots=True)
class HelmertParameters:
    """A seven parameter Helmert transformation, in SI units.

    Stored in the position vector convention regardless of how the source
    method stated it, so that two sets can be composed without re-checking
    which way the rotations point.

    Attributes:
        tx: X-axis translation, metres.
        ty: Y-axis translation, metres.
        tz: Z-axis translation, metres.
        rx: X-axis rotation, radians, position vector convention.
        ry: Y-axis rotation, radians, position vector convention.
        rz: Z-axis rotation, radians, position vector convention.
        scale: Scale difference as a fraction, so 1 ppm is ``1e-6``.

    Example:
        >>> operation = CoordinateOperation.from_authority("EPSG", 1147)
        >>> helmert_parameters(operation).rotations_arc_seconds()[0]
        -0.390459...
    """

    tx: float
    ty: float
    tz: float
    rx: float
    ry: float
    rz: float
    scale: float

    def rotations_arc_seconds(self) -> tuple[float, float, float]:
        """The three rotations in arc-seconds, the unit EPSG and PROJ state."""
        return (self.rx / _ARC_SECOND, self.ry / _ARC_SECOND, self.rz / _ARC_SECOND)

    def scale_ppm(self) -> float:
        """The scale difference in parts per million."""
        return self.scale * 1e6

    def proj_string(self) -> str:
        """Render as a ``+proj=helmert`` step, in PROJ's units.

        Returns:
            A PROJ pipeline step, rotations in arc-seconds and scale in ppm.
        """
        rx, ry, rz = self.rotations_arc_seconds()
        return (
            f"+proj=helmert +x={_number(self.tx)} +y={_number(self.ty)} "
            f"+z={_number(self.tz)} +rx={_number(rx)} +ry={_number(ry)} "
            f"+rz={_number(rz)} +s={_number(self.scale_ppm())} "
            "+convention=position_vector"
        )


def _number(value: float) -> str:
    """Render a float without losing precision or emitting a numpy repr."""
    return f"{float(value):.17g}"


def _rotation_matrix(parameters: HelmertParameters) -> list[list[float]]:
    """EPSG's linearised rotation matrix for the position vector convention."""
    rx, ry, rz = parameters.rx, parameters.ry, parameters.rz
    return [[1.0, -rz, ry], [rz, 1.0, -rx], [-ry, rx, 1.0]]


def helmert_parameters(operation: CoordinateOperation) -> HelmertParameters | None:
    """Read a plain Helmert's parameters, normalised to position vector.

    Args:
        operation: A single coordinate operation.

    Returns:
        The parameters in SI units, or None if the operation is not a plain
        Helmert. Molodensky-Badekas, time-dependent, time-specific and full
        matrix variants all return None, because none of them is the linearised
        affine map about the geocentre that :func:`compose` assumes.

    Example:
        >>> helmert_parameters(CoordinateOperation.from_authority("EPSG", 1147))
        HelmertParameters(tx=-1.51, ...)
    """
    method_code = _method_code(operation)
    if method_code is None or method_code not in _METHODS:
        return None

    values: dict[int, float] = {}
    for parameter in operation.params:
        code = _parameter_code(parameter)
        if code is None:
            continue
        if code in _DISQUALIFYING_CODES:
            return None
        # The conversion factor is what makes the unit explicit: EPSG states
        # some rotations in microradians and others in arc-seconds.
        values[code] = float(parameter.value) * float(parameter.unit_conversion_factor)

    convention, _ = _METHODS[method_code]
    sign = -1.0 if convention is Rotation.COORDINATE_FRAME else 1.0
    return HelmertParameters(
        tx=values.get(_TRANSLATION_CODES[0], 0.0),
        ty=values.get(_TRANSLATION_CODES[1], 0.0),
        tz=values.get(_TRANSLATION_CODES[2], 0.0),
        rx=sign * values.get(_ROTATION_CODES[0], 0.0),
        ry=sign * values.get(_ROTATION_CODES[1], 0.0),
        rz=sign * values.get(_ROTATION_CODES[2], 0.0),
        scale=values.get(_SCALE_CODE, 0.0),
    )


def compose(first: HelmertParameters, second: HelmertParameters) -> HelmertParameters:
    """Compose two Helmert transformations applied in order.

    Args:
        first: The transformation applied first.
        second: The transformation applied to the result of ``first``.

    Returns:
        A single Helmert equivalent to applying ``first`` then ``second``.

    Example:
        >>> ed50_to_ed87 = helmert_parameters(
        ...     CoordinateOperation.from_authority("EPSG", 1147)
        ... )
        >>> ed87_to_wgs84 = helmert_parameters(
        ...     CoordinateOperation.from_authority("EPSG", 1146)
        ... )
        >>> round(compose(ed50_to_ed87, ed87_to_wgs84).tx, 3)
        -84.491
    """
    r_first = _rotation_matrix(first)
    r_second = _rotation_matrix(second)
    scale_second = 1.0 + second.scale

    translation_first = (first.tx, first.ty, first.tz)
    rotated = [
        sum(r_second[row][col] * translation_first[col] for col in range(3))
        for row in range(3)
    ]
    tx, ty, tz = (
        second.tx + scale_second * rotated[0],
        second.ty + scale_second * rotated[1],
        second.tz + scale_second * rotated[2],
    )

    product = [
        [sum(r_second[row][k] * r_first[k][col] for k in range(3)) for col in range(3)]
        for row in range(3)
    ]
    # The product of two linearised rotations is not exactly linearised, so the
    # angles are read back from its antisymmetric part. Any residual that this
    # discards is what the numerical check in collapse_concatenated catches.
    return HelmertParameters(
        tx=tx,
        ty=ty,
        tz=tz,
        rx=(product[2][1] - product[1][2]) / 2.0,
        ry=(product[0][2] - product[2][0]) / 2.0,
        rz=(product[1][0] - product[0][1]) / 2.0,
        scale=(1.0 + first.scale) * (1.0 + second.scale) - 1.0,
    )


def is_collapsible(operation: CoordinateOperation) -> bool:
    """Whether a concatenated operation is a chain of plain Helmert steps.

    A cheap structural check. It does not prove the collapse is numerically
    faithful; only :func:`collapse_concatenated` does that.

    Args:
        operation: The operation to inspect.

    Returns:
        True if every step is a plain Helmert and all steps share one domain.
    """
    try:
        _chain(operation)
    except NotCollapsibleError:
        return False
    return True


def _chain(operation: CoordinateOperation) -> tuple[list[HelmertParameters], _Domain]:
    """Validate the structure of a concatenated operation and read its steps."""
    definition = operation.to_json_dict()
    steps = definition.get("steps")
    if not isinstance(steps, list) or len(steps) < 2:
        raise NotCollapsibleError(
            f"{_label(operation)} is not a concatenated operation with at least "
            "two steps, so there is nothing to collapse"
        )

    parameters: list[HelmertParameters] = []
    domains: set[_Domain] = set()
    for index, step in enumerate(steps, start=1):
        try:
            single = CoordinateOperation.from_json_dict(step)
        except CRSError as error:
            raise NotCollapsibleError(
                f"step {index} of {_label(operation)} could not be read as a "
                f"coordinate operation: {error}"
            ) from error
        values = helmert_parameters(single)
        method_code = _method_code(single)
        if values is None or method_code is None:
            raise NotCollapsibleError(
                f"step {index} of {_label(operation)} applies "
                f"{single.method_name!r}, which is not a plain Helmert and so "
                "cannot be composed into a single step"
            )
        domains.add(_METHODS[method_code][1])
        parameters.append(values)

    if len(domains) != 1:
        raise NotCollapsibleError(
            f"{_label(operation)} mixes Helmert domains "
            f"{sorted(str(item) for item in domains)}; they treat the "
            "ellipsoidal height differently and do not compose"
        )
    return parameters, domains.pop()


def collapse_concatenated(
    operation: CoordinateOperation,
    *,
    tolerance_m: float = 1e-3,
    samples: int = 512,
) -> CoordinateOperation:
    """Rewrite a concatenated Helmert chain as one equivalent Helmert.

    The composed parameters are verified against PROJ's own rendering of the
    original chain over the operation's area of use. A collapse that does not
    reproduce the chain is refused rather than returned, because the whole point
    of collapsing is to keep the transformation the authority defined.

    Args:
        operation: A concatenated operation whose steps are all plain Helmerts.
        tolerance_m: Largest residual, in metres, that may remain between the
            collapsed operation and the original chain. Defaults to 1 mm, which
            is far below the accuracy of any published datum shift while still
            catching a genuine convention or unit error.
        samples: How many points to compare across the area of use.

    Returns:
        A single step coordinate operation between the same CRSs, stating the
        composed parameters in the position vector convention.

    Raises:
        NotCollapsibleError: If a step is not a plain Helmert, if the steps mix
            domains, if the source or target CRS cannot be read, or if the
            composed parameters do not reproduce the chain within
            ``tolerance_m``.

    Example:
        >>> chain = CoordinateOperation.from_authority("EPSG", 8047)
        >>> collapsed = collapse_concatenated(chain)
        >>> collapsed.towgs84[0]
        -84.491...
    """
    steps, domain = _chain(operation)

    combined = steps[0]
    for step in steps[1:]:
        combined = compose(combined, step)

    definition = operation.to_json_dict()
    source = _crs_of(definition, "source_crs", operation)
    target = _crs_of(definition, "target_crs", operation)

    collapsed = _build_operation(operation, combined, domain, source, target)
    _verify(operation, collapsed, source, target, tolerance_m, samples)
    return collapsed


def _build_operation(
    operation: CoordinateOperation,
    parameters: HelmertParameters,
    domain: _Domain,
    source: CRS,
    target: CRS,
) -> CoordinateOperation:
    """State the composed parameters as a single step coordinate operation."""
    method_code, method_name = _COLLAPSED_METHOD[domain]
    rx, ry, rz = parameters.rotations_arc_seconds()
    rendered = [
        _length_parameter(8605, parameters.tx),
        _length_parameter(8606, parameters.ty),
        _length_parameter(8607, parameters.tz),
        _angle_parameter(8608, rx),
        _angle_parameter(8609, ry),
        _angle_parameter(8610, rz),
        _scale_parameter(8611, parameters.scale_ppm()),
    ]
    accuracy = (
        f",OPERATIONACCURACY[{_number(operation.accuracy)}]"
        if operation.accuracy is not None
        else ""
    )
    wkt = (
        f'COORDINATEOPERATION["{_collapsed_name(operation)}",'
        f"SOURCECRS[{source.to_wkt()}],"
        f"TARGETCRS[{target.to_wkt()}],"
        f'METHOD["{method_name}",ID["EPSG",{method_code}]],'
        + ",".join(rendered)
        + accuracy
        + "]"
    )
    try:
        return CoordinateOperation.from_string(wkt)
    except CRSError as error:
        raise NotCollapsibleError(
            f"the composed parameters for {_label(operation)} could not be read "
            f"back as a coordinate operation: {error}"
        ) from error


def _collapsed_name(operation: CoordinateOperation) -> str:
    """Name the collapsed operation after the chain it replaces."""
    name = str(operation.name or "concatenated operation").replace('"', "'")
    return f"{name} (collapsed to a single step)"


def _length_parameter(code: int, value: float) -> str:
    return (
        f'PARAMETER["{_PARAMETER_NAMES[code]}",{_number(value)},'
        f'LENGTHUNIT["metre",1],ID["EPSG",{code}]]'
    )


def _angle_parameter(code: int, value: float) -> str:
    return (
        f'PARAMETER["{_PARAMETER_NAMES[code]}",{_number(value)},'
        f'ANGLEUNIT["arc-second",{_number(_ARC_SECOND)}],ID["EPSG",{code}]]'
    )


def _scale_parameter(code: int, value: float) -> str:
    return (
        f'PARAMETER["{_PARAMETER_NAMES[code]}",{_number(value)},'
        f'SCALEUNIT["parts per million",1e-06],ID["EPSG",{code}]]'
    )


def _verify(
    original: CoordinateOperation,
    collapsed: CoordinateOperation,
    source: CRS,
    target: CRS,
    tolerance_m: float,
    samples: int,
) -> None:
    """Refuse a collapse that does not reproduce the original chain."""
    try:
        reference = Transformer.from_pipeline(original.to_wkt())
        candidate = Transformer.from_pipeline(collapsed.to_wkt())
    except (CRSError, ProjError) as error:
        raise NotCollapsibleError(
            f"{_label(original)} and its collapsed form could not both be built "
            f"as transformers, so the collapse cannot be verified: {error}"
        ) from error

    worst = 0.0
    geod = target.get_geod() or source.get_geod()
    for longitude, latitude in _sample_points(original, source, samples):
        try:
            got = reference.transform(latitude, longitude, 0.0, errcheck=True)
            expected = candidate.transform(latitude, longitude, 0.0, errcheck=True)
        except ProjError:
            # Outside the domain of one of the two; it proves nothing either way.
            continue
        if not all(math.isfinite(value) for value in (*got, *expected)):
            continue
        worst = max(worst, _separation(geod, got, expected))

    if worst > tolerance_m:
        raise NotCollapsibleError(
            f"collapsing {_label(original)} into a single Helmert moves "
            f"coordinates by up to {worst:.4g} m, more than the {tolerance_m:g} m "
            "allowed; the chain is not equivalent to one Helmert step"
        )


def _separation(
    geod: Any, got: tuple[float, ...], expected: tuple[float, ...]
) -> float:
    """Ground distance in metres between two transformed positions."""
    horizontal = 0.0
    if geod is not None:
        _, _, horizontal = geod.inv(got[1], got[0], expected[1], expected[0])
        horizontal = abs(horizontal)
    vertical = abs(got[2] - expected[2]) if len(got) > 2 and len(expected) > 2 else 0.0
    return math.hypot(horizontal, vertical)


def _sample_points(
    operation: CoordinateOperation, source: CRS, samples: int
) -> list[tuple[float, float]]:
    """Points spread over the operation's area of use, in ``xy`` order.

    Falls back to the source CRS's area, then to a global spread, so that an
    operation without a stated extent is still checked somewhere sensible.
    """
    area = operation.area_of_use or source.area_of_use
    west, south, east, north = (
        (area.west, area.south, area.east, area.north)
        if area is not None
        else (-180.0, -80.0, 180.0, 80.0)
    )
    if east < west:  # Crosses the antimeridian.
        east += 360.0

    side = max(2, math.isqrt(max(samples, 4)))
    points: list[tuple[float, float]] = []
    for row in range(side):
        for column in range(side):
            fraction_y = row / (side - 1)
            fraction_x = column / (side - 1)
            longitude = west + (east - west) * fraction_x
            latitude = south + (north - south) * fraction_y
            points.append(((longitude + 180.0) % 360.0 - 180.0, latitude))
    return points


def _crs_of(
    definition: dict[str, Any], key: str, operation: CoordinateOperation
) -> CRS:
    """Read one end of an operation, refusing to guess if it is absent."""
    node = definition.get(key)
    if not isinstance(node, dict):
        raise NotCollapsibleError(
            f"{_label(operation)} does not state its {key.replace('_', ' ')}, so "
            "the collapsed operation could not say what it goes between"
        )
    try:
        return CRS.from_json_dict(node)
    except CRSError as error:
        raise NotCollapsibleError(
            f"the {key.replace('_', ' ')} of {_label(operation)} could not be "
            f"read: {error}"
        ) from error


def _method_code(operation: CoordinateOperation) -> int | None:
    """The EPSG method code of a single step operation, if it states one."""
    code = operation.method_code
    if code is None or str(operation.method_auth_name or "").upper() != "EPSG":
        return None
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _parameter_code(parameter: Any) -> int | None:
    """The EPSG code of a parameter, if it states one."""
    try:
        return int(parameter.code)
    except (TypeError, ValueError):
        return None


def _label(operation: CoordinateOperation) -> str:
    """Name an operation for an error message."""
    identifier = operation.to_json_dict().get("id")
    if isinstance(identifier, dict):
        authority, code = identifier.get("authority"), identifier.get("code")
        if authority is not None and code is not None:
            return f"{authority}:{code}"
    return f"{operation.name!r}"
