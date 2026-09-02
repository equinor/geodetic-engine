"""Collapsing a Helmert chain must not change where the coordinates land.

The composition algebra is only a proposal; what makes it trustworthy is that
every collapse reproduces PROJ's own rendering of the original chain. These
tests check that on the published dataset rather than on hand-built fixtures,
because the failure this guards against is a convention or unit error that a
fixture written from the same misunderstanding would not catch.
"""

from __future__ import annotations

import math

import pytest
from pyproj import CRS, Transformer
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy.errors import NotCollapsibleError
from geodetic_engine.geodesy.utils import (
    collapse_concatenated,
    compose,
    helmert_parameters,
    is_collapsible,
)

# ED50 to WGS 84 (15): two Position Vector Helmerts through ED87. Superseded,
# which is why it is a stable example -- it will not be revised again.
CHAIN = ("EPSG", 8047)
FIRST_STEP = ("EPSG", 1147)  # ED50 to ED87 (2)
SECOND_STEP = ("EPSG", 1146)  # ED87 to WGS 84 (1)

# Norwegian North Sea, comfortably inside the chain's area of use.
SAMPLE_XY = (2.5, 56.0)


def _operation(reference: tuple[str, int]) -> CoordinateOperation:
    return CoordinateOperation.from_authority(*reference)


def test_rotation_unit_is_read_from_the_parameter_not_assumed() -> None:
    """EPSG states these rotations in microradians, not arc-seconds.

    Reading the value without applying its own conversion factor would scale
    every rotation by 4.85, which is the single most likely way to get a
    plausible but wrong collapsed transformation.
    """
    operation = _operation(FIRST_STEP)
    units = {int(p.code): p.unit_name for p in operation.params}
    assert units[8608] == "microradian"

    parameters = helmert_parameters(operation)
    assert parameters is not None
    # -1.893 microradian expressed in arc-seconds.
    assert parameters.rotations_arc_seconds()[0] == pytest.approx(
        -0.390459278, abs=1e-9
    )


def test_composition_matches_the_published_chain() -> None:
    """The composed parameters are the ones EPSG's chain implies."""
    first = helmert_parameters(_operation(FIRST_STEP))
    second = helmert_parameters(_operation(SECOND_STEP))
    assert first is not None and second is not None

    combined = compose(first, second)

    assert combined.tx == pytest.approx(-84.491, abs=1e-6)
    assert combined.ty == pytest.approx(-100.559002, abs=1e-6)
    assert combined.tz == pytest.approx(-114.208998, abs=1e-6)
    assert combined.scale_ppm() == pytest.approx(0.2947, abs=1e-6)


def test_collapsed_chain_reproduces_the_original() -> None:
    """The collapsed operation must move points where the chain moves them."""
    original = _operation(CHAIN)
    collapsed = collapse_concatenated(original)

    reference = Transformer.from_pipeline(original.to_wkt())
    candidate = Transformer.from_pipeline(collapsed.to_wkt())
    geod = CRS.from_epsg(4326).get_geod()

    longitude, latitude = SAMPLE_XY
    got = reference.transform(latitude, longitude)
    expected = candidate.transform(latitude, longitude)
    _, _, separation = geod.inv(got[1], got[0], expected[1], expected[0])

    # A published datum shift is accurate to metres; agreement to a millimetre
    # proves the collapse is the same transformation, not merely a close one.
    assert abs(separation) < 1e-3


def test_collapsed_operation_keeps_the_accuracy_and_the_endpoints() -> None:
    """Provenance survives the rewrite, or the result cannot be audited."""
    original = _operation(CHAIN)
    collapsed = collapse_concatenated(original)

    assert collapsed.accuracy == original.accuracy
    assert collapsed.method_code == "9606"
    assert "collapsed" in collapsed.name


def test_collapsed_operation_can_be_bound() -> None:
    """The whole point: PROJ can embed the collapsed form, not the chain."""
    original = _operation(CHAIN)

    with pytest.raises(Exception):  # noqa: B017 - pyproj raises CRSError here
        BoundCRS(CRS.from_epsg(4230), CRS.from_epsg(4326), original)

    bound = BoundCRS(
        CRS.from_epsg(4230), CRS.from_epsg(4326), collapse_concatenated(original)
    )
    assert bound.is_bound


def test_a_single_step_operation_is_not_collapsible() -> None:
    """There is nothing to collapse, and pretending otherwise would hide that."""
    operation = _operation(FIRST_STEP)
    assert not is_collapsible(operation)
    with pytest.raises(NotCollapsibleError, match="at least"):
        collapse_concatenated(operation)


def test_a_grid_chain_is_refused() -> None:
    """A chain containing anything but a plain Helmert must be refused.

    Composing a grid interpolation into a Helmert is not possible at all, so
    the only correct outcome is to decline rather than approximate it.
    """
    grid_chain = next(
        (
            operation
            for code in _concatenated_codes()
            if (operation := _safe_operation(code)) is not None
            and _has_grid_step(operation)
        ),
        None,
    )
    if grid_chain is None:
        pytest.skip("no grid-bearing concatenated operation in this EPSG release")

    assert not is_collapsible(grid_chain)
    with pytest.raises(NotCollapsibleError, match="not a plain Helmert"):
        collapse_concatenated(grid_chain)


@pytest.mark.dataset
def test_every_collapsible_chain_in_epsg_is_faithful() -> None:
    """Sweep the dataset: no collapse may move a coordinate by over a millimetre.

    This is the test that would fail if the rotation convention, the unit
    handling or the composition order were wrong for some family of operations
    that the single worked example happens not to cover.
    """
    geod = CRS.from_epsg(4326).get_geod()
    checked = 0
    for code in _concatenated_codes():
        operation = _safe_operation(code)
        if operation is None or not is_collapsible(operation):
            continue
        collapsed = collapse_concatenated(operation)
        area = operation.area_of_use
        if area is None:
            continue
        reference = Transformer.from_pipeline(operation.to_wkt())
        candidate = Transformer.from_pipeline(collapsed.to_wkt())
        latitude = (area.south + area.north) / 2
        longitude = (area.west + area.east) / 2
        got = reference.transform(latitude, longitude)
        expected = candidate.transform(latitude, longitude)
        if not all(math.isfinite(value) for value in (*got, *expected)):
            continue
        _, _, separation = geod.inv(got[1], got[0], expected[1], expected[0])
        assert abs(separation) < 1e-3, f"EPSG:{code} moved by {separation} m"
        checked += 1

    assert checked > 10, f"only {checked} chains exercised; the sweep proved little"


def _concatenated_codes() -> list[int]:
    """Every EPSG concatenated operation code in the installed database."""
    import os
    import sqlite3

    import pyproj

    database = os.path.join(pyproj.datadir.get_data_dir(), "proj.db")
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT code FROM concatenated_operation WHERE auth_name = 'EPSG'"
            )
        ]


def _safe_operation(code: int) -> CoordinateOperation | None:
    """Load an operation, or None when PROJ cannot build it at all."""
    try:
        return CoordinateOperation.from_authority("EPSG", code)
    except Exception:
        return None


def _has_grid_step(operation: CoordinateOperation) -> bool:
    """Whether any step of a chain reads a grid file."""
    steps = operation.to_json_dict().get("steps") or []
    return any(
        "grid" in str(step.get("method", {}).get("name", "")).lower() for step in steps
    )
