"""Well trajectories in an interactive 3D view, in a notebook or a browser.

Needs plotly, from the ``plot`` extra: ``pip install geodetic-engine[plot]``.
It is imported only when a plot is drawn, so nothing else in the package
depends on it. Paths are drawn along their arcs, every axis at one scale, with
the survey stations marked on them and shadows on the floor and walls. Drag to
rotate, scroll to zoom, right-drag to pan, and hover a point for its MD,
angles, TVD and dogleg severity.

:func:`open_in_browser` serves a figure from this process on ``127.0.0.1`` and
opens it in the default browser. In a dev container that is the host's
browser, through VS Code's port forwarding. Still images come from
``figure.write_image(path)``, through kaleido and Chromium.
"""

from __future__ import annotations

import html
import itertools
import math
import re
import sys
import tempfile
import threading
import webbrowser
from collections.abc import Sequence
from functools import cache, partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from geodetic_engine.welltrajectory.survey import length_factor

if TYPE_CHECKING:
    from geodetic_engine.welltrajectory.datamodels import WellTrajectory

type ColorBy = Literal["dls", "md"] | None

# Paths are drawn through this many points along their arcs, not as chords.
_SMOOTHNESS = 400
# No axis is shown shorter than this share of the longest, at the same scale.
_SHORTEST_AXIS = 0.3

# How a unit is written, by metres or radians per unit.
_UNITS = (
    (1.0, "m"),
    (0.3048, "ft"),
    (1200 / 3937, "ftUS"),
    (math.pi / 180, "°"),
)
_FONT = "Inter, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"
_INK, _MUTED, _GRID, _PANE = "#1f2937", "#6b7280", "#e5e7eb", "#f8fafc"
_PALETTE = ("#2563eb", "#ea580c", "#059669", "#dc2626", "#7c3aed", "#0891b2")
# Paths coloured by value run from cool at low values to warm at high ones.
_DLS_COLOURS = [[0.0, "#1d4ed8"], [0.3, "#0ea5e9"], [0.65, "#f59e0b"], [1.0, "#e11d48"]]
_MD_COLOURS = [[0.0, "#38bdf8"], [0.5, "#6366f1"], [1.0, "#a21caf"]]
_WELLHEAD, _TD, _BEAD_RIM = "#059669", "#e11d48", "rgba(15, 23, 42, 0.7)"
_SHADOW = {"color": "rgba(100, 116, 139, 0.35)", "width": 2}
_STATIONS = "Survey stations"
# The camera looks from the south-east, above, from a distance fitting the box.
_VIEW = np.array([1.25, -2.0, 0.95]) / np.linalg.norm([1.25, -2.0, 0.95])
_CAMERA_DISTANCE = 1.7
_FIGURES = itertools.count(1)
# A notebook shows the figure at this size; a browser tab gets the whole window.
_NOTEBOOK_SIZE = {"width": 960, "height": 640}
# Horizontal extent counts less toward the camera distance: the figure is wide.
_WIDTH_ALLOWANCE = np.array([2 / 3, 2 / 3, 1.0])
_CONFIG = {"responsive": True, "displaylogo": False, "scrollZoom": True}
_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>html, body {{ margin: 0; height: 100%; overflow: hidden; }}</style>
<script src="plotly.min.js"></script>
</head>
<body>{body}</body>
</html>
"""


def plot_trajectory(
    *trajectories: WellTrajectory,
    color_by: ColorBy = "dls",
    labels: Sequence[str] | None = None,
    projections: bool = True,
    stations: bool = True,
    title: str | None = None,
) -> Any:
    """Draw trajectories as an interactive plotly figure.

    Shown in place by a notebook, or in the browser by :func:`open_in_browser`.

    Args:
        trajectories: One or more trajectories, all in the same CRS.
        color_by: Colour each path by dogleg severity or by measured depth;
            with None each well gets a colour of its own.
        labels: A name per trajectory, for the legend and the hover box;
            each trajectory's own :attr:`~WellTrajectory.name` when omitted.
        projections: Also draw each path's shadow on the floor and two walls.
        stations: Also mark each surveyed station on its path, to show the
            path passing through them. Points added by interpolation are not
            marked.
        title: Figure title; the CRS's name when omitted.

    Returns:
        A ``plotly.graph_objects.Figure``.

    Raises:
        ImportError: If plotly is not installed.
        ValueError: If no trajectory is given, they are in different CRSs, or
            the labels do not match them one for one.
    """
    _validate(trajectories, labels)
    go = _plotly()
    smooth = [_smoothed(trajectory) for trajectory in trajectories]
    first = smooth[0]
    paths = [np.column_stack([t.x, t.y, t.z]) for t in smooth]
    points = np.vstack(paths)
    lower, upper, aspect = _box_at_one_scale(
        first, points.min(axis=0), points.max(axis=0)
    )
    named = labels is not None or any(t.name for t in trajectories)
    names = list(
        labels or [t.name or f"Well {index + 1}" for index, t in enumerate(smooth)]
    )
    values = [t.dls() if color_by == "dls" else t.md for t in smooth]
    joined = np.concatenate(values)

    figure = go.Figure()
    stations_in_legend = False
    for index, (given, trajectory, path) in enumerate(
        zip(trajectories, smooth, paths, strict=True)
    ):
        line: dict[str, Any] = {"width": 7}
        if color_by is None:
            line["color"] = _PALETTE[index % len(_PALETTE)]
        else:
            line |= {
                "color": values[index],
                "colorscale": _DLS_COLOURS if color_by == "dls" else _MD_COLOURS,
                "cmin": float(joined.min()),
                "cmax": float(max(joined.max(), joined.min() + 1e-9)),
                "showscale": index == 0,
                "colorbar": {
                    "title": {
                        "text": _colour_title(first, color_by),
                        "side": "right",
                        "font": {"size": 14},
                    },
                    "thickness": 24,
                    "len": 0.65,
                    "outlinewidth": 0,
                    "tickformat": ",.0f" if color_by == "md" else ".1f",
                    "tickfont": {"color": _MUTED, "size": 13},
                },
            }
        figure.add_trace(
            go.Scatter3d(
                x=path[:, 0],
                y=path[:, 1],
                z=path[:, 2],
                mode="lines",
                name=names[index],
                legendrank=index,
                line=line,
                customdata=_hover_data(trajectory),
                hovertemplate=_hover(trajectory),
            )
        )
        if projections:
            for x, y, z in (
                (path[:, 0], path[:, 1], np.full(len(path), lower[2])),
                (np.full(len(path), lower[0]), path[:, 1], path[:, 2]),
                (path[:, 0], np.full(len(path), upper[1]), path[:, 2]),
            ):
                figure.add_trace(
                    go.Scatter3d(
                        x=x,
                        y=y,
                        z=z,
                        mode="lines",
                        line=_SHADOW,
                        hoverinfo="skip",
                        showlegend=False,
                    )
                )
        surveyed = given.is_survey_station
        if stations and surveyed.any():
            bead: dict[str, Any] = {
                "symbol": "circle",
                "size": 4.5,
                "line": {"color": _BEAD_RIM, "width": 1},
            }
            if color_by is None:
                bead["color"] = line["color"]
            else:
                shown = given.dls() if color_by == "dls" else given.md
                bead |= {key: line[key] for key in ("colorscale", "cmin", "cmax")}
                bead["color"] = shown[surveyed]
            figure.add_trace(
                go.Scatter3d(
                    x=given.x[surveyed],
                    y=given.y[surveyed],
                    z=given.z[surveyed],
                    mode="markers",
                    marker=bead,
                    name=_STATIONS,
                    legendgroup=_STATIONS,
                    showlegend=not stations_in_legend,
                    customdata=_hover_data(given)[surveyed],
                    hovertemplate=f"Survey station of {names[index]}"
                    f"<br>{_hover(given)}<extra></extra>",
                )
            )
            stations_in_legend = True
        for position, symbol, size, colour, marker in (
            (0, "diamond", 9, _WELLHEAD, "Wellhead"),
            (-1, "square", 7, _TD, "TD"),
        ):
            figure.add_trace(
                go.Scatter3d(
                    x=[path[position, 0]],
                    y=[path[position, 1]],
                    z=[path[position, 2]],
                    mode="markers",
                    marker={
                        "symbol": symbol,
                        "size": size,
                        "color": colour,
                        "line": {"color": "white", "width": 2},
                    },
                    name=marker,
                    legendgroup=marker,
                    showlegend=index == 0,
                    hovertemplate=f"{marker} of {names[index]}<extra></extra>",
                )
            )

    # Drawn over the scene rather than in it, so no wall or path hides them.
    annotations = [
        {
            "x": float(path[-1, 0]),
            "y": float(path[-1, 1]),
            "z": float(path[-1, 2]),
            "text": name,
            "ax": 0,
            "ay": -30,
            "arrowhead": 0,
            "arrowwidth": 1,
            "arrowcolor": _MUTED,
            "bgcolor": "rgba(255, 255, 255, 0.9)",
            "bordercolor": _PALETTE[index % len(_PALETTE)]
            if color_by is None
            else _GRID,
            "borderpad": 3,
            "font": {"size": 11, "color": _INK},
        }
        for index, (name, path) in enumerate(zip(names, paths, strict=True))
        if named
    ]

    eye = _VIEW * _CAMERA_DISTANCE * float(np.linalg.norm(aspect * _WIDTH_ALLOWANCE))
    figure.update_layout(
        template="plotly_white",
        **_NOTEBOOK_SIZE,
        font={"family": _FONT, "size": 12, "color": _INK},
        title={
            "text": title or first.crs.name,
            "font": {"size": 17, "weight": "bold"},
            "x": 0.02,
            "xanchor": "left",
            "subtitle": {
                "text": _subtitle(smooth, title),
                "font": {"color": _MUTED, "size": 12},
            },
        },
        scene={
            **_axes(first, lower, upper),
            "aspectmode": "manual",
            "aspectratio": dict(zip("xyz", map(float, aspect), strict=True)),
            "camera": {"eye": dict(zip("xyz", map(float, eye), strict=True))},
            "annotations": annotations,
        },
        legend={
            "x": 0.01,
            "y": 0.97,
            "bgcolor": "rgba(255, 255, 255, 0.85)",
            "bordercolor": _GRID,
            "borderwidth": 1,
            "font": {"size": 13},
        },
        hoverlabel={
            "bgcolor": "white",
            "bordercolor": _GRID,
            "font": {"family": _FONT, "size": 12, "color": _INK},
        },
        margin={"l": 0, "r": 0, "t": 72, "b": 0},
    )
    return figure


def open_in_browser(figure: Any, name: str | None = None) -> str:
    """Open a plotly figure in the default browser, and return its address.

    The page fills the browser window and follows it when resized. It is
    served from a local web server in this process, for as long as it runs: a
    notebook's kernel keeps it up, a script's exit takes it down. To keep a
    figure, write it to a file instead, with ``figure.write_html(path)``.

    Args:
        figure: A figure from :func:`plot_trajectory`, or any plotly figure.
        name: Stem of the page's file name; the figure's title when omitted.

    Returns:
        The page's address, for opening by hand if no browser could be started.
    """
    from plotly.offline import get_plotlyjs

    directory, port = _server()
    title = name or figure.layout.title.text or "trajectory"
    page = f"{re.sub(r'[^A-Za-z0-9_-]+', '-', title).strip('-')}-{next(_FIGURES)}.html"
    script = directory / "plotly.min.js"
    if not script.exists():
        script.write_text(get_plotlyjs(), encoding="utf-8")
    fitted = _plotly().Figure(figure).update_layout(width=None, height=None)
    body = fitted.to_html(
        full_html=False,
        include_plotlyjs=False,
        default_width="100%",
        default_height="100vh",
        config=_CONFIG,
    )
    (directory / page).write_text(
        _PAGE.format(title=html.escape(title), body=body), encoding="utf-8"
    )
    address = f"http://127.0.0.1:{port}/{page}"
    webbrowser.open(address)
    return address


def _axes(
    trajectory: WellTrajectory, lower: np.ndarray, upper: np.ndarray
) -> dict[str, Any]:
    horizontal = trajectory.frame.horizontal_crs
    axes = [horizontal.axes[index] for index in horizontal.value_axis_order[:2]]
    tick = ".5f" if horizontal.crs.is_geographic else ".0f"
    z_unit = _unit(length_factor(trajectory.z_unit), trajectory.z_unit)
    titles = [
        *(f"{a.abbrev} [{_unit(a.unit_conversion_factor, a.unit_name)}]" for a in axes),
        f"z\u2080 \u2212 TVD [{z_unit}]",
    ]
    return {
        f"{key}axis": {
            "title": {"text": text, "font": {"color": _MUTED, "size": 12}},
            "range": [float(lower[index]), float(upper[index])],
            "tickformat": tick if key != "z" else ".0f",
            "tickfont": {"color": _MUTED, "size": 12},
            "nticks": 6,
            "backgroundcolor": _PANE,
            "gridcolor": _GRID,
            "zerolinecolor": _GRID,
            "showbackground": True,
            "showspikes": False,
        }
        for index, (key, text) in enumerate(zip("xyz", titles, strict=True))
    }


def _subtitle(trajectories: Sequence[WellTrajectory], title: str | None) -> str:
    """The CRS if the title is not it, how the wells were georeferenced, and TD."""
    first = trajectories[0]
    methods = " / ".join(
        sorted({re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t.method) for t in trajectories})
    )
    parts = [first.crs.name] if title else []
    parts.append(f"{methods} georeferencing")
    if len(trajectories) == 1:
        parts.append(
            f"TD {first.md[-1]:,.0f} {first.md_unit} MD, "
            f"{first.tvd[-1]:,.0f} {first.z_unit} TVD"
        )
    else:
        parts.append(f"{len(trajectories)} wells")
    return "  ·  ".join(parts)


def _hover_data(trajectory: WellTrajectory) -> np.ndarray:
    """The values :func:`_hover` reads, one row per point."""
    return np.column_stack(
        [
            trajectory.md,
            trajectory.inclination,
            trajectory.azimuth_true,
            trajectory.tvd,
            trajectory.dls(),
        ]
    )


def _hover(trajectory: WellTrajectory) -> str:
    md, z = trajectory.md_unit, trajectory.z_unit
    xy = ".7f" if trajectory.frame.horizontal_crs.crs.is_geographic else ".2f"
    return (
        f"MD %{{customdata[0]:.2f}} {md}<br>"
        "Inclination %{customdata[1]:.2f}°<br>"
        "Azimuth %{customdata[2]:.2f}° true<br>"
        f"TVD %{{customdata[3]:.2f}} {z}<br>"
        f"DLS %{{customdata[4]:.2f}}°/{trajectory.dls_length:g} {md}<br>"
        f"x %{{x:{xy}}}, y %{{y:{xy}}}, z %{{z:.2f}}"
    )


def _colour_title(trajectory: WellTrajectory, color_by: ColorBy) -> str:
    unit = _unit(length_factor(trajectory.md_unit), trajectory.md_unit)
    if color_by == "md":
        return f"MD [{unit}]"
    return f"DLS [°/{trajectory.dls_length:g} {unit}]"


def _unit(factor: float, name: str) -> str:
    for known, written in _UNITS:
        if math.isclose(factor, known, rel_tol=1e-9):
            return written
    return name


def _validate(
    trajectories: Sequence[WellTrajectory], labels: Sequence[str] | None
) -> None:
    if not trajectories:
        raise ValueError("give at least one trajectory to plot")
    if any(item.crs != trajectories[0].crs for item in trajectories):
        raise ValueError("every trajectory must be in the same CRS to share axes")
    if labels is not None and len(labels) != len(trajectories):
        raise ValueError("give one label per trajectory")


def _smoothed(trajectory: WellTrajectory) -> WellTrajectory:
    """The trajectory with enough points along its arcs to draw them as curves."""
    span = float(trajectory.md[-1] - trajectory.md[0])
    if len(trajectory) >= _SMOOTHNESS or span <= 0:
        return trajectory
    return trajectory.resample(span / _SMOOTHNESS)


def _box_at_one_scale(
    trajectory: WellTrajectory, low: np.ndarray, high: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Limits around ``low`` to ``high``, and the aspect showing them at one scale.

    An axis the wells barely span is widened around them rather than
    stretched, so a vertical well looks vertical and a straight one straight.
    """
    # Room around the paths, so no point sits on a wall with its shadow, and
    # beneath the deepest point, so the floor shadow does not overlap it.
    span = high - low
    low = low - np.array([0.03, 0.03, 0.05]) * span
    high = high + np.array([0.03, 0.03, 0.0]) * span
    metres = _metres_per_unit(trajectory, 0.5 * (low[1] + high[1]))
    extent = (high - low) * metres
    target = np.maximum(extent, _SHORTEST_AXIS * max(float(extent.max()), 1.0))
    pad = (target - extent) / 2 / metres
    return low - pad, high + pad, target / target.max()


def _metres_per_unit(trajectory: WellTrajectory, latitude: float) -> np.ndarray:
    """Metres per unit of x, y and z, near enough to draw at one scale."""
    frame = trajectory.frame
    vertical = length_factor(trajectory.z_unit)
    horizontal = frame.horizontal_unit
    if not frame.horizontal_crs.crs.is_geographic:
        return np.array([horizontal, horizontal, vertical])
    # An angular unit: radians per unit, turned into arc length on the ellipsoid.
    radius = frame.semi_axes[0] * horizontal
    return np.array([radius * np.cos(latitude * horizontal), radius, vertical])


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        """Keep requests out of the notebook's output."""


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A browser dropping a connection is routine; anything else is not."""
        if not isinstance(sys.exception(), ConnectionError):
            super().handle_error(request, client_address)


@cache
def _server() -> tuple[Path, int]:
    """A directory, and the port of a local server serving it, for this process."""
    directory = Path(tempfile.mkdtemp(prefix="welltrajectory-"))
    handler = partial(_QuietHandler, directory=str(directory))
    server = _QuietServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return directory, int(server.server_address[1])


def _plotly() -> Any:
    try:
        import plotly.graph_objects as go
    except ImportError as error:
        raise ImportError(
            "interactive plots need plotly: pip install 'geodetic-engine[plot]'"
        ) from error
    return go
