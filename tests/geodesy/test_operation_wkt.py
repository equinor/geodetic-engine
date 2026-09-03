"""The operation that was applied must be re-exportable, not just named.

A code is enough to look an operation up again only when the authority defines
it. A collapsed chain has no code, so the WKT has to come from what PROJ built.
"""

from __future__ import annotations

import pytest
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

from geodetic_engine.geodesy import Transformation
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
