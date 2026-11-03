"""Round coordinate values half up, as the decimals a reader sees them in.

Private to the package, shared by :mod:`geodetic_engine.geodesy.geometry` and
:mod:`geodetic_engine.geojson`.

A value rounds by its shortest decimal form, the one :func:`repr` shows: with
2 decimals ``2.675`` is ``2.68``, although the double nearest 2.675 is a
little below it, and ties go away from zero, so ``-0.125`` is ``-0.13``. The
reference is ``Decimal(repr(x)).quantize(Decimal(10) ** -decimals,
ROUND_HALF_UP)``. Python's :func:`round` and :func:`numpy.round` both round
ties to even, and disagree with each other where the double is not the
decimal.

Most values are rounded in numpy: ``x * 10**d`` is off from the decimal
``x * 10**d`` by a few units in its last place at most, which moves it across
a tie only when it is that close to one. Those values, and any too large for
the product to resolve a tie, are rounded with :mod:`decimal` instead, so the
result is the reference's for every value.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Final

import numpy as np
from numpy.typing import ArrayLike, NDArray

type FloatArray = NDArray[np.float64]
type Decimals = int | Sequence[int | None] | None

# Powers of ten up to this one are exact doubles, so dividing by one is
# correctly rounded.
_EXACT_POWER: Final = 22
# Below this a product's fraction, and the sum with 0.5, are exact enough.
_FAST_LIMIT: Final = 2.0**51
# How close to a tie a product has to be, relative to its size, for the
# decimal form and the double to possibly round apart.
_TIE_MARGIN: Final = 2.0**-46
# Enough digits for any double at any number of decimals asked for.
_CONTEXT: Final = Context(prec=800, rounding=ROUND_HALF_UP)


def round_half_up(values: ArrayLike, decimals: int) -> FloatArray:
    """Round ``values`` to ``decimals`` decimals, ties away from zero.

    NaN and infinities are kept. ``-0.0``, which ``-0.001`` rounds to with 2
    decimals, becomes ``0.0``: the two encode differently.

    Args:
        values: The values, any shape.
        decimals: Decimals to keep, from 0.

    Returns:
        A new float64 array of the same shape.

    Raises:
        ValueError: If ``decimals`` is not a non-negative integer.

    Example:
        >>> round_half_up([2.675, -0.125, 123456.785, 2.5], 2).tolist()
        [2.68, -0.13, 123456.79, 2.5]
        >>> round_half_up([2.5, -0.4], 0).tolist()
        [3.0, 0.0]
    """
    _require_decimals(decimals)
    array = np.array(values, dtype=np.float64)
    finite = np.isfinite(array)
    if decimals > _EXACT_POWER:
        exact = finite
    else:
        scale = 10.0**decimals
        with np.errstate(over="ignore", invalid="ignore"):
            magnitude = np.abs(array) * scale
            whole = np.floor(magnitude)
            fraction = magnitude - whole
        exact = finite & (
            (magnitude >= _FAST_LIMIT)
            | (np.abs(fraction - 0.5) <= magnitude * _TIE_MARGIN)
        )
        fast = finite & ~exact
        rounded = np.floor(magnitude[fast] + 0.5) / scale
        array[fast] = np.copysign(rounded, array[fast])
    if exact.any():
        quantum = Decimal(1).scaleb(-decimals)
        array[exact] = [
            float(Decimal(repr(float(value))).quantize(quantum, context=_CONTEXT))
            for value in array[exact]
        ]
    array += 0.0
    return array


def round_columns(
    columns: Sequence[FloatArray], decimals: Decimals
) -> tuple[FloatArray, ...]:
    """Round each column to the decimals given for its axis.

    Args:
        columns: One array per axis, in value order.
        decimals: One number for every axis, one per axis (None to keep that
            axis as it is), or None to keep every value.

    Returns:
        New arrays; ``columns`` is not modified.

    Raises:
        ValueError: If ``decimals`` gives fewer numbers than there are axes, or
            one that is not a non-negative integer.
    """
    per_axis = axis_decimals(decimals, len(columns))
    return tuple(
        np.array(column, dtype=np.float64)
        if places is None
        else round_half_up(column, places)
        for column, places in zip(columns, per_axis, strict=True)
    )


def axis_decimals(decimals: Decimals, width: int) -> tuple[int | None, ...]:
    """The decimals for each of ``width`` axes, checked.

    A sequence may be longer than ``width``: a ``(8, 8, 3)`` for longitude,
    latitude and height also applies to positions without a height.

    Raises:
        ValueError: As for :func:`round_columns`.
    """
    if decimals is None:
        return (None,) * width
    if isinstance(decimals, int):
        _require_decimals(decimals)
        return (decimals,) * width
    if isinstance(decimals, str | bytes) or not isinstance(decimals, Sequence):
        raise ValueError(
            "decimals is a number, one per axis, or None, not "
            f"{type(decimals).__name__}"
        )
    if len(decimals) < width:
        raise ValueError(
            f"decimals gives {len(decimals)} numbers for {width} axes; give one "
            "per axis, or None for an axis to keep"
        )
    for places in decimals[:width]:
        if places is not None:
            _require_decimals(places)
    return tuple(decimals[:width])


def _require_decimals(decimals: object) -> None:
    if (
        not isinstance(decimals, int)
        or isinstance(decimals, bool)
        or decimals < 0
        or decimals > _CONTEXT.prec // 2
    ):
        raise ValueError(
            f"decimals must be a whole number from 0 to {_CONTEXT.prec // 2}, "
            f"not {decimals!r}"
        )
