"""A bound CRS applies the shift it was bound to, whichever kinds its ends are.

A bound CRS names one transformation, and that is the whole point of it: the
datum change is settled by the definition rather than chosen later. PROJ has
not always honoured that. Up to at least PROJ 9.4 a ``BoundCRS`` transformed
towards a *projected* target by quietly picking whichever operation it judged
best, discarding the one the CRS carried and reporting nothing
(https://github.com/pyproj4/pyproj/issues/1467). The same call towards a
geographic target used the bound operation correctly, so the failure was
invisible unless the two were compared.

It does not reproduce on the PROJ this package is pinned to, which is exactly
why it is worth pinning down: a future PROJ could reintroduce it, and the
symptom is coordinates that are plausible and a couple of metres wrong rather
than an error.

Every combination of geographic and projected ends is checked, and each is
checked twice over:

* against a route computed step by step -- unproject, apply the named shift,
  reproject -- where the datum change is named explicitly and nothing is left
  for PROJ to choose;
* against the same route through a *different* published shift, which the
  result must not match. Agreeing with the right answer proves little if every
  answer is close; disagreeing with the wrong one is what shows the binding is
  being read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pyproj import CRS

from geodetic_engine.geodesy import transform
from geodetic_engine.persistablereference import (
    CrsReference,
    OperationReference,
    parse_persistable_reference,
)

# ED50 to WGS 84 (23), seven parameters, and ED50 to WGS 84 (1), three. Both are
# published for the same pair and land about 4.5 m apart in the North Sea.
BOUND_SHIFT = "st_position_vector"
OTHER_SHIFT = "st_geocentric_translation"

_PAYLOADS = Path(__file__).parent / "payloads.jsonl"


@pytest.fixture(scope="module")
def payload() -> dict[str, str]:
    """The committed payloads, by case."""
    with _PAYLOADS.open(encoding="utf-8") as stream:
        return {
            entry["case"]: entry["payload"]
            for entry in (json.loads(line) for line in stream if line.strip())
        }


def _crs(payloads: dict[str, str], case: str) -> CrsReference:
    reference = parse_persistable_reference(payloads[case])
    assert isinstance(reference, CrsReference)
    return reference


def _shift(payloads: dict[str, str], case: str) -> OperationReference:
    reference = parse_persistable_reference(payloads[case])
    assert isinstance(reference, OperationReference)
    return reference


def _step_by_step(
    source: str, target: str, shift: str, point: tuple[float, float]
) -> tuple[float, ...]:
    """The same journey with every datum change named, as an oracle.

    Unprojecting and reprojecting change representation rather than datum, so
    neither leaves PROJ a choice to make. The one datum change in the middle is
    named outright. Nothing here can silently pick a different operation.
    """
    source_crs, target_crs = CRS.from_user_input(source), CRS.from_user_input(target)
    geographic = source_crs.geodetic_crs.to_authority()
    ed50 = f"{geographic[0]}:{geographic[1]}"

    moving: list[tuple[float, ...]] = [point]
    if source_crs.is_projected:
        moving = list(transform(source, ed50, moving).coordinates)
    moving = list(transform(ed50, "EPSG:4326", moving, operation=shift).coordinates)
    if target_crs.is_projected:
        moving = list(transform("EPSG:4326", target, moving).coordinates)
    return moving[0]


# Source CRS payload, its authority code, target payload, target code, and a
# point inside ED50 / UTM zone 32N's area of use in the source's own units.
DIRECTIONS = [
    pytest.param(
        "lbc_ed50_geographic",
        "EPSG:4230",
        "lbc_wgs84_geographic",
        "EPSG:4326",
        (4.5, 65.0),
        id="geographic-to-geographic",
    ),
    pytest.param(
        "lbc_ed50_geographic",
        "EPSG:4230",
        "lbc_wgs84_utm32n",
        "EPSG:32632",
        (4.5, 65.0),
        id="geographic-to-projected",
    ),
    pytest.param(
        "lbc_ed50_utm32n",
        "EPSG:23032",
        "lbc_wgs84_geographic",
        "EPSG:4326",
        (572742.772, 7234299.211),
        id="projected-to-geographic",
    ),
    pytest.param(
        "lbc_ed50_utm32n",
        "EPSG:23032",
        "lbc_wgs84_utm32n",
        "EPSG:32632",
        (572742.772, 7234299.211),
        id="projected-to-projected",
    ),
]


@pytest.mark.parametrize(
    ("source", "source_code", "target", "target_code", "point"), DIRECTIONS
)
def test_the_bound_shift_is_the_one_applied(
    source: str,
    source_code: str,
    target: str,
    target_code: str,
    point: tuple[float, float],
    payload: dict[str, str],
) -> None:
    """The bound CRS lands where the explicitly named shift lands."""
    bound = _crs(payload, source).to_bound_crs(_shift(payload, BOUND_SHIFT))
    result = transform(bound, payload[target], [point])

    expected = _step_by_step(source_code, target_code, "EPSG:1612", point)
    assert result.coordinates[0] == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(
    ("source", "source_code", "target", "target_code", "point"), DIRECTIONS
)
def test_a_different_shift_is_not_substituted(
    source: str,
    source_code: str,
    target: str,
    target_code: str,
    point: tuple[float, float],
    payload: dict[str, str],
) -> None:
    """Binding one shift does not land where another published shift would.

    This is the half that catches a silently discarded binding. The two shifts
    are both real, both published for this pair, and far enough apart that
    applying the wrong one cannot pass for rounding.
    """
    bound = _crs(payload, source).to_bound_crs(_shift(payload, BOUND_SHIFT))
    result = transform(bound, payload[target], [point])

    other = _step_by_step(source_code, target_code, "EPSG:1133", point)
    apart = max(abs(a - b) for a, b in zip(result.coordinates[0], other, strict=True))
    assert apart > 1e-5, (
        f"binding EPSG:1612 landed on EPSG:1133's answer for "
        f"{source_code} -> {target_code}"
    )


@pytest.mark.parametrize(
    ("source", "source_code", "target", "target_code", "point"), DIRECTIONS
)
def test_the_applied_operation_is_reported(
    source: str,
    source_code: str,
    target: str,
    target_code: str,
    point: tuple[float, float],
    payload: dict[str, str],
) -> None:
    """The result names the bound operation rather than whatever PROJ preferred.

    Reported under the name the payload gave it, not EPSG's name for the same
    operation, so a reader can match it against the record they were handed.
    """
    bound = _crs(payload, source).to_bound_crs(_shift(payload, BOUND_SHIFT))
    applied = transform(bound, payload[target], [point]).operation.name

    assert applied == _shift(payload, BOUND_SHIFT).name == "ED_1950_To_WGS_1984_23"


@pytest.mark.parametrize(
    ("source", "source_code", "target", "target_code", "point"), DIRECTIONS
)
def test_an_early_bound_payload_agrees_with_one_bound_here(
    source: str,
    source_code: str,
    target: str,
    target_code: str,
    point: tuple[float, float],
    payload: dict[str, str],
) -> None:
    """Binding at use time gives what OSDU states in one piece.

    The catalogue publishes both shapes, and a reader should not be able to
    tell from the coordinates which one it was handed.
    """
    here = _crs(payload, source).to_bound_crs(_shift(payload, BOUND_SHIFT))
    packaged = _crs(
        payload,
        "ebc_projected_position_vector"
        if source_code == "EPSG:23032"
        else "ebc_geographic_via_1612",
    ).to_crs()

    assert transform(here, payload[target], [point]).coordinates == (
        transform(packaged, payload[target], [point]).coordinates
    )


@pytest.mark.parametrize(
    "case", ["ebc_geographic_via_1612", "ebc_projected_position_vector"]
)
def test_rebinding_a_payload_that_states_its_own_shift_is_refused(
    case: str, payload: dict[str, str]
) -> None:
    """An early bound payload will not quietly have its shift swapped.

    The payload settled the datum change already. Accepting another would give
    coordinates metres from what the record states, under a CRS that still
    looks like the one that arrived.
    """
    already_bound = _crs(payload, case)
    assert already_bound.is_bound

    with pytest.raises(ValueError, match="already states the transformation"):
        already_bound.to_bound_crs(_shift(payload, OTHER_SHIFT))


@pytest.mark.parametrize(
    "case", ["ebc_geographic_via_1612", "ebc_projected_position_vector"]
)
def test_the_late_bound_crs_inside_can_be_rebound_deliberately(
    case: str, payload: dict[str, str]
) -> None:
    """Reaching for the unbound CRS inside is the way to choose another shift."""
    inner = _crs(payload, case).late_bound
    assert inner is not None and not inner.is_bound

    rebound = inner.to_bound_crs(_shift(payload, OTHER_SHIFT))
    assert rebound.is_bound
