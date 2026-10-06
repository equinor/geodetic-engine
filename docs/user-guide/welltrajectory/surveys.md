---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Surveys and units

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

A directional survey states, at each station, the measured depth along the
hole, the inclination from vertical, and the azimuth of the hole's direction.
{class}`~geodetic_engine.welltrajectory.Survey` holds those values with the
units they are stated in. The units are checked when the survey is made, and
everything downstream works in metres and radians.

Every {class}`~geodetic_engine.welltrajectory.TrajectoryInput` constructor
builds the survey for you, from `md_unit` and `angle_unit`; see {doc}`input`.
This page is about the survey on its own, and the units it accepts.

## Stating a survey

Give one value per station for each quantity:

```{code-cell} python
from geodetic_engine.welltrajectory import Survey

survey = Survey(
    md=[0, 500, 1500, 2500],
    inclination=[0, 20, 60, 70],
    azimuth=[10, 30, 45, 50],
    md_unit="m",
)

print("md        :", survey.md, survey.md_unit)
print("angles in :", survey.angle_unit)
print("md_metres :", survey.md_metres)
print("radians   :", survey.inclination_radians.round(6))
print("azimuths  :", survey.azimuth_degrees)
```

The values are kept as given. {attr}`~geodetic_engine.welltrajectory.Survey.md_metres`,
{attr}`~geodetic_engine.welltrajectory.Survey.inclination_radians` and
{attr}`~geodetic_engine.welltrajectory.Survey.azimuth_degrees` convert them.
`angle_unit` defaults to degrees and applies to both inclination and azimuth.

## Units

A unit can be given in three forms, and is resolved the same way for MD, for
depths and elevations (`z_unit`), and for angles:

| Form | Example |
|---|---|
| A symbol or name, in any case | `"m"`, `"metre"`, `"ft"`, `"ftUS"`, `"km"`, `"degree"`, `"rad"`, `"gon"` |
| An OSDU unit id | `"dev:reference-data--UnitOfMeasure:ft:"` |
| An OSDU unit `persistableReference` | `{"scaleOffset": {"scale": 0.3048, "offset": 0.0}, "symbol": "ft", ...}` |

{func}`~geodetic_engine.welltrajectory.length_factor` and
{func}`~geodetic_engine.welltrajectory.angle_factor` show what a unit resolves
to, in metres and radians per unit:

```{code-cell} python
from geodetic_engine.welltrajectory import angle_factor, length_factor

for unit in ("m", "Metre", "ft", "ftUS", "dev:reference-data--UnitOfMeasure:ft:"):
    print(f"{unit:40} {length_factor(unit)!r}")
for unit in ("degree", "rad", "gon"):
    print(f"{unit:40} {angle_factor(unit)!r}")
```

`ft` is the international foot, 0.3048 m, and `ftUS` the US survey foot,
1200/3937 m. They differ by 2 parts per million, 2 cm over a 10 km well, so
they are never treated as the same unit.

An OSDU unit `persistableReference` is read for its scale. A unit with an
offset, or one that is not a plain length or angle, is refused:

```{code-cell} python
import json

feet = json.dumps(
    {
        "scaleOffset": {"scale": 0.3048, "offset": 0.0},
        "symbol": "ft",
        "baseMeasurement": {"ancestry": "Length", "type": "UM"},
        "type": "USO",
    }
)
length_factor(feet)
```

Anything else raises {class}`~geodetic_engine.welltrajectory.UnitError`. A
unit is never defaulted:

```{code-cell} python
:tags: [raises-exception]

length_factor("fathom")
```

```{code-cell} python
:tags: [raises-exception]

length_factor("deg")  # an angle, not a length
```

The check happens as soon as the survey is made, not when it is first used:

```{code-cell} python
:tags: [raises-exception]

Survey([0, 1000], [0, 10], md_unit="cubit")
```

A survey in feet converts once, to metres:

```{code-cell} python
in_feet = Survey([0, 1000, 2000], [0, 30, 60], [0, 45, 45], md_unit="ft")
in_feet.md_metres
```

## Surveys without azimuths

An inclination-only survey leaves `azimuth` out. The hole is then taken to head
north throughout, and the trajectory records that it was:

```{code-cell} python
from geodetic_engine.welltrajectory import compute_trajectory

inclination_only = Survey([0, 500, 1000], [0, 5, 10])
print("azimuth          :", inclination_only.azimuth)
print("azimuth_degrees  :", inclination_only.azimuth_degrees)

trajectory = compute_trajectory(
    inclination_only, (500000.0, 6600000.0), "EPSG:32631", north="TN"
)
print(*trajectory.operations, sep="\n")
```

## The wellhead

The wellhead is the point measured depth and true vertical depth count from.
It is given in the trajectory CRS, in `xy` order, with its elevation:

```{code-cell} python
from geodetic_engine.welltrajectory import Wellhead

Wellhead(500000.0, 6600000.0, 25.0)
```

`x` and `y` are in the CRS's own units: easting and northing for a projected
CRS, longitude and latitude for a geographic one. `z` is the elevation of the
reference point, positive up, in the trajectory's `z_unit`. Every position is
reported at elevation $z - \mathrm{TVD}$. A plain tuple, `(x, y)` or
`(x, y, z)`, works anywhere a {class}`~geodetic_engine.welltrajectory.Wellhead`
does, with the elevation 0 when left out.

## What the azimuths are measured from

{class}`~geodetic_engine.welltrajectory.NorthReference` says whether the
azimuths are against grid north or true north. The strings `"GN"` and `"TN"`
work too:

```{code-cell} python
from geodetic_engine.welltrajectory import NorthReference

list(NorthReference), NorthReference("GN")
```

Grid azimuths are turned onto true north with the grid convergence at the
wellhead before minimum curvature is run; see {doc}`trajectories`. This needs
a conformal projection, one that preserves angles.

## Surveys that describe no wellbore

The stations must give one value each, as flat sequences of finite numbers:

```{code-cell} python
:tags: [raises-exception]

Survey([0, 500, 1000], [0, 10], [0, 0, 0])
```

The rest is checked when minimum curvature runs: at least two stations, MD
strictly increasing, inclinations between 0 and 180 degrees:

```{code-cell} python
:tags: [raises-exception]

compute_trajectory(
    Survey([0, 500, 400], [0, 10, 20], [0, 0, 0]), (500000.0, 6600000.0), "EPSG:32631"
)
```

Two consecutive stations pointing in opposite directions do not determine
a unique arc plane, which raises
{class}`~geodetic_engine.welltrajectory.DegenerateSurveyError`:

```{code-cell} python
:tags: [raises-exception]

compute_trajectory(
    Survey([0, 100, 200], [0, 90, 90], [0, 0, 180]), (500000.0, 6600000.0), "EPSG:32631"
)
```
