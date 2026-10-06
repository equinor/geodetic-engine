"""Trajectories end to end: azimuth reference, units, provenance and datums."""

from __future__ import annotations

import numpy as np
import pytest
from pyproj import CRS
from pyproj.crs import BoundCRS, CompoundCRS, CoordinateOperation

from geodetic_engine.geodesy import (
    AmbiguousOperationError,
    TransformationFailedError,
    UnsupportedCRSError,
)
from geodetic_engine.persistablereference import to_persistable_reference
from geodetic_engine.welltrajectory import (
    InvalidInputError,
    Method,
    NorthReference,
    Survey,
    TrajectoryInput,
    Wellhead,
    compute_trajectory,
)

from .conftest import VOLVE_F1, legacy, volve_f1

UTM31N = "EPSG:32631"
OFF_CENTRAL_MERIDIAN = (666000.0, 6660000.0, 30.0)
AZIMUTH = np.array([10.0, 30.0, 45.0, 50.0])
SURVEY = Survey([0, 500, 1500, 2500], [0, 20, 60, 70], AZIMUTH)


def test_grid_azimuths_are_true_azimuths_turned_by_the_convergence() -> None:
    by_grid = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, north="GN")
    gamma = float(by_grid.factors.grid_convergence[0])
    turned = Survey(SURVEY.md, SURVEY.inclination, AZIMUTH + gamma)

    by_true = compute_trajectory(turned, OFF_CENTRAL_MERIDIAN, UTM31N, north="TN")

    assert gamma > 1.0
    assert by_grid.x == pytest.approx(by_true.x, abs=1e-9)
    assert by_grid.y == pytest.approx(by_true.y, abs=1e-9)
    assert by_grid.azimuth_grid == pytest.approx(AZIMUTH)
    assert by_grid.azimuth_true == pytest.approx(AZIMUTH + gamma)


def test_grid_azimuths_in_a_geographic_crs_are_refused() -> None:
    with pytest.raises(UnsupportedCRSError, match="grid north"):
        compute_trajectory(SURVEY, (4.0, 58.0), "EPSG:4326", north="GN")


@pytest.mark.parametrize(
    "method", [Method.AZIMUTHAL_EQUIDISTANT, Method.ENU, Method.LMP]
)
def test_an_out_of_range_geographic_wellhead_is_refused(method: Method) -> None:
    well = TrajectoryInput.from_arrays(
        [0, 100],
        [90, 90],
        [0, 0],
        wellhead=(6, 95),
        crs="EPSG:4326",
        north_reference="TN",
        method=method,
    )

    with pytest.raises(TransformationFailedError, match="range"):
        well.compute()


def test_depths_are_reported_below_the_wellhead_elevation() -> None:
    trajectory = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N)

    assert trajectory.z == pytest.approx(30.0 - trajectory.tvd)
    assert trajectory.east[0] == trajectory.north[0] == trajectory.tvd[0] == 0.0


@pytest.mark.parametrize("method", list(Method))
def test_feet_and_metres_describe_the_same_hole(method: Method) -> None:
    in_feet = Survey(SURVEY.md / 0.3048, SURVEY.inclination, AZIMUTH, md_unit="ft")
    wellhead = Wellhead(666000.0, 6660000.0, 30.0 / 0.3048)

    feet = compute_trajectory(in_feet, wellhead, UTM31N, z_unit="ft", method=method)
    metres = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, method=method)

    assert feet.x == pytest.approx(metres.x, abs=1e-6)
    assert feet.y == pytest.approx(metres.y, abs=1e-6)
    assert feet.tvd * 0.3048 == pytest.approx(metres.tvd, abs=1e-9)
    assert feet.z * 0.3048 == pytest.approx(metres.z, abs=1e-9)
    assert feet.dls_length == 100.0
    assert metres.dls_length == 30.0
    assert feet.dls() == pytest.approx(metres.dls() * 100 * 0.3048 / 30)


def test_md_in_metres_can_report_depths_in_feet() -> None:
    feet = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, z_unit="ft")
    metres = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N)

    assert feet.md == pytest.approx(metres.md)
    assert feet.tvd == pytest.approx(metres.tvd / 0.3048)
    assert feet.z[0] == pytest.approx(30.0)


@pytest.mark.parametrize("method", list(Method))
def test_resampling_and_interpolating_stay_on_the_trajectory(method: Method) -> None:
    trajectory = compute_trajectory(
        SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, method=method, md_step=100
    )
    survey_only = compute_trajectory(
        SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, method=method
    )

    assert len(trajectory) == 26
    kept = trajectory.is_survey_station
    assert trajectory.x[kept] == pytest.approx(survey_only.x, abs=1e-9)
    assert trajectory.y[kept] == pytest.approx(survey_only.y, abs=1e-9)

    points = survey_only.interpolate([1500, 700])
    assert points.x[0] == pytest.approx(survey_only.x[2], abs=1e-9)
    assert points.x[1] == pytest.approx(trajectory.x[trajectory.md == 700][0], abs=1e-9)
    assert "interpolated 2 points on the minimum curvature arcs" in points.operations

    listed = compute_trajectory(
        SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, method=method, md_points=[700]
    )
    assert listed.x[listed.is_survey_station] == pytest.approx(survey_only.x, abs=1e-9)
    assert listed.y[listed.is_survey_station] == pytest.approx(survey_only.y, abs=1e-9)
    assert listed.x[~listed.is_survey_station] == pytest.approx(points.x[1], abs=1e-9)


@pytest.mark.parametrize("method", list(Method))
@pytest.mark.parametrize("depths", [[2500.0], [2500.0, 0.0, 1500.0, 2500.0], []])
def test_interpolation_preserves_query_order_and_shape(
    method: Method, depths: list[float]
) -> None:
    trajectory = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, method=method)

    queried = trajectory.interpolate(depths)

    indices = np.searchsorted(trajectory.md, depths)
    assert queried.md.tolist() == depths
    assert queried.x == pytest.approx(trajectory.x[indices], abs=1e-9)
    assert queried.y == pytest.approx(trajectory.y[indices], abs=1e-9)
    assert queried.z == pytest.approx(trajectory.z[indices], abs=1e-9)
    assert len(queried.to_dataframe()) == len(depths)


def test_the_provenance_says_what_was_done() -> None:
    trajectory = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, north="GN")

    text = " | ".join(trajectory.operations)
    assert "EPSG:32631" in text
    assert "grid convergence" in text
    assert "turned onto true north" in text
    assert "minimum curvature over 4 survey stations" in text
    assert "Azimuthal Equidistant" in text


def test_a_derived_trajectory_keeps_how_its_model_was_made() -> None:
    no_azimuth = Survey(SURVEY.md, SURVEY.inclination)
    trajectory = compute_trajectory(
        no_azimuth, OFF_CENTRAL_MERIDIAN, UTM31N, north="TN", md_step=100
    )

    twice = trajectory.resample(50).interpolate([700.0])

    steps = twice.operations
    assert "the survey states no azimuth; taken as 0 throughout" in steps
    assert "minimum curvature over 4 survey stations" in steps
    assert "interpolated 1 points on the minimum curvature arcs" in steps
    assert not any(step.startswith("resampled") for step in steps)


def test_method_and_north_reference_names_are_read_in_any_case() -> None:
    lower = compute_trajectory(
        SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N, north="gn", method="lmp"
    )

    assert lower.method is Method.LMP
    assert lower.north_reference is NorthReference.GRID


def test_a_wellhead_that_is_not_finite_is_refused() -> None:
    with pytest.raises(InvalidInputError, match="finite"):
        compute_trajectory(SURVEY, (666000.0, 6660000.0, float("nan")), UTM31N)


def test_a_table_and_the_factors_at_every_point() -> None:
    trajectory = compute_trajectory(SURVEY, OFF_CENTRAL_MERIDIAN, UTM31N)

    table = trajectory.to_dataframe()
    factors = trajectory.projection_factors()

    assert list(table.columns[:4]) == [
        "md",
        "inclination",
        "azimuth_true",
        "azimuth_grid",
    ]
    assert len(table) == len(factors.scale_factor) == 4
    assert factors.grid_convergence[0] == pytest.approx(
        trajectory.factors.grid_convergence[0]
    )


def test_moving_to_wgs84_needs_the_datum_shift_named() -> None:
    trajectory = compute_trajectory(SURVEY, (500000.0, 6600000.0), "EPSG:23032")

    with pytest.raises(AmbiguousOperationError):
        trajectory.to_geographic()

    result = trajectory.to_geographic(operation="EPSG:1133")
    assert result.operation.authority_code == "EPSG:1133"
    assert result.count == 4


@pytest.mark.parametrize("compound", [False, True])
def test_a_bound_crs_names_its_own_datum_shift(compound: bool) -> None:
    bound = BoundCRS(
        CRS("EPSG:23032"), CRS("EPSG:4326"), CoordinateOperation.from_epsg(1133)
    )
    source = (
        CompoundCRS("Bound ED50 + height", [bound, CRS("EPSG:5776")])
        if compound
        else bound
    )
    representations = [source, source.to_wkt()]
    if not compound:
        representations.append(to_persistable_reference(source))
    for crs in representations:
        trajectory = compute_trajectory(SURVEY, (500000.0, 6600000.0), crs)

        named = compute_trajectory(SURVEY, (500000.0, 6600000.0), "EPSG:23032")
        assert trajectory.x == pytest.approx(named.x, abs=1e-9)
        wgs84 = trajectory.to_geographic().coordinates.to_numpy()
        expected = named.to_geographic(operation="EPSG:1133").coordinates.to_numpy()
        assert wgs84 == pytest.approx(expected, abs=1e-12)


def test_the_legacy_horizontal_well_runs_due_east_on_the_grid() -> None:
    """Horizontal from the surface, grid azimuth 90: on the grid that is a
    straight line east, stretched by the scale factor."""
    data = legacy("horizontal_well_test_data")
    rows = np.asarray(data["rows"], dtype=float)
    survey = Survey(rows[:, 0], rows[:, 2], rows[:, 1])

    for method in Method:
        trajectory = compute_trajectory(
            survey, data["reference_point"], data["crs"], north="GN", method=method
        )
        assert np.all(np.diff(trajectory.x) > 0)
        assert trajectory.tvd == pytest.approx(0.0, abs=1e-9)

    local = compute_trajectory(
        survey,
        data["reference_point"],
        data["crs"],
        north="GN",
        method="GridNorthLocal",
    )
    k = local.factors.scale_factor[0]
    assert local.y == pytest.approx(6500000.0, abs=1e-6)
    assert local.x == pytest.approx(400000.0 + k * rows[:, 0], abs=1e-6)


@pytest.mark.parametrize("method", list(Method))
def test_the_legacy_vertical_well_stays_at_its_reference(method: Method) -> None:
    data = legacy("lmp_trajectory_data_vertical_well")
    rows = np.asarray(data["rows"], dtype=float)
    survey = Survey(rows[:, 0], rows[:, 4], rows[:, 5])

    trajectory = compute_trajectory(
        survey, data["reference_point"], data["crs"], north="GN", method=method
    )

    assert trajectory.x == pytest.approx(rows[:, 1], abs=1e-6)
    assert trajectory.y == pytest.approx(rows[:, 2], abs=1e-6)
    assert trajectory.z == pytest.approx(rows[:, 3], abs=1e-9)


def test_the_legacy_lmp_table_contradicts_its_own_angles() -> None:
    """TVD over a 30 m step whose inclination never exceeds 2.5 degrees is at
    least 30 cos(2.5) = 29.97 m; TRJ_E has it drop 29.92 m. So its positions
    cannot come from the inclinations and azimuths beside them."""
    rows = np.asarray(legacy("lmp_trajectory_data_utm31n")["rows"], dtype=float)
    step = rows[21, 3] - rows[20, 3]

    assert -step < 30 * np.cos(np.radians(rows[21, 4]))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "legacy defect: TRJ_E's positions are not those of its stated angles by "
        "any placement method, with either north reference"
    ),
)
@pytest.mark.parametrize("north", list(NorthReference))
def test_the_legacy_lmp_table_is_reproduced(north: NorthReference) -> None:
    data = legacy("lmp_trajectory_data_utm31n")
    rows = np.asarray(data["rows"], dtype=float)
    survey = Survey(rows[:, 0], rows[:, 4], rows[:, 5])

    trajectory = compute_trajectory(
        survey, data["reference_point"], data["crs"], north=north, method="LMP"
    )

    assert trajectory.x == pytest.approx(rows[:, 1], abs=0.02)
    assert trajectory.y == pytest.approx(rows[:, 2], abs=0.02)


@pytest.mark.parametrize(
    "method", [Method.AZIMUTHAL_EQUIDISTANT, Method.GRID_NORTH_LOCAL]
)
def test_volve_f1_reproduces_its_survey_report(method: Method) -> None:
    """A real well, to the report's rounding: MD and TVD to the centimetre,
    angles and DLS to 0.01 degree, coordinates to the millimetre."""
    report = volve_f1()
    # The survey starts at the wellhead on the seabed, 91 m below sea level.
    well = TrajectoryInput.from_csv(
        VOLVE_F1,
        wellhead=(435046.488, 6478566.687, -91.0),
        crs="EPSG:23031",
        method=method,
    )
    trajectory = well.compute()

    assert (well.name, well.north_reference) == ("F-1", NorthReference.GRID)
    assert len(trajectory) == len(report) == 100
    # The report's TVD is below the rotary table, 54.90 m above sea level.
    assert 54.90 - trajectory.z == pytest.approx(report.tvd, abs=0.005)
    assert trajectory.x == pytest.approx(report.easting, abs=0.002)
    assert trajectory.y == pytest.approx(report.northing, abs=0.002)
    assert trajectory.dls() == pytest.approx(report.dls, abs=0.005)
