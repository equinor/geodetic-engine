"""Parameter serialization refuses information loss."""

from dataclasses import replace

import pytest

from geodetic_engine.projdb import parameters as pm
from geodetic_engine.projdb.errors import ProjDbBuildError


def parameter(code: str) -> pm.Parameter:
    """A finite metre-valued test parameter."""
    return pm.Parameter(code, "test", 1.0, uom_auth_name="EPSG", uom_code="9001")


def test_mixed_helmert_units_are_refused() -> None:
    values = [parameter(code) for code in ("8605", "8606", "8607")]
    values[1] = replace(values[1], uom_code="9002")
    with pytest.raises(ProjDbBuildError, match="mixed units"):
        pm.helmert_columns(values)


@pytest.mark.parametrize("count,conversion", [(8, True), (10, False)])
def test_numeric_slot_overflow_is_refused(count: int, conversion: bool) -> None:
    values = [parameter(str(index)) for index in range(count)]
    with pytest.raises(ProjDbBuildError, match="slots"):
        (pm.conversion_columns if conversion else pm.other_columns)(values)


def test_grid_slot_overflow_is_refused() -> None:
    with pytest.raises(ProjDbBuildError, match="slots"):
        pm.grid_columns(
            [pm.Parameter(str(index), "grid", file="test.tif") for index in range(3)]
        )


def test_unknown_helmert_parameter_is_refused() -> None:
    with pytest.raises(ProjDbBuildError, match="unsupported Helmert"):
        pm.helmert_columns([parameter("99999")])


def test_duplicate_parameters_are_refused() -> None:
    with pytest.raises(ProjDbBuildError, match="duplicate"):
        pm.other_columns([parameter("1"), parameter("1")])
