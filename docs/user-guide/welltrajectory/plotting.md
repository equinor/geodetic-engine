---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Plotting

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

{func}`~geodetic_engine.welltrajectory.plot_trajectory`, or
{meth}`WellTrajectory.plot <geodetic_engine.welltrajectory.WellTrajectory.plot>`,
draws one or more trajectories as an interactive 3D plotly figure.

Plotting needs the `plot` extra, which brings plotly and kaleido:

```bash
pip install 'geodetic-engine[plot]'
```

plotly is imported only when a figure is drawn, so nothing else in the package
needs it. Without it, drawing raises an `ImportError` that names the extra.

## One well

```{code-cell} python
from geodetic_engine.welltrajectory import Survey, compute_trajectory

survey = Survey(
    md=[0, 400, 900, 1500, 2200, 3000],
    inclination=[0, 0, 25, 50, 50, 30],
    azimuth=[0, 0, 60, 80, 110, 140],
)
well = compute_trajectory(survey, (500000.0, 6600000.0, 25.0), "EPSG:32631")

well.plot(color_by="dls", labels=["Example well"])
```

Paths are drawn along their arcs rather than as chords between the stations,
coloured by dogleg severity, with faint shadows on the floor and two walls to
make depth readable. Each surveyed station is marked on its path with a small
ring, so you can see the arcs pass through the stations they were computed
from; hover one for its MD, angles, TVD and DLS. All three axes are at one
scale, in metres on the ground, so a vertical well is drawn vertical and a 45°
build looks like 45°. An axis the well barely spans is widened around it
rather than stretched.

| Action | Control |
|---|---|
| Rotate | Drag with the left button |
| Pan | Drag with the right button, or the toolbar's pan tool |
| Zoom | Scroll |
| Inspect a point | Hover: MD, angles, TVD, DLS and position |
| Hide a well | Click its name in the legend |
| Hide the station markers | Click *Survey stations* in the legend |
| Back to the start | Double-click, or the toolbar's home button |

## Several wells

Wells in the same CRS share one set of axes. With `color_by=None` each well
gets a colour of its own, and labels, when given, are drawn at each well's end:

```{code-cell} python
from geodetic_engine.welltrajectory import plot_trajectory

wellhead = (500000.0, 6600000.0, 25.0)
vertical = compute_trajectory(Survey([0, 2500], [0, 0], [0, 0]), wellhead, "EPSG:32631")
deviated = compute_trajectory(
    Survey([0, 500, 1400, 3000], [0, 0, 45, 45], [45, 45, 45, 45]), wellhead, "EPSG:32631"
)

plot_trajectory(
    vertical,
    deviated,
    well,
    color_by=None,
    labels=["Vertical", "Deviated", "Example well"],
    title="Three wells from one wellhead",
)
```

## Options

| Argument | Effect |
|---|---|
| `color_by` | `"dls"`, the default, colours each path by dogleg severity; `"md"` by measured depth; None gives each well a colour of its own. |
| `labels` | A name per trajectory, for the legend, the hover box and the label at its end. |
| `projections` | Also draw each path's shadow on the floor and two walls. True by default. |
| `stations` | Also mark each surveyed station on its path. True by default. Points added between the stations, by `md_step`, `md_points`, `interpolate` or `resample`, are part of the path but not marked. |
| `title` | The figure's title; the CRS's name when left out. |

```{code-cell} python
well.plot(
    color_by="md",
    projections=False,
    stations=False,
    title="Coloured by MD, without shadows or station markers",
)
```

Wells in different CRSs, or labels that do not match the wells one for one,
raise `ValueError`.

## In the browser

A notebook shows a figure in place, at a fixed size.
{func}`~geodetic_engine.welltrajectory.open_in_browser` opens it in a browser
tab that fills the window, and returns the page's address, to open by hand if
no browser could be started:

```python
from geodetic_engine.welltrajectory import open_in_browser

open_in_browser(well.plot())
```

The page is served from a local web server on `127.0.0.1`, in the Python
process, for as long as that process runs: a notebook's kernel keeps it up, a
script's exit takes it down. In a dev container the browser is the host's,
through VS Code's port forwarding.

## Keeping a figure

The figure is a plain plotly figure. Restyle it with `figure.update_layout(...)`,
and write it to a file to keep or share:

```python
figure = well.plot()
figure.write_html("well.html")  # self-contained and interactive
figure.write_image("well.png")  # still PNG, SVG or PDF
```

`write_image` renders through kaleido, which needs Chromium or Chrome; the dev
container installs Chromium.
