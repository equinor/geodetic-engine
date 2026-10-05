---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Across a zone

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

The grid convergence and the point scale factor are not constants of a CRS:
they change from point to point. This page draws them across UTM zone 31N
(WGS 84, central meridian 3°E), the zone the other pages in this section use,
from the equator to 84°N. Each figure is interactive: hover for values, drag
to zoom, double-click to reset.

## The two angles

Grid north is the direction of the grid's $y$ axis, the same everywhere on the
map. True north is the direction of the meridian through the point. East of
the central meridian of a transverse Mercator grid, the meridians lean towards
the central meridian as they go north, so true north lies anticlockwise of
grid north and the convergence $\gamma$ is positive. A direction measured from
true north is $\gamma$ larger than the same direction measured from grid north:

$$A_{grid} = A_{true} - \gamma.$$

```{code-cell} python
:tags: [hide-input]

import numpy as np
import plotly.graph_objects as go

INK, MUTED = "#1f2937", "#6b7280"
gamma = np.radians(20.0)  # exaggerated: within a UTM zone it stays under about 3 degrees
target = np.radians(60.0)  # the target's azimuth from true north


def arc(start: float, end: float, radius: float) -> tuple[np.ndarray, np.ndarray]:
    turned = np.linspace(start, end, 50)
    return radius * np.sin(turned), radius * np.cos(turned)


angles = go.Figure()
for start, end, radius, label, colour in (
    (-gamma, 0.0, 0.32, "γ", "#dc2626"),
    (-gamma, target - gamma, 0.58, "A<sub>true</sub>", "#059669"),
    (0.0, target - gamma, 0.84, "A<sub>grid</sub>", "#2563eb"),
):
    x, y = arc(start, end, radius)
    angles.add_trace(
        go.Scatter(x=x, y=y, mode="lines", line={"color": colour, "width": 2.5}, hoverinfo="skip")
    )
    middle = (start + end) / 2
    angles.add_annotation(
        x=1.15 * radius * np.sin(middle),
        y=1.15 * radius * np.cos(middle),
        text=label,
        showarrow=False,
        font={"color": colour, "size": 16},
    )
for direction, label, colour in (
    (0.0, "grid north", INK),
    (-gamma, "true north", MUTED),
    (target - gamma, "target", "#7c3aed"),
):
    tip_x, tip_y = np.sin(direction), np.cos(direction)
    angles.add_annotation(
        x=tip_x, y=tip_y, ax=0, ay=0, xref="x", yref="y", axref="x", ayref="y",
        showarrow=True, arrowhead=2, arrowsize=1.2, arrowwidth=2, arrowcolor=colour, text="",
    )
    angles.add_annotation(
        x=1.1 * tip_x, y=1.1 * tip_y, text=label, showarrow=False, font={"color": colour, "size": 14}
    )
angles.update_layout(
    template="plotly_white",
    height=460,
    showlegend=False,
    title={"text": "East of the central meridian, γ > 0 (angle exaggerated)", "x": 0.02},
    xaxis={"visible": False, "range": [-0.75, 1.15]},
    yaxis={"visible": False, "range": [-0.1, 1.2], "scaleanchor": "x"},
    margin={"l": 10, "r": 10, "t": 60, "b": 10},
)
angles
```

West of the central meridian the meridians lean the other way, true north
lies clockwise of grid north, and $\gamma$ is negative. The figure exaggerates
the angle: across a UTM zone it is never more than about 3°.

## Evaluating the zone

One call evaluates the factors on a grid of longitudes and latitudes covering
the zone:

```{code-cell} python
from geodetic_engine.geodesy import projection_factors

longitudes = np.linspace(0.0, 6.0, 61)
latitudes = np.linspace(0.0, 84.0, 85)
grid_longitude, grid_latitude = np.meshgrid(longitudes, latitudes)
zone = projection_factors(
    "EPSG:32631",
    np.column_stack([grid_longitude.ravel(), grid_latitude.ravel()]),
    geographic=True,
)
convergence = zone.grid_convergence.reshape(grid_latitude.shape)
scale = zone.scale_factor.reshape(grid_latitude.shape)

print(f"{len(zone.grid_convergence)} points")
print(f"grid convergence from {convergence.min():.3f} to {convergence.max():.3f} degrees")
print(f"scale factor from {scale.min():.6f} to {scale.max():.6f}")
```

```{code-cell} python
:tags: [hide-input]

def zone_map(values, title, colour_title, colorscale, contours, number_format, **extra):
    figure = go.Figure(
        go.Contour(
            x=longitudes,
            y=latitudes,
            z=values,
            colorscale=colorscale,
            contours={
                **contours,
                "showlabels": True,
                "labelfont": {"size": 11, "color": INK},
                "labelformat": number_format,
            },
            colorbar={"title": {"text": colour_title, "side": "right"}, "tickformat": number_format},
            hovertemplate="%{x:.2f}°E, %{y:.1f}°N<br>%{z:" + number_format + "}<extra></extra>",
            **extra,
        )
    )
    figure.add_vline(x=3.0, line={"color": INK, "dash": "dot", "width": 1})
    figure.update_layout(
        template="plotly_white",
        height=620,
        title={"text": title, "x": 0.02},
        xaxis={"title": "longitude [°E]", "dtick": 1},
        yaxis={"title": "latitude [°N]"},
        margin={"l": 60, "r": 20, "t": 60, "b": 50},
    )
    return figure
```

## Grid convergence

```{code-cell} python
:tags: [hide-input]

zone_map(
    convergence,
    "Grid convergence γ across UTM zone 31N",
    "γ [°]",
    "RdBu_r",
    {"start": -3.0, "end": 3.0, "size": 0.25},
    ".2f",
    zmid=0.0,
)
```

The convergence is zero along the central meridian, the dotted line, where
the meridian is a grid line, and along the equator, where the meridians are
parallel. It grows with the distance from the central meridian and with
latitude, roughly as $(\lambda - \lambda_0)\sin\varphi$ (see {doc}`theory`), to
about ±3° at the zone's edges near the pole. A survey in the north-east of the
zone that confuses grid and true north is about 3° off: 50 m sideways for
every kilometre travelled.

## Point scale factor

```{code-cell} python
:tags: [hide-input]

zone_map(
    scale,
    "Point scale factor k across UTM zone 31N",
    "k",
    "Viridis",
    {"start": 0.9996, "end": 1.001, "size": 0.0001},
    ".4f",
)
```

The scale factor is $k_0 = 0.9996$ on the central meridian, where a distance
on the grid is 0.04 % shorter than on the ellipsoid, and grows towards the
zone's edges. Where it crosses one, the two agree. Away from the equator the
zone is narrower, so its edges do not reach as far from the central meridian
and the scale factor stays smaller there.

Plotted against the distance from the central meridian, the scale factor at
different latitudes falls on almost the same curve. On the grid it depends on
how far east or west a point is, hardly on its latitude:

```{code-cell} python
from geodetic_engine.geodesy import Transformation

to_grid = Transformation("EPSG:4326", "EPSG:32631")
eastings = np.linspace(166000.0, 834000.0, 201)
profiles = {}
for latitude in (0.0, 30.0, 60.0):
    ((_, northing),) = to_grid.transform([(3.0, latitude)]).coordinates
    line = projection_factors(
        "EPSG:32631", np.column_stack([eastings, np.full(len(eastings), northing)])
    )
    inside = np.abs(line.longitude - 3.0) <= 3.0
    profiles[latitude] = ((eastings[inside] - 500000.0) / 1000.0, line.scale_factor[inside])

distance, k = profiles[0.0]
crossing = np.interp(1.0, k[distance > 0], distance[distance > 0])
edge = np.interp(1.0, scale[::-1, -1], latitudes[::-1])
print(f"k = 1 at {crossing:.1f} km either side of the central meridian")
print(f"the zone's edge is closer than that poleward of {edge:.1f}°N")
```

```{code-cell} python
:tags: [hide-input]

profile = go.Figure()
for (latitude, (distance, k)), colour in zip(
    profiles.items(), ("#2563eb", "#ea580c", "#059669"), strict=True
):
    profile.add_trace(
        go.Scatter(
            x=distance,
            y=k,
            mode="lines",
            name=f"{latitude:.0f}°N",
            line={"color": colour, "width": 2.5},
            hovertemplate="%{x:.0f} km<br>k %{y:.6f}<extra>" + f"{latitude:.0f}°N</extra>",
        )
    )
profile.add_hline(y=1.0, line={"color": MUTED, "dash": "dash", "width": 1})
for side in (-crossing, crossing):
    profile.add_vline(x=side, line={"color": MUTED, "dash": "dash", "width": 1})
profile.update_layout(
    template="plotly_white",
    height=460,
    title={"text": "Point scale factor against distance from the central meridian", "x": 0.02},
    xaxis={"title": "distance from the central meridian [km]", "zeroline": False},
    yaxis={"title": "k", "tickformat": ".4f"},
    legend={"title": {"text": "latitude"}},
    margin={"l": 70, "r": 20, "t": 60, "b": 50},
)
profile
```

Each curve stops at the zone's edge, which at 60°N is under 170 km from the
central meridian, so there the whole zone has $k < 1$. The curve is close to
$k \approx k_0\,(1 + x^2 / 2R^2)$, with $x$ the distance from the central
meridian and $R$ the earth's radius. {doc}`azimuths-and-distances` uses the
factors on azimuths and distances.
