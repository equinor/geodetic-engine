"""Listing coordinate operations, against what geodesy and pyproj report."""

from __future__ import annotations

from typing import Any

import pyproj
import pytest
from pyproj.crs import CoordinateOperation

from geodetic_engine import catalog
from geodetic_engine.catalog import InvalidFilterError, OperationKind
from geodetic_engine.geodesy import CoordinateReferenceSystem, transform


def _codes(listing: catalog.Listing[Any]) -> set[str]:
    return {entry.authority_code for entry in listing}


def test_finds_the_transformations_registered_between_two_crss() -> None:
    found = catalog.operations(source_crs="EPSG:4230", target_crs="EPSG:4326")
    assert {"EPSG:1133", "EPSG:1612", "EPSG:1613"} <= _codes(found)
    for entry in found:
        assert entry.source_crs is not None and entry.target_crs is not None
        assert entry.source_crs.authority_code == "EPSG:4230"
        assert entry.target_crs.authority_code == "EPSG:4326"
        assert entry.source_crs.name == "ED50"


def test_either_direction_also_finds_operations_registered_the_other_way() -> None:
    reversed_ = catalog.operations(source_crs="EPSG:4326", target_crs="EPSG:4230")
    assert "EPSG:1612" not in _codes(reversed_)
    both = catalog.operations(
        source_crs="EPSG:4326", target_crs="EPSG:4230", either_direction=True
    )
    assert "EPSG:1612" in _codes(both)


def test_registered_rows_only_not_what_proj_assembles() -> None:
    # PROJ chains ED50 -> WGS 84 -> UTM 31N, but nothing is registered so.
    assert not catalog.operations(source_crs="EPSG:4230", target_crs="EPSG:32631")


@pytest.mark.parametrize(
    "source",
    [
        "EPSG:4230",
        4230,
        "ED50",
        pyproj.CRS.from_epsg(4230),
        CoordinateReferenceSystem.from_user_input("EPSG:4230"),
        ["EPSG:4230"],
    ],
)
def test_source_crs_forms(source: object) -> None:
    found = catalog.operations(source_crs=source, target_crs="EPSG:4326")
    assert "EPSG:1612" in _codes(found)


def test_an_unknown_crs_is_refused() -> None:
    with pytest.raises(InvalidFilterError):
        catalog.operations(source_crs="EPSG:999999999")


def test_lists_deprecated_operations_proj_never_offers() -> None:
    deprecated = catalog.operations(authority="EPSG", deprecated=True, limit=20)
    assert deprecated
    assert all(entry.deprecated for entry in deprecated)


def test_max_accuracy_leaves_out_unstated_accuracy() -> None:
    found = catalog.operations(source_crs="EPSG:4230", max_accuracy=1.0)
    assert found
    assert all(e.accuracy is not None and e.accuracy <= 1.0 for e in found)


def test_area_filters() -> None:
    found = catalog.operations(
        source_crs="EPSG:4230",
        target_crs="EPSG:4326",
        area_of_interest=(4.0, 63.0, 5.0, 64.0),
    )
    assert "EPSG:1612" in _codes(found)
    assert "EPSG:1613" not in _codes(found)
    named = catalog.operations(source_crs="EPSG:4230", area_name="Norway")
    assert {"EPSG:1612", "EPSG:1613"} <= _codes(named)


def test_method_by_code_name_or_entry_agree() -> None:
    by_code = _codes(catalog.operations(method="EPSG:9615", deprecated=None))
    by_name = _codes(catalog.operations(method="NTv2", deprecated=None))
    method = catalog.methods(code="EPSG:9615").one()
    by_entry = _codes(catalog.operations(method=method, deprecated=None))
    assert by_code == by_name == by_entry
    assert len(by_code) == method.operation_count


def test_an_unknown_method_is_refused() -> None:
    with pytest.raises(InvalidFilterError, match="operation method"):
        catalog.operations(method="Not a method")


def test_kinds() -> None:
    conversion = catalog.operations(code="EPSG:16032").one()
    assert conversion.kind is OperationKind.CONVERSION
    assert conversion.source_crs is None and conversion.target_crs is None
    assert conversion.method is not None
    assert conversion.method.name == "Transverse Mercator"
    chain = catalog.operations(code="EPSG:8047").one()
    assert chain.kind is OperationKind.CONCATENATED_OPERATION
    assert chain.method is None
    assert len(chain.steps) == 2
    found = catalog.operations(authority="EPSG", kind="concatenated_operation")
    assert {e.projjson_type for e in found} == {"ConcatenatedOperation"}


def test_grid_based_operations_name_their_grids() -> None:
    entry = catalog.operations(code="EPSG:15851").one()
    assert entry.grid_based
    assert set(entry.grid_names) == {"conus.las", "conus.los"}
    found = catalog.operations(authority="EPSG", grid_based=True, limit=50)
    assert all(e.grid_names for e in found)


def test_grids_available_is_what_proj_reports() -> None:
    grid_based = catalog.operations(grid_based=True, area_name="Norway")
    available = catalog.operations(
        grid_based=True, area_name="Norway", grids_available=True
    )
    missing = catalog.operations(
        grid_based=True, area_name="Norway", grids_available=False
    )
    assert _codes(available) | _codes(missing) == _codes(grid_based)
    assert not _codes(available) & _codes(missing)
    for entry in grid_based[:5]:
        operation = CoordinateOperation.from_authority(entry.auth_name, entry.code)
        expected = all(grid.available for grid in operation.grids)
        assert entry.grids_available() is expected


def test_an_operation_without_grids_counts_as_available() -> None:
    entry = catalog.operations(code="EPSG:1612").one()
    assert entry.grids() == ()
    assert entry.grids_available() is True


def test_superseded() -> None:
    superseded = catalog.operations(authority="EPSG", superseded=True, limit=10)
    assert superseded
    assert all(entry.superseded_by for entry in superseded)


def test_its_code_is_an_operation_for_transform() -> None:
    entry = catalog.operations(code="EPSG:1612").one()
    result = transform(
        "EPSG:4230", "EPSG:4326", (2.5, 63.5), operation=entry.authority_code
    )
    assert result.operation.authority_code == "EPSG:1612"


def test_to_json_dict_is_pyprojs_projjson(projjson_validator: Any) -> None:
    entry = catalog.operations(code="EPSG:1612").one()
    definition = entry.to_json_dict()
    assert definition == CoordinateOperation.from_epsg(1612).to_json_dict()
    projjson_validator.validate(definition)


def test_version_and_accuracy() -> None:
    entry = catalog.operations(code="EPSG:1612").one()
    assert entry.accuracy == 1.0
    assert entry.version == "EPSG-Nor N62 2001"
    assert entry.to_summary_dict()["accuracy"] == "1.0"


@pytest.mark.parametrize("value", [-1, "1", True])
def test_a_malformed_max_accuracy_is_refused(value: object) -> None:
    with pytest.raises(InvalidFilterError, match="max_accuracy"):
        catalog.operations(max_accuracy=value)  # type: ignore[arg-type]
