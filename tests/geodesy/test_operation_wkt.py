"""The operation that was applied must be re-exportable, not just named.

A code is enough to look an operation up again only when the authority defines
it. A collapsed chain has no code, so the WKT has to come from what PROJ built.
"""

from __future__ import annotations

import json

import pytest
from pyproj import CRS, Transformer
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy import Transformation, available_operations
from geodetic_engine.geodesy.operation import has_inverted_step
from geodetic_engine.geodesy.utils import collapse_concatenated

ED50_TO_WGS84 = "EPSG:1133"


def test_named_operation_exports_as_wkt() -> None:
    operation = Transformation(
        "EPSG:4230", "EPSG:4326", operation=ED50_TO_WGS84
    ).operation

    wkt = operation.to_wkt()

    assert wkt is not None
    assert wkt.startswith("COORDINATEOPERATION[")
    assert 'ID["EPSG",1133]' in wkt


def test_exported_wkt_is_the_operation_that_was_applied() -> None:
    """The parameters exported must be the ones the registry publishes.

    Not a string comparison: the export renders what PROJ built, which drops
    the registry's VERSION, USAGE and REMARK. The geodesy has to match even
    though the metadata does not.
    """
    operation = Transformation(
        "EPSG:4230", "EPSG:4326", operation=ED50_TO_WGS84
    ).operation
    registry = CoordinateOperation.from_authority("EPSG", 1133)

    exported = CoordinateOperation.from_string(operation.to_wkt() or "")

    assert exported.method_name == registry.method_name
    assert [(param.name, param.value) for param in exported.params] == [
        (param.name, param.value) for param in registry.params
    ]


def test_export_omits_registry_metadata() -> None:
    """Pinned so the omission is a known property rather than a surprise."""
    operation = Transformation(
        "EPSG:4230", "EPSG:4326", operation=ED50_TO_WGS84
    ).operation

    wkt = operation.to_wkt() or ""

    assert "USAGE[" not in wkt
    assert 'ID["EPSG",1133]' in wkt


def test_pretty_is_the_same_definition_indented() -> None:
    operation = Transformation(
        "EPSG:4230", "EPSG:4326", operation=ED50_TO_WGS84
    ).operation

    pretty = operation.to_wkt(pretty=True)

    assert pretty is not None
    assert pretty.count("\n") > 0
    assert "".join(pretty.split()) == "".join((operation.to_wkt() or "").split())


def test_operation_without_a_code_still_exports() -> None:
    """The case a code lookup cannot serve: a chain collapsed into one step."""
    collapsed = collapse_concatenated(CoordinateOperation.from_authority("EPSG", 8047))
    bound = BoundCRS(CRS.from_epsg(4230), CRS.from_epsg(4326), collapsed)

    operation = Transformation(bound, "EPSG:4326").operation

    assert operation.authority_code is None
    wkt = operation.to_wkt()
    assert wkt is not None
    assert wkt.startswith("COORDINATEOPERATION[")
    assert "Position Vector" in wkt


def test_same_datum_conversion_exports_the_projection() -> None:
    operation = Transformation("EPSG:4326", "EPSG:3395").operation

    wkt = operation.to_wkt()

    assert wkt is not None
    assert wkt.startswith("CONVERSION[")


def test_wkt_is_not_in_the_serialized_result() -> None:
    """to_json_dict() stays a summary; the WKT is opt-in through to_wkt()."""
    result = Transformation(
        "EPSG:4230", "EPSG:4326", operation=ED50_TO_WGS84
    ).transform([(10.75, 59.91)])

    assert "wkt" not in result.to_json_dict()["operation"]
    assert "projjson" not in result.to_json_dict()["operation"]


@pytest.mark.parametrize("pretty", [False, True])
def test_export_round_trips_back_into_pyproj(pretty: bool) -> None:
    """A consumer must be able to parse what we hand them."""
    operation = Transformation(
        "EPSG:4230", "EPSG:4326", operation=ED50_TO_WGS84
    ).operation

    wkt = operation.to_wkt(pretty=pretty)

    assert wkt is not None
    assert CoordinateOperation.from_string(wkt).name == operation.name


def test_operation_record_stays_hashable_and_terse() -> None:
    """The stored definition must not leak into repr or equality."""
    operation = Transformation(
        "EPSG:4230", "EPSG:4326", operation=ED50_TO_WGS84
    ).operation

    assert isinstance(hash(operation), int)
    assert "PROJJSON" not in repr(operation)
    assert "projjson" not in repr(operation)


def test_an_inverted_datum_shift_refuses_to_export() -> None:
    """An export PROJ would read back as the forward operation is withheld.

    PROJ marks a step it applies backwards by wrapping its authority as
    ``INVERSE(...)``, which neither WKT2 nor PROJJSON can express. Re-reading
    such an export silently yields the forward operation, reversing the sign
    of the datum shift, so None is returned rather than a document that looks
    complete and computes something else.
    """
    inverted = [
        c
        for c in available_operations("EPSG:4326", "EPSG:4267")
        if has_inverted_step(json.loads(c.projjson))
    ]
    assert inverted, "expected PROJ to offer an inverted candidate for this pair"

    for candidate in inverted:
        assert candidate.to_wkt() is None
        assert candidate.to_json_dict() is None
        # The raw text stays reachable for anyone who needs it knowing the caveat.
        assert candidate.projjson


def test_an_inverted_conversion_still_exports() -> None:
    """Only datum shifts are withheld, not inverted map projections.

    An inverse conversion is analytically invertible from the same parameters,
    so PROJ reconstructs it correctly and withholding it would lose a faithful
    export. ``EPSG:25831`` to ``EPSG:4258`` is exactly that: one inverted
    conversion, no datum change.
    """
    operation = Transformation("EPSG:25831", "EPSG:4258").operation
    assert operation.name == "Inverse of UTM zone 31N"
    assert "INVERSE(" in operation.projjson

    wkt = operation.to_wkt()
    assert wkt is not None

    point = (590000.0, 6700000.0)
    rebuilt = Transformer.from_pipeline(wkt).transform(*point)[:2]
    applied = Transformation("EPSG:25831", "EPSG:4258").transform(*point)
    assert rebuilt == pytest.approx(applied.coordinates[0][:2], abs=1e-12)


def test_a_forward_only_operation_still_exports_and_round_trips() -> None:
    """The guard must not withhold an export that is faithful."""
    candidate = available_operations("EPSG:4230", "EPSG:4326")[0]

    assert not has_inverted_step(candidate.to_json_dict())
    wkt = candidate.to_wkt()
    assert wkt is not None

    rebuilt = Transformer.from_pipeline(wkt).transform(10, 60, 100)[:2]
    applied = Transformation(
        "EPSG:4230", "EPSG:4326", operation=candidate.authority_code
    ).transform(10, 60, 100)
    assert rebuilt == pytest.approx(applied.coordinates[0][:2], abs=1e-12)
