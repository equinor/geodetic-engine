"""Reading real payloads into the CRSs, transformations and units they state."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pyproj import CRS, Transformer
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.persistablereference import (
    CrsReference,
    Kind,
    MalformedReferenceError,
    OperationReference,
    UnitReference,
    UnsupportedMethodError,
    UnsupportedReferenceError,
    operation_from_geogtran,
    parse_persistable_reference,
)
from tests.persistablereference.conftest import (
    DEGREES_FAHRENHEIT,
    FOOT,
    MOLODENSKY_BADEKAS,
    cases,
)

READABLE = [case for case in cases() if not case.startswith("refused_")]


@pytest.mark.parametrize("case", READABLE)
def test_every_readable_payload_builds(case: str, payload: Any) -> None:
    """Each committed payload produces the object it describes."""
    reference = parse_persistable_reference(payload(case))
    built = (
        reference.to_crs()
        if isinstance(reference, CrsReference)
        else reference.to_operation()
    )
    assert built is not None


def test_a_bound_reference_carries_its_transformation(payload: Any) -> None:
    """An early bound CRS resolves to a bound CRS, which settles the datum shift."""
    reference = parse_persistable_reference(payload("ebc_projected_position_vector"))
    assert isinstance(reference, CrsReference)
    assert reference.is_bound
    assert reference.operation.method_names == ("Position_Vector",)
    crs = reference.to_crs()
    assert crs.is_bound
    assert crs.name == "ED50 / UTM zone 32N"


@pytest.mark.parametrize("top_level", [23031, 4326, "not valid WKT"])
@pytest.mark.parametrize("as_text", [False, True])
def test_nested_crs_takes_precedence_over_top_level_wkt(
    top_level: int | str, as_text: bool, payload: Any
) -> None:
    from geodetic_engine.geodesy import transform

    original = payload("ebc_projected_position_vector")
    stated = json.loads(original)
    stated["WKT"] = (
        CRS.from_epsg(top_level).to_wkt("WKT1_ESRI")
        if isinstance(top_level, int)
        else top_level
    )
    nested = stated.pop("lateBoundCRS")
    stated["LateBoundCRS"] = json.dumps(nested) if as_text else nested
    definition = json.dumps(stated)
    reference = parse_persistable_reference(definition)
    assert isinstance(reference, CrsReference)
    assert reference.late_bound is not None
    assert reference.wkt == reference.late_bound.wkt == nested["wkt"]
    assert reference.to_crs().source_crs.equals(CRS.from_epsg(23032))
    point = (500000.0, 6600000.0)
    assert (
        transform(definition, "EPSG:4326", point).coordinates
        == transform(original, "EPSG:4326", point).coordinates
    )


def test_early_bound_crs_without_nested_crs_uses_top_level_wkt(payload: Any) -> None:
    original = payload("ebc_projected_position_vector")
    stated = json.loads(original)
    stated["wkt"] = stated.pop("lateBoundCRS")["wkt"]
    reference = parse_persistable_reference(json.dumps(stated))
    assert reference.late_bound is None
    assert reference.to_crs().equals(parse_persistable_reference(original).to_crs())


@pytest.mark.parametrize("nested_wkt", [None, "not valid WKT"])
def test_top_level_wkt_does_not_mask_invalid_nested_crs(
    nested_wkt: str | None, payload: Any
) -> None:
    stated = json.loads(payload("ebc_projected_position_vector"))
    stated["wkt"] = stated["lateBoundCRS"].pop("wkt")
    if nested_wkt is not None:
        stated["lateBoundCRS"]["wkt"] = nested_wkt
    with pytest.raises(MalformedReferenceError):
        parse_persistable_reference(json.dumps(stated)).to_crs()


@pytest.mark.parametrize("member", ["lateBoundCRS", "singleCT", "compoundCT"])
@pytest.mark.parametrize("as_text", [False, True])
def test_a_late_bound_crs_refuses_binding_members(
    member: str, as_text: bool, payload: Any
) -> None:
    stated = json.loads(payload("lbc_ed50_geographic"))
    value = json.loads(
        payload(
            {
                "lateBoundCRS": "lbc_ed50_geographic",
                "singleCT": "st_position_vector",
                "compoundCT": "ct_concatenated",
            }[member]
        )
    )
    stated[member.upper()] = json.dumps(value) if as_text else value
    with pytest.raises(MalformedReferenceError, match="early-bound members"):
        parse_persistable_reference(json.dumps(stated))


@pytest.mark.parametrize(
    ("member", "case", "expected"),
    [
        ("lateBoundCRS", "lbc_ed50_geographic", Kind.LATE_BOUND_CRS),
        ("singleCT", "st_position_vector", Kind.TRANSFORMATION),
        ("compoundCT", "ct_concatenated", Kind.CONCATENATED_TRANSFORMATION),
    ],
)
@pytest.mark.parametrize(
    "stated_kind", [None, "LBC", "EBC", "ST", "CT", "USO", "UAD", "ZZZ"]
)
def test_nested_discriminators_match_their_container(
    member: str, case: str, expected: Kind, stated_kind: str | None, payload: Any
) -> None:
    stated = json.loads(payload("ebc_projected_position_vector"))
    nested = json.loads(payload(case))
    nested.pop("type", None)
    if stated_kind is not None:
        nested["Type"] = stated_kind.lower()
    if member == "compoundCT":
        stated.pop("singleCT")
        operation = parse_persistable_reference(payload(case)).to_operation()
        source = CRS.from_json_dict(operation.to_json_dict()["source_crs"])
        stated["lateBoundCRS"] = {"type": "LBC", "wkt": source.to_wkt("WKT1_ESRI")}
    stated[member] = nested
    if stated_kind == "ZZZ":
        with pytest.raises(UnsupportedReferenceError, match="ZZZ"):
            parse_persistable_reference(json.dumps(stated))
    elif stated_kind is not None and stated_kind != expected.value:
        with pytest.raises(MalformedReferenceError, match="nested type"):
            parse_persistable_reference(json.dumps(stated))
    else:
        reference = parse_persistable_reference(json.dumps(stated))
        child = (
            reference.late_bound if member == "lateBoundCRS" else reference.operation
        )
        assert child.kind is expected
        assert reference.to_crs().is_bound


def test_the_authority_code_is_recorded_and_not_followed(payload: Any) -> None:
    """The envelope's code is provenance; the parts state their own."""
    reference = parse_persistable_reference(payload("ebc_projected_position_vector"))
    assert str(reference.authority_code) == "OSDU:23032023"
    assert str(reference.late_bound.authority_code) == "EPSG:23032"
    assert str(reference.operation.authority_code) == "EPSG:1612"


def test_parameters_are_built_from_rather_than_the_code(payload: Any) -> None:
    """The payload states EPSG:1612 and its parameters, and the parameters win.

    They agree here, which is what makes this worth asserting: the operation is
    reproduced exactly without the register being consulted.
    """
    reference = parse_persistable_reference(payload("ebc_projected_position_vector"))
    claimed = BoundCRS(
        CRS.from_epsg(23032),
        CRS.from_epsg(4326),
        CoordinateOperation.from_authority("EPSG", "1612"),
    )
    mine = Transformer.from_crs(reference.to_crs(), "EPSG:4326", always_xy=True)
    theirs = Transformer.from_crs(claimed, "EPSG:4326", always_xy=True)
    assert mine.transform(500000.0, 6600000.0) == theirs.transform(500000.0, 6600000.0)


def test_a_fabricated_code_does_not_substitute_a_different_crs(payload: Any) -> None:
    """A code that names nothing is ignored, because the WKT is the definition."""
    original = payload("lbc_projected")
    tampered = original.replace('"auth":"EPSG"', '"auth":"NOSUCH"')
    assert (
        parse_persistable_reference(tampered).to_crs()
        == parse_persistable_reference(original).to_crs()
    )


def test_a_transformation_states_its_accuracy(payload: Any) -> None:
    """OPERATIONACCURACY rides inside the WKT and is carried through."""
    operation = parse_persistable_reference(
        payload("st_position_vector")
    ).to_operation()
    assert operation.accuracy == pytest.approx(1.0)


def test_rotations_are_read_as_arc_seconds(payload: Any) -> None:
    """ESRI states no unit, so the one it implies is the one that must be used."""
    operation = parse_persistable_reference(
        payload("st_position_vector")
    ).to_operation()
    rotations = [
        parameter
        for parameter in operation.params
        if parameter.name.endswith("axis rotation")
    ]
    assert [parameter.unit_name for parameter in rotations] == ["arc-second"] * 3
    assert [parameter.value for parameter in rotations] == [0.893, 0.921, -0.917]


def test_a_ten_parameter_transformation_reads() -> None:
    """Molodensky-Badekas states a pivot, whose EPSG codes are not consecutive."""
    operation = parse_persistable_reference(MOLODENSKY_BADEKAS).to_operation()
    assert operation.method_name == "Molodensky-Badekas (CF geog2D domain)"
    pivot = [
        parameter.value
        for parameter in operation.params
        if parameter.name.startswith("Ordinate")
    ]
    assert pivot == [2464351.59, -5783466.61, 974809.81]


def test_a_grid_transformation_resolves_to_the_files_proj_reads(payload: Any) -> None:
    """ESRI names a dataset; PROJ needs the file names its own database states."""
    operation = parse_persistable_reference(payload("st_nadcon_grid")).to_operation()
    assert operation.method_name == "NADCON"
    assert [parameter.value for parameter in operation.params] == [
        "conus.las",
        "conus.los",
    ]


def test_a_dataset_filed_under_a_backslash_folder_resolves(payload: Any) -> None:
    """The regional folder is dropped whichever separator ESRI filed it with."""
    stated = payload("st_ntv2_grid")
    backslashed = stated.replace("Dataset_australia/", "Dataset_australia\\\\")
    assert backslashed != stated
    forward, backward = (
        parse_persistable_reference(text).to_operation()
        for text in (stated, backslashed)
    )
    assert [p.value for p in backward.params] == [p.value for p in forward.params]


def test_a_chain_is_read_as_a_concatenated_operation(payload: Any) -> None:
    """A compound CT states its steps, and they stay steps."""
    reference = parse_persistable_reference(payload("ct_concatenated"))
    assert isinstance(reference, OperationReference)
    assert reference.is_concatenated
    assert reference.to_operation().to_json_dict()["type"] == "ConcatenatedOperation"


@pytest.mark.parametrize(
    ("name", "radius", "meridian", "axes", "compatible"),
    [
        ("Shared label", 6378123, 0, "", True),
        ("Different label", 6378123, 0, "", True),
        ("Different label", 6378123, 0, ',AXIS["Lat",NORTH],AXIS["Lon",EAST]', True),
        ("Shared label", 6378000, 0, "", False),
        ("Shared label", 6378123, 1, "", False),
    ],
)
def test_chain_connections_compare_crs_definitions(
    name: str, radius: int, meridian: int, axes: str, compatible: bool
) -> None:
    ending = (
        'GEOGCS["Shared label",DATUM["Custom datum",'
        'SPHEROID["Custom ellipsoid",6378123,298.25]],'
        'PRIMEM["Custom meridian",0],UNIT["Degree",0.0174532925199433]]'
    )
    beginning = (
        f'GEOGCS["{name}",DATUM["Custom datum",'
        f'SPHEROID["Custom ellipsoid",{radius},298.25]],'
        f'PRIMEM["Custom meridian",{meridian}],'
        f'UNIT["Degree",0.0174532925199433]{axes}]'
    )
    assert CRS.from_wkt(ending).to_authority() is None
    assert (
        CRS.from_wkt(ending).equals(CRS.from_wkt(beginning), ignore_axis_order=True)
        is compatible
    )
    steps = []
    for source, target in (
        (CRS.from_epsg(4230).to_wkt("WKT1_ESRI"), ending),
        (beginning, CRS.from_epsg(4326).to_wkt("WKT1_ESRI")),
    ):
        steps.append(
            {
                "type": "ST",
                "wkt": (
                    f'GEOGTRAN["Custom shift {len(steps)}",{source},{target},'
                    'METHOD["Geocentric_Translation"],'
                    'PARAMETER["X_Axis_Translation",1],'
                    'PARAMETER["Y_Axis_Translation",2],'
                    'PARAMETER["Z_Axis_Translation",3]]'
                ),
            }
        )
    reference = parse_persistable_reference(json.dumps({"type": "CT", "cts": steps}))
    if not compatible:
        with pytest.raises(
            UnsupportedReferenceError, match="CRS definitions are not equivalent"
        ):
            reference.to_operation()
        return
    operation = reference.to_operation()
    point = (7.5, 57.0)
    expected = point
    for step in steps:
        transformer = Transformer.from_pipeline(
            operation_from_geogtran(step["wkt"]).to_json(), always_xy=True
        )
        expected = transformer.transform(*expected)
    actual = Transformer.from_pipeline(operation.to_json(), always_xy=True).transform(
        *point
    )
    assert actual == pytest.approx(expected, abs=1e-9)
    assert len(operation.operations) == 2


def test_a_vertical_crs_reads(payload: Any) -> None:
    """VERTCS is a CRS PROJ parses, so it needs nothing from this package."""
    crs = parse_persistable_reference(payload("lbc_vertical")).to_crs()
    assert crs.is_vertical


def test_axis_order_is_reported_not_reinterpreted(payload: Any) -> None:
    """ESRI WKT declares no axes for a geographic CRS, and the contract still holds."""
    from geodetic_engine.geodesy import CoordinateReferenceSystem

    crs = CoordinateReferenceSystem.from_persistable_reference(
        payload("lbc_geographic")
    )
    assert crs.is_geographic
    assert crs.axis_abbreviations[:2] == ("Lat", "Lon")
    assert crs.value_axis_abbreviations[:2] == ("Lon", "Lat")


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("refused_time_specific", UnsupportedMethodError),
    ],
)
def test_what_cannot_be_translated_exactly_is_refused(
    case: str, expected: type[Exception], payload: Any
) -> None:
    """Each of these differs from a supported definition by something silent."""
    reference = parse_persistable_reference(payload(case))
    with pytest.raises(expected):
        reference.to_operation()


# EPSG method 9651's worked example, ED50 to ED87 (1): (lat, lon) in, dX, dY out.
EPSG_POLYNOMIAL_FORWARD = ((52.508333333, 2.0), (-3.12958e-06, 9.80126e-06))
EPSG_POLYNOMIAL_REVERSE = ((52.5083301944, 2.0000098055), (3.12957e-06, -9.80124e-06))


@pytest.mark.parametrize("identified", [True, False])
def test_a_reversible_polynomial_reproduces_epsgs_worked_example(
    identified: bool, payload: Any
) -> None:
    """OSDU states the very transformation EPSG works through for method 9651.

    Frames PROJ identifies reach the polynomial latitude first and others
    longitude first, and both must give EPSG's figures.
    """
    stated = payload("st_reversible_polynomial")
    if not identified:
        stated = stated.replace("European_1950", "Custom_A").replace(
            "European_1987", "Custom_B"
        )
    operation = parse_persistable_reference(stated).to_operation()
    first = operation.to_json_dict()["source_crs"]["coordinate_system"]["axis"][0]
    assert first["direction"] == ("north" if identified else "east")
    transformer = Transformer.from_pipeline(operation.to_json(), always_xy=True)
    for ((lat, lon), (d_lat, d_lon)), direction in (
        (EPSG_POLYNOMIAL_FORWARD, "FORWARD"),
        (EPSG_POLYNOMIAL_REVERSE, "INVERSE"),
    ):
        assert transformer.transform(lon, lat, direction=direction) == pytest.approx(
            (lon + d_lon, lat + d_lat), abs=1e-11
        )


def test_a_reversible_polynomial_runs_through_transform(payload: Any) -> None:
    from geodetic_engine.geodesy import transform

    (lat, lon), (d_lat, d_lon) = EPSG_POLYNOMIAL_FORWARD
    result = transform(
        "EPSG:4230",
        "EPSG:4231",
        (lon, lat),
        operation=payload("st_reversible_polynomial"),
    )
    assert result.coordinates[0] == pytest.approx((lon + d_lon, lat + d_lat), abs=1e-11)


def test_a_polynomial_scaling_factor_divides_the_series(payload: Any) -> None:
    """EPSG states m.dX = A0 + A1.U + ... with U = m(X - X0), so dX ~ m^(i+j-1)."""
    stated = json.loads(payload("st_reversible_polynomial"))
    scale = 'PARAMETER["Scaling_factor_for_coord_Differences",1]'
    assert scale in stated["wkt"]
    rescaled = re.sub(
        r'PARAMETER\["(?P<name>[AB](?:0|u(?P<u>\d)v(?P<v>\d)))",(?P<value>[^\]]+)\]',
        lambda found: (
            f'PARAMETER["{found["name"]}",'
            f"{float(found['value']) / 2 ** (int(found['u'] or 0) + int(found['v'] or 0) - 1)!r}]"
        ),
        stated["wkt"],
    ).replace(scale, scale.replace(",1]", ",2]"))
    before, after = (
        Transformer.from_pipeline(
            parse_persistable_reference(json.dumps(stated | {"wkt": wkt}))
            .to_operation()
            .to_json(),
            always_xy=True,
        )
        for wkt in (stated["wkt"], rescaled)
    )
    for point in ((2.0, 52.508333333), (4.5, 58.0)):
        assert after.transform(*point) == pytest.approx(
            before.transform(*point), abs=1e-12
        )


def test_a_polynomial_missing_a_coefficient_is_refused(payload: Any) -> None:
    stated = json.loads(payload("st_reversible_polynomial"))
    coefficient = 'PARAMETER["Bu0v4",7.62236E-09],'
    assert coefficient in stated["wkt"]
    stated["wkt"] = stated["wkt"].replace(coefficient, "")
    with pytest.raises(
        MalformedReferenceError, match="Reversible_polynomial_of_degree_4"
    ):
        parse_persistable_reference(json.dumps(stated)).to_operation()


def test_a_reversed_helmert_step_is_applied_as_esri_inverts_it(payload: Any) -> None:
    """EPSG:4837 applies ED_1950_To_WGS_1984_18 from WGS 84 to ED50.

    The expected values are ESRI's Geometry Service applying that GEOGTRAN
    with transformForward=false. The step is read back from PROJJSON, which
    has no flag for an inverted step, so this also checks it states one.
    """
    operation = parse_persistable_reference(
        payload("ct_reversed_helmert_step")
    ).to_operation()
    step = Transformer.from_pipeline(operation.operations[1].to_json(), always_xy=True)
    for point, esri in (
        ((5.4, 52.2), (5.401285939573649, 52.200778831259356)),
        ((3.0, 51.0), (3.001310972081192, 51.000841811644065)),
        ((7.0, 53.5), (7.001281918736436, 53.50071976771124)),
    ):
        assert step.transform(*point) == pytest.approx(esri, abs=1e-10)
    chain = Transformer.from_pipeline(operation.to_json(), always_xy=True)
    epsg = Transformer.from_pipeline(
        "urn:ogc:def:coordinateOperation:EPSG::4837", always_xy=True
    )
    assert chain.transform(5.4, 52.2) == pytest.approx(
        epsg.transform(5.4, 52.2), abs=1e-10
    )


def test_a_reversed_step_that_is_not_a_helmert_is_refused(payload: Any) -> None:
    """Only a Helmert's inverse is restated; a pivot or a grid is not."""
    steps = [
        json.loads(payload("st_position_vector")),
        json.loads(MOLODENSKY_BADEKAS),
    ]
    reference = parse_persistable_reference(json.dumps({"type": "CT", "cts": steps}))
    with pytest.raises(UnsupportedReferenceError, match="in reverse"):
        reference.to_operation()


def test_a_bound_crs_base_carries_the_prime_meridian_step_of_its_chain(
    payload: Any,
) -> None:
    """EPSG:8094's longitude rotation is the Paris meridian GCS_NTF_Paris states."""
    crs = parse_persistable_reference(payload("ebc_ntf_paris_via_8094")).to_crs()
    mine = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    epsg = Transformer.from_pipeline(
        CoordinateOperation.from_epsg(8094).to_json(), always_xy=True
    )
    for grads in ((0.0, 50.0), (-3.0, 48.0), (5.0, 54.0)):
        assert mine.transform(*grads) == pytest.approx(epsg.transform(*grads), abs=1e-9)


def test_a_longitude_rotation_reads_its_offset_from_the_prime_meridians(
    payload: Any,
) -> None:
    """ESRI states EPSG:1763 as Paris and Greenwich PRIMEMs with no parameter."""
    stated = parse_persistable_reference(
        payload("st_longitude_rotation_from_prime_meridians")
    ).to_operation()
    mine = Transformer.from_pipeline(stated.to_json(), always_xy=True)
    epsg = Transformer.from_pipeline(
        CoordinateOperation.from_epsg(1763).to_json(), always_xy=True
    )
    assert mine.transform(0.0, 50.0) == pytest.approx(
        epsg.transform(0.0, 50.0), abs=1e-9
    )


def test_a_unit_converts_both_ways() -> None:
    """A scale and offset unit states its conversion to the SI base."""
    foot = parse_persistable_reference(FOOT)
    assert isinstance(foot, UnitReference)
    assert foot.symbol == "ft"
    assert foot.scale == pytest.approx(0.3048)
    assert foot.to_si(1.0) == pytest.approx(0.3048)
    assert foot.from_si(0.3048) == pytest.approx(1.0)


def test_a_polynomial_unit_reduces_to_a_scale_and_offset() -> None:
    """A UAD with no denominator term is a linear conversion stated the long way."""
    fahrenheit = parse_persistable_reference(DEGREES_FAHRENHEIT)
    assert fahrenheit.to_si(212.0) == pytest.approx(373.15)
    assert fahrenheit.from_si(273.15) == pytest.approx(32.0)
    assert fahrenheit.scale == pytest.approx(5.0 / 9.0)


def test_units_measuring_different_things_do_not_convert() -> None:
    """A length is not a temperature, and the error says so rather than a number."""
    with pytest.raises(UnsupportedReferenceError, match="no conversion"):
        parse_persistable_reference(FOOT).convert_to(
            parse_persistable_reference(DEGREES_FAHRENHEIT), 1.0
        )


@pytest.mark.parametrize("definition", [FOOT, DEGREES_FAHRENHEIT])
@pytest.mark.parametrize("explicit_kind", [False, True])
@pytest.mark.parametrize("as_text", [False, True])
def test_unit_kinds_preserve_case_insensitive_and_encoded_members(
    definition: str, explicit_kind: bool, as_text: bool
) -> None:
    original = parse_persistable_reference(definition)
    stated = json.loads(definition)
    kind = stated.pop("type")
    if explicit_kind:
        stated["Type"] = kind.lower()
    member = "scaleOffset" if kind == "USO" else "abcd"
    conversion = stated.pop(member)
    stated[member.upper()] = json.dumps(conversion) if as_text else conversion
    reference = parse_persistable_reference(json.dumps(stated))
    assert reference.kind is original.kind
    assert reference.coefficients == original.coefficients
    assert reference.to_si(1.0) == original.to_si(1.0)


@pytest.mark.parametrize("definition", [FOOT, DEGREES_FAHRENHEIT])
def test_unit_kind_rejects_the_other_conversion_member(definition: str) -> None:
    stated = json.loads(definition)
    stated["type"] = "UAD" if stated["type"] == "USO" else "USO"
    with pytest.raises(MalformedReferenceError, match="requires exactly one"):
        parse_persistable_reference(json.dumps(stated))


@pytest.mark.parametrize("kind", [None, "USO", "UAD"])
@pytest.mark.parametrize("extra", [None, {}, {"a": 0, "b": 10, "c": 1, "d": 1}])
def test_unit_payloads_reject_both_conversion_members(
    kind: str | None, extra: Any
) -> None:
    stated = {"scaleOffset": {"scale": 2, "offset": 0}, "ABCD": extra}
    if kind is not None:
        stated["type"] = kind
    with pytest.raises(MalformedReferenceError, match="requires exactly one"):
        parse_persistable_reference(json.dumps(stated))


@pytest.mark.parametrize("definition", [FOOT, DEGREES_FAHRENHEIT])
def test_unit_payloads_reject_duplicate_conversion_keys(definition: str) -> None:
    stated = json.loads(definition)
    member = "scaleOffset" if stated["type"] == "USO" else "abcd"
    stated[member.upper()] = stated[member]
    with pytest.raises(MalformedReferenceError, match="requires exactly one"):
        parse_persistable_reference(json.dumps(stated))


@pytest.mark.parametrize("kind", ["USO", "UAD"])
def test_unit_payloads_require_a_conversion_member(kind: str) -> None:
    with pytest.raises(MalformedReferenceError, match="requires exactly one"):
        parse_persistable_reference(json.dumps({"type": kind}))


@pytest.mark.parametrize(("kind", "member"), [("USO", "scaleOffset"), ("UAD", "abcd")])
@pytest.mark.parametrize("conversion", [None, [], 1, "null", {}, "{}"])
def test_unit_conversion_members_must_be_complete_objects(
    kind: str, member: str, conversion: Any
) -> None:
    with pytest.raises(MalformedReferenceError):
        parse_persistable_reference(json.dumps({"type": kind, member: conversion}))


@pytest.mark.parametrize("name", ["X_Axis_Translation", "x_axis_translation"])
def test_duplicate_parameters_are_refused(name: str, payload: Any) -> None:
    stated = json.loads(payload("st_position_vector"))
    stated["wkt"] = stated["wkt"][:-1] + f',PARAMETER["{name}",0.0]]'
    with pytest.raises(MalformedReferenceError, match="duplicate parameter"):
        parse_persistable_reference(json.dumps(stated)).to_operation()


@pytest.mark.parametrize("as_payload", [False, True])
@pytest.mark.parametrize(
    "contents",
    [
        '"X_Axis_Translation"',
        '"X_Axis_Translation",10,20',
        '"X_Axis_Translation",bogus,10,20',
        '"X_Axis_Translation",bogus,10',
        '"X_Axis_Translation",10,bogus',
        '"X_Axis_Translation","bogus",10',
        '"X_Axis_Translation",10,"bogus"',
        '"X_Axis_Translation",UNIT["metre",1],10',
        '"X_Axis_Translation",10,UNIT["metre",1]',
        '"X_Axis_Translation","10"',
        "X_Axis_Translation,10",
        '10,"X_Axis_Translation"',
    ],
)
def test_malformed_numeric_parameter_nodes_are_refused(
    contents: str, as_payload: bool, payload: Any
) -> None:
    stated = json.loads(payload("st_position_vector"))
    original = 'PARAMETER["X_Axis_Translation",-116.641]'
    assert original in stated["wkt"]
    stated["wkt"] = stated["wkt"].replace(original, f"PARAMETER[{contents}]")
    with pytest.raises(
        MalformedReferenceError, match="quoted name and one numeric value"
    ):
        if as_payload:
            parse_persistable_reference(json.dumps(stated)).to_operation()
        else:
            operation_from_geogtran(stated["wkt"])


@pytest.mark.parametrize("as_payload", [False, True])
@pytest.mark.parametrize(
    "contents",
    [
        '"bad",1',
        '"bad"',
        "1,2",
        "-1",
        'OPERATIONACCURACY["bad"]',
        "OPERATIONACCURACY[OPERATIONACCURACY[1]]",
        "1.0],OPERATIONACCURACY[2.0",
    ],
)
def test_malformed_or_duplicate_accuracy_is_refused(
    contents: str, as_payload: bool, payload: Any
) -> None:
    stated = json.loads(payload("st_position_vector"))
    original = "OPERATIONACCURACY[1.0]"
    assert original in stated["wkt"]
    stated["wkt"] = stated["wkt"].replace(original, f"OPERATIONACCURACY[{contents}]")
    with pytest.raises(MalformedReferenceError, match="OPERATIONACCURACY"):
        if as_payload:
            parse_persistable_reference(json.dumps(stated)).to_operation()
        else:
            operation_from_geogtran(stated["wkt"])


@pytest.mark.parametrize("as_payload", [False, True])
def test_a_singly_nested_accuracy_is_read(as_payload: bool, payload: Any) -> None:
    """Three OSDU catalogue GEOGTRANs state OPERATIONACCURACY[OPERATIONACCURACY[1]]."""
    stated = json.loads(payload("st_position_vector"))
    stated["wkt"] = stated["wkt"].replace(
        "OPERATIONACCURACY[1.0]", "OPERATIONACCURACY[OPERATIONACCURACY[2.5]]"
    )
    operation = (
        parse_persistable_reference(json.dumps(stated)).to_operation()
        if as_payload
        else operation_from_geogtran(stated["wkt"])
    )
    assert operation.accuracy == pytest.approx(2.5)


@pytest.mark.parametrize("as_payload", [False, True])
@pytest.mark.parametrize("literal", ["0", "-10", "1.25", "1e1"])
def test_single_numeric_parameter_values_are_preserved(
    literal: str, as_payload: bool, payload: Any
) -> None:
    stated = json.loads(payload("st_position_vector"))
    stated["wkt"] = stated["wkt"].replace(
        'PARAMETER["X_Axis_Translation",-116.641]',
        f'PARAMETER["X_Axis_Translation",{literal}]',
    )
    operation = (
        parse_persistable_reference(json.dumps(stated)).to_operation()
        if as_payload
        else operation_from_geogtran(stated["wkt"])
    )
    assert operation.params[0].value == float(literal)
    assert len(operation.params) == 7


def test_grid_method_must_match_the_dataset(payload: Any) -> None:
    stated = json.loads(payload("st_nadcon_grid"))
    stated["wkt"] = stated["wkt"].replace('METHOD["NADCON"]', 'METHOD["NTv2"]')
    with pytest.raises(MalformedReferenceError, match="requires NADCON"):
        parse_persistable_reference(json.dumps(stated)).to_operation()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 10**400])
@pytest.mark.parametrize(
    ("definition", "member", "coefficient"),
    [(FOOT, "scaleOffset", key) for key in ("scale", "offset")]
    + [(DEGREES_FAHRENHEIT, "abcd", key) for key in ("a", "b", "c", "d")],
)
def test_unit_coefficients_must_be_finite(
    definition: str, member: str, coefficient: str, value: float | int
) -> None:
    stated = json.loads(definition)
    stated[member][coefficient] = value
    with pytest.raises(MalformedReferenceError, match="nonfinite"):
        parse_persistable_reference(json.dumps(stated))
