"""Length and angle units, read from a symbol, an OSDU record id or a payload."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal
from urllib.parse import unquote

from geodetic_engine.persistablereference import (
    PersistableReferenceError,
    UnitReference,
    looks_like_reference,
    parse_persistable_reference,
)
from geodetic_engine.units.errors import UnitError

# Metres per unit, by casefolded symbol or name.
_LENGTHS: Final = {
    **dict.fromkeys(("m", "metre", "meter", "metres", "meters"), 1.0),
    **dict.fromkeys(("ft", "foot", "feet", "international foot"), 0.3048),
    **dict.fromkeys(("ftus", "ft[us]", "usft", "us survey foot"), 1200 / 3937),
    "km": 1000.0,
    "cm": 0.01,
    "mm": 0.001,
    "in": 0.0254,
}
# Radians per unit, by casefolded symbol or name.
_ANGLES: Final = {
    **dict.fromkeys(("deg", "dega", "degree", "degrees"), math.pi / 180),
    **dict.fromkeys(("rad", "radian", "radians"), 1.0),
    **dict.fromkeys(("gon", "grad", "grads"), math.pi / 200),
}
_UNIT_OF_MEASURE: Final = "UnitOfMeasure:"


@dataclass(frozen=True, slots=True)
class _Quantity:
    """How the units of one quantity are read and written.

    Attributes:
        ancestries: The casefolded ``baseMeasurement`` ancestries a payload
            for one of its units may state: the OSDU CRS conversion service's
            names, and the Energistics dimension the OSDU unit catalogue uses.
        ancestry: The ancestry a payload written for one of its units states.
        symbols: SI units per unit, by casefolded symbol or name.
    """

    ancestries: frozenset[str]
    ancestry: str
    symbols: Mapping[str, float]


_QUANTITIES: Final[Mapping[str, _Quantity]] = {
    "length": _Quantity(frozenset({"length", "l"}), "Length", _LENGTHS),
    "angle": _Quantity(
        frozenset({"plane_angle", "angle", "a"}), "Plane_Angle", _ANGLES
    ),
}


@dataclass(frozen=True, slots=True)
class Unit:
    """A length or angle unit, as ``si = scale * value``.

    Attributes:
        symbol: Its symbol, as given or as its payload states it.
        quantity: ``"length"`` or ``"angle"``.
        scale: SI units per unit: metres, or radians.
        payload: The persistableReference it was read from, to be written
            back unchanged, or None. Not compared.

    Raises:
        UnitError: If ``quantity`` is neither, or ``scale`` is not a finite
            positive number.
    """

    symbol: str
    quantity: Literal["length", "angle"]
    scale: float
    payload: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.quantity not in _QUANTITIES:
            raise UnitError(f"a unit is a length or an angle, not {self.quantity!r}")
        if not (math.isfinite(self.scale) and self.scale > 0):
            raise UnitError(f"{self.symbol!r} converts by a scale of {self.scale!r}")

    def to_si(self, value: float) -> float:
        """A value in this unit, in metres or radians."""
        return self.scale * value

    def from_si(self, value: float) -> float:
        """A value in metres or radians, in this unit."""
        return value / self.scale

    def reference(self) -> str:
        """The unit as an OSDU unit persistableReference.

        The payload it was read from, if any. Otherwise a ``USO`` payload in
        the form the OSDU CRS conversion service writes: for metres, exactly
        the service's own.
        """
        if self.payload is not None:
            return self.payload
        stated = {
            "scaleOffset": {"scale": self.scale, "offset": 0.0},
            "symbol": self.symbol,
            "baseMeasurement": {
                "ancestry": _QUANTITIES[self.quantity].ancestry,
                "type": "UM",
            },
            "type": "USO",
        }
        return json.dumps(stated, separators=(",", ":"))


def length_unit(value: str) -> Unit:
    """Read a length unit.

    Args:
        value: A symbol or name in any case, such as ``"ft"`` or ``"metre"``;
            an OSDU ``UnitOfMeasure`` record id, URL-encoded or not, such as
            ``"osdu:reference-data--UnitOfMeasure:ft:"``; or an OSDU unit
            persistableReference, ``USO`` or ``UAD``.

    Raises:
        UnitError: If ``value`` is none of these or not a length, or is a
            payload converting with an offset, by a rational polynomial, or by
            a scale that is not finite and positive.

    Example:
        >>> length_unit("ftUS").scale == 1200 / 3937
        True
    """
    return _read(value, "length")


def angle_unit(value: str) -> Unit:
    """Read an angle unit, in the forms :func:`length_unit` reads.

    Raises:
        UnitError: If ``value`` is not an angle unit this package reads.

    Example:
        >>> angle_unit("dega").scale == math.pi / 180
        True
    """
    return _read(value, "angle")


def _read(value: str, quantity: Literal["length", "angle"]) -> Unit:
    if not isinstance(value, str) or not value.strip():
        raise UnitError(f"a {quantity} unit is a non-empty string, not {value!r}")
    text = value.strip()
    if looks_like_reference(text):
        return _from_payload(text, quantity)
    symbol = unquote(text).strip()
    if _UNIT_OF_MEASURE in symbol:
        symbol = symbol.split(_UNIT_OF_MEASURE, 1)[1].split(":", 1)[0]
    scale = _QUANTITIES[quantity].symbols.get(symbol.casefold())
    if scale is None:
        raise UnitError(f"{value!r} is not a {quantity} unit this package knows")
    return Unit(symbol, quantity, scale)


def _from_payload(text: str, quantity: Literal["length", "angle"]) -> Unit:
    try:
        reference = parse_persistable_reference(text)
        if not isinstance(reference, UnitReference):
            raise UnitError(
                f"the persistableReference given as a {quantity} unit is not a unit"
            )
        scale, offset = reference.scale, reference.offset
    except PersistableReferenceError as error:
        raise UnitError(f"cannot read the unit: {error}") from error
    if reference.measurement not in _QUANTITIES[quantity].ancestries:
        raise UnitError(
            f"{reference.symbol!r} ({reference.measurement or 'no measurement'}) "
            f"is not a {quantity} unit"
        )
    if offset != 0.0:
        raise UnitError(
            f"{reference.symbol!r} converts with an offset of {offset!r}, which no "
            f"{quantity} unit has"
        )
    return Unit(reference.symbol or text, quantity, scale, payload=text)
