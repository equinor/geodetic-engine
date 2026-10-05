---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Azimuths and distances

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

Surveying uses two of the factors: the grid convergence to turn azimuths, and
the point scale factor to scale distances. This page applies both, and checks
each against a measurement made with ordinary coordinate transformations, so
the sign convention is shown rather than only stated.

## Turning azimuths

Grid north lies $\gamma$ clockwise of true north, so

$$A_{grid} = A_{true} - \gamma, \qquad A_{true} = A_{grid} + \gamma.$$

{meth}`~geodetic_engine.geodesy.ProjectionFactors.to_grid_azimuth` and
{meth}`~geodetic_engine.geodesy.ProjectionFactors.to_true_azimuth` apply this,
in degrees, and wrap the result into $[0°, 360°)$:

```{code-cell} python
from geodetic_engine.geodesy import projection_factors

factors = projection_factors("EPSG:32631", (6.0, 60.0), geographic=True)

print("grid convergence  :", factors.grid_convergence)
print("true 0 on the grid:", factors.to_grid_azimuth(0.0))
print("grid 0 is true    :", factors.to_true_azimuth(0.0))
print("grid 90, 180, 270 :", factors.to_true_azimuth([90.0, 180.0, 270.0]))
```

With factors at several points, one azimuth is turned at every point, or one
azimuth per point when as many are given:

```{code-cell} python
both_sides = projection_factors(
    "EPSG:32631", [(400000.0, 6650000.0), (600000.0, 6650000.0)]
)

print("convergence      :", both_sides.grid_convergence)
print("grid 45, each    :", both_sides.to_true_azimuth(45.0))
print("grid 45 and 135  :", both_sides.to_true_azimuth([45.0, 135.0]))
```

West of the central meridian grid north lies west of true north, so the
convergence is negative and a grid azimuth is turned anticlockwise onto true
north.

### Checking the sign

Transform two points along the local meridian onto the grid, and measure the
grid azimuth of the line joining them. That line is true north, and the
convention says true north lies at grid azimuth $-\gamma$:

```{code-cell} python
import math

from geodetic_engine.geodesy import Transformation

to_grid = Transformation("EPSG:4326", "EPSG:32631")
(x1, y1), (x2, y2) = to_grid.transform([(6.0, 60.0), (6.0, 60.0001)]).coordinates
bearing = math.degrees(math.atan2(x2 - x1, y2 - y1))

print(f"grid azimuth of true north: {bearing:.6f}")
print(f"minus the grid convergence: {-factors.grid_convergence[0]:.6f}")
```

They agree to about a millionth of a degree, which is how much the convergence
changes along the 11 m line itself. The same check, at points on every side of
a zone and in a Lambert projection, is part of the test suite, so the
convention cannot drift.

## Scaling distances

The point scale factor relates a short distance on the grid to the same
distance on the ellipsoid:

$$d_{grid} = k \, d_{ellipsoid}.$$

Measure a line about 560 m long both ways: as a geodesic on the WGS 84
ellipsoid, and as the straight distance between the line's ends on the grid.
Their ratio is the scale factor at the line's middle:

```{code-cell} python
from pyproj import Geod

west, east = (5.995, 60.0), (6.005, 60.0)
_, _, on_ellipsoid = Geod(ellps="WGS84").inv(*west, *east)
(xw, yw), (xe, ye) = to_grid.transform([west, east]).coordinates
on_grid = math.hypot(xe - xw, ye - yw)

print(f"on the ellipsoid: {on_ellipsoid:.4f} m")
print(f"on the grid     : {on_grid:.4f} m")
print(f"ratio           : {on_grid / on_ellipsoid:.10f}")
print(f"scale factor    : {factors.scale_factor[0]:.10f}")
```

The scale factor of a conformal projection is the same in every direction, so
the same holds for a line at any azimuth. For a non-conformal projection,
`scale_factor` is the scale along the parallel and `meridional_scale` the scale
along the meridian.

```{note}
`scale_factor` relates the grid to the **ellipsoid**. A distance measured on
the ground at a height $h$ above the ellipsoid is longer than its ellipsoidal
counterpart by about $(R + h) / R$, with $R$ the earth's radius in the line's
direction. Reduce a measured distance to the ellipsoid before scaling it onto
the grid. This package does not apply that elevation factor.
```

## Over a distance

The factors change from point to point. A traverse, or an extended reach well,
that uses the values at one end for its whole length takes on their change
across it. Here is a 10 km line heading east, off the central meridian:

```{code-cell} python
import numpy as np
import pandas as pd

eastings = np.linspace(666000.0, 676000.0, 6)
line = projection_factors(
    "EPSG:32631", np.column_stack([eastings, np.full(len(eastings), 6660000.0)])
)

pd.DataFrame(
    {
        "easting": eastings,
        "grid_convergence": line.grid_convergence,
        "scale_factor": line.scale_factor,
    }
).round(7)
```

The {doc}`grid north local </user-guide/welltrajectory/georeferencing>`
method of georeferencing a well trajectory takes the factors at the wellhead
for the whole well, which is why it drifts from the other methods as the reach
grows.
