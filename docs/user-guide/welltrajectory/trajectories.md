---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Computing a trajectory

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal

import pandas as pd

pd.set_option("display.precision", 3)
pd.set_option("display.width", 160)
```

{func}`~geodetic_engine.welltrajectory.compute_trajectory` takes a survey, a
wellhead and a CRS, and returns a
{class}`~geodetic_engine.welltrajectory.WellTrajectory`: every position along
the wellbore, with how it was arrived at.
{meth}`TrajectoryInput.compute <geodetic_engine.welltrajectory.TrajectoryInput.compute>`
calls it with everything an input holds, so what this page shows applies to
both; {doc}`input` builds the input.

## A first trajectory

```{code-cell} python
from geodetic_engine.welltrajectory import Survey, compute_trajectory

survey = Survey(
    md=[0, 500, 1500, 2500],
    inclination=[0, 20, 60, 70],
    azimuth=[10, 30, 45, 50],
    md_unit="m",
)
trajectory = compute_trajectory(
    survey,
    wellhead=(500000.0, 6600000.0, 25.0),
    crs="EPSG:32631",
    north="GN",
    method="AzimuthalEquidistant",
    z_unit="m",
)
trajectory.to_dataframe()
```

{meth}`~geodetic_engine.welltrajectory.WellTrajectory.to_dataframe` gives one
row per point. `x` and `y` are in the CRS, and `z` is the elevation, the
wellhead's 25 m less the true vertical depth.

## The arguments

| Argument | Meaning |
|---|---|
| `survey` | The {class}`~geodetic_engine.welltrajectory.Survey`; see {doc}`surveys`. |
| `wellhead` | Where MD and TVD count from: a {class}`~geodetic_engine.welltrajectory.Wellhead`, or `(x, y)` or `(x, y, z)` in the CRS, with `z` its elevation in `z_unit`. |
| `crs` | The trajectory CRS: anything {meth}`CoordinateReferenceSystem.from_user_input <geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input>` accepts, such as an EPSG code, WKT, PROJJSON, an OSDU `persistableReference`, or a {term}`bound CRS`. Projected or geographic. |
| `north` | What the azimuths are measured from: `"GN"`, grid north, the default, or `"TN"`, true north. |
| `method` | How the offsets are georeferenced in the CRS; see {doc}`georeferencing`. `"AzimuthalEquidistant"` by default. |
| `z_unit` | The unit of the wellhead elevation and of every depth, elevation and offset reported. Metres by default. |
| `md_step` | If given, also a point every `md_step` of MD, in the survey's MD unit, between the stations. |
| `md_points` | If given, also a point at each of these measured depths, in the survey's MD unit. |
| `name` | The well's name, carried to the trajectory and used as its label in plots. |

The points `md_step` and `md_points` add come back among the survey stations,
in MD order, with `is_survey_station` False.

## What a trajectory holds

One array entry per point, surveyed or interpolated:

| Attribute | Content |
|---|---|
| `md` | Measured depth, in `md_unit`. |
| `inclination`, `azimuth_true`, `azimuth_grid` | The hole's direction, in degrees. `azimuth_grid` equals `azimuth_true` in a geographic CRS. |
| `east`, `north`, `tvd` | Offsets from the wellhead against true north, TVD positive down, in `z_unit`. |
| `x`, `y` | Position in the CRS, in its own units. |
| `z` | Elevation, the wellhead's `z` less `tvd`, in `z_unit`. |
| `curvature` | Dogleg per metre of MD, in radians; see {meth}`~geodetic_engine.welltrajectory.WellTrajectory.dls`. |
| `is_survey_station` | True for a surveyed station, False for an interpolated point. |

and, once for the whole trajectory, how it was computed, and the well's `name`
if the input gave one:

```{code-cell} python
print("crs            :", trajectory.crs.name)
print("method         :", trajectory.method)
print("north_reference:", trajectory.north_reference)
print("units          :", trajectory.md_unit, trajectory.z_unit)
print("local_crs      :", trajectory.local_crs.name)
print()
print(*trajectory.operations, sep="\n")
```

{attr}`~geodetic_engine.welltrajectory.WellTrajectory.operations` says what
was done, in order. `local_crs` is the CRS the offsets were read in, here the
azimuthal equidistant projection centred on the wellhead; methods that read
the offsets in no CRS of their own leave it None.

## Units of the results

MD stays in the survey's unit. Depths, elevations and offsets are in `z_unit`,
which also applies to the wellhead's elevation. A survey in metres can report
its depths in feet:

```{code-cell} python
in_feet = compute_trajectory(
    survey, (500000.0, 6600000.0, 82.0), "EPSG:32631", z_unit="ft"
)
in_feet.to_dataframe()[["md", "tvd", "z", "east", "north"]]
```

`x` and `y` are always in the CRS's own units, whatever `z_unit` is.

## Dogleg severity

{meth}`~geodetic_engine.welltrajectory.WellTrajectory.dls` gives the dogleg
severity in degrees per a length of MD: per 30 m by default, or per 100 ft for
a survey in feet, which is
{attr}`~geodetic_engine.welltrajectory.WellTrajectory.dls_length`. Any other
length can be asked for:

```{code-cell} python
print("per", trajectory.dls_length, trajectory.md_unit, ":", trajectory.dls().round(3))
print("per 10 m   :", trajectory.dls(per_length=10).round(3))
print("rad per m  :", trajectory.curvature.round(6))
```

The value at a station is the curvature of the arc that ends there, and zero
at the first station.

## Points between stations

{meth}`~geodetic_engine.welltrajectory.WellTrajectory.interpolate` gives the
trajectory at any measured depths inside the survey, on the minimum curvature
arcs:

```{code-cell} python
trajectory.interpolate([1234.5, 2000]).to_dataframe()
```

{meth}`~geodetic_engine.welltrajectory.WellTrajectory.resample` gives a point
every given length of MD, keeping the surveyed stations unless told not to,
and always the last one:

```{code-cell} python
every_250 = trajectory.resample(250)
every_250.to_dataframe()[["md", "inclination", "azimuth_true", "tvd", "is_survey_station"]]
```

`md_step` does the same while the trajectory is computed. A depth outside the
survey raises {class}`~geodetic_engine.welltrajectory.InvalidSurveyError`:

```{code-cell} python
:tags: [raises-exception]

trajectory.interpolate([3000])
```

{doc}`minimum-curvature` explains how the points between stations are found.

## Grid north and true north

Minimum curvature runs against true north. Azimuths against grid north are
turned onto true north first, by the grid convergence at the wellhead, from
{func}`~geodetic_engine.geodesy.projection_factors`. The wellhead above sits on
the central meridian, where the convergence is zero. Off it, the two readings
of the same survey part:

```{code-cell} python
off_meridian = (666000.0, 6660000.0, 25.0)
as_grid = compute_trajectory(survey, off_meridian, "EPSG:32631", north="GN")
as_true = compute_trajectory(survey, off_meridian, "EPSG:32631", north="TN")

print("grid convergence at the wellhead:", as_grid.factors.grid_convergence)
print("scale factor at the wellhead    :", as_grid.factors.scale_factor)

pd.DataFrame(
    {
        "md": as_grid.md,
        "azimuth_grid (read as GN)": as_grid.azimuth_grid,
        "azimuth_true (read as GN)": as_grid.azimuth_true,
        "x (GN) - x (TN)": as_grid.x - as_true.x,
        "y (GN) - y (TN)": as_grid.y - as_true.y,
    }
)
```

The convergence is taken at the wellhead and held for the whole well.
{meth}`~geodetic_engine.welltrajectory.WellTrajectory.projection_factors`
shows how much it changes along the well:

```{code-cell} python
along = as_grid.projection_factors()
along.grid_convergence - as_grid.factors.grid_convergence
```

## A geographic CRS

In a geographic CRS the positions are longitude and latitude, in the CRS's own
unit, on its own ellipsoid. There is no grid, so the azimuths must be against
true north:

```{code-cell} python
geographic = compute_trajectory(survey, (4.0, 58.0, 25.0), "EPSG:4326", north="TN")
geographic.to_dataframe()[["md", "east", "north", "x", "y", "z"]]
```

```{code-cell} python
:tags: [raises-exception]

compute_trajectory(survey, (4.0, 58.0, 25.0), "EPSG:4326", north="GN")
```

## Moving to another CRS

{meth}`~geodetic_engine.welltrajectory.WellTrajectory.to_geographic` gives the
horizontal positions in another CRS, WGS 84 by default, as a
{class}`~geodetic_engine.geodesy.TransformationResult` with its provenance.
From WGS 84 / UTM zone 31N that needs no datum change:

```{code-cell} python
trajectory.to_geographic().coordinates.to_numpy()
```

From ED50 it does, and a datum change has to be named, exactly as for
{class}`~geodetic_engine.geodesy.Transformation`:

```{code-cell} python
:tags: [raises-exception]

ed50 = compute_trajectory(survey, (500000.0, 6600000.0, 25.0), "EPSG:23031")
ed50.to_geographic()
```

```{code-cell} python
wgs84 = ed50.to_geographic(operation="EPSG:1133")
print(wgs84.operation.name)
wgs84.coordinates.to_numpy()
```

A {term}`bound CRS` names its own datum shift, so a trajectory computed in one
moves without an operation:

```{code-cell} python
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

bound = BoundCRS(
    CRS("EPSG:23031"), CRS("EPSG:4326"), CoordinateOperation.from_epsg(1133)
)
in_bound = compute_trajectory(survey, (500000.0, 6600000.0, 25.0), bound)
print(in_bound.to_geographic().operation.name)
```

The positions in the bound CRS are the same as in its base, ED50 / UTM zone
31N: the binding only matters when the positions leave the CRS. See
{doc}`/user-guide/geodesy/results` for what a transformation result records.
