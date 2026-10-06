"""Building a trajectory input: arrays, rows, DataFrames, survey files, OSDU bodies."""

from __future__ import annotations

import dataclasses
import io
import json
from importlib.resources import files
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from geodetic_engine.persistablereference import (
    CrsReference,
    parse_persistable_reference,
    to_persistable_reference,
)
from geodetic_engine.welltrajectory import (
    InvalidInputError,
    InvalidSurveyError,
    Method,
    NorthReference,
    Survey,
    TrajectoryInput,
    UnitError,
    Wellhead,
)

from .conftest import DATA, REQUESTS, request

EXAMPLES = DATA / "requests"
EXAMPLE_FILE = (
    files("geodetic_engine.welltrajectory") / "example_data/synthetic_well.csv"
)
MD = [0.0, 500.0, 1500.0, 2500.0]
INCLINATION = [0.0, 20.0, 60.0, 70.0]
AZIMUTH = [10.0, 30.0, 45.0, 50.0]
REQUIRED: dict[str, Any] = {
    "wellhead": (500000.0, 6600000.0, 25.0),
    "crs": "EPSG:32631",
    "north_reference": "GN",
}
HEADER = "# crs: EPSG:32631\n# wellhead_x: 500000\n# wellhead_y: 6600000\n"
TABLE = "md,inclination,azimuth\n0,0,10\n500,20,30\n1500,60,45\n"
# A survey report: free text, then a table lined up with spaces, with units.
REPORT = (
    "SURVEY REPORT (all units in default)\n"
    "WELL NAME: A-10\n"
    "WELLBORE NAME: A-10 T2\n"
    "Surface EW: 500000.00m\n"
    "Surface Latitude: 58° 26' 29.9 N\n"
    "North Reference: Grid\n"
    "Bottom Hole MD: 1500.00m\n"
    "--------------------------------\n"
    "SURVEY LIST\n"
    "MD        Inc       Azim      TVD       UTM E/W\n"
    "ft RKB    deg       deg       ft RKB    m\n"
    "0.00      0.00      10.00     0.00      500000.000\n"
    "500.00    20.00     30.00     489.90    500043.000\n"
    "1500.00   60.00     45.00     1200.00   500300.000\n"
)


def _arrays(**options: Any) -> TrajectoryInput:
    return TrajectoryInput.from_arrays(MD, INCLINATION, AZIMUTH, **REQUIRED | options)


def _csv(text: str, **arguments: Any) -> TrajectoryInput:
    return TrajectoryInput.from_csv(io.StringIO(text), **arguments)


def test_every_constructor_states_the_same_well() -> None:
    rows = list(zip(MD, INCLINATION, AZIMUTH, strict=True))
    frame = pd.DataFrame({"md": MD, "inclination": INCLINATION, "azimuth": AZIMUTH})
    reference = _arrays().compute()

    inputs = [
        TrajectoryInput.from_arrays(
            np.array(MD), np.array(INCLINATION), np.array(AZIMUTH), **REQUIRED
        ),
        TrajectoryInput.from_records(rows, **REQUIRED),
        TrajectoryInput.from_records(
            [{"md": m, "inclination": i, "azimuth": a} for m, i, a in rows],
            **REQUIRED,
        ),
        TrajectoryInput.from_dataframe(frame, **REQUIRED),
        _csv(_arrays().to_csv()),
        TrajectoryInput(
            survey=Survey(MD, INCLINATION, AZIMUTH),
            wellhead=Wellhead(500000.0, 6600000.0, 25.0),
            crs="EPSG:32631",
            north_reference=NorthReference.GRID,
        ),
    ]

    for well in inputs:
        trajectory = well.compute()
        assert trajectory.x == pytest.approx(reference.x, abs=1e-9)
        assert trajectory.y == pytest.approx(reference.y, abs=1e-9)
        assert trajectory.z == pytest.approx(reference.z, abs=1e-9)


def test_settings_given_as_text_are_read_in_any_case() -> None:
    well = _arrays(north_reference="tn", method="lmp")

    assert well.north_reference is NorthReference.TRUE
    assert well.method is Method.LMP
    assert well.wellhead == Wellhead(500000.0, 6600000.0, 25.0)


@pytest.mark.parametrize(
    ("change", "error", "match"),
    [
        ({"north_reference": "magnetic"}, InvalidInputError, "north_reference"),
        ({"method": "Tangential"}, InvalidInputError, "method"),
        ({"wellhead": (1.0,)}, InvalidInputError, "wellhead"),
        ({"crs": " "}, InvalidInputError, "CRS"),
        ({"z_unit": "cubit"}, UnitError, "cubit"),
        ({"md_unit": "deg"}, UnitError, "deg"),
        ({"md_step": 0}, InvalidInputError, "md_step"),
        ({"md_points": [[1.0, 2.0]]}, InvalidInputError, "md_points"),
        ({"md_points": [2600.0]}, InvalidSurveyError, "outside"),
        ({"colour": "red"}, TypeError, "colour"),
    ],
    ids=lambda value: str(value)[:24] if isinstance(value, dict) else "",
)
def test_a_setting_is_checked_when_the_input_is_made(
    change: dict[str, Any], error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        _arrays(**change)


@pytest.mark.parametrize(
    ("md", "inclination"),
    [([0.0], [0.0]), ([0.0, 500.0, 400.0], [0, 1, 2]), ([0.0, 500.0], [0, 190])],
    ids=["one-station", "md-decreases", "inclination-over-180"],
)
def test_a_survey_that_describes_no_wellbore_is_refused_up_front(
    md: list[float], inclination: list[float]
) -> None:
    with pytest.raises(InvalidSurveyError):
        TrajectoryInput.from_arrays(md, inclination, **REQUIRED)


def test_a_copy_with_other_settings_is_checked_again() -> None:
    well = _arrays()

    assert dataclasses.replace(well, method="LMP").method is Method.LMP
    with pytest.raises(InvalidInputError):
        dataclasses.replace(well, north_reference="up")


def test_added_points_are_in_md_order_and_flagged() -> None:
    trajectory = _arrays(md_step=1000, md_points=[1234.5]).compute()

    assert trajectory.md.tolist() == [0, 500, 1000, 1234.5, 1500, 2000, 2500]
    assert trajectory.is_survey_station.tolist() == [
        True,
        True,
        False,
        False,
        True,
        False,
        True,
    ]


def test_the_name_reaches_the_trajectory_and_its_points() -> None:
    trajectory = _arrays(name="Well A").compute()

    assert trajectory.name == "Well A"
    assert trajectory.resample(100).name == "Well A"


def test_the_stations_come_back_as_given() -> None:
    frame = _arrays(md_unit="ft").to_dataframe()

    assert list(frame.columns) == ["md", "inclination", "azimuth"]
    assert frame["md"].tolist() == MD


# -- From rows and DataFrames -------------------------------------------------


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        ([(0, 0, 0), (100, 10)], "every station"),
        ([{"md": 0, "inclination": 0}, {"md": 100}], "inclination"),
        (
            [{"md": 0, "inclination": 0, "azimuth": 0}, {"md": 1, "inclination": 1}],
            "some",
        ),
        ([{"md": 0, "inclination": 0}, (100, 10)], "mapping"),
    ],
    ids=["mixed-widths", "missing-key", "some-azimuths", "mixed-forms"],
)
def test_rows_must_all_have_one_form(rows: list[Any], match: str) -> None:
    with pytest.raises(InvalidInputError, match=match):
        TrajectoryInput.from_records(rows, **REQUIRED)


def test_rows_of_two_values_are_an_inclination_only_survey() -> None:
    well = TrajectoryInput.from_records([(0, 0), (100, 10)], **REQUIRED)

    assert well.survey.azimuth is None


def test_a_dataframe_is_read_by_the_columns_named() -> None:
    frame = pd.DataFrame(
        {"MD": MD, "INC": INCLINATION, "AZI": AZIMUTH, "TVD": [0.0, 1.0, 2.0, 3.0]}
    )

    well = TrajectoryInput.from_dataframe(
        frame,
        md_column="MD",
        inclination_column="INC",
        azimuth_column="AZI",
        **REQUIRED,
    )
    inclination_only = TrajectoryInput.from_dataframe(
        frame,
        md_column="MD",
        inclination_column="INC",
        azimuth_column=None,
        **REQUIRED,
    )

    assert well.survey.azimuth is not None
    assert well.survey.azimuth.tolist() == AZIMUTH
    assert inclination_only.survey.azimuth is None


def test_a_missing_dataframe_column_is_named() -> None:
    frame = pd.DataFrame({"MD": MD, "INC": INCLINATION})

    with pytest.raises(InvalidInputError, match=r"'md'.*'MD', 'INC'"):
        TrajectoryInput.from_dataframe(frame, **REQUIRED)


# -- Survey files --------------------------------------------------------------


def test_the_example_file_states_a_whole_well() -> None:
    well = TrajectoryInput.from_csv(EXAMPLE_FILE)

    trajectory = well.compute()

    assert well.name == "Synthetic-1"
    assert well.crs == "EPSG:23031"
    assert well.north_reference is NorthReference.GRID
    assert well.wellhead == Wellhead(455000.0, 6785000.0, 32.0)
    assert len(trajectory) == 122
    assert np.all(np.isfinite(trajectory.x)) and np.all(np.isfinite(trajectory.y))
    assert trajectory.dls().max() < 3.0


def test_a_written_file_reads_back_exactly(tmp_path: Path) -> None:
    well = TrajectoryInput.from_arrays(
        np.array(MD) / 0.3048,
        np.array(INCLINATION) + 0.1,
        np.array(AZIMUTH) / 3,
        wellhead=(500000.1, 6600000.2, 82.3),
        crs="EPSG:32631",
        north_reference="TN",
        md_unit="ft",
        z_unit="ft",
        method="ENU",
        md_step=100,
        md_points=[1234.5, 2000],
        name="Well A",
    )
    path = tmp_path / "well.csv"
    well.to_csv(path)

    for again in (TrajectoryInput.from_csv(path), TrajectoryInput.from_csv(str(path))):
        assert again.survey.md.tolist() == well.survey.md.tolist()
        assert again.survey.inclination.tolist() == well.survey.inclination.tolist()
        assert again.survey.azimuth is not None and well.survey.azimuth is not None
        assert again.survey.azimuth.tolist() == well.survey.azimuth.tolist()
        assert again.md_points.tolist() == well.md_points.tolist()
        for setting in (
            "wellhead",
            "crs",
            "north_reference",
            "method",
            "z_unit",
            "md_step",
            "name",
        ):
            assert getattr(again, setting) == getattr(well, setting)
        assert again.survey.md_unit == "ft"


def test_a_crs_that_is_not_one_line_is_written_as_wkt() -> None:
    from pyproj import CRS

    well = _arrays(crs=CRS("EPSG:32631"))

    again = _csv(well.to_csv())

    assert again.crs.startswith("PROJCRS")
    assert again.compute().x == pytest.approx(well.compute().x, abs=1e-9)


def test_arguments_fill_in_and_take_precedence_over_the_header() -> None:
    bare = _csv(TABLE, **REQUIRED)
    overridden = TrajectoryInput.from_csv(EXAMPLE_FILE, method="LMP", z_unit="ft")

    assert bare.crs == "EPSG:32631"
    assert overridden.method is Method.LMP
    assert overridden.z_unit == "ft"
    assert overridden.wellhead.z == 32.0


def test_columns_may_come_in_any_order_and_case() -> None:
    well = _csv(
        HEADER + "# north_reference: GN\nAzimuth, MD, Inclination\n10,0,0\n30,500,20\n"
    )

    assert well.survey.md.tolist() == [0.0, 500.0]
    assert well.survey.azimuth is not None
    assert well.survey.azimuth.tolist() == [10.0, 30.0]


def test_a_file_without_azimuths_is_an_inclination_only_survey() -> None:
    well = _csv(HEADER + "# north_reference: TN\nmd,inclination\n0,0\n100,5\n")

    assert well.survey.azimuth is None


def test_a_byte_order_mark_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "excel.csv"
    path.write_bytes(("# north_reference: GN\n" + HEADER + TABLE).encode("utf-8-sig"))

    assert TrajectoryInput.from_csv(path).north_reference is NorthReference.GRID


@pytest.mark.parametrize(
    ("text", "match"),
    [
        (
            HEADER + "# md_unti: ft\n" + TABLE,
            "line 4: 'md_unti' .* did you mean md_unit",
        ),
        (HEADER + "# crs: EPSG:4326\n" + TABLE, "line 4: crs is stated twice"),
        (HEADER + "# Easting: 1\n" + TABLE, "line 4: wellhead_x is stated twice"),
        ("# wellhead_x: 1\n# wellhead_y: 2\n" + TABLE, "states no crs"),
        ("# crs: EPSG:32631\n# wellhead_x: 1\n" + TABLE, "only one of"),
        ("# crs: EPSG:32631\n# wellhead: 1\n" + TABLE, "not 1 values"),
        (HEADER + "# origin: 1, 2\n" + TABLE, "wellhead twice"),
        (HEADER + "# wellhead_z: high\n" + TABLE, "'high' in wellhead_z"),
        (HEADER + "0,0,0\n100,10,45\n", "line 4: the table has no md column"),
        (HEADER + "inclination,azimuth\n0,0\n", "no md column among"),
        (HEADER + "md,md,inclination\n0,0,0\n", "named twice"),
        (HEADER + "md,inc,azim_gn,azim_tn\n0,0,0,0\n", "could each be the azimuth"),
        (HEADER + "md|inc|azi\n0|0|0\n", "delimiter"),
        (HEADER + "# md_unit: m\nMD (ft),inc\n0,0\n", "states md_unit 'm'.*'ft'"),
        (HEADER + "md,Inc (deg),Azi (rad)\n0,0,0\n", "one angle unit"),
        (HEADER + TABLE + "2000,abc,45\n", "line 8: 'abc' in inclination"),
        (HEADER + TABLE + "2000,60\n", "line 8: 2 values for 3 columns"),
        (HEADER + TABLE + "# end\n", "line 8: header and comment lines"),
        (HEADER, "has no table"),
        (HEADER + "md,inclination,azimuth\n", "has no stations"),
    ],
    ids=[
        "misspelt-key",
        "key-twice",
        "synonym-twice",
        "no-crs",
        "half-a-wellhead",
        "wellhead-one-value",
        "wellhead-twice",
        "header-not-a-number",
        "no-column-names",
        "no-md",
        "column-twice",
        "two-azimuths",
        "other-delimiter",
        "unit-differs",
        "two-angle-units",
        "not-a-number",
        "short-row",
        "comment-in-table",
        "no-table",
        "no-stations",
    ],
)
def test_a_file_out_of_format_is_refused_at_the_line_at_fault(
    text: str, match: str
) -> None:
    with pytest.raises(InvalidInputError, match=match):
        _csv(text, north_reference="GN")


def test_comments_are_skipped() -> None:
    well = _csv(
        "# Survey of a test well, by hand.\n#\n" + HEADER + "\n" + TABLE,
        north_reference="GN",
    )

    assert len(well.survey.md) == 3


def test_a_file_from_other_software_is_read_as_it_is() -> None:
    well = _csv(
        "# Exported by: SurveyTool 4.2\n"
        "# Well name: A-10\n"
        "# Coordinate system: EPSG:32631\n"
        "# Easting: 500000\n"
        "# Northing: 6600000\n"
        "# RKB: 25\n"
        "# Azimuth reference: Grid north\n"
        "Measured Depth (ft);Inc [deg];Azimuth;TVD;Comment\n"
        "0;0;10;0;tie-in\n"
        "500;20;30;490;\n"
        "1500;60;45;1200;TD\n"
    )

    assert well.name == "A-10"
    assert well.crs == "EPSG:32631"
    assert well.wellhead == Wellhead(500000.0, 6600000.0, 25.0)
    assert well.north_reference is NorthReference.GRID
    assert (well.survey.md_unit, well.survey.angle_unit) == ("ft", "deg")
    assert well.survey.md.tolist() == [0.0, 500.0, 1500.0]
    assert well.survey.azimuth is not None
    assert well.survey.azimuth.tolist() == [10.0, 30.0, 45.0]


def test_a_survey_report_is_read_below_its_free_text(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.write_bytes(REPORT.encode("latin-1"))
    where = {"wellhead": (500000.0, 6600000.0), "crs": "EPSG:32631"}

    well = TrajectoryInput.from_csv(path, **where)
    header_first = _csv("# name: B-2\n" + REPORT, **where)

    assert well.name == "A-10"
    assert header_first.name == "B-2"
    assert well.north_reference is NorthReference.GRID
    assert (well.survey.md_unit, well.survey.angle_unit) == ("ft", "deg")
    assert well.survey.md.tolist() == [0.0, 500.0, 1500.0]
    assert well.survey.azimuth is not None
    assert well.survey.azimuth.tolist() == [10.0, 30.0, 45.0]


def test_a_line_of_units_below_the_names_gives_their_units() -> None:
    table = HEADER + "md,inc,azi\nft,deg,deg\n0,0,10\n500,20,30\n"

    well = _csv(table, north_reference="GN")

    assert (well.survey.md_unit, well.survey.angle_unit) == ("ft", "deg")
    with pytest.raises(UnitError, match="line 5: 'furlong' is not a unit of 'md'"):
        _csv(HEADER + "md,inc\nfurlong,deg\n0,0\n500,5\n", north_reference="GN")
    with pytest.raises(InvalidInputError, match=r"line 5: 'MD \(ft\)' is in 'ft'"):
        _csv(HEADER + "MD (ft),inc\nm,deg\n0,0\n500,5\n", north_reference="GN")


@pytest.mark.parametrize(
    ("lines", "wellhead"),
    [
        ("# wellhead: 500000, 6600000, 25", Wellhead(500000.0, 6600000.0, 25.0)),
        ("# Origin: 500000 6600000", Wellhead(500000.0, 6600000.0, 0.0)),
        ("# x: 500000\n# y: 6600000\n# z: 25", Wellhead(500000.0, 6600000.0, 25.0)),
        ("# Wellhead X: 500000\n# WellheadY: 6600000", Wellhead(500000.0, 6600000.0)),
    ],
    ids=["one-line", "origin", "xyz", "spaced-and-joined"],
)
def test_the_wellhead_may_be_stated_several_ways(
    lines: str, wellhead: Wellhead
) -> None:
    well = _csv(f"# crs: EPSG:32631\n{lines}\n" + TABLE, north_reference="GN")

    assert well.wellhead == wellhead


def test_columns_are_found_by_their_usual_names_or_named() -> None:
    text = HEADER + "DEPTH_M,I,A,MD_PLAN\n0,0,10,0\n500,20,30,0\n"

    with pytest.raises(InvalidInputError, match="no inclination column"):
        _csv(text, md_column="depth_m", north_reference="GN")
    with pytest.raises(InvalidInputError, match=r"no column 'B'.*'DEPTH_M', 'I'"):
        _csv(text, md_column="DEPTH_M", inclination_column="B", north_reference="GN")
    named = _csv(
        text,
        md_column="depth_m",
        inclination_column="I",
        azimuth_column="A",
        north_reference="GN",
    )
    usual = _csv(
        HEADER + "MD (m),INCL,AZIM_GN\n0,0,10\n500,20,30\n", north_reference="GN"
    )
    without_unit = _csv(
        HEADER + "MD (m),INCL,AZIM_GN\n0,0,10\n500,20,30\n",
        md_column="MD",
        north_reference="GN",
    )

    for well in (named, usual, without_unit):
        assert well.survey.md.tolist() == [0.0, 500.0]
        assert well.survey.inclination.tolist() == [0.0, 20.0]
        assert well.survey.azimuth is not None
        assert well.survey.azimuth.tolist() == [10.0, 30.0]


def test_the_unit_after_a_column_name_gives_way_to_an_argument() -> None:
    text = HEADER + "MD (ft),Inc (deg),Azi\n0,0,0\n500,20,30\n"

    assert _csv(text, north_reference="GN").survey.md_unit == "ft"
    assert _csv(text, north_reference="GN", md_unit="m").survey.md_unit == "m"
    assert _csv("# md_unit: ft\n" + text, north_reference="GN").survey.md_unit == "ft"


@pytest.mark.parametrize(
    "heading",
    [
        "MD (furlong),Inc (deg),Azimuth",
        "MD (ftt),Inc (deg),Azimuth",
        "MD (deg),Inc (deg),Azimuth",
        "MD (m),Inc (m),Azimuth",
        "MD (m),Inc (radd),Azimuth",
        "MD (m),Inc (deg),Azimuth (m)",
    ],
)
def test_unsupported_annotated_units_never_default(heading: str) -> None:
    with pytest.raises(UnitError, match="line 4"):
        _csv(HEADER + heading + "\n0,0,0\n100,1,0\n", north_reference="TN")


@pytest.mark.parametrize(
    "units", ["furlong,deg,deg", "deg,deg,deg", "m,m,deg", "m,deg,m"]
)
def test_unit_rows_refuse_unknown_units_and_wrong_quantities(units: str) -> None:
    with pytest.raises(UnitError, match="line 5"):
        _csv(
            HEADER + "MD,Inc,Azimuth\n" + units + "\n0,0,0\n100,1,0\n",
            north_reference="TN",
        )


@pytest.mark.parametrize(
    ("table", "delimiter"),
    [
        ("md;inclination;azimuth\n0;0;10\n500;20;30\n", None),
        ("md\tinclination\tazimuth\n0\t0\t10\n500\t20\t30\n", None),
        ("md  inclination  azimuth\n0  0  10\n500 20   30\n", " "),
        ("md|inclination|azimuth\n0|0|10\n500|20|30\n", "|"),
    ],
    ids=["semicolons", "tabs", "spaces", "named"],
)
def test_the_delimiter_is_found_or_named(table: str, delimiter: str | None) -> None:
    well = _csv(HEADER + table, north_reference="GN", delimiter=delimiter)

    assert well.survey.azimuth is not None
    assert well.survey.azimuth.tolist() == [10.0, 30.0]


def test_a_delimiter_is_one_character() -> None:
    with pytest.raises(InvalidInputError, match="one character"):
        _csv(HEADER + TABLE, north_reference="GN", delimiter=";;")


# -- OSDU request bodies -------------------------------------------------------


@pytest.mark.parametrize("path", REQUESTS, ids=lambda path: path.stem)
def test_every_legacy_example_computes(path: Path) -> None:
    body = request(path)

    trajectory = TrajectoryInput.from_osdu_payload(body).compute()

    assert len(trajectory) >= len(body["inputStations"])
    assert trajectory.is_survey_station.sum() == len(body["inputStations"])
    for values in (trajectory.x, trajectory.y, trajectory.z, trajectory.dls()):
        assert np.all(np.isfinite(values))
    assert trajectory.operations
    assert (trajectory.local_crs is not None) == (
        trajectory.method is Method.AZIMUTHAL_EQUIDISTANT
    )


def test_the_local_crs_can_be_written_as_a_persistable_reference() -> None:
    trajectory = TrajectoryInput.from_osdu_payload(request(REQUESTS[1])).compute()

    assert trajectory.local_crs is not None
    reference = to_persistable_reference(trajectory.local_crs.crs)
    assert isinstance(parse_persistable_reference(reference), CrsReference)


def test_json_text_is_read_as_readily_as_a_mapping() -> None:
    body = request(REQUESTS[1])

    from_text = TrajectoryInput.from_osdu_payload(json.dumps(body)).compute()
    assert from_text.x == pytest.approx(
        TrajectoryInput.from_osdu_payload(body).compute().x
    )


def test_listed_depths_are_among_the_stations() -> None:
    body = request(EXAMPLES / "06_feet_with_md_interpolation.json")

    trajectory = TrajectoryInput.from_osdu_payload(body).compute()

    added = trajectory.md[~trajectory.is_survey_station]
    assert added == pytest.approx([1640, 4920, 8200])
    assert trajectory.md_unit == "ft"


def test_an_interval_covers_the_survey_end_to_end() -> None:
    body = request(EXAMPLES / "08_md_interval_interpolation.json")

    trajectory = TrajectoryInput.from_osdu_payload(body).compute()

    assert trajectory.md.tolist() == [float(md) for md in range(201)]
    assert trajectory.md[trajectory.is_survey_station].tolist() == [0, 100, 200]


def test_md_in_metres_with_depths_in_feet() -> None:
    body = request(EXAMPLES / "09_mixed_units_md_meters_z_feet.json")
    metric = {**body, "unitZ": "m", "unitMD": "m"}

    mixed = TrajectoryInput.from_osdu_payload(body).compute()
    metres = TrajectoryInput.from_osdu_payload(metric).compute()

    assert mixed.md == pytest.approx(metres.md)
    assert mixed.tvd == pytest.approx(metres.tvd / 0.3048)
    assert mixed.x == pytest.approx(metres.x)


def test_gnl_is_grid_north_local() -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json") | {"method": "GNL"}

    assert TrajectoryInput.from_osdu_payload(body).method is Method.GRID_NORTH_LOCAL


@pytest.mark.parametrize(
    ("change", "error", "match"),
    [
        ({"MD_i": {"md_i": [10.0], "md_interval": 5}}, InvalidInputError, "both"),
        ({"MD_i": {"md_i": [99999.0]}}, InvalidSurveyError, "outside"),
        (
            {"interpolate": True, "MD_i": {"md_interval": 30}},
            InvalidInputError,
            "multiple",
        ),
        ({"unitXY": "ft"}, UnitError, "unitXY"),
        ({"method": "Tangential"}, InvalidInputError, "Tangential"),
    ],
    ids=[
        "both-md-i-forms",
        "md-i-outside",
        "spacings-apart",
        "foreign-xy-unit",
        "unknown-method",
    ],
)
def test_a_payload_that_cannot_be_honoured_is_refused(
    change: dict[str, Any], error: type[Exception], match: str
) -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json") | change

    with pytest.raises(error, match=match):
        TrajectoryInput.from_osdu_payload(body)


def test_a_missing_field_is_named() -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json")
    del body["trajectoryCRS"]

    with pytest.raises(InvalidInputError, match="trajectoryCRS"):
        TrajectoryInput.from_osdu_payload(body)


def test_the_crs_own_xy_unit_is_accepted() -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json") | {"unitXY": "m"}

    well = TrajectoryInput.from_osdu_payload(body)

    assert len(well.survey.md) == len(body["inputStations"])


def test_an_inclination_only_payload_ignores_any_azimuth() -> None:
    body = request(EXAMPLES / "02_deviated_well_build.json") | {"inputKind": "MD_Incl"}
    body["azimuthReference"] = "TN"

    trajectory = TrajectoryInput.from_osdu_payload(body).compute()

    assert trajectory.east == pytest.approx(0.0, abs=1e-9)
    assert np.all(trajectory.north >= 0)
