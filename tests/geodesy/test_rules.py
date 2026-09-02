"""The non-negotiable rules are enforced, not merely documented.

The published dataset cannot cover these: it contains no ballpark records and
no missing grids, because it only holds transformations that produce a
trustworthy answer. These are the cases where the right outcome is a refusal,
so they are constructed deliberately.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pyproj
import pytest
from pyproj import CRS
from pyproj.transformer import TransformerGroup

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    GeodesyError,
    MissingCoordinateEpochError,
    MissingGridError,
    OperationNotAvailableError,
    OperationRoute,
    Transformation,
    UnresolvableCRSError,
)
from geodetic_engine.geodesy.operation import is_ballpark

# No datum shift is defined between the Puerto Rico datum and GDA94, so PROJ
# can only offer a ballpark geographic offset between them.
BALLPARK_SOURCE = "EPSG:4139"
BALLPARK_TARGET = "EPSG:4283"


def test_ballpark_only_pair_never_returns_coordinates() -> None:
    """A pair PROJ can only bridge with a ballpark yields no coordinates at all.

    Which refusal comes first is not the point. Naming the operation is
    impossible here, since PROJ's ballpark offset has no EPSG code, so the
    ambiguity rule fires before the ballpark rule. Either way no approximate
    number reaches the caller.
    """
    with pytest.raises(GeodesyError):
        Transformation(BALLPARK_SOURCE, BALLPARK_TARGET)


def test_ballpark_detector_recognises_projs_own_ballpark() -> None:
    """The detector fires on the operation PROJ actually builds for that pair."""
    group = TransformerGroup(
        CRS(BALLPARK_SOURCE), CRS(BALLPARK_TARGET), allow_ballpark=True, always_xy=True
    )
    definition = group.transformers[0].to_json_dict()
    assert "ballpark" in group.transformers[0].description.lower()
    assert is_ballpark(definition)


def test_real_operation_is_not_mistaken_for_a_ballpark() -> None:
    """A genuine datum shift is not caught by the ballpark rule."""
    transformation = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1133")
    assert transformation.operation.authority_code == "EPSG:1133"
    assert transformation.transform([(4.0, 52.0)]).count == 1


def test_unknown_operation_is_refused_rather_than_substituted() -> None:
    """An operation PROJ cannot apply here raises instead of falling back."""
    with pytest.raises(OperationNotAvailableError):
        Transformation("EPSG:4326", "EPSG:3395", operation="EPSG:1133")


def test_nonexistent_operation_is_refused() -> None:
    """A code that is not in the database raises rather than being ignored."""
    with pytest.raises(OperationNotAvailableError):
        Transformation("EPSG:4326", "EPSG:3395", operation="EPSG:99999999")


def test_datum_change_without_a_named_operation_is_ambiguous() -> None:
    """PROJ is not allowed to pick the datum shift silently."""
    with pytest.raises(AmbiguousOperationError, match="datum change"):
        Transformation("EPSG:4230", "EPSG:4326")


def test_same_datum_conversion_needs_no_operation() -> None:
    """A projection change on one datum has a single answer, so it is allowed."""
    transformation = Transformation("EPSG:4326", "EPSG:3395")
    assert transformation.operation.route == OperationRoute.PROJ_DEFAULT
    assert transformation.operation.authority_code is not None


def test_requested_operation_is_the_one_reported() -> None:
    """The operation reported is the one asked for, verified against PROJ."""
    transformation = Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")
    assert transformation.operation.requested == "EPSG:3858"
    assert transformation.operation.authority_code == "EPSG:3858"
    assert transformation.operation.method_name == (
        "Geographic3D to GravityRelatedHeight (EGM2008)"
    )


def test_unresolvable_crs_raises_our_own_error() -> None:
    """A bad CRS surfaces as this package's error, not a raw pyproj one."""
    with pytest.raises(UnresolvableCRSError):
        Transformation("EPSG:not-a-crs", "EPSG:4326")


def test_time_dependent_operation_requires_an_epoch() -> None:
    """An operation that reads the epoch refuses to run without one."""
    transformation = Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277")
    assert transformation.requires_epoch
    with pytest.raises(MissingCoordinateEpochError, match="coordinate epoch"):
        transformation.transform([(-2593197.524, 5656917.6189, -1394397.8828)])


def test_epoch_changes_the_result() -> None:
    """The epoch is passed through to PROJ rather than silently dropped."""
    transformation = Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277")
    point = [(-2593197.524, 5656917.6189, -1394397.8828)]
    early = transformation.transform(point, coordinate_epoch=1994.0).coordinates[0]
    late = transformation.transform(point, coordinate_epoch=2024.0).coordinates[0]
    assert early != late


def test_static_operation_does_not_demand_an_epoch() -> None:
    """A Helmert without rates gives the same answer at every epoch."""
    transformation = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1133")
    assert not transformation.requires_epoch
    assert transformation.transform([(4.0, 52.0)]).count == 1


def test_missing_grid_is_named_and_refused(tmp_path: Path) -> None:
    """A grid-based operation refuses to run when its grid is not installed."""
    with (
        _without_grids(tmp_path),
        pytest.raises(MissingGridError, match="not installed"),
    ):
        Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")


def test_grids_are_reported_when_present() -> None:
    """A successful grid transformation records the grid it consumed."""
    transformation = Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")
    assert [grid.name for grid in transformation.grids] == ["us_nga_egm08_25.tif"]
    assert all(grid.available for grid in transformation.grids)


@contextmanager
def _without_grids(tmp_path: Path) -> Generator[None]:
    """Point PROJ at a directory holding proj.db but no grid files.

    The database has to come along or no CRS could be resolved at all, which
    would test something else entirely.
    """
    empty = tmp_path / "proj-no-grids"
    empty.mkdir()
    source = Path(pyproj.datadir.get_data_dir())
    shutil.copy(source / "proj.db", empty / "proj.db")

    previous_env = os.environ.get("PROJ_DATA")
    previous_dir = pyproj.datadir.get_data_dir()
    os.environ["PROJ_DATA"] = str(empty)
    pyproj.datadir.set_data_dir(str(empty))
    try:
        yield
    finally:
        pyproj.datadir.set_data_dir(previous_dir)
        if previous_env is None:
            os.environ.pop("PROJ_DATA", None)
        else:
            os.environ["PROJ_DATA"] = previous_env
