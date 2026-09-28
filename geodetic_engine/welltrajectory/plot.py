"""A 3D view of one or more well trajectories.

Needs matplotlib, from the ``plot`` extra: ``pip install geodetic-engine[plot]``.
It is imported only when a plot is drawn, so nothing else in the package
depends on it.

Labels are written in LaTeX that matplotlib's own mathtext renders too, so a
figure reads the same with or without a TeX installation. TeX is used when it
is installed and a trial render succeeds, and mathtext otherwise.
"""

from __future__ import annotations

import math
import shutil
from collections.abc import Sequence
from functools import cache
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from geodetic_engine.welltrajectory.survey import length_factor

if TYPE_CHECKING:
    from matplotlib.figure import Figure
    from mpl_toolkits.mplot3d import Axes3D

    from geodetic_engine.geodesy import AxisSpec
    from geodetic_engine.welltrajectory.trajectory import WellTrajectory

type ColorBy = Literal["dls", "md"] | None

# Paths are drawn through this many points along their arcs, not as chords.
_SMOOTHNESS = 400

# How a length or angle unit is written in math mode, by metres or radians
# per unit; anything else is written out by name.
_UNITS = (
    (1.0, r"\mathrm{m}"),
    (0.3048, r"\mathrm{ft}"),
    (1200 / 3937, r"\mathrm{ft_{US}}"),
    (math.pi / 180, r"{}^{\circ}"),
)
_TEX_SPECIALS = str.maketrans({character: "\\" + character for character in "#$%&_{}"})


def plot_trajectory(
    *trajectories: WellTrajectory,
    color_by: ColorBy = "dls",
    labels: Sequence[str] | None = None,
    ax: Axes3D | None = None,
    projections: bool = True,
    usetex: bool | None = None,
    title: str | None = None,
) -> tuple[Figure, Axes3D]:
    """Draw trajectories in 3D, in their CRS's own coordinates.

    Args:
        trajectories: One or more trajectories, all in the same CRS.
        color_by: Colour each path by dogleg severity or by measured depth;
            with None each well gets a colour of its own.
        labels: A name per trajectory, written beside its deepest point.
        ax: 3D axes to draw into; new ones on a new figure when omitted.
        projections: Also draw each path's faint shadow on the floor and two
            walls, which is what makes depth readable in a 3D view.
        usetex: Render text with TeX. None uses it only if it works here.
        title: Figure title; the CRS's name when omitted.

    Returns:
        The figure and the axes, for further styling or saving.

    Raises:
        ImportError: If matplotlib is not installed.
        ValueError: If no trajectory is given, they are in different CRSs, or
            the labels do not match them one for one.
    """
    if not trajectories:
        raise ValueError("give at least one trajectory to plot")
    if any(item.crs != trajectories[0].crs for item in trajectories):
        raise ValueError("every trajectory must be in the same CRS to share axes")
    if labels is not None and len(labels) != len(trajectories):
        raise ValueError("give one label per trajectory")

    plt = _pyplot()
    tex = _tex_available() if usetex is None else usetex
    smooth = [_smoothed(trajectory) for trajectory in trajectories]
    with plt.rc_context(_style(tex)):
        if ax is None:
            ax = plt.figure(figsize=(8.0, 7.0), layout="constrained").add_subplot(
                projection="3d"
            )
        _draw(ax, smooth, color_by, labels, projections, tex)
        _label_axes(ax, smooth[0], title, tex)
    return ax.get_figure(root=True), ax


def _draw(
    ax: Axes3D,
    trajectories: Sequence[WellTrajectory],
    color_by: ColorBy,
    labels: Sequence[str] | None,
    projections: bool,
    tex: bool,
) -> None:
    from matplotlib import colormaps
    from matplotlib.colors import Normalize
    from matplotlib.lines import Line2D
    from mpl_toolkits.mplot3d.art3d import Line3DCollection

    paths = [np.column_stack([t.x, t.y, t.z]) for t in trajectories]
    points = np.vstack(paths)
    low, high = points.min(axis=0), points.max(axis=0)
    floor = low[2] - 0.05 * ((high[2] - low[2]) or 1.0)

    values = [_colour_values(t, color_by) for t in trajectories]
    colormap = colormaps["plasma" if color_by == "dls" else "viridis"]
    joined = np.concatenate(values)
    norm = Normalize(float(joined.min()), float(max(joined.max(), joined.min() + 1e-9)))
    shadow = {"color": "0.6", "linewidth": 0.8, "alpha": 0.6}

    collection = None
    for index, path in enumerate(paths):
        segments = np.stack([path[:-1], path[1:]], axis=1)
        if color_by is None:
            collection = Line3DCollection(
                segments, colors=[colormaps["tab10"](index % 10)], linewidths=2.4
            )
        else:
            collection = Line3DCollection(
                segments, cmap=colormap, norm=norm, linewidths=2.4
            )
            collection.set_array(values[index])
        ax.add_collection3d(collection)
        if projections:
            flat = np.full(len(path), floor)
            ax.plot(path[:, 0], path[:, 1], flat, **shadow)
            ax.plot(np.full(len(path), low[0]), path[:, 1], path[:, 2], **shadow)
            ax.plot(path[:, 0], np.full(len(path), high[1]), path[:, 2], **shadow)
        ax.scatter(*path[0], marker="^", s=70, color="black", depthshade=False)
        ax.scatter(*path[-1], marker="o", s=36, color="black", depthshade=False)
        if labels is not None:
            ax.text(*path[-1], "  " + _escape(labels[index], tex), fontsize=9)

    if color_by is not None and collection is not None:
        colorbar = ax.get_figure(root=True).colorbar(
            collection, ax=ax, shrink=0.6, pad=0.1
        )
        colorbar.set_label(_colour_label(trajectories[0], color_by))
    ax.legend(
        handles=[
            Line2D([], [], marker="^", color="k", linestyle="none", label="Wellhead"),
            Line2D([], [], marker="o", color="k", linestyle="none", label="TD"),
        ],
        loc="upper left",
        frameon=False,
    )
    ax.set_xlim(low[0], max(high[0], low[0] + 1.0))
    ax.set_ylim(low[1], max(high[1], low[1] + 1.0))
    ax.set_zlim(floor, high[2])
    _equal_aspect(ax, trajectories[0], low, high, floor)


def _label_axes(
    ax: Axes3D,
    trajectory: WellTrajectory,
    title: str | None,
    tex: bool,
) -> None:
    from matplotlib.ticker import MaxNLocator

    horizontal = trajectory.frame.horizontal_crs
    axes = [horizontal.axes[index] for index in horizontal.value_axis_order[:2]]
    ax.set_xlabel(_label(axes[0], tex))
    ax.set_ylabel(_label(axes[1], tex))
    z_unit = _unit(length_factor(trajectory.z_unit), trajectory.z_unit, tex)
    ax.set_zlabel(rf"$z_0 - \mathrm{{TVD}}\ [{z_unit}]$")
    ax.set_title(_escape(title or trajectory.crs.name, tex))
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.view_init(elev=22, azim=-58)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_pane_color((0.97, 0.97, 0.97, 1.0))
        axis.set_major_locator(MaxNLocator(nbins=4))
        axis.labelpad = 10
    ax.zaxis.labelpad = 16


def _equal_aspect(
    ax: Axes3D,
    trajectory: WellTrajectory,
    low: np.ndarray,
    high: np.ndarray,
    floor: float,
) -> None:
    """Scale the box to the data in metres, so a vertical well looks vertical."""
    if trajectory.frame.horizontal_crs.crs.is_geographic:
        return
    horizontal = trajectory.frame.horizontal_unit
    extent = np.array(
        [
            (high[0] - low[0]) * horizontal,
            (high[1] - low[1]) * horizontal,
            (high[2] - floor) * length_factor(trajectory.z_unit),
        ]
    )
    # A near vertical well would otherwise collapse into an unreadable sliver.
    extent = np.maximum(extent, 0.3 * extent.max())
    ax.set_box_aspect(tuple(extent / extent.max()))


def _smoothed(trajectory: WellTrajectory) -> WellTrajectory:
    """The trajectory with enough points along its arcs to draw them as curves."""
    span = float(trajectory.md[-1] - trajectory.md[0])
    if len(trajectory) >= _SMOOTHNESS or span <= 0:
        return trajectory
    return trajectory.resample(span / _SMOOTHNESS)


def _colour_values(trajectory: WellTrajectory, color_by: ColorBy) -> np.ndarray:
    """One value per segment: the dogleg severity of its arc, or its mid-depth."""
    if color_by == "dls":
        return trajectory.dls()[1:]
    return 0.5 * (trajectory.md[:-1] + trajectory.md[1:])


def _colour_label(trajectory: WellTrajectory, color_by: ColorBy) -> str:
    unit = _unit(length_factor(trajectory.md_unit), trajectory.md_unit, tex=False)
    if color_by == "md":
        return rf"$\mathrm{{MD}}\ [{unit}]$"
    return rf"$\mathrm{{DLS}}\ [{{}}^{{\circ}}/{trajectory.dls_length:g}\,{unit}]$"


def _label(axis: AxisSpec, tex: bool) -> str:
    unit = _unit(axis.unit_conversion_factor, axis.unit_name, tex)
    return rf"$\mathrm{{{_escape(axis.abbrev, tex)}}}\ [{unit}]$"


def _unit(factor: float, name: str, tex: bool) -> str:
    """A unit in math mode, by its conversion factor, else by its name."""
    for known, written in _UNITS:
        if math.isclose(factor, known, rel_tol=1e-9):
            return written
    return r"\mathrm{" + _escape(name, tex).replace(" ", r"\ ") + "}"


def _escape(text: str, tex: bool) -> str:
    """Make free text safe for TeX, or for mathtext outside math mode."""
    return text.translate(_TEX_SPECIALS) if tex else text.replace("$", r"\$")


def _style(tex: bool) -> dict[str, Any]:
    style: dict[str, Any] = {
        "text.usetex": tex,
        "font.family": "serif",
        "font.size": 10,
        "axes.titlesize": 11,
        "legend.fontsize": 9,
        "grid.color": "0.85",
        "grid.linewidth": 0.5,
    }
    if not tex:
        style["mathtext.fontset"] = "cm"
    return style


def _pyplot() -> Any:
    try:
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise ImportError(
            "plotting needs matplotlib: pip install 'geodetic-engine[plot]'"
        ) from error
    return plt


@cache
def _tex_available() -> bool:
    """Whether TeX is installed and matplotlib can actually render with it."""
    if not all(shutil.which(tool) for tool in ("latex", "dvipng")):
        return False
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    try:
        with _pyplot().rc_context({"text.usetex": True}):
            trial = Figure()
            FigureCanvasAgg(trial)
            trial.text(0.5, 0.5, r"$\mathrm{DLS}\ [{}^{\circ}/30\,\mathrm{m}]$")
            trial.canvas.draw()
    except Exception:
        return False
    return True
