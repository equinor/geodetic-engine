"""Writing definitions back out, which PROJ will not do for a transformation."""

from __future__ import annotations

from typing import Any

import pytest
from pyproj import CRS, Transformer
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.persistablereference import (
    AuthorityCode,
    CrsReference,
    Kind,
    MalformedReferenceError,
    UnsupportedMethodError,
    UnsupportedReferenceError,
    esriwkt,
    parse_persistable_reference,
    to_persistable_reference,
)
from geodetic_engine.persistablereference.emit import geogtran
from tests.persistablereference.conftest import MOLODENSKY_BADEKAS, cases

READABLE = [case for case in cases() if not case.startswith("refused_")]

# Somewhere inside the domain of every fixture CRS is not a thing that exists,
# so round trips are compared where the projections are well behaved rather
# than at an origin a thousand kilometres outside the area of use.
PROBES = ((500000.0, 6600000.0), (400000.0, 5000000.0))


@pytest.mark.parametrize("case", READABLE)
def test_everything_that_reads_writes_again(case: str, payload: Any) -> None:
    """A definition this package accepts is one it can state."""
    original = parse_persistable_reference(payload(case))
    built = (
        original.to_crs()
        if isinstance(original, CrsReference)
        else original.to_operation()
    )
    again = parse_persistable_reference(to_persistable_reference(built))
    assert again.kind is original.kind


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in READABLE
        if case.startswith(("lbc_", "ebc_")) and case != "lbc_vertical"
    ],
)
def test_a_crs_round_trips_to_the_same_coordinates(case: str, payload: Any) -> None:
    """What comes back transforms points the way what went in did.

    A vertical CRS is left out: there is no horizontal transformation to compare
    it through, and its round trip is covered above.
    """
    original = parse_persistable_reference(payload(case)).to_crs()
    rebuilt = parse_persistable_reference(to_persistable_reference(original)).to_crs()
    before = Transformer.from_crs(original, "EPSG:4326", always_xy=True)
    after = Transformer.from_crs(rebuilt, "EPSG:4326", always_xy=True)
    for x, y in PROBES:
        assert before.transform(x, y) == pytest.approx(after.transform(x, y), abs=1e-9)


def test_a_bound_crs_keeps_its_transformation(payload: Any) -> None:
    """PROJ's own ESRI export drops it silently, so this is the thing to check."""
    original = parse_persistable_reference(
        payload("ebc_projected_position_vector")
    ).to_crs()
    written = to_persistable_reference(original)
    again = parse_persistable_reference(written)
    assert again.kind is Kind.EARLY_BOUND_CRS
    assert again.is_bound
    assert again.to_crs().is_bound


def test_the_transformation_written_is_the_one_read(payload: Any) -> None:
    """Parameters survive the trip through ESRI's unitless dialect."""
    original = parse_persistable_reference(payload("st_position_vector")).to_operation()
    rebuilt = parse_persistable_reference(
        to_persistable_reference(original)
    ).to_operation()
    assert rebuilt.method_name == original.method_name
    assert [parameter.value for parameter in rebuilt.params] == [
        parameter.value for parameter in original.params
    ]


@pytest.mark.parametrize("index", [0, 3, 6])
@pytest.mark.parametrize("authority", ["OSDU", "CUSTOM"])
def test_numeric_parameter_codes_require_the_epsg_authority(
    index: int, authority: str, payload: Any
) -> None:
    definition = (
        parse_persistable_reference(payload("st_position_vector"))
        .to_operation()
        .to_json_dict()
    )
    definition["parameters"][index]["id"]["authority"] = authority
    operation = CoordinateOperation.from_json_dict(definition)
    assert operation.to_json_dict()["parameters"][index]["id"]["authority"] == authority
    with pytest.raises(UnsupportedMethodError, match=r"parameter .* with no EPSG code"):
        to_persistable_reference(operation)


def test_parameters_are_restated_in_the_unit_esri_implies() -> None:
    """EPSG states these rotations in microradians; ESRI reads arc-seconds."""
    epsg = CoordinateOperation.from_authority("EPSG", "1066")
    rotations = {
        parameter.name: (parameter.value, parameter.unit_name)
        for parameter in epsg.params
        if parameter.name.endswith("axis rotation")
    }
    assert {unit for _, unit in rotations.values()} == {"microradian"}

    written = esriwkt.read(esriwkt.write(geogtran(epsg)))
    stated = {
        node.name: node.values[0]
        for node in written.nodes("PARAMETER")
        if node.name.endswith("_Axis_Rotation")
    }
    # One microradian is 0.2062648... arc-seconds.
    assert stated["X_Axis_Rotation"] == pytest.approx(
        rotations["X-axis rotation"][0] * 1e-06 / 4.84813681109536e-06
    )
    assert parse_persistable_reference(
        to_persistable_reference(epsg)
    ).to_operation().params[3].value == (pytest.approx(stated["X_Axis_Rotation"]))


def test_a_ten_parameter_transformation_round_trips() -> None:
    """The pivot ordinates are written back under the names ESRI reads."""
    original = parse_persistable_reference(MOLODENSKY_BADEKAS).to_operation()
    rebuilt = parse_persistable_reference(
        to_persistable_reference(original)
    ).to_operation()
    assert rebuilt.method_name == original.method_name
    assert [parameter.value for parameter in rebuilt.params] == [
        parameter.value for parameter in original.params
    ]


def test_a_grid_transformation_is_written_as_one_dataset(payload: Any) -> None:
    """EPSG's two NADCON files are the one dataset ESRI names."""
    original = parse_persistable_reference(payload("st_nadcon_grid")).to_operation()
    parameters = geogtran(original).nodes("PARAMETER")
    assert len(parameters) == 1
    assert parameters[0].name == "Dataset_conus"

    rebuilt = parse_persistable_reference(
        to_persistable_reference(original)
    ).to_operation()
    assert [parameter.value for parameter in rebuilt.params] == [
        "conus.las",
        "conus.los",
    ]


def test_no_authority_is_claimed_unless_one_is_given() -> None:
    """A code is a claim about a register, and this package will not invent one."""
    written = parse_persistable_reference(
        to_persistable_reference(CRS.from_epsg(23032))
    )
    assert written.authority_code is None

    stamped = parse_persistable_reference(
        to_persistable_reference(
            CRS.from_epsg(23032), authority=AuthorityCode("OSDU", "1")
        )
    )
    assert str(stamped.authority_code) == "OSDU:1"


def test_a_bound_projected_crs_is_written_through_its_geographic_parts() -> None:
    """A GEOGTRAN goes between geographic CRSs even when the CRS bound is projected."""
    bound = BoundCRS(
        CRS.from_epsg(23032),
        CRS.from_epsg(4326),
        CoordinateOperation.from_authority("EPSG", "1612"),
    )
    written = parse_persistable_reference(to_persistable_reference(bound))
    assert written.kind is Kind.EARLY_BOUND_CRS
    assert written.to_crs().is_bound


@pytest.mark.parametrize("code", ["4441", "15596", "1035"])
def test_a_method_esri_has_no_name_for_is_refused(code: str) -> None:
    """Writing it as the nearest method that fits would change the transformation."""
    with pytest.raises(UnsupportedMethodError, match="no ESRI equivalent"):
        geogtran(CoordinateOperation.from_authority("EPSG", code))


@pytest.mark.parametrize("code", [7912, 7789])
def test_dynamic_datums_are_not_exported_as_static_datums(code: int) -> None:
    with pytest.raises(UnsupportedReferenceError, match="dynamic datum"):
        to_persistable_reference(CRS.from_epsg(code))


def test_dynamic_datum_in_a_compound_crs_is_refused() -> None:
    from pyproj.crs import CompoundCRS

    compound = CompoundCRS(
        "dynamic compound", [CRS.from_epsg(9000), CRS.from_epsg(5703)]
    )
    with pytest.raises(UnsupportedReferenceError, match="dynamic datum"):
        to_persistable_reference(compound)


def test_proj_export_errors_use_the_package_exception() -> None:
    with pytest.raises(MalformedReferenceError, match="ESRI WKT"):
        to_persistable_reference(CRS.from_epsg(4978))


@pytest.mark.parametrize("code", ["survey_1", "custom:shift-2"])
def test_operation_identifiers_can_be_alphanumeric(code: str, payload: Any) -> None:
    original = parse_persistable_reference(payload("st_position_vector")).to_operation()
    definition = original.to_json_dict()
    definition["id"] = {"authority": "OSDU", "code": code}
    operation = CoordinateOperation.from_json_dict(definition)
    written = parse_persistable_reference(to_persistable_reference(operation))
    assert written.steps[0].node("AUTHORITY").children == ("OSDU", code)
    assert [parameter.value for parameter in written.to_operation().params] == [
        parameter.value for parameter in original.params
    ]
