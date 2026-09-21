"""An operation may be stated outright, not only named for PROJ to look up.

A code is a question put to PROJ's database; a persistableReference is an
answer already given. OSDU publishes the parameters themselves, and they are
the definition of record: a register may hold a different object under the same
code, or no object at all. So a stated operation has to be applied exactly as
written, which is what these tests pin down.

The discriminating case is the one where the two disagree. A payload whose
translation has been altered must move a point by that alteration; if the code
on the payload were being resolved instead, the alteration would vanish and the
result would silently be EPSG's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

import pytest
from pyproj import Geod
from pyproj.crs import CoordinateOperation

from geodetic_engine.geodesy import (
    OperationNotAvailableError,
    OperationRoute,
    transform,
)
from geodetic_engine.persistablereference import parse_persistable_reference

ED50 = "EPSG:4230"
WGS84 = "EPSG:4326"
ED50_UTM32 = "EPSG:23032"
WGS84_UTM32 = "EPSG:32632"

# The same two frames as ESRI writes them, with no authority code to look up.
ED50_ESRI = (
    'GEOGCS["GCS_European_1950",DATUM["D_European_1950",'
    'SPHEROID["International_1924",6378388.0,297.0]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)
WGS84_ESRI = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)

# Inside ED50's area of use, in xy order.
POINT = (7.5, 57.0)

# EPSG:1133's own X translation, and one differing by a round 10 m so that the
# difference shows up far above any rounding.
PUBLISHED_X = -87.0
ALTERED_X = -77.0


def payload(x_translation: float = PUBLISHED_X) -> str:
    """An OSDU payload for ED50 to WGS 84 (1), with its X translation settable."""
    return json.dumps(
        {
            "authCode": {"auth": "EPSG", "code": "1133"},
            "name": "ED_1950_To_WGS_1984_1",
            "type": "ST",
            "ver": "PE_10_9_1",
            "wkt": (
                'GEOGTRAN["ED_1950_To_WGS_1984_1",'
                'GEOGCS["GCS_European_1950",DATUM["D_European_1950",'
                'SPHEROID["International_1924",6378388.0,297.0]],'
                'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
                'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
                'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
                'METHOD["Geocentric_Translation"],'
                f'PARAMETER["X_Axis_Translation",{x_translation}],'
                'PARAMETER["Y_Axis_Translation",-98.0],'
                'PARAMETER["Z_Axis_Translation",-121.0],'
                'OPERATIONACCURACY[10.0],AUTHORITY["EPSG",1133]]'
            ),
        }
    )


def geogtran(x_translation: float = PUBLISHED_X) -> str:
    """The same transformation as a bare ESRI ``GEOGTRAN``, with no envelope."""
    return (
        'GEOGTRAN["ED_1950_To_WGS_1984_1",'
        f"{ED50_ESRI},{WGS84_ESRI},"
        'METHOD["Geocentric_Translation"],'
        f'PARAMETER["X_Axis_Translation",{x_translation}],'
        'PARAMETER["Y_Axis_Translation",-98.0],'
        'PARAMETER["Z_Axis_Translation",-121.0],'
        'OPERATIONACCURACY[10.0],AUTHORITY["EPSG",1133]]'
    )


def test_a_stated_payload_agrees_with_the_code_it_stamps_on_itself() -> None:
    """Where the parameters do agree with the register, so must the answers."""
    stated = transform(ED50, WGS84, POINT, operation=payload())
    named = transform(ED50, WGS84, POINT, operation="EPSG:1133")

    assert stated.coordinates[0] == pytest.approx(named.coordinates[0], abs=1e-9)


def test_a_twice_encoded_operation_payload_is_recognised() -> None:
    assert transform(
        ED50, WGS84, POINT, operation=quote(quote(payload()))
    ).coordinates == (transform(ED50, WGS84, POINT, operation=payload()).coordinates)


def test_a_parsed_reference_is_accepted_as_readily_as_its_payload() -> None:
    """Having already read a payload should not mean serialising it again."""
    parsed = parse_persistable_reference(payload())

    assert transform(ED50, WGS84, POINT, operation=parsed).coordinates == (
        transform(ED50, WGS84, POINT, operation=payload()).coordinates
    )


def test_the_stated_parameters_are_applied_and_not_the_published_ones() -> None:
    """The whole point: what the payload says is what runs.

    Moving the X translation 10 m east must move the result, and by about that
    much. An implementation that resolved the payload's authority code instead
    would return EPSG's answer and this would not move at all.
    """
    published = transform(ED50, WGS84, POINT, operation=payload())
    altered = transform(ED50, WGS84, POINT, operation=payload(ALTERED_X))

    first_lon, first_lat = published.coordinates[0]
    second_lon, second_lat = altered.coordinates[0]
    _, _, apart_m = Geod(ellps="WGS84").inv(
        first_lon, first_lat, second_lon, second_lat
    )

    # A 10 m change in the geocentric X translation carries several metres of
    # it into the horizontal at this latitude. The distance is not pinned down
    # exactly because the claim is only that the alteration survived: resolving
    # the payload's authority code instead would not move the point at all.
    assert 5.0 < apart_m < 15.0


def test_a_stated_operation_reports_itself_as_what_was_applied() -> None:
    """Provenance has to name the payload's operation, not a database entry."""
    applied = transform(ED50, WGS84, POINT, operation=payload()).operation

    assert applied.name == "ED_1950_To_WGS_1984_1"
    assert applied.route is OperationRoute.CHAINED


def test_a_stated_operation_runs_backwards_when_the_pair_is_reversed() -> None:
    """A datum shift is published one way and inverted to run the other."""
    there = transform(ED50, WGS84, POINT, operation=payload())
    back = transform(WGS84, ED50, there.coordinates[0], operation=payload())

    assert back.coordinates[0] == pytest.approx(POINT, abs=1e-7)
    assert back.operation.name == "Inverse of ED_1950_To_WGS_1984_1"


def test_a_geographic_operation_is_chained_between_projected_ends() -> None:
    """The shift is stated between geographic CRSs; the caller may be in UTM."""
    projected = transform(
        ED50_UTM32, WGS84_UTM32, (500000.0, 6818644.447), operation=payload()
    )
    geographic = transform(ED50, WGS84, POINT, operation=payload())

    assert projected.operation.name == geographic.operation.name
    assert projected.coordinates[0] != pytest.approx((500000.0, 6818644.447), abs=1e-3)


def test_a_payload_stating_a_crs_is_not_an_operation() -> None:
    """A CRS where an operation belongs is a mistake worth naming as one."""
    crs_payload = json.dumps(
        {
            "authCode": {"auth": "EPSG", "code": "4230"},
            "name": "GCS_European_1950",
            "type": "LBC",
            "ver": "PE_10_9_1",
            "wkt": (
                'GEOGCS["GCS_European_1950",DATUM["D_European_1950",'
                'SPHEROID["International_1924",6378388.0,297.0]],'
                'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433],'
                'AUTHORITY["EPSG",4230]]'
            ),
        }
    )

    with pytest.raises(ValueError, match="not a coordinate operation"):
        transform(ED50, WGS84, POINT, operation=crs_payload)


def test_a_stated_operation_cannot_be_combined_with_a_named_one() -> None:
    """Several references are chosen among PROJ's candidates; this is not one."""
    with pytest.raises(OperationNotAvailableError, match="on its own"):
        transform(ED50, WGS84, POINT, operation=[payload(), "EPSG:1133"])


def test_a_bare_geogtran_states_the_same_operation_as_its_payload() -> None:
    """The envelope carries the WKT; without one the WKT still states it."""
    from_wkt = transform(ED50, WGS84, POINT, operation=geogtran())
    from_payload = transform(ED50, WGS84, POINT, operation=payload())

    assert from_wkt.coordinates[0] == pytest.approx(
        from_payload.coordinates[0], abs=1e-9
    )
    assert from_wkt.operation.name == "ED_1950_To_WGS_1984_1"


def test_a_bare_geogtran_applies_its_own_parameters() -> None:
    """PROJ cannot read a GEOGTRAN at all, so this proves it was translated."""
    published = transform(ED50, WGS84, POINT, operation=geogtran())
    altered = transform(ED50, WGS84, POINT, operation=geogtran(ALTERED_X))

    first_lon, first_lat = published.coordinates[0]
    second_lon, second_lat = altered.coordinates[0]
    _, _, apart_m = Geod(ellps="WGS84").inv(
        first_lon, first_lat, second_lon, second_lat
    )

    assert 5.0 < apart_m < 15.0


def test_every_end_may_be_esri_wkt_with_no_authority_code_anywhere() -> None:
    """A register that publishes only ESRI WKT can still be transformed with."""
    stated = transform(ED50_ESRI, WGS84_ESRI, POINT, operation=geogtran())
    named = transform(ED50, WGS84, POINT, operation="EPSG:1133")

    assert stated.coordinates[0] == pytest.approx(named.coordinates[0], abs=1e-9)


@pytest.mark.parametrize("end", ["source", "target"])
def test_stated_operations_do_not_bridge_custom_ensemble_suffixes(end: str) -> None:
    original = (
        'GEOGCS["Custom CRS",DATUM["Example",'
        'SPHEROID["Custom",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
    )
    unrelated = original.replace('"Example"', '"Example ensemble"').replace(
        "6378137", "6378000"
    )
    if end == "source":
        operation = geogtran().replace(ED50_ESRI, original)
        source, target = unrelated, WGS84_ESRI
        message = "neither of which shares a datum"
    else:
        operation = geogtran().replace(WGS84_ESRI, original)
        source, target = ED50_ESRI, unrelated
        message = "additional, unrequested datum change"
    with pytest.raises(OperationNotAvailableError, match=message):
        transform(source, target, POINT, operation=operation)


@pytest.mark.parametrize("as_payload", [False, True])
def test_a_vertical_esri_transformation_is_refused_as_unmodelled(
    as_payload: bool,
) -> None:
    """VERTTRAN is read far enough to say plainly that it is not modelled."""
    verttran = 'VERTTRAN["Some_Vertical_Shift",PARAMETER["Vertical_Shift",1.0]]'
    operation = json.dumps({"wkt": verttran}) if as_payload else verttran

    with pytest.raises(ValueError, match="does not model"):
        transform(ED50, WGS84, POINT, operation=operation)


@pytest.mark.parametrize("as_sequence", [False, True])
def test_mutable_stated_operations_are_not_cached(as_sequence: bool) -> None:
    @dataclass
    class MutableOperation:
        x_translation: float

        def to_operation(self) -> CoordinateOperation:
            from geodetic_engine.persistablereference import operation_from_geogtran

            return operation_from_geogtran(geogtran(self.x_translation))

    stated = MutableOperation(PUBLISHED_X)
    operation = [stated] if as_sequence else stated
    before = transform(ED50, WGS84, POINT, operation=operation)
    stated.x_translation = ALTERED_X
    after = transform(ED50, WGS84, POINT, operation=operation)

    assert (
        before.coordinates
        == transform(ED50, WGS84, POINT, operation=payload(PUBLISHED_X)).coordinates
    )
    assert (
        after.coordinates
        == transform(ED50, WGS84, POINT, operation=payload(ALTERED_X)).coordinates
    )
    assert before.coordinates != after.coordinates
