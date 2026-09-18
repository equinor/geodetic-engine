"""A persistableReference works anywhere a CRS does.

The payload is a CRS definition like any other as far as a caller is concerned:
it goes into :func:`~geodetic_engine.geodesy.transform` and comes out as
coordinates. What makes it worth its own tests is that a bound payload settles
the datum shift, so a pair of CRSs this package would otherwise refuse as
ambiguous transforms without the caller choosing an operation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    CoordinateReferenceSystem,
    UnresolvableCRSError,
    transform,
)

_PAYLOADS = Path(__file__).parents[1] / "persistablereference" / "payloads.jsonl"


@pytest.fixture(scope="module")
def payloads() -> dict[str, str]:
    """The same committed payloads the reference tests read."""
    with _PAYLOADS.open(encoding="utf-8") as stream:
        return {
            entry["case"]: entry["payload"]
            for entry in (json.loads(line) for line in stream if line.strip())
        }


def test_a_payload_resolves_as_a_crs(payloads: dict[str, str]) -> None:
    """from_user_input recognises one without being told what it is."""
    crs = CoordinateReferenceSystem.from_user_input(
        payloads["ebc_projected_position_vector"]
    )
    assert crs.name == "ED50 / UTM zone 32N"
    assert crs.axis_abbreviations == ("E", "N")


def test_resolving_a_payload_is_cached(payloads: dict[str, str]) -> None:
    """Reading one is expensive, and callers pass the same string repeatedly."""
    payload = payloads["lbc_projected"]
    assert CoordinateReferenceSystem.from_user_input(
        payload
    ) is CoordinateReferenceSystem.from_persistable_reference(payload)


def test_a_bound_payload_settles_the_datum_shift(payloads: dict[str, str]) -> None:
    """The same CRS named by its code leaves a choice this package will not make."""
    with pytest.raises(AmbiguousOperationError):
        transform("EPSG:23032", "EPSG:4326", [(500000.0, 6600000.0)])

    result = transform(
        payloads["ebc_projected_position_vector"],
        "EPSG:4326",
        [(500000.0, 6600000.0)],
    )
    assert result.operation.name == "ED_1950_To_WGS_1984_23"
    assert result.coordinates[0] == pytest.approx(
        (8.998592586917425, 59.53649895928227)
    )


def test_a_bound_geographic_payload_settles_it_too(payloads: dict[str, str]) -> None:
    """A geographic base binds as readily as a projected one.

    Worth its own test: ESRI states no axis order inside a ``GEOGTRAN``, so the
    frames it goes between used to come back differing from the identically
    defined base CRS beside them. PROJ reconciled that with a null offset, and
    an undeclared operation in the chain made every bound geographic payload
    look like an ambiguous datum change.
    """
    result = transform(payloads["ebc_geographic_via_1133"], "EPSG:4326", [(9.0, 59.5)])
    assert result.operation.name == "ED_1950_To_WGS_1984_1"
    assert result.coordinates[0] == pytest.approx(
        (8.998531385165236, 59.49951341965447)
    )


def test_both_ends_may_be_stated_by_reference(payloads: dict[str, str]) -> None:
    """Neither CRS has to be named by code for the transformation to resolve."""
    result = transform(
        payloads["ebc_projected_via_1133"],
        payloads["lbc_wgs84_geographic"],
        [(500000.0, 6600000.0)],
    )
    assert result.operation.name == "ED_1950_To_WGS_1984_1"
    assert result.coordinates[0] == pytest.approx(
        (8.998529777000586, 59.536487235139475)
    )


def test_coordinate_values_stay_in_xy_order(payloads: dict[str, str]) -> None:
    """The contract does not change because the CRS arrived as a payload."""
    result = transform(
        payloads["ebc_projected_position_vector"],
        "EPSG:4326",
        [(500000.0, 6600000.0)],
    )
    assert result.target_axes == ("Lat", "Lon")
    assert result.coordinate_order == "xy"
    longitude, latitude = result.coordinates[0]
    assert 8.0 < longitude < 10.0
    assert 59.0 < latitude < 60.0


def test_a_payload_stating_a_transformation_is_not_a_crs(
    payloads: dict[str, str],
) -> None:
    """The error says what was given rather than that PROJ did not like it."""
    with pytest.raises(UnresolvableCRSError, match="not a CRS"):
        CoordinateReferenceSystem.from_user_input(payloads["st_position_vector"])


def test_an_unreadable_payload_fails_as_a_crs_would(payloads: dict[str, str]) -> None:
    """However a payload is broken, resolving it raises the CRS failure."""
    with pytest.raises(UnresolvableCRSError):
        CoordinateReferenceSystem.from_user_input('{"type":"LBC"}')


def test_ordinary_definitions_still_resolve() -> None:
    """Recognising payloads must not change what everything else does."""
    assert CoordinateReferenceSystem.from_user_input("EPSG:4326").authority_code == (
        "EPSG:4326"
    )


def test_a_pyproj_crs_still_resolves() -> None:
    """A CRS object reaches this package as PROJJSON, which also opens with a brace.

    Claiming it as a payload would refuse every pyproj CRS handed in, including
    the bound ones ``transform()`` builds internally from its own cache key.
    """
    from pyproj import CRS
    from pyproj.crs import BoundCRS, CoordinateOperation

    bound = BoundCRS(
        CRS.from_epsg(23032),
        CRS.from_epsg(4326),
        CoordinateOperation.from_authority("EPSG", "1133"),
    )
    assert CoordinateReferenceSystem.from_user_input(bound).crs.is_bound
    assert CoordinateReferenceSystem.from_user_input(CRS.from_epsg(4326).to_json())
    assert transform(bound, "EPSG:4326", [(500000.0, 6600000.0)]).coordinates
