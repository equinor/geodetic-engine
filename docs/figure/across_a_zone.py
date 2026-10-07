"""Draw the figures of docs/user-guide/geodesy/projection-factors.md.

Run from the repository root with the dev extra installed::

    uv run python docs/figure/across_a_zone.py

The SVGs are written next to this file, and the numbers the page quotes are
printed. Text is drawn as outlines, so a figure looks the same in every
browser; change it here and run the script again.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.colors import LinearSegmentedColormap, Normalize

from geodetic_engine.geodesy import Transformation, projection_factors

HERE = Path(__file__).parent
CRS = "EPSG:32631"  # UTM zone 31N, central meridian 3°E

# Mid-tone colours that keep their contrast on both the light and the dark theme.
NEUTRAL, FAINT = "#64748b", "#64748b40"
GRID, TRUE, GAMMA, TARGET = "#3b82f6", "#059669", "#ea580c", "#8b5cf6"
plt.rcParams.update(
    {
        "figure.facecolor": "none",
        "axes.facecolor": "none",
        # Computer Modern, as LaTeX sets it, without needing a TeX installation.
        "font.family": "serif",
        "font.serif": ["cmr10"],
        "mathtext.fontset": "cm",
        "axes.formatter.use_mathtext": True,
        "font.size": 12,
        "text.color": NEUTRAL,
        "axes.edgecolor": NEUTRAL,
        "axes.labelcolor": NEUTRAL,
        "axes.titlecolor": NEUTRAL,
        "xtick.color": NEUTRAL,
        "ytick.color": NEUTRAL,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "xtick.top": True,
        "ytick.right": True,
        "legend.frameon": False,
        "svg.hashsalt": "across-a-zone",
    }
)


def save(figure: plt.Figure, name: str) -> None:
    # No date in the metadata: an unchanged figure leaves its file unchanged.
    figure.savefig(
        HERE / f"{name}.svg",
        bbox_inches="tight",
        pad_inches=0.05,
        metadata={"Date": None},
    )
    plt.close(figure)


def polar(angle: float | np.ndarray, radius: float) -> tuple:
    return radius * np.sin(angle), radius * np.cos(angle)


def arrow(
    ax: plt.Axes, tip: tuple, tail: tuple, colour: str, width: float = 1.8
) -> None:
    style = {"arrowstyle": "-|>", "color": colour, "lw": width, "mutation_scale": 15}
    ax.annotate(
        "", xy=tip, xytext=tail, arrowprops={**style, "shrinkA": 0, "shrinkB": 0}
    )


def angles() -> None:
    gamma = np.radians(
        20.0
    )  # exaggerated: within a UTM zone it stays under about 3 degrees
    target = np.radians(50.0)  # the target's azimuth from true north
    figure, panels = plt.subplots(1, 2, figsize=(8.0, 3.8))
    titles = (
        r"East of the central meridian: $\gamma > 0$",
        r"West of the central meridian: $\gamma < 0$",
    )
    for ax, convergence, title in zip(panels, (gamma, -gamma), titles, strict=True):
        true_north, on_grid = -convergence, target - convergence
        ax.set_title(title)

        wedge_x, wedge_y = polar(np.linspace(true_north, 0.0, 40), 0.36)
        ax.fill(
            [0, *wedge_x], [0, *wedge_y], facecolor=GAMMA + "38", edgecolor=GAMMA, lw=1
        )
        ax.text(
            *polar(true_north / 2, 0.47),
            r"$\gamma$",
            color=GAMMA,
            fontsize=15,
            ha="center",
            va="center",
        )

        for start, end, radius, text, colour in (
            (true_north, on_grid, 0.6, r"$\alpha_\mathrm{true}$", TRUE),
            (0.0, on_grid, 0.84, r"$\alpha_\mathrm{grid}$", GRID),
        ):
            ax.plot(*polar(np.linspace(start, end, 60), radius), color=colour, lw=1.8)
            arrow(
                ax,
                polar(end, radius),
                polar(end - 0.05 * np.sign(end - start), radius),
                colour,
            )
            # Mid-way along the part of the arc clear of grid north.
            middle = (max(start, 0.0) + end) / 2
            ax.text(
                *polar(middle, 1.17 * radius),
                text,
                color=colour,
                fontsize=15,
                ha="center",
                va="center",
            )

        for direction, text, colour in (
            (0.0, "grid north", GRID),
            (true_north, "true north", TRUE),
            (on_grid, "target", TARGET),
        ):
            tip = polar(direction, 1.0)
            arrow(ax, tip, (0.0, 0.0), colour, width=2.0)
            side = (
                "center" if abs(tip[0]) < 0.1 else ("left" if tip[0] > 0 else "right")
            )
            ax.text(
                *polar(direction, 1.05),
                text,
                color=colour,
                ha=side,
                va="bottom" if side == "center" else "center",
            )
        ax.plot(0, 0, "o", color=NEUTRAL, ms=5)
        ax.set(xlim=(-0.75, 1.25), ylim=(-0.05, 1.15), aspect="equal")
        ax.axis("off")
    save(figure, "grid-convergence-angles")


def zone_map(
    name,
    lons,
    lats,
    values,
    title,
    colour_label,
    colours,
    limits,
    levels,
    decimals,
) -> None:
    colour_map = LinearSegmentedColormap.from_list(name, colours)
    norm = Normalize(*limits)
    figure, ax = plt.subplots(figsize=(8.0, 6.6))
    # Lines rather than fills keep the map readable on either theme.
    lines = ax.contour(
        lons,
        lats,
        values,
        levels=levels,
        cmap=colour_map,
        norm=norm,
        linewidths=1.3,
    )
    ax.clabel(lines, fmt=lambda value: f"${value:.{decimals}f}$", fontsize=10)
    ax.axvline(3.0, color=NEUTRAL, ls=":", lw=1)
    bar = figure.colorbar(
        ScalarMappable(norm, colour_map), ax=ax, fraction=0.04, pad=0.03
    )
    bar.ax.set_title(colour_label, fontsize=12, pad=8)
    bar.ax.yaxis.set_major_formatter(lambda value, _: f"${value:.{decimals}f}$")
    bar.outline.set_visible(False)
    ax.set(
        xlabel=r"longitude $\lambda$ [$^\circ$]",
        ylabel=r"latitude $\varphi$ [$^\circ$]",
        xticks=range(7),
        yticks=range(0, 90, 10),
    )
    ax.set_title(title, loc="left")
    save(figure, name)


def zone_maps() -> None:
    lons = np.linspace(0.0, 6.0, 61)
    lats = np.linspace(0.0, 84.0, 85)
    grid_lon, grid_lat = np.meshgrid(lons, lats)
    points = np.column_stack([grid_lon.ravel(), grid_lat.ravel()])
    zone = projection_factors(CRS, points, geographic=True)
    convergence = zone.grid_convergence.reshape(grid_lat.shape)
    scale = zone.scale_factor.reshape(grid_lat.shape)

    zone_map(
        "grid-convergence-map",
        lons,
        lats,
        convergence,
        r"Grid convergence $\gamma$ across UTM zone 31N",
        r"$\gamma$ [$^\circ$]",
        [GRID, NEUTRAL, GAMMA],
        (-3.0, 3.0),
        # Not 0: that runs along the central meridian and the equator.
        np.delete(np.linspace(-2.5, 2.5, 11), 5),
        1,
    )
    zone_map(
        "scale-factor-map",
        lons,
        lats,
        scale,
        r"Point scale factor $k$ across UTM zone 31N",
        r"$k$",
        [GRID, TARGET, GAMMA],
        (0.9996, 1.001),
        # Not 0.9996: that is the central meridian.
        np.linspace(0.9998, 1.001, 7),
        4,
    )
    edge = np.interp(1.0, scale[::-1, -1], lats[::-1])
    print(f"{len(points)} points")
    print(f"grid convergence from {convergence.min():.3f} to {convergence.max():.3f}")
    print(f"scale factor from {scale.min():.6f} to {scale.max():.6f}")
    print(f"k = 1 at the zone's edge at {edge:.1f}°N")


def scale_curve() -> None:
    to_grid = Transformation("EPSG:4326", CRS)
    # Along each parallel, from one edge of the zone to the other.
    lons = np.linspace(0.0, 6.0, 241)
    curves = {}
    for lat in (0.0, 30.0, 60.0):
        points = np.column_stack([lons, np.full(len(lons), lat)])
        easting = np.asarray(to_grid.transform(points).coordinates)[:, 0]
        k = projection_factors(CRS, points, geographic=True).scale_factor
        curves[lat] = (easting - 500000.0) / 1000.0, k
        edge = easting.max() / 1000.0 - 500.0
        print(f"{lat:.0f}°N: the zone's edge {edge:.1f} km out, k {k.max():.6f}")
    distance, k = curves[0.0]
    east = distance > 0
    crossing = np.interp(1.0, k[east], distance[east])
    print(f"k = 1 at {crossing:.1f} km either side of the central meridian")
    for lat in (30.0, 60.0):
        other_distance, other_k = curves[lat]
        difference = np.abs(other_k - np.interp(other_distance, distance, k)).max()
        print(f"{lat:.0f}°N: k differs from the equator's by {difference:.1e}")

    # Only the equator: at the same distance the others differ too little to see.
    figure, ax = plt.subplots(figsize=(8.0, 4.4))
    ax.plot(distance, k, color=GRID, lw=2.5)
    ax.axhline(1.0, color=NEUTRAL, lw=0.8, alpha=0.7)
    for side in (-crossing, crossing):
        ax.axvline(side, color=NEUTRAL, lw=0.8, alpha=0.7)
    ax.grid(color=FAINT, lw=0.6)
    ax.yaxis.set_major_formatter(lambda value, _: f"${value:.4f}$")
    ax.set(xlabel="distance from the central meridian [km]", ylabel=r"$k$")
    ax.set_title(
        r"Point scale factor $k$ against the distance from the central meridian",
        loc="left",
    )
    save(figure, "scale-factor-profile")


if __name__ == "__main__":
    angles()
    zone_maps()
    scale_curve()
