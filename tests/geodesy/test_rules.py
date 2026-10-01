"""The non-negotiable rules are enforced, not merely documented.

The published dataset cannot cover these: it contains no ballpark records and
no missing grids, because it only holds transformations that produce a
trustworthy answer. These are the cases where the right outcome is a refusal,
so they are constructed deliberately.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pyproj
import pytest
from pyproj import CRS
from pyproj.crs import CoordinateOperation
from pyproj.crs.crs import BoundCRS
from pyproj.enums import TransformDirection
from pyproj.transformer import TransformerGroup

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    BallparkTransformationError,
    CoordinateOutOfRangeError,
    CoordinateReferenceSystem,
    MissingCoordinateEpochError,
    MissingGridError,
    OperationNotAvailableError,
    OperationRoute,
    Transformation,
    TransformationFailedError,
    UnresolvableCRSError,
    available_operations,
    transformation,
)
from geodetic_engine.geodesy.operation import is_ballpark
from geodetic_engine.geodesy.transformation import (
    _datum_names,
    _Pipeline,
    _require_in_range,
)
from tests.support import installed_proj_db

_PAYLOADS = Path(__file__).parents[1] / "persistablereference" / "payloads.jsonl"

# No datum shift is defined between the Puerto Rico datum and GDA94, so PROJ
# can only offer a ballpark geographic offset between them.
BALLPARK_SOURCE = "EPSG:4139"
BALLPARK_TARGET = "EPSG:4283"


def test_ballpark_only_pair_never_returns_coordinates() -> None:
    """A pair PROJ can only bridge with a ballpark yields no coordinates at all.

    Naming the operation is impossible here, since PROJ's ballpark offset has
    no EPSG code, so the ambiguity rule fires before the ballpark rule ever
    could: the refusal is the datum-change one. It is asserted by type so
    that an unrelated failure -- an unresolvable CRS, say -- cannot pass for
    it. ``BallparkTransformationError`` itself is a second line of defence
    that no stock input reaches, see ``test_ballpark_rule_is_a_second_line``.
    """
    with pytest.raises(AmbiguousOperationError, match="datum change"):
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


@pytest.mark.parametrize(
    "source,target",
    [
        ("EPSG:4230", "EPSG:4326"),
        (BALLPARK_SOURCE, BALLPARK_TARGET),
    ],
)
def test_allow_any_operation_cannot_bypass_strict_policy(
    source: str, target: str
) -> None:
    with pytest.raises(AmbiguousOperationError):
        Transformation(source, target, allow_any_operation=True)


def test_allow_any_operation_has_no_effect_when_an_operation_is_named() -> None:
    """Naming an operation is already an explicit choice; the flag adds nothing."""
    transformation = Transformation(
        "EPSG:4230", "EPSG:4326", operation="EPSG:1133", allow_any_operation=True
    )
    assert transformation.operation.authority_code == "EPSG:1133"
    assert transformation.operation.route != OperationRoute.ANY_OPERATION


def test_same_datum_conversion_needs_no_operation() -> None:
    """A projection change on one datum has a single answer, so it is allowed."""
    transformation = Transformation("EPSG:4326", "EPSG:3395")
    assert transformation.operation.route == OperationRoute.PROJ_DEFAULT
    assert transformation.operation.authority_code is not None


def test_custom_datums_are_not_equated_by_an_ensemble_suffix() -> None:
    source = CRS.from_wkt(
        'GEOGCS["Custom source",DATUM["Example",'
        'SPHEROID["Custom",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
    )
    target = CRS.from_wkt(
        'GEOGCS["Custom target",DATUM["Example ensemble",'
        'SPHEROID["Custom",6378136,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
    )
    assert _datum_names(source) != _datum_names(target)
    with pytest.raises(AmbiguousOperationError, match="datum change"):
        Transformation(source, target)


@pytest.mark.parametrize("code", [4326, 4258, 4979, 4937, 32632, 25832])
def test_verified_wkt1_ensembles_remain_same_datum_conversions(code: int) -> None:
    registered = CRS.from_epsg(code)
    legacy = CRS.from_wkt(registered.to_wkt("WKT1_ESRI"))
    assert _datum_names(legacy) == _datum_names(registered)
    point = (500000.0, 6600000.0) if registered.is_projected else (10.0, 60.0)
    if len(registered.axis_info) == 3:
        point = (*point, 100.0)
    for source, target in ((legacy, registered), (registered, legacy)):
        result = Transformation(source, target).transform(point)
        assert result.coordinates[0] == pytest.approx(point, abs=1e-8)


def test_a_registry_identifier_does_not_override_a_different_datum_definition() -> None:
    registered = CRS.from_epsg(4326)
    definition = CRS.from_wkt(registered.to_wkt("WKT1_ESRI")).to_json_dict()
    definition["id"] = {"authority": "EPSG", "code": 4326}
    definition["datum"]["ellipsoid"]["semi_major_axis"] = 6378000.0
    altered = CRS.from_json_dict(definition)
    assert altered.to_authority(auth_name="EPSG", min_confidence=0) == ("EPSG", "4326")
    assert not altered.equals(registered, ignore_axis_order=True)
    assert _datum_names(altered) != _datum_names(registered)


def test_requested_operation_is_the_one_reported() -> None:
    """The operation reported is the one asked for, verified against PROJ."""
    transformation = Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")
    assert transformation.operation.requested == "EPSG:3858"
    assert transformation.operation.authority_code == "EPSG:3858"
    assert transformation.operation.method_name == (
        "Geographic3D to GravityRelatedHeight (EGM2008)"
    )


def test_operation_folded_into_a_compound_crs_is_still_recognised() -> None:
    """A step touching only part of a compound target still satisfies its code.

    Applying EPSG:9484 into EPSG:6172 (a projected + NN54 height compound)
    makes PROJ rebuild it as an unidentified "PROJ-based operation method"
    pipeline, dropping its EPSG id, since it can no longer be looked up as
    the registered operation as-is. Its name survives that rebuild, so the
    request must still be honoured rather than refused as unavailable.
    """
    transformation = Transformation("EPSG:4937", "EPSG:6172", operation="EPSG:9484")
    assert transformation.operation.requested == "EPSG:9484"
    assert transformation.operation.authority_code is None  # PROJ dropped the id
    assert transformation.operation.name == "ETRS89-NOR [EUREF89] to NN54 height (1)"

    easting, northing, height = transformation.transform(
        (11.12789451, 63.58496782, 100)
    ).coordinates[0]
    assert easting == pytest.approx(605606.253, abs=1e-3)
    assert northing == pytest.approx(7052523.904, abs=1e-3)
    assert height == pytest.approx(61.742, abs=1e-3)


def test_operation_folded_into_a_compound_crs_is_recognised_in_reverse() -> None:
    """PROJ renames the same rebuilt step "Inverse of ..." in the other direction."""
    transformation = Transformation("EPSG:6172", "EPSG:4937", operation="EPSG:9484")
    assert transformation.operation.requested == "EPSG:9484"
    assert transformation.operation.name == (
        "Inverse of ETRS89-NOR [EUREF89] to NN54 height (1)"
    )

    lon, lat, height = transformation.transform(
        (605606.253, 7052523.904, 61.742)
    ).coordinates[0]
    assert lon == pytest.approx(11.12789451, abs=1e-6)
    assert lat == pytest.approx(63.58496782, abs=1e-6)
    assert height == pytest.approx(100, abs=1e-3)


def test_two_operations_fused_into_one_step_can_both_be_named() -> None:
    """A horizontal and a vertical operation fused into one step, named together.

    EPSG:4979 (WGS 84) to EPSG:6172 needs both a horizontal datum equivalence
    (EPSG:11028) and the vertical shift (EPSG:9484) already exercised above.
    PROJ fuses the two into one unidentified step, joining their names with
    " + ", since the compound target only needs each to touch part of it.
    Naming only one would leave the other chosen without being asked for, so
    ``operation=`` accepts a sequence naming every operation involved.
    """
    transformation = Transformation(
        "EPSG:4979", "EPSG:6172", operation=["EPSG:11028", "EPSG:9484"]
    )
    assert transformation.operation.requested == "EPSG:11028 + EPSG:9484"

    easting, northing, height = transformation.transform(
        (11.12789451, 63.58496782, 100)
    ).coordinates[0]
    assert easting == pytest.approx(605606.253, abs=1e-3)
    assert northing == pytest.approx(7052523.904, abs=1e-3)
    assert height == pytest.approx(61.742, abs=1e-3)


def test_two_operations_fused_into_one_step_round_trip_in_reverse() -> None:
    transformation = Transformation(
        "EPSG:6172", "EPSG:4979", operation=["EPSG:11028", "EPSG:9484"]
    )
    lon, lat, height = transformation.transform(
        (605606.253, 7052523.904, 61.742)
    ).coordinates[0]
    assert lon == pytest.approx(11.12789451, abs=1e-6)
    assert lat == pytest.approx(63.58496782, abs=1e-6)
    assert height == pytest.approx(100, abs=1e-3)


def test_a_candidate_from_available_operations_can_be_passed_directly() -> None:
    """An identified candidate is equivalent to naming its authority code."""
    candidates = available_operations("EPSG:4230", "EPSG:4326")
    named = Transformation(
        "EPSG:4230", "EPSG:4326", operation=candidates[0].authority_code
    )
    by_candidate = Transformation("EPSG:4230", "EPSG:4326", operation=candidates[0])

    point = (4.0, 52.0)
    assert by_candidate.operation.authority_code == named.operation.authority_code
    assert (
        by_candidate.transform(point).coordinates == named.transform(point).coordinates
    )


def test_an_unidentified_candidate_can_be_pinned_down_by_object() -> None:
    """The one case naming an authority code cannot cover: no id to name.

    PROJ fuses EPSG:11028 and EPSG:9484 into one step with no EPSG id of its
    own (see the fused-step tests above), so its
    :attr:`~geodetic_engine.geodesy.operation.OperationCandidate.authority_code`
    is None -- there is no string that names it. Passing the candidate object
    itself, found via its name instead, is the only way to pin it down
    precisely rather than falling back to ``allow_any_operation=True``.
    """
    candidates = available_operations("EPSG:4979", "EPSG:6172")
    unidentified = next(c for c in candidates if c.authority_code is None)
    assert unidentified.name.startswith("Inverse of")

    transformation = Transformation("EPSG:4979", "EPSG:6172", operation=unidentified)

    easting, northing, height = transformation.transform(
        (11.12789451, 63.58496782, 100)
    ).coordinates[0]
    assert easting == pytest.approx(605606.253, abs=1e-3)
    assert northing == pytest.approx(7052523.904, abs=1e-3)
    assert height == pytest.approx(61.742, abs=1e-3)


def test_a_ballpark_candidate_is_still_refused_when_passed_by_object() -> None:
    """Picking a candidate by object does not bypass the rules.

    A ballpark candidate has no code and no registered name, so passing it
    by object is refused as an operation PROJ cannot apply here rather than
    as a ballpark: it never gets far enough to be built.
    """
    candidates = available_operations(BALLPARK_SOURCE, BALLPARK_TARGET)
    ballpark = next(c for c in candidates if c.ballpark)

    with pytest.raises(OperationNotAvailableError):
        Transformation(BALLPARK_SOURCE, BALLPARK_TARGET, operation=ballpark)


def test_ballpark_rule_is_a_second_line() -> None:
    """The ballpark rule fires when a ballpark gets past the others.

    No stock input reaches it, because a ballpark has no code to name and
    the ambiguity rule refuses the pair first. It exists for the day PROJ
    offers a ballpark under some name a caller could pass, so it is exercised
    here by handing the constructor a ballpark pipeline directly.
    """
    group = TransformerGroup(
        CRS(BALLPARK_SOURCE), CRS(BALLPARK_TARGET), allow_ballpark=True, always_xy=True
    )
    ballpark = group.transformers[0]
    pipeline = _Pipeline(
        steps=((ballpark, TransformDirection.FORWARD),),
        core=ballpark,
        route=OperationRoute.PROJ_DEFAULT,
    )

    with (
        patch.object(transformation, "_resolve", return_value=pipeline),
        pytest.raises(BallparkTransformationError, match="ballpark"),
    ):
        Transformation(BALLPARK_SOURCE, BALLPARK_TARGET)


def test_unresolvable_crs_raises_our_own_error() -> None:
    """A bad CRS surfaces as this package's error, not a raw pyproj one."""
    with pytest.raises(UnresolvableCRSError):
        Transformation("EPSG:not-a-crs", "EPSG:4326")


def test_projected_coordinates_given_to_a_geographic_crs_are_named_as_such() -> None:
    """PROJ's bare "Invalid latitude" is replaced by an actionable message.

    Handing eastings and northings in metres to a geographic CRS is the most
    common way to get this wrong, and PROJ's own message names neither the
    CRS, nor its units, nor which value it read as latitude. All three have to
    appear or the caller cannot tell what to change.
    """
    transformation = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1612")

    with pytest.raises(CoordinateOutOfRangeError) as raised:
        transformation.transform(590000, 6700000)

    message = str(raised.value)
    assert "6700000" in message
    assert "degree" in message
    assert "EPSG:4230" in message
    assert "'Lon', 'Lat'" in message


def test_an_out_of_range_coordinate_stays_catchable_as_a_failed_transformation() -> (
    None
):
    """The new error is a subclass, so existing handlers keep working."""
    transformation = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1612")

    with pytest.raises(TransformationFailedError):
        transformation.transform(590000, 6700000)


def test_the_latitude_limit_comes_from_the_axis_unit_not_a_hard_coded_90() -> None:
    """EPSG:4807 declares grads, where the pole is at 100, not 90.

    Assuming degrees here would refuse a perfectly valid grad latitude, so the
    limit is derived from the axis's own conversion factor.
    """
    ntf_paris = CoordinateReferenceSystem.from_user_input("EPSG:4807")
    assert ntf_paris.axis_units == ("grad", "grad")

    _require_in_range(ntf_paris, [[0.0], [95.0]])

    with pytest.raises(CoordinateOutOfRangeError, match=r"\[-100, 100\]"):
        _require_in_range(ntf_paris, [[0.0], [105.0]])


def test_a_projected_crs_is_not_subject_to_the_latitude_check() -> None:
    """A northing of millions of metres is ordinary, not out of range."""
    transformation = Transformation("EPSG:25831", "EPSG:4258")

    lon, lat = transformation.transform(590000, 6700000).coordinates[0]
    assert lon == pytest.approx(4.634735474, abs=1e-9)
    assert lat == pytest.approx(60.426250138, abs=1e-9)


def test_an_out_of_range_longitude_is_refused() -> None:
    """PROJ wraps a longitude of 400 degrees to 40 and says nothing.

    A longitude beyond a full turn is a unit or order mistake as surely as a
    latitude beyond the pole, so it is refused with the same message. A
    dataset counted from 0 to 360 is a convention and stays accepted.
    """
    transformation = Transformation("EPSG:4326", "EPSG:32632")

    with pytest.raises(CoordinateOutOfRangeError, match=r"longitude 400\.0 degree"):
        transformation.transform(400.0, 60.0)

    accepted = transformation.transform(351.0, 60.0).coordinates[0]
    assert accepted == pytest.approx(
        transformation.transform(-9.0, 60.0).coordinates[0]
    )


def test_the_longitude_limit_comes_from_the_axis_unit_too() -> None:
    """In grads a full turn is 400, so 380 is a valid longitude there."""
    ntf_paris = CoordinateReferenceSystem.from_user_input("EPSG:4807")

    _require_in_range(ntf_paris, ((380.0,), (50.0,)))

    with pytest.raises(CoordinateOutOfRangeError, match=r"\[-400, 400\]"):
        _require_in_range(ntf_paris, ((405.0,), (50.0,)))


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


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_missing_grid_is_named_and_refused(tmp_path: Path) -> None:
    """A grid-based operation refuses to run when its grid is not installed."""
    with (
        _without_grids(tmp_path),
        pytest.raises(MissingGridError, match="not installed"),
    ):
        Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_a_missing_grid_is_named_even_when_no_candidate_offers_the_operation(
    tmp_path: Path,
) -> None:
    """An operation built on its own still blames the grid, not the operation.

    EPSG:3859 is not among the candidates PROJ offers for this pair, so it is
    built as a pipeline of its own, and PROJ reports its absent grid as a
    malformed pipeline step. Reporting that as "not available" would send the
    caller looking for a different operation instead of for the grid.
    """
    with (
        _without_grids(tmp_path),
        pytest.raises(MissingGridError, match="not installed"),
    ):
        Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3859")


def test_grids_are_reported_when_present() -> None:
    """A successful grid transformation records the grid it consumed."""
    transformation = Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")
    assert [grid.name for grid in transformation.grids] == ["us_nga_egm08_25.tif"]
    assert all(grid.available for grid in transformation.grids)


@pytest.fixture(scope="module")
def grid_payloads() -> dict[str, str]:
    """The committed OSDU payloads that state grid-based operations."""
    with _PAYLOADS.open(encoding="utf-8") as stream:
        return {
            entry["case"]: entry["payload"]
            for entry in (json.loads(line) for line in stream if line.strip())
        }


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_a_stated_ntv2_operation_names_its_missing_grid(tmp_path: Path) -> None:
    """A stated operation is held to the missing-grid rule like a named one.

    PROJ cannot even build the pipeline without the grid, and reports that as
    a malformed step. Reporting *that* as "operation not available" would
    send the caller looking for another operation instead of for the grid.

    Run in a fresh interpreter: PROJ keeps a grid it has opened in memory, so
    after any earlier test read this one the step would compile and take the
    on-disk path below instead, which names PROJ's file rather than this one.
    """
    script = (
        "import json, os, sys\n"
        "from geodetic_engine.geodesy import MissingGridError, Transformation\n"
        "payloads = {json.loads(line)['case']: json.loads(line)['payload']\n"
        "            for line in open(sys.argv[1])}\n"
        "try:\n"
        "    Transformation('EPSG:4202', 'EPSG:4283',\n"
        "                   operation=payloads['st_ntv2_grid'])\n"
        "except MissingGridError as error:\n"
        "    print(error)\n"
        "    sys.exit(0)\n"
        "sys.exit('not refused')\n"
    )
    empty = tmp_path / "proj-no-grids"
    empty.mkdir()
    shutil.copy(installed_proj_db(), empty / "proj.db")

    result = subprocess.run(
        [sys.executable, "-c", script, str(_PAYLOADS)],
        env={**os.environ, "PROJ_DATA": str(empty), "PROJ_NETWORK": "OFF"},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "A66 National (13.09.01).gsb" in result.stdout


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_a_stated_ntv2_operation_compiled_from_cache_names_its_missing_grid(
    tmp_path: Path, grid_payloads: dict[str, str]
) -> None:
    """With the grid already in PROJ's memory, the disk check names the file.

    The step compiles because PROJ opened the grid earlier, so the refusal
    comes from checking the pipeline's own file on disk, under PROJ's name.
    """
    payload = grid_payloads["st_ntv2_grid"]
    Transformation("EPSG:4202", "EPSG:4283", operation=payload).transform(
        [(147.0, -35.0)]
    )

    with (
        _without_grids(tmp_path),
        pytest.raises(
            MissingGridError, match=r"au_icsm_A66_National_13_09_01\.tif"
        ) as raised,
    ):
        Transformation("EPSG:4202", "EPSG:4283", operation=payload)
    assert "needs 1 grid file(s)" in str(raised.value)


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_a_stated_nadcon_operation_names_its_missing_grid(
    tmp_path: Path, grid_payloads: dict[str, str]
) -> None:
    """A grid PROJ has already opened once still has to be on disk.

    PROJ keeps opened grids in memory, so after another test has read
    ``us_noaa_conus.tif`` the pipeline compiles without the file and would
    fail at the first coordinate. The rule is that the refusal comes at
    construction and names the grid.
    """
    Transformation("EPSG:4267", "EPSG:4269", operation=grid_payloads["st_nadcon_grid"])

    with (
        _without_grids(tmp_path),
        pytest.raises(MissingGridError, match="not installed") as raised,
    ):
        Transformation(
            "EPSG:4267", "EPSG:4269", operation=grid_payloads["st_nadcon_grid"]
        )
    message = str(raised.value)
    assert "conus.las" in message or "us_noaa_conus.tif" in message


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_a_bound_crs_grid_operation_names_its_missing_grid(tmp_path: Path) -> None:
    """The renamed-grid case, without the grid: refused, not run into the ground.

    The registry calls ``conus.las`` missing whether or not PROJ's
    ``us_noaa_conus.tif`` is installed, so the pipeline's own file is what
    has to be checked -- on disk, not merely compiled. And only that file is
    reported: the legacy names the pipeline does not read are not needed, so
    listing them would send the caller after files that would not help.
    """
    wgs84 = CRS.from_epsg(4326)
    nad83 = BoundCRS(
        CRS.from_epsg(4269), wgs84, CoordinateOperation.from_authority("EPSG", 1188)
    )
    nad27 = BoundCRS(
        CRS.from_epsg(4267), wgs84, CoordinateOperation.from_authority("EPSG", 15851)
    )
    Transformation(nad83, nad27).transform([(-95.0, 30.0)])

    with (
        _without_grids(tmp_path),
        pytest.raises(MissingGridError, match=r"us_noaa_conus\.tif") as raised,
    ):
        Transformation(nad83, nad27)
    message = str(raised.value)
    assert "needs 1 grid file(s)" in message
    assert "conus.las" not in message
    assert "conus.los" not in message


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_a_superseded_grid_filename_is_not_reported_missing() -> None:
    """A grid PROJ ships under a different name than the authority published.

    EPSG:15851 cites the NADCON pair ``conus.las``/``conus.los``, which PROJ
    no longer distributes; it reads the installed ``us_noaa_conus.tif``
    instead, resolved through proj.db's ``grid_alternatives``. The registry
    still reports the published names as missing, so believing it refuses a
    transformation PROJ performs perfectly well -- the missing-grid rule
    failing in the opposite direction to the one it exists to prevent.

    Built from stock EPSG codes rather than from a registry CRS, so the case
    stands on the official database alone.
    """
    wgs84 = CRS.from_epsg(4326)
    nad83 = BoundCRS(
        CRS.from_epsg(4269), wgs84, CoordinateOperation.from_authority("EPSG", 1188)
    )
    nad27 = BoundCRS(
        CRS.from_epsg(4267), wgs84, CoordinateOperation.from_authority("EPSG", 15851)
    )

    transformation = Transformation(nad83, nad27, allow_any_operation=True)

    assert [grid.name for grid in transformation.grids] == ["conus.las", "conus.los"]
    assert all(grid.available for grid in transformation.grids)
    # The pipeline names what is actually read, which is neither of those.
    assert "us_noaa_conus.tif" in (
        transformation.transform([(-95.0, 30.0)]).pipeline or ""
    )


@pytest.mark.filterwarnings("ignore:Best transformation is not available.*:UserWarning")
def test_two_bound_crss_chain_without_being_told_which_operation() -> None:
    """Early binding applies when the withheld candidate is the declared one.

    Both CRSs name their own transformation to WGS 84, so the datum change is
    not ambiguous and must not be reported as such. PROJ chains them readily,
    but ``TransformerGroup`` withholds that one candidate as not instantiable
    over the ``conus.las`` the authority published, rather than the installed
    ``us_noaa_conus.tif`` it would read. Falling back to the candidate search
    there leaves early binding with nothing to apply.
    """
    wgs84 = CRS.from_epsg(4326)
    nad83 = BoundCRS(
        CRS.from_epsg(4269), wgs84, CoordinateOperation.from_authority("EPSG", 1188)
    )
    nad27 = BoundCRS(
        CRS.from_epsg(4267), wgs84, CoordinateOperation.from_authority("EPSG", 15851)
    )

    transformation = Transformation(nad83, nad27)

    assert transformation.operation.route == OperationRoute.BOUND
    assert transformation.operation.ballpark is False
    result = transformation.transform([(-95.0, 30.0)])
    assert "us_noaa_conus.tif" in (result.pipeline or "")
    assert result.coordinates[0] == pytest.approx((-94.99979514, 29.99978169), abs=1e-8)


@contextmanager
def _without_grids(tmp_path: Path) -> Generator[None]:
    """Point PROJ at a directory holding proj.db but no grid files.

    The database has to come along or no CRS could be resolved at all, which
    would test something else entirely.
    """
    empty = tmp_path / "proj-no-grids"
    empty.mkdir()
    shutil.copy(installed_proj_db(), empty / "proj.db")

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
