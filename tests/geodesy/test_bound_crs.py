"""A bound CRS carries its own transformation, so it is not an ambiguous pair.

The rule this package enforces is that a datum change needs a named operation.
A bound CRS satisfies that rule rather than escaping it: whoever defined the CRS
named the operation, and PROJ is left with exactly one candidate. These tests
pin that distinction down, because relaxing it by accident would turn the
ambiguity guard off for ordinary CRSs too.
"""

from __future__ import annotations

import pytest
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    OperationRoute,
    Transformation,
)
from geodetic_engine.geodesy.utils import collapse_concatenated

OSLO_XY = (10.7522, 59.9139)

ED50 = ("EPSG", 4230)
WGS84 = ("EPSG", 4326)
ED50_UTM32 = ("EPSG", 23032)  # ED50 / UTM zone 32N, a projected base
WGS84_UTM32 = "EPSG:32632"
ED50_TO_WGS84 = ("EPSG", 1133)  # ED50 to WGS 84 (1), a single Helmert
ED50_TO_WGS84_CHAIN = ("EPSG", 8047)  # ED50 to WGS 84 (15), two Helmerts


def _bound(operation: CoordinateOperation) -> CRS:
    return BoundCRS(CRS.from_epsg(ED50[1]), CRS.from_epsg(WGS84[1]), operation)


def _bound_projected(operation: CoordinateOperation) -> CRS:
    """A bound CRS whose base is projected, which is the common real case."""
    return BoundCRS(CRS.from_epsg(ED50_UTM32[1]), CRS.from_epsg(WGS84[1]), operation)


def test_unbound_datum_change_is_still_refused() -> None:
    """The guard must stay on for a CRS that names no operation."""
    with pytest.raises(AmbiguousOperationError, match="must be named"):
        Transformation("EPSG:4230", "EPSG:4326")


def test_bound_crs_supplies_its_own_operation() -> None:
    """The embedded transformation is applied, and reported as what was used."""
    transformation = Transformation(
        _bound(CoordinateOperation.from_authority(*ED50_TO_WGS84)), "EPSG:4326"
    )

    assert transformation.operation.route is OperationRoute.BOUND
    assert transformation.operation.authority_code == "EPSG:1133"


def test_bound_crs_agrees_with_naming_the_same_operation() -> None:
    """Early binding must not quietly mean a different answer."""
    through_bound = Transformation(
        _bound(CoordinateOperation.from_authority(*ED50_TO_WGS84)), "EPSG:4326"
    ).transform([OSLO_XY])
    through_name = Transformation(
        "EPSG:4230", "EPSG:4326", operation="EPSG:1133"
    ).transform([OSLO_XY])

    assert through_bound.coordinates == through_name.coordinates


def test_bound_crs_over_a_collapsed_chain() -> None:
    """A chain that had to be collapsed still transforms once embedded."""
    chain = CoordinateOperation.from_authority(*ED50_TO_WGS84_CHAIN)
    transformation = Transformation(_bound(collapse_concatenated(chain)), "EPSG:4326")

    result = transformation.transform([OSLO_XY])

    assert transformation.operation.route is OperationRoute.BOUND
    assert result.count == 1
    # ED50 and WGS 84 differ by roughly 100 m in Norway; a result that did not
    # move at all would mean the embedded transformation was never applied.
    assert result.coordinates[0][0] != pytest.approx(OSLO_XY[0], abs=1e-6)


def test_bound_result_still_carries_provenance() -> None:
    """A bound route must be as auditable as a named one."""
    transformation = Transformation(
        _bound(CoordinateOperation.from_authority(*ED50_TO_WGS84)), "EPSG:4326"
    )

    rendered = transformation.transform([OSLO_XY]).to_json_dict()

    assert rendered["operation"]["applied"] == "EPSG:1133"
    assert rendered["operation"]["route"] == "bound"
    assert rendered["coordinate_order"] == "xy"


@pytest.mark.parametrize("target", ["EPSG:4326", WGS84_UTM32])
def test_projected_bound_crs_still_names_its_operation(target: str) -> None:
    """A projected base needs the map projection wrapped around the shift.

    Letting the bound CRS build its own transformer loses the identity of the
    embedded operation for a projected base: the pipeline's substantive step
    becomes the map projection, and the applied operation comes back unnamed.
    Resolving through the transformer group keeps it identifiable.
    """
    transformation = Transformation(
        _bound_projected(CoordinateOperation.from_authority(*ED50_TO_WGS84)), target
    )

    assert transformation.operation.route is OperationRoute.BOUND
    assert transformation.operation.authority_code == "EPSG:1133"


@pytest.mark.parametrize("target", ["EPSG:4326", WGS84_UTM32])
def test_bound_crs_is_not_reported_as_requested_by_the_caller(target: str) -> None:
    """The operation was named by the CRS, not asked for; provenance must say so."""
    transformation = Transformation(
        _bound_projected(CoordinateOperation.from_authority(*ED50_TO_WGS84)), target
    )

    assert transformation.operation.requested is None


def test_projected_bound_crs_agrees_with_naming_the_operation() -> None:
    """Going through the group must not change where the coordinates land."""
    point = (600000.0, 6643000.0)  # ED50 / UTM zone 32N, offshore Norway

    through_bound = Transformation(
        _bound_projected(CoordinateOperation.from_authority(*ED50_TO_WGS84)),
        WGS84_UTM32,
    ).transform([point])
    through_name = Transformation(
        "EPSG:23032", WGS84_UTM32, operation="EPSG:1133"
    ).transform([point])

    assert through_bound.coordinates == through_name.coordinates


def test_bound_crs_as_target_resolves_its_own_operation() -> None:
    """The embedded operation must be found on either side of the pair.

    PROJ reports the authority of an inverted operation as INVERSE(EPSG), not
    EPSG: a bound CRS used as the target builds the inverse of its embedded
    operation, and that wrapper has to be stripped the same way the
    axis-order-normalisation wrapper already is, or the requested code is never
    found among the candidates.
    """
    bound = _bound(CoordinateOperation.from_authority(*ED50_TO_WGS84))
    forward = Transformation(bound, "EPSG:4326")
    reverse = Transformation("EPSG:4326", bound)

    assert reverse.operation.route is OperationRoute.BOUND
    assert reverse.operation.authority_code == "EPSG:1133"

    there = forward.transform([OSLO_XY])
    back = reverse.transform(there.coordinates[0])
    assert back.coordinates[0] == pytest.approx(OSLO_XY, abs=1e-6)


def test_projected_bound_crs_as_target_resolves_its_own_operation() -> None:
    """The same, with a projected base: the common real case in a register."""
    point = (600000.0, 6643000.0)  # ED50 / UTM zone 32N, offshore Norway
    bound = _bound_projected(CoordinateOperation.from_authority(*ED50_TO_WGS84))

    forward = Transformation(bound, WGS84_UTM32)
    reverse = Transformation(WGS84_UTM32, bound)

    assert reverse.operation.route is OperationRoute.BOUND
    assert reverse.operation.authority_code == "EPSG:1133"

    there = forward.transform([point])
    back = reverse.transform(there.coordinates[0])
    # A Helmert's inverse is computed rather than exact, so the roundtrip is
    # only precise to sub-millimetre, not bit-identical.
    assert back.coordinates[0] == pytest.approx(point, abs=1e-3)
