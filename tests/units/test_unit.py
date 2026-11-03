"""Reading length and angle units."""

from __future__ import annotations

import json
import math
from typing import Any
from urllib.parse import quote

import pytest
from pyproj import CRS

from geodetic_engine.errors import GeodeticEngineError
from geodetic_engine.persistablereference import to_persistable_reference
from geodetic_engine.units import Unit, UnitError, angle_unit, length_unit

US_FOOT = 1200 / 3937
DEGREE = math.pi / 180
# The OSDU CRS conversion service's own payload for metres, CRSConverter.METER.
SERVICE_METRE = (
    '{"scaleOffset":{"scale":1.0,"offset":0.0},"symbol":"m",'
    '"baseMeasurement":{"ancestry":"Length","type":"UM"},"type":"USO"}'
)
# As the OSDU unit catalogue, UnitOfMeasure.1.0.0.json, states them.
CATALOGUE_FOOT = (
    '{"abcd":{"a":0.0,"b":0.3048,"c":1.0,"d":0.0},"symbol":"ft",'
    '"baseMeasurement":{"ancestry":"L","type":"UM"},"type":"UAD"}'
)
CATALOGUE_US_FOOT = (
    '{"abcd":{"a":0.0,"b":1200.0,"c":3937.0,"d":0.0},"symbol":"ft[US]",'
    '"baseMeasurement":{"ancestry":"L","type":"UM"},"type":"UAD"}'
)
CATALOGUE_DEGREE = (
    '{"abcd":{"a":0.0,"b":3.141592653589793,"c":180.0,"d":0.0},"symbol":"dega",'
    '"baseMeasurement":{"ancestry":"A","type":"UM"},"type":"UAD"}'
)


def _uso(symbol: str, scale: float, ancestry: str | None, offset: float = 0.0) -> str:
    stated: dict[str, Any] = {
        "scaleOffset": {"scale": scale, "offset": offset},
        "symbol": symbol,
        "type": "USO",
    }
    if ancestry is not None:
        stated["baseMeasurement"] = {"ancestry": ancestry, "type": "UM"}
    return json.dumps(stated)


def _uad(symbol: str, abcd: tuple[float, float, float, float], ancestry: str) -> str:
    return json.dumps(
        {
            "abcd": dict(zip("abcd", abcd, strict=True)),
            "symbol": symbol,
            "baseMeasurement": {"ancestry": ancestry, "type": "UM"},
            "type": "UAD",
        }
    )


@pytest.mark.parametrize(
    ("symbol", "metres"),
    [
        *((name, 1.0) for name in ("m", "metre", "meter", "metres", "meters")),
        *((name, 0.3048) for name in ("ft", "foot", "feet", "international foot")),
        *((name, US_FOOT) for name in ("ftUS", "ft[US]", "usft", "US survey foot")),
        ("km", 1000.0),
        ("cm", 0.01),
        ("mm", 0.001),
        ("in", 0.0254),
        ("Metre", 1.0),
        ("FT", 0.3048),
        (" ft ", 0.3048),
    ],
)
def test_every_length_symbol_and_name_is_read(symbol: str, metres: float) -> None:
    unit = length_unit(symbol)
    assert (unit.symbol, unit.quantity, unit.scale) == (
        symbol.strip(),
        "length",
        metres,
    )
    assert unit.payload is None


@pytest.mark.parametrize(
    ("symbol", "radians"),
    [
        *((name, DEGREE) for name in ("deg", "dega", "degree", "degrees")),
        *((name, 1.0) for name in ("rad", "radian", "radians")),
        *((name, math.pi / 200) for name in ("gon", "grad", "grads")),
        ("Degree", DEGREE),
    ],
)
def test_every_angle_symbol_and_name_is_read(symbol: str, radians: float) -> None:
    assert angle_unit(symbol) == Unit(symbol, "angle", radians)


@pytest.mark.parametrize(
    ("record_id", "expected"),
    [
        ("osdu:reference-data--UnitOfMeasure:ft:", Unit("ft", "length", 0.3048)),
        ("dev:reference-data--UnitOfMeasure:ftUS:", Unit("ftUS", "length", US_FOOT)),
        (
            "osdu:reference-data--UnitOfMeasure:ft%5BUS%5D:",
            Unit("ft[US]", "length", US_FOOT),
        ),
        (
            quote("osdu:reference-data--UnitOfMeasure:km:", safe=""),
            Unit("km", "length", 1000.0),
        ),
        ("osdu:reference-data--UnitOfMeasure:m:1", Unit("m", "length", 1.0)),
        ("%20ft%20", Unit("ft", "length", 0.3048)),
    ],
    ids=[
        "plain",
        "other-namespace",
        "encoded-symbol",
        "encoded-id",
        "versioned",
        "encoded-blanks",
    ],
)
def test_a_record_id_is_read_for_its_symbol(record_id: str, expected: Unit) -> None:
    assert length_unit(record_id) == expected


def test_an_angle_record_id_is_read_for_its_symbol() -> None:
    assert angle_unit("osdu:reference-data--UnitOfMeasure:dega:") == Unit(
        "dega", "angle", DEGREE
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (SERVICE_METRE, Unit("m", "length", 1.0)),
        (_uso("ft", 0.3048, "length"), Unit("ft", "length", 0.3048)),
        (
            _uad("ftUS", (0.0, 1200.0, 3937.0, 0.0), "Length"),
            Unit("ftUS", "length", US_FOOT),
        ),
        (CATALOGUE_FOOT, Unit("ft", "length", 0.3048)),
        (CATALOGUE_US_FOOT, Unit("ft[US]", "length", US_FOOT)),
        (quote(_uso("ft", 0.3048, "Length")), Unit("ft", "length", 0.3048)),
    ],
    ids=["service", "uso", "uad", "catalogue", "catalogue-us-foot", "encoded"],
)
def test_a_length_payload_is_read_and_kept(payload: str, expected: Unit) -> None:
    unit = length_unit(payload)
    assert unit == expected
    assert unit.payload == payload
    assert unit.reference() == payload


@pytest.mark.parametrize(
    "payload",
    [
        _uso("rad", 1.0, "Plane_Angle"),
        _uso("rad", 1.0, "angle"),
        _uad("rad", (0.0, 1.0, 1.0, 0.0), "A"),
    ],
)
def test_an_angle_payload_is_read(payload: str) -> None:
    assert angle_unit(payload) == Unit("rad", "angle", 1.0)


def test_the_catalogue_degree_is_a_degree() -> None:
    assert angle_unit(CATALOGUE_DEGREE).scale == pytest.approx(DEGREE, rel=1e-15)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "non-empty string"),
        ("  ", "non-empty string"),
        (None, "non-empty string"),
        (0.3048, "non-empty string"),
        ("fathom", "'fathom' is not a length unit this package knows"),
        ("deg", "'deg' is not a length unit"),
        ("osdu:reference-data--UnitOfMeasure:furlong:", "furlong"),
        (_uso("deg/30m", 5.81776417331443e-4, "Rotation_Per_Length"), "deg/30m"),
        (_uad("dega/m", (0.0, math.pi, 180.0, 0.0), "A/L"), r"\(a/l\)"),
        (_uso("rad", 1.0, "Plane_Angle"), r"\(plane_angle\) is not a length unit"),
        (CATALOGUE_DEGREE, r"\(a\) is not a length unit"),
        (_uso("m", 1.0, None), "no measurement"),
        (_uso("degC", 1.0, "Length", offset=273.15), "offset of 273.15"),
        (_uad("odd", (1.0, 1.0, 1.0, 0.0), "Length"), "offset of 1.0"),
        (_uad("odd", (0.0, 1.0, 1.0, 1.0), "Length"), "cannot read the unit"),
        (_uso("m0", 0.0, "Length"), "scale of 0.0"),
        (_uso("-ft", -0.3048, "Length"), "scale of -0.3048"),
        (_uad("huge", (0.0, 1e308, 1e-308, 0.0), "Length"), "scale of inf"),
        (
            '{"scaleOffset":{"scale":1e400,"offset":0.0},"symbol":"huge",'
            '"baseMeasurement":{"ancestry":"Length","type":"UM"},"type":"USO"}',
            "nonfinite",
        ),
        (to_persistable_reference(CRS.from_epsg(4326)), "is not a unit"),
    ],
    ids=[
        "empty",
        "blank",
        "none",
        "number",
        "unknown",
        "an-angle",
        "unknown-record-id",
        "a-rate-per-length",
        "an-angle-per-length",
        "an-angle-payload",
        "a-catalogue-angle",
        "no-measurement",
        "uso-offset",
        "uad-offset",
        "rational",
        "zero-scale",
        "negative-scale",
        "infinite-scale",
        "nonfinite-scale",
        "a-crs",
    ],
)
def test_anything_but_a_plain_length_is_refused(value: Any, reason: str) -> None:
    with pytest.raises(UnitError, match=reason):
        length_unit(value)


@pytest.mark.parametrize(
    "value", ["m", "ft", SERVICE_METRE, CATALOGUE_FOOT, _uso("rad", 1.0, "length")]
)
def test_a_length_is_not_an_angle(value: str) -> None:
    with pytest.raises(UnitError, match="angle unit"):
        angle_unit(value)


@pytest.mark.parametrize(
    ("unit", "ancestry"),
    [(length_unit("ft"), "Length"), (angle_unit("dega"), "Plane_Angle")],
)
def test_a_unit_read_from_a_symbol_is_written_as_a_uso_payload(
    unit: Unit, ancestry: str
) -> None:
    written = json.loads(unit.reference())
    assert written == {
        "scaleOffset": {"scale": unit.scale, "offset": 0.0},
        "symbol": unit.symbol,
        "baseMeasurement": {"ancestry": ancestry, "type": "UM"},
        "type": "USO",
    }
    reader = length_unit if unit.quantity == "length" else angle_unit
    assert reader(unit.reference()) == unit


def test_metres_are_written_as_the_service_writes_them() -> None:
    assert length_unit("m").reference() == SERVICE_METRE


def test_the_payload_is_not_compared() -> None:
    assert length_unit(SERVICE_METRE) == length_unit("m")
    assert hash(length_unit(SERVICE_METRE)) == hash(length_unit("m"))


def test_values_convert_to_and_from_si() -> None:
    feet = length_unit("ft")
    assert feet.to_si(10.0) == pytest.approx(3.048)
    assert feet.from_si(3.048) == pytest.approx(10.0)
    assert angle_unit("gon").to_si(200.0) == pytest.approx(math.pi)


@pytest.mark.parametrize(
    ("quantity", "scale", "reason"),
    [
        ("area", 1.0, "length or an angle"),
        ("length", 0.0, "scale of 0.0"),
        ("length", -1.0, "scale of -1.0"),
        ("length", math.inf, "scale of inf"),
        ("angle", math.nan, "scale of nan"),
    ],
)
def test_a_unit_cannot_be_made_with_a_scale_that_is_not_one(
    quantity: Any, scale: float, reason: str
) -> None:
    with pytest.raises(UnitError, match=reason):
        Unit("x", quantity, scale)


def test_a_unit_error_is_a_value_error_of_this_package() -> None:
    assert issubclass(UnitError, GeodeticEngineError)
    assert issubclass(UnitError, ValueError)
