"""The 3D view: one scale on every axis, a page a browser can load, an image."""

from __future__ import annotations

import importlib.util
import shutil
import urllib.request
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("plotly")

from geodetic_engine.welltrajectory import (
    Survey,
    WellTrajectory,
    compute_trajectory,
    length_factor,
    open_in_browser,
    plot_trajectory,
)

SURVEY = Survey([0, 500, 1500, 2500], [0, 30, 60, 60], [0, 2, 358, 2])


@pytest.fixture
def trajectory() -> WellTrajectory:
    return compute_trajectory(SURVEY, (500000.0, 6600000.0, 30.0), "EPSG:32631")


@pytest.mark.parametrize("z_unit", ["m", "ft"])
def test_every_axis_is_drawn_at_the_same_scale(z_unit: str) -> None:
    """A well that barely wanders east must not have that wander magnified."""
    wandering = compute_trajectory(
        SURVEY, (500000.0, 6600000.0), "EPSG:32631", north="TN", z_unit=z_unit
    )

    scene = plot_trajectory(wandering).layout.scene

    metres = np.array([1.0, 1.0, length_factor(z_unit)])
    spans = [np.ptp(getattr(scene, f"{key}axis").range) for key in "xyz"]
    ratios = [getattr(scene.aspectratio, key) for key in "xyz"]
    per_unit = np.array(spans) * metres / np.array(ratios)
    assert per_unit == pytest.approx(np.full(3, per_unit[0]), rel=1e-9)


def test_a_geographic_trajectory_is_drawn_in_degrees() -> None:
    geographic = compute_trajectory(SURVEY, (4.0, 58.0), "EPSG:4326", north="TN")

    scene = geographic.plot().layout.scene

    assert scene.xaxis.title.text == "Lon [°]"
    assert scene.yaxis.title.text == "Lat [°]"


def test_each_well_has_its_path_markers_and_hover(trajectory: WellTrajectory) -> None:
    other = compute_trajectory(SURVEY, (500400.0, 6600300.0, 30.0), "EPSG:32631")

    figure = plot_trajectory(trajectory, other, labels=["A", "B"], color_by=None)

    paths = [trace for trace in figure.data if trace.name in ("A", "B")]
    assert [trace.name for trace in paths] == ["A", "B"]
    assert "MD" in paths[0].hovertemplate
    assert paths[0].customdata.shape == (len(paths[0].x), 5)
    markers = [trace.name for trace in figure.data if trace.mode == "markers"]
    assert markers == ["Survey stations", "Wellhead", "TD"] * 2
    assert [note.text for note in figure.layout.scene.annotations] == ["A", "B"]


def test_a_named_well_is_labelled_by_its_name(trajectory: WellTrajectory) -> None:
    named = compute_trajectory(
        SURVEY, (500400.0, 6600300.0, 30.0), "EPSG:32631", name="Named"
    )

    figure = plot_trajectory(trajectory, named, color_by=None)

    notes = [note.text for note in figure.layout.scene.annotations]
    assert notes == ["Well 1", "Named"]


def test_projections_and_stations_can_be_left_out(trajectory: WellTrajectory) -> None:
    assert len(plot_trajectory(trajectory).data) == 7
    assert len(plot_trajectory(trajectory, projections=False).data) == 4
    assert len(plot_trajectory(trajectory, stations=False).data) == 6


def test_the_survey_stations_are_marked_on_the_path() -> None:
    """Points added between the stations are drawn in the path, not marked."""
    trajectory = compute_trajectory(
        SURVEY, (500000.0, 6600000.0, 30.0), "EPSG:32631", md_step=100
    )

    figure = plot_trajectory(trajectory, projections=False)

    path = next(trace for trace in figure.data if trace.mode == "lines")
    marked = next(trace for trace in figure.data if trace.name == "Survey stations")
    surveyed = trajectory.is_survey_station
    assert len(marked.x) == len(SURVEY.md) < len(trajectory)
    assert marked.x == pytest.approx(trajectory.x[surveyed])
    assert marked.z == pytest.approx(trajectory.z[surveyed])
    vertices = np.column_stack([path.x, path.y, path.z])
    for station in np.column_stack([marked.x, marked.y, marked.z]):
        assert np.linalg.norm(vertices - station, axis=1).min() < 1e-6
    assert marked.customdata[:, 0] == pytest.approx(SURVEY.md)


def test_one_legend_entry_toggles_the_stations_of_every_well(
    trajectory: WellTrajectory,
) -> None:
    other = compute_trajectory(SURVEY, (500400.0, 6600300.0, 30.0), "EPSG:32631")

    figure = plot_trajectory(trajectory, other)

    marked = [trace for trace in figure.data if trace.name == "Survey stations"]
    assert [trace.showlegend for trace in marked] == [True, False]
    assert {trace.legendgroup for trace in marked} == {"Survey stations"}


def test_the_browser_is_given_a_page_it_can_load(
    trajectory: WellTrajectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", opened.append)

    address = open_in_browser(trajectory.plot(), name="well A/1")

    assert opened == [address]
    assert address.startswith("http://127.0.0.1:")
    with urllib.request.urlopen(address, timeout=10) as page:
        assert page.status == 200
        assert b"plotly.min.js" in page.read()
    script = address.rsplit("/", 1)[0] + "/plotly.min.js"
    with urllib.request.urlopen(script, timeout=10) as response:
        assert response.status == 200


@pytest.mark.skipif(
    importlib.util.find_spec("kaleido") is None
    or not any(shutil.which(name) for name in ("chromium", "google-chrome")),
    reason="still images need kaleido and Chromium or Chrome",
)
def test_a_figure_is_written_as_a_still_image(
    trajectory: WellTrajectory, tmp_path: Path
) -> None:
    path = tmp_path / "trajectory.png"

    trajectory.plot().write_image(path, width=480, height=320)

    assert path.read_bytes().startswith(b"\x89PNG")


def test_what_cannot_be_drawn_is_refused(trajectory: WellTrajectory) -> None:
    elsewhere = compute_trajectory(SURVEY, (500000.0, 6600000.0), "EPSG:32632")

    with pytest.raises(ValueError, match="at least one"):
        plot_trajectory()
    with pytest.raises(ValueError, match="same CRS"):
        plot_trajectory(trajectory, elsewhere)
    with pytest.raises(ValueError, match="one label"):
        plot_trajectory(trajectory, labels=["A", "B"])
