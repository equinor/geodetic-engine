"""Rounding half up by the shortest decimal form, vectorised."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Context, Decimal

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from geodetic_engine.geodesy._rounding import (
    axis_decimals,
    round_columns,
    round_half_up,
)

_CONTEXT = Context(prec=800, rounding=ROUND_HALF_UP)


def _reference(value: float, decimals: int) -> float:
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(value)).quantize(quantum, context=_CONTEXT)) + 0.0


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (2.675, 2, 2.68),
        (-0.125, 2, -0.13),
        (123456.785, 2, 123456.79),
        (2.5, 0, 3.0),
        (-2.5, 0, -3.0),
        (0.0001, 2, 0.0),
        (1e-07, 3, 0.0),
        (597868.38123456, 2, 597868.38),
    ],
)
def test_ties_go_away_from_zero_by_the_decimal_a_reader_sees(
    value: float, decimals: int, expected: float
) -> None:
    assert round_half_up([value], decimals)[0] == expected


def test_minus_zero_becomes_zero() -> None:
    rounded = round_half_up([-0.001, -0.0, -0.4], 0)
    assert rounded.tolist() == [0.0, 0.0, 0.0]
    assert not np.signbit(rounded).any()


def test_non_finite_values_are_kept() -> None:
    rounded = round_half_up([np.nan, np.inf, -np.inf], 2)
    assert np.isnan(rounded[0])
    assert rounded[1:].tolist() == [np.inf, -np.inf]


def test_rounding_does_not_modify_its_input() -> None:
    values = np.array([2.675, 1.005])
    round_half_up(values, 2)
    assert values.tolist() == [2.675, 1.005]


@given(
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    st.integers(min_value=0, max_value=30),
)
def test_vectorised_rounding_matches_the_decimal_reference(
    value: float, decimals: int
) -> None:
    got = round_half_up([value], decimals)[0]
    expected = _reference(value, decimals)
    assert got == expected
    assert np.signbit(got) == np.signbit(expected)


@given(
    st.decimals(min_value=-(10**9), max_value=10**9, places=6, allow_nan=False).map(
        float
    ),
    st.integers(min_value=0, max_value=5),
)
def test_values_near_ties_match_the_decimal_reference(
    value: float, decimals: int
) -> None:
    assert round_half_up([value], decimals)[0] == _reference(value, decimals)


@given(
    st.floats(min_value=-1e9, max_value=1e9, allow_nan=False),
    st.integers(min_value=0, max_value=12),
)
def test_rounding_twice_is_rounding_once(value: float, decimals: int) -> None:
    once = round_half_up([value], decimals)
    assert round_half_up(once, decimals).tolist() == once.tolist()


def test_each_axis_has_its_own_decimals() -> None:
    columns = [np.array([10.123456789]), np.array([60.987654321]), np.array([2.345])]
    x, y, z = round_columns(columns, (8, 8, 2))
    assert (x[0], y[0], z[0]) == (10.12345679, 60.98765432, 2.35)
    x, y, z = round_columns(columns, (3, None, 0))
    assert (x[0], y[0], z[0]) == (10.123, 60.987654321, 2.0)
    assert round_columns(columns, None)[0][0] == 10.123456789


@pytest.mark.parametrize(
    ("decimals", "match"),
    [((8, 8), "2 numbers for 3 axes"), (-1, "whole number"), (True, "whole number")],
)
def test_decimals_are_checked(decimals: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        axis_decimals(decimals, 3)  # type: ignore[arg-type]
