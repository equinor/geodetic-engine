"""The JSON envelope: how it is encoded, how it drifts, and what it states."""

from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from geodetic_engine.persistablereference import (
    Kind,
    MalformedReferenceError,
    UnsupportedReferenceError,
    looks_like_reference,
)
from geodetic_engine.persistablereference.envelope import decode

LATE_BOUND = {
    "type": "LBC",
    "name": "GCS_WGS_1984",
    "ver": "PE_10_9_1",
    "authCode": {"auth": "EPSG", "code": "4326"},
    "wkt": 'GEOGCS["GCS_WGS_1984"]',
}


def test_reads_what_the_envelope_states() -> None:
    """Kind, name, authority code and version all come off the envelope."""
    envelope = decode(json.dumps(LATE_BOUND))
    assert envelope.kind is Kind.LATE_BOUND_CRS
    assert envelope.name == "GCS_WGS_1984"
    assert str(envelope.authority_code) == "EPSG:4326"
    assert envelope.version == "PE_10_9_1"


@pytest.mark.parametrize("rounds", [0, 1, 2])
def test_url_encoding_is_undone(rounds: int) -> None:
    """A payload encoded on its way through a service still reads."""
    text = json.dumps(LATE_BOUND)
    for _ in range(rounds):
        text = quote(text)
    assert decode(text).kind is Kind.LATE_BOUND_CRS
    assert looks_like_reference(text)


def test_decoding_stops_rather_than_running_to_a_fixed_point() -> None:
    """A percent inside a name is left alone, not decoded into something else."""
    named = dict(LATE_BOUND, name="grid 50% coverage")
    assert decode(json.dumps(named)).name == "grid 50% coverage"


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("lateBoundCRS", Kind.EARLY_BOUND_CRS),
        ("cts", Kind.CONCATENATED_TRANSFORMATION),
        ("scaleOffset", Kind.UNIT_SCALE_OFFSET),
        ("abcd", Kind.UNIT_ABCD),
    ],
)
def test_kind_is_settled_by_members_when_type_is_absent(
    key: str, expected: Kind
) -> None:
    """Producers omit ``type``; what the payload carries then decides."""
    assert decode(json.dumps({key: {}})).kind is expected


@pytest.mark.parametrize("keyword", ["GEOGTRAN", "VERTTRAN", "  verttran"])
def test_a_transformation_is_recognised_by_its_wkt(keyword: str) -> None:
    """A payload stating only WKT is told apart by the dialect keyword."""
    transformation = json.dumps({"wkt": f'{keyword}["a"]'})
    assert decode(transformation).kind is Kind.TRANSFORMATION
    assert decode(json.dumps({"wkt": 'GEOGCS["a"]'})).kind is Kind.LATE_BOUND_CRS


def test_key_casing_does_not_matter() -> None:
    """Producers disagree on it, so keys are matched without case."""
    assert decode('{"Type":"USO","ScaleOffset":{}}').kind is Kind.UNIT_SCALE_OFFSET


@pytest.mark.parametrize(
    "value",
    ["EPSG:4326", "", 'GEOGCS["a"]', "+proj=utm +zone=32"],
)
def test_ordinary_crs_definitions_are_not_mistaken_for_payloads(value: str) -> None:
    """The check runs on every definition a caller passes in, so it must not misfire."""
    assert not looks_like_reference(value)


@pytest.mark.parametrize("code", [4326, 23032, 4979])
def test_projjson_is_not_mistaken_for_a_payload(code: int) -> None:
    """PROJJSON opens as a JSON object too, and is a CRS definition in its own right.

    Claiming one would stop it resolving, and every pyproj CRS object handed to
    this package arrives as PROJJSON.
    """
    from pyproj import CRS

    assert not looks_like_reference(CRS.from_epsg(code).to_json())


def test_a_payload_with_no_recognisable_member_is_not_claimed() -> None:
    """Opening brace and all, a document stating none of these members is not one."""
    assert not looks_like_reference('{"type":"GeographicCRS","name":"WGS 84"}')


@pytest.mark.parametrize("name", ["wkt", "authCode", "lateBoundCRS"])
def test_values_are_not_mistaken_for_reference_keys(name: str) -> None:
    assert not looks_like_reference(json.dumps({"type": "GeographicCRS", "name": name}))


def test_detection_is_independent_of_key_order_and_name_length() -> None:
    assert looks_like_reference(
        json.dumps({"name": "x" * 5000, **LATE_BOUND, "ver": "x" * 5000})
    )
    assert looks_like_reference(json.dumps({"name": "x" * 5000, "wkt": "GEOGCS[...]"}))


def test_string_encoded_members_have_depth_limits() -> None:
    from geodetic_engine.persistablereference.envelope import object_field

    with pytest.raises(MalformedReferenceError, match="nests"):
        object_field({"lateBoundCRS": json.dumps(_nested(64))}, "lateBoundCRS")


@pytest.mark.parametrize(
    "value", ['{"type":"LBC","wkt":"a"}', "  %7B%22wkt%22", "%7b%22authCode%22"]
)
def test_payloads_are_recognised_encoded_or_not(value: str) -> None:
    """Both spellings of an opening brace count, and the member is found either way."""
    assert looks_like_reference(value)


def test_an_oversized_payload_is_refused_before_it_is_parsed() -> None:
    """The payload comes from another system, so its size is not taken on trust."""
    with pytest.raises(MalformedReferenceError, match="over the"):
        decode(json.dumps(dict(LATE_BOUND, wkt="x" * (1 << 21))))


def test_a_deeply_nested_payload_is_refused() -> None:
    """One definition does not nest thirty levels deep."""
    payload = json.dumps({"type": "LBC", "wkt": "a", "x": _nested(64)})
    with pytest.raises(MalformedReferenceError, match="nests"):
        decode(payload)


def test_brackets_inside_wkt_are_not_counted_as_nesting() -> None:
    """A projected CRS's WKT is bracket-heavy and nests the envelope not at all."""
    deep = 'PROJCS["a",' + 'GEOGCS["b",' * 20 + "]" * 20 + "]"
    assert decode(json.dumps({"type": "LBC", "wkt": deep})).kind is Kind.LATE_BOUND_CRS


@pytest.mark.parametrize(
    "broken", ["not json", "[1,2]", '"text"', '{"name":"x"}', "{}"]
)
def test_unreadable_payloads_are_refused(broken: str) -> None:
    """Anything that is not one JSON object stating a kind is an error."""
    with pytest.raises(MalformedReferenceError):
        decode(broken)


def test_an_unknown_kind_names_the_ones_that_are_known() -> None:
    """The error says what would have been accepted."""
    with pytest.raises(UnsupportedReferenceError, match="LBC, EBC"):
        decode('{"type":"ZZZ"}')


def _nested(depth: int) -> dict[str, object]:
    """A payload nested ``depth`` levels deep."""
    value: dict[str, object] = {}
    for _ in range(depth):
        value = {"x": value}
    return value
