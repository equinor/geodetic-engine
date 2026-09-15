"""A scale must survive the round trip through an abridged transformation.

An ``ABRIDGEDTRANSFORMATION`` carries no units, so the only thing standing
between a correct scale and a silently wrong one is the convention each side
assumes. PROJ converts a scale in parts per million into the factor that form
requires, but writes one in parts per billion through unchanged, which is how
``0.33`` ppb becomes a scale of ``-670000`` ppm and a position wrong by
kilometres.

These tests use published EPSG operations rather than hand-built fixtures,
because a fixture written from the same misunderstanding would not catch it.
"""

from __future__ import annotations

import re

import pytest
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy.utils import scale_in_parts_per_million

# ITRF2020 to ETRF2000 (1): a plain Helmert stating its scale in parts per
# billion, the unit EPSG uses for recent ITRF and ETRF realisations. It is also
# time dependent, so it carries a rate of change of scale that must not be
# touched even though that is a scale unit too.
PARTS_PER_BILLION = ("EPSG", 10586)
PPB_SCALE = 2.25

# ED50 to WGS 84 (24): the same kind of operation, but already in parts per
# million, so it must come back untouched.
PARTS_PER_MILLION = ("EPSG", 1613)

# ETRS89 to WGS 84 (1): a null transformation, which states no scale at all.
NO_SCALE = ("EPSG", 1149)

SCALE_CODE = "8611"
SCALE_RATE_CODE = "1046"


def _operation(reference: tuple[str, int]) -> CoordinateOperation:
    return CoordinateOperation.from_authority(*reference)


def _scale(operation: CoordinateOperation) -> tuple[float, str] | None:
    return next(
        ((p.value, p.unit_name) for p in operation.params if p.code == SCALE_CODE),
        None,
    )


def _abridged_scale(operation: CoordinateOperation) -> float:
    """The scale PROJ writes when it embeds the operation in a bound CRS."""
    wkt = BoundCRS(
        CRS.from_user_input("EPSG:4258"),
        CRS.from_user_input("EPSG:4326"),
        operation,
    ).to_wkt()
    match = re.search(
        r'ABRIDGEDTRANSFORMATION.*?PARAMETER\["Scale difference",([-0-9.eE]+)',
        wkt,
        re.S,
    )
    assert match is not None
    return float(match.group(1))


def test_parts_per_billion_scale_is_restated_in_parts_per_million() -> None:
    restated = _scale(scale_in_parts_per_million(_operation(PARTS_PER_BILLION)))

    assert restated is not None
    value, unit = restated
    assert unit == "parts per million"
    assert value == pytest.approx(PPB_SCALE * 1e-3, rel=1e-12)


def test_restating_the_scale_changes_nothing_else() -> None:
    operation = _operation(PARTS_PER_BILLION)

    restated = scale_in_parts_per_million(operation)

    assert restated.name == operation.name
    assert restated.method_name == operation.method_name
    assert restated.accuracy == operation.accuracy
    assert restated.to_json_dict()["id"] == operation.to_json_dict()["id"]
    others = [
        (p.code, p.value, p.unit_name) for p in operation.params if p.code != SCALE_CODE
    ]
    assert [
        (p.code, p.value, p.unit_name) for p in restated.params if p.code != SCALE_CODE
    ] == others


def test_a_rate_of_change_of_scale_is_left_alone() -> None:
    """Also a scale unit, but PROJ states its factor per second, not per year."""
    operation = _operation(PARTS_PER_BILLION)

    restated = scale_in_parts_per_million(operation)

    rate = next(p for p in restated.params if p.code == SCALE_RATE_CODE)
    assert rate.unit_name == "parts per billion per year"
    assert rate.value == pytest.approx(
        next(p.value for p in operation.params if p.code == SCALE_RATE_CODE)
    )


@pytest.mark.parametrize("reference", [PARTS_PER_MILLION, NO_SCALE])
def test_an_operation_needing_no_change_is_returned_as_is(
    reference: tuple[str, int],
) -> None:
    operation = _operation(reference)

    assert scale_in_parts_per_million(operation) is operation


def test_restating_the_scale_fixes_what_proj_embeds() -> None:
    operation = _operation(PARTS_PER_BILLION)

    # PROJ writes the parts per billion value through as if it were the factor.
    assert _abridged_scale(operation) == pytest.approx(PPB_SCALE)
    assert _abridged_scale(scale_in_parts_per_million(operation)) == pytest.approx(
        1 + PPB_SCALE * 1e-9, rel=1e-15
    )


def test_a_parts_per_million_scale_is_embedded_correctly_already() -> None:
    operation = _operation(PARTS_PER_MILLION)
    value, _ = _scale(operation)  # type: ignore[misc]

    assert _abridged_scale(operation) == pytest.approx(1 + value * 1e-6, rel=1e-15)
