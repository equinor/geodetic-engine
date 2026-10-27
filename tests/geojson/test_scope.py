"""Where a document's positions are, and where they are to go."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pyproj import CRS

from geodetic_engine.geodesy import CoordinateReferenceSystem, UnresolvableCRSError
from geodetic_engine.geojson import (
    GeoJsonCrsError,
    InvalidGeoJsonError,
    UnsupportedGeoJsonError,
)
from geodetic_engine.geojson._model import Document, Flavour, read_document
from geodetic_engine.geojson._scope import (
    METRE,
    LengthUnit,
    is_wgs84_lonlat,
    length_unit,
    read_source,
    read_target,
)
from geodetic_engine.persistablereference import (
    CrsReference,
    parse_persistable_reference,
    to_persistable_reference,
)

ED50_UTM31 = to_persistable_reference(CRS.from_epsg(23031))
WGS84 = to_persistable_reference(CRS.from_epsg(4326))
# The service's own payload for metres, CRSConverter.METER.
SERVICE_METRE = (
    '{"scaleOffset":{"scale":1.0,"offset":0.0},"symbol":"m",'
    '"baseMeasurement":{"ancestry":"Length","type":"UM"},"type":"USO"}'
)
FEET_ID = "osdu:reference-data--UnitOfMeasure:ft:"
MISSING_ID = "osdu:reference-data--CoordinateReferenceSystem:Projected:EPSG::99999:"


def _crs(value: str) -> CoordinateReferenceSystem:
    return CoordinateReferenceSystem.from_user_input(value)


def _unit(
    symbol: str, scale: float, offset: float = 0.0, ancestry: str = "Length"
) -> str:
    return json.dumps(
        {
            "scaleOffset": {"scale": scale, "offset": offset},
            "symbol": symbol,
            "baseMeasurement": {"ancestry": ancestry, "type": "UM"},
            "type": "USO",
        }
    )


def _document(
    *positions: list[float], anycrs: bool = False, **members: Any
) -> Document:
    prefix = "AnyCrs" if anycrs else ""
    features = [
        {
            "type": f"{prefix}Feature",
            "properties": {},
            "geometry": {"type": f"{prefix}Point", "coordinates": position},
        }
        for position in positions
    ]
    return read_document(
        {"type": f"{prefix}FeatureCollection", "features": features, **members}
    )


def test_plain_geojson_is_crs84_and_its_heights_crs84h() -> None:
    source = read_source(_document([3.0, 60.0], [3.0, 60.0, 10.0]))
    assert source.crs is None
    assert source.crs_for(2) == _crs("OGC:CRS84")
    assert source.crs_for(3) == _crs("OGC:CRS84h")
    assert source.z_unit == METRE
    assert source.stated_by == "RFC 7946"


def test_source_crs_is_a_prior_arrangement_for_plain_geojson() -> None:
    source = read_source(_document([500000.0, 6600000.0]), "EPSG:25832")
    assert source.crs_for(2) == source.crs_for(3) == _crs("EPSG:25832")
    assert source.stated_by == "source_crs"


@pytest.mark.parametrize(
    "name",
    ["urn:ogc:def:crs:OGC:1.3:CRS84", "http://www.opengis.net/def/crs/OGC/1.3/CRS84"],
)
def test_a_legacy_crs_member_naming_crs84_is_read(name: str) -> None:
    member = {"type": "name", "properties": {"name": name}}
    document = _document([3.0, 60.0], crs=member)
    source = read_source(document)
    assert source.crs_for(2) == _crs("OGC:CRS84")
    assert source.stated_by == "$.crs"
    assert read_source(document, "OGC:CRS84").crs == _crs("OGC:CRS84")
    with pytest.raises(GeoJsonCrsError, match="names CRS84") as raised:
        read_source(document, "EPSG:25832")
    assert raised.value.path == "$.crs"


@pytest.mark.parametrize(
    "member",
    [
        {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25832"}},
        {"type": "link", "properties": {"href": "http://example.com/crs"}},
    ],
)
def test_a_legacy_crs_member_naming_another_crs_is_refused(member: Any) -> None:
    with pytest.raises(UnsupportedGeoJsonError, match="GeoJSON 2008") as raised:
        read_source(_document([500000.0, 6600000.0], crs=member))
    assert raised.value.path == "$.crs"


def test_plain_geojson_cannot_state_an_anycrs_crs() -> None:
    with pytest.raises(InvalidGeoJsonError, match="AnyCrs types") as raised:
        read_source(_document([3.0, 60.0], persistableReferenceCrs=ED50_UTM31))
    assert raised.value.path == "$.persistableReferenceCrs"
    nulls = _document([3.0, 60.0], persistableReferenceCrs=None, VerticalUnitID="")
    assert read_source(nulls).crs is None


def test_anycrs_geojson_states_its_crs() -> None:
    document = _document(
        [500000.0, 6600000.0], anycrs=True, persistableReferenceCrs=ED50_UTM31
    )
    source = read_source(document)
    assert source.crs == _crs("EPSG:23031")
    assert source.crs_for(3) is source.crs
    assert source.stated_by == "$.persistableReferenceCrs"
    assert source.z_unit is None


def test_the_record_id_takes_precedence_over_the_payload() -> None:
    document = _document(
        [500000.0, 6600000.0],
        anycrs=True,
        CoordinateReferenceSystemID=MISSING_ID,
        persistableReferenceCrs=ED50_UTM31,
    )
    with pytest.raises(UnresolvableCRSError) as raised:
        read_source(document)
    assert str(raised.value).startswith("$.CoordinateReferenceSystemID: ")


@pytest.mark.parametrize(
    ("stated", "given"),
    [(ED50_UTM31, "EPSG:23031"), (WGS84, "EPSG:4326")],
)
def test_source_crs_may_repeat_the_stated_crs(stated: str, given: str) -> None:
    document = _document([3.0, 60.0], anycrs=True, persistableReferenceCrs=stated)
    assert read_source(document, given).stated_by == "$.persistableReferenceCrs"


def test_source_crs_cannot_contradict_the_stated_crs() -> None:
    document = _document(
        [500000.0, 6600000.0], anycrs=True, persistableReferenceCrs=ED50_UTM31
    )
    with pytest.raises(GeoJsonCrsError, match="EPSG:32631") as raised:
        read_source(document, "EPSG:32631")
    assert raised.value.path == "$.persistableReferenceCrs"


def test_anycrs_geojson_without_a_crs_needs_source_crs() -> None:
    document = _document([500000.0, 6600000.0], anycrs=True)
    with pytest.raises(GeoJsonCrsError, match="states neither") as raised:
        read_source(document)
    assert raised.value.path == "$"
    source = read_source(document, "EPSG:23031")
    assert (source.crs, source.stated_by) == (_crs("EPSG:23031"), "source_crs")


def test_the_vertical_unit_id_takes_precedence_over_the_payload() -> None:
    document = _document(
        [500000.0, 6600000.0, 10.0],
        anycrs=True,
        persistableReferenceCrs=ED50_UTM31,
        VerticalUnitID=FEET_ID,
        persistableReferenceUnitZ=SERVICE_METRE,
    )
    assert read_source(document).z_unit == LengthUnit("ft", 0.3048)


def test_a_unit_payload_is_kept_with_its_offset() -> None:
    payload = _unit("odd", 2.0, 5.0)
    document = _document(
        [1.0, 2.0, 3.0], anycrs=True, persistableReferenceCrs=ED50_UTM31,
        persistableReferenceUnitZ=payload,
    )  # fmt: skip
    unit = read_source(document).z_unit
    assert unit is not None
    assert (unit.symbol, unit.scale, unit.offset, unit.payload) == (
        "odd",
        2.0,
        5.0,
        payload,
    )
    assert unit.to_metres(3.0) == 11.0
    assert unit.from_metres(11.0) == 3.0
    assert unit.reference() == payload


@pytest.mark.parametrize(
    ("member", "value", "reason"),
    [
        ("persistableReferenceUnitZ", _unit("degC", 1.0, 273.15, "Temperature"), "not a length"),
        ("VerticalUnitID", "osdu:reference-data--UnitOfMeasure:furlong:", "furlong"),
        ("VerticalUnitID", 12, "must be a string"),
        ("persistableReferenceCrs", 23031, "must be a string"),
    ],
)  # fmt: skip
def test_unreadable_members_are_named(member: str, value: Any, reason: str) -> None:
    members = {"persistableReferenceCrs": ED50_UTM31, member: value}
    with pytest.raises(InvalidGeoJsonError, match=reason) as raised:
        read_source(_document([1.0, 2.0, 3.0], anycrs=True, **members))
    assert raised.value.path == f"$.{member}"


@pytest.mark.parametrize(
    "member", ["VerticalCoordinateReferenceSystemID", "persistableReferenceVerticalCrs"]
)
def test_a_vertical_crs_is_refused_only_for_heights(member: str) -> None:
    members = {"persistableReferenceCrs": ED50_UTM31, member: "stated"}
    with pytest.raises(UnsupportedGeoJsonError, match="vertical CRS") as raised:
        read_source(_document([1.0, 2.0, 3.0], anycrs=True, **members))
    assert raised.value.path == f"$.{member}"
    assert read_source(_document([1.0, 2.0], anycrs=True, **members)).crs is not None


def test_anycrs_geojson_cannot_state_a_legacy_crs_member() -> None:
    member = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    document = _document(
        [1.0, 2.0], anycrs=True, persistableReferenceCrs=ED50_UTM31, crs=member
    )
    with pytest.raises(
        UnsupportedGeoJsonError, match="not in a GeoJSON 2008"
    ) as raised:
        read_source(document)
    assert raised.value.path == "$.crs"


@pytest.mark.parametrize(
    "target", ["EPSG:4326", "OGC:CRS84", "OGC:CRS84h", "EPSG:4979"]
)
def test_auto_keeps_plain_geojson_for_wgs84_longitude_and_latitude(target: str) -> None:
    resolved = read_target(target, source=Flavour.GEOJSON)
    assert resolved.flavour is Flavour.GEOJSON
    assert resolved.z_unit == METRE
    assert resolved.record_id is None


def test_auto_refuses_to_write_plain_geojson_in_another_crs() -> None:
    with pytest.raises(ValueError, match="output='anycrs'"):
        read_target("EPSG:23031", source=Flavour.GEOJSON)
    resolved = read_target("EPSG:23031", source=Flavour.GEOJSON, output="anycrs")
    assert resolved.flavour is Flavour.ANYCRS
    assert resolved.z_unit is None


def test_auto_keeps_anycrs_geojson_anycrs() -> None:
    assert read_target("EPSG:4326", source=Flavour.ANYCRS).flavour is Flavour.ANYCRS
    assert (
        read_target("EPSG:4326", source=Flavour.ANYCRS, output="geojson").flavour
        is Flavour.GEOJSON
    )


def test_outputs_are_checked() -> None:
    with pytest.raises(ValueError, match="RFC 7946"):
        read_target("EPSG:23031", source=Flavour.ANYCRS, output="geojson")
    with pytest.raises(ValueError, match="output must be one of"):
        read_target("EPSG:4326", source=Flavour.GEOJSON, output="jsonfg")  # type: ignore[arg-type]


def test_target_heights_are_in_the_unit_asked_for() -> None:
    feet = read_target("EPSG:23031", source=Flavour.ANYCRS, target_unit_z="ft")
    assert feet.z_unit == LengthUnit("ft", 0.3048)
    metres = read_target(
        "EPSG:4326", source=Flavour.GEOJSON, target_unit_z=SERVICE_METRE
    )
    assert metres.z_unit == METRE
    with pytest.raises(ValueError, match="in metres"):
        read_target("EPSG:4326", source=Flavour.GEOJSON, target_unit_z="ft")
    with pytest.raises(ValueError, match=r"^target_unit_z: "):
        read_target("EPSG:23031", source=Flavour.ANYCRS, target_unit_z="parsec")


def test_the_target_reference_is_the_payload_given() -> None:
    target = read_target(ED50_UTM31, source=Flavour.ANYCRS)
    assert target.crs_reference() == ED50_UTM31


@pytest.mark.parametrize("target", ["EPSG:23031", "OGC:CRS84"])
def test_the_target_reference_states_the_target(target: str) -> None:
    reference = read_target(target, source=Flavour.ANYCRS).crs_reference()
    stated = parse_persistable_reference(reference)
    assert isinstance(stated, CrsReference)
    assert stated.to_crs().equals(_crs(target).crs, ignore_axis_order=True)


def test_a_target_named_by_record_id_keeps_it() -> None:
    record = "osdu:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031:"
    try:
        target = read_target(record, source=Flavour.ANYCRS)
    except UnresolvableCRSError:
        pytest.skip("the proj.db in use records no OSDU ids")
    assert target.record_id == record
    assert target.crs == _crs("EPSG:23031")


@pytest.mark.parametrize("target", ["EPSG:23031", "23031", ED50_UTM31])
def test_a_target_named_otherwise_has_no_record_id(target: str) -> None:
    assert read_target(target, source=Flavour.ANYCRS).record_id is None


@pytest.mark.parametrize(
    ("crs", "expected"),
    [
        ("OGC:CRS84", True),
        ("OGC:CRS84h", True),
        ("EPSG:4326", True),
        ("EPSG:4979", True),
        (WGS84, True),
        ("EPSG:9057", True),
        ("EPSG:4230", False),
        ("EPSG:32631", False),
        ("EPSG:4978", False),
        ("EPSG:9705", False),
        ("+proj=longlat +ellps=intl +towgs84=-87,-98,-121 +no_defs +type=crs", False),
        ("+proj=longlat +datum=WGS84 +pm=paris +no_defs +type=crs", False),
        (
            'GEOGCRS["WGS 84 in grads",DATUM["World Geodetic System 1984",'
            'ELLIPSOID["WGS 84",6378137,298.257223563]],CS[ellipsoidal,2],'
            'AXIS["lat",north,ANGLEUNIT["grad",0.015707963267949]],'
            'AXIS["lon",east,ANGLEUNIT["grad",0.015707963267949]]]',
            False,
        ),
    ],
)
def test_what_rfc_7946_calls_wgs_84(crs: str, expected: bool) -> None:
    assert is_wgs84_lonlat(_crs(crs)) is expected


def test_metres_are_written_as_the_service_writes_them() -> None:
    assert METRE.reference() == SERVICE_METRE
    feet = json.loads(length_unit("ft").reference())
    assert feet["scaleOffset"] == {"scale": 0.3048, "offset": 0.0}
    assert feet["symbol"] == "ft"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ft", LengthUnit("ft", 0.3048)),
        ("osdu:reference-data--UnitOfMeasure:ftUS:", LengthUnit("ftUS", 1200 / 3937)),
        (SERVICE_METRE, METRE),
        (
            '{"abcd":{"a":0.0,"b":1200.0,"c":3937.0,"d":0.0},"symbol":"ftUS",'
            '"baseMeasurement":{"ancestry":"Length","type":"UM"},"type":"UAD"}',
            LengthUnit("ftUS", 1200 / 3937),
        ),
    ],
)
def test_length_units_are_read_as_elsewhere(value: str, expected: LengthUnit) -> None:
    assert length_unit(value) == expected


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "non-empty string"),
        (None, "non-empty string"),
        ("degC", "degC"),
        (
            '{"abcd":{"a":0.0,"b":1.0,"c":1.0,"d":1.0},"symbol":"odd",'
            '"baseMeasurement":{"ancestry":"Length","type":"UM"},"type":"UAD"}',
            "cannot read the unit",
        ),
    ],
)
def test_other_units_are_refused(value: Any, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        length_unit(value)
