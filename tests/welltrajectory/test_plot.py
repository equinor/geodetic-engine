"""The 3D plot builds, with and without TeX, and refuses what it cannot draw."""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

from geodetic_engine.welltrajectory import (  # noqa: E402
    Survey,
    WellTrajectory,
    compute_trajectory,
    plot_trajectory,
)
from geodetic_engine.welltrajectory import plot as plot_module  # noqa: E402

SURVEY = Survey([0, 500, 1500, 2500], [0, 20, 60, 70], [10, 30, 45, 50])


@pytest.fixture(autouse=True)
def _close_figures() -> Iterator[None]:
    yield
    plt.close("all")


@pytest.fixture
def trajectory() -> WellTrajectory:
    return compute_trajectory(SURVEY, (500000.0, 6600000.0, 30.0), "EPSG:32631")


@pytest.mark.parametrize("color_by", ["dls", "md", None])
def test_a_figure_is_drawn_and_rendered(
    trajectory: WellTrajectory, color_by: plot_module.ColorBy
) -> None:
    figure, ax = plot_trajectory(trajectory, color_by=color_by, usetex=False)

    figure.savefig(io.BytesIO(), format="png")
    assert ax.get_xlabel() == r"$\mathrm{E}\ [\mathrm{m}]$"
    assert ax.get_title() == "WGS 84 / UTM zone 31N"
    assert len(figure.axes) == (1 if color_by is None else 2)


def test_several_wells_share_the_axes(trajectory: WellTrajectory) -> None:
    other = compute_trajectory(SURVEY, (500400.0, 6600300.0, 30.0), "EPSG:32631")

    figure, ax = plot_trajectory(trajectory, other, labels=["A", "B"], usetex=False)
    trajectory.plot(ax=ax, usetex=False, projections=False)

    figure.savefig(io.BytesIO(), format="png")
    assert {text.get_text().strip() for text in ax.texts} >= {"A", "B"}


def test_without_tex_it_falls_back_to_mathtext(
    trajectory: WellTrajectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)
    plot_module._tex_available.cache_clear()

    try:
        figure, ax = plot_trajectory(trajectory)
    finally:
        plot_module._tex_available.cache_clear()

    figure.savefig(io.BytesIO(), format="png")
    assert not ax.xaxis.label.get_usetex()


def test_a_geographic_trajectory_is_drawn_in_degrees() -> None:
    trajectory = compute_trajectory(SURVEY, (4.0, 58.0), "EPSG:4326", north="TN")

    _, ax = plot_trajectory(trajectory, usetex=False)

    assert ax.get_xlabel() == r"$\mathrm{Lon}\ [{}^{\circ}]$"


def test_what_cannot_be_drawn_is_refused(trajectory: WellTrajectory) -> None:
    elsewhere = compute_trajectory(SURVEY, (500000.0, 6600000.0), "EPSG:32632")

    with pytest.raises(ValueError, match="at least one"):
        plot_trajectory()
    with pytest.raises(ValueError, match="same CRS"):
        plot_trajectory(trajectory, elsewhere)
    with pytest.raises(ValueError, match="one label"):
        plot_trajectory(trajectory, labels=["A", "B"])
