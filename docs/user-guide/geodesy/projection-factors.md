---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Projection factors

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

A map projection turns directions and stretches distances, by an amount that
changes from point to point.
{func}`~geodetic_engine.geodesy.projection_factors` reports how much, at any
point of a projected CRS:

- the {term}`grid convergence`, the angle between true north and grid north;
- the {term}`point scale factor`, a short distance on the grid over the same
  distance on the ellipsoid;
- the meridional scale, the areal scale and the angular distortion.

Use the grid convergence to turn an azimuth between true north and grid north,
and the scale factor to scale a distance between the ellipsoid and the grid.
The values are PROJ's, from `Proj.get_factors()`.

## Conventions

| Value | Meaning |
|---|---|
| `grid_convergence`, $\gamma$ | The angle from true north to grid north, in degrees, positive clockwise. On a conformal projection, $\alpha_{grid} = \alpha_{true} - \gamma$. |
| `scale_factor`, $k$ | Grid distance over ellipsoid distance, along the parallel. On a conformal projection it is the same in every direction. |
| `meridional_scale`, $h$ | The same along the meridian. Equal to $k$ on a conformal projection. |
| `areal_scale` | Grid area over ellipsoid area. 1 on an equal-area projection. |
| `angular_distortion` | The most the projection changes an angle, in degrees. 0 on a conformal projection. |

Points are in `xy` order and in the CRS's own units, or longitude and latitude
with `geographic=True`. A third value per point, such as a height, is ignored.

## At one point

```{code-cell} python
from geodetic_engine.geodesy import projection_factors

factors = projection_factors("EPSG:32631", (600000.0, 6700000.0))

print("grid convergence  :", factors.grid_convergence)
print("scale factor      :", factors.scale_factor)
print("angular distortion:", factors.angular_distortion)
print("longitude         :", factors.longitude)
print("latitude          :", factors.latitude)
```

Every value is an array with one entry per point. The point is 100 km east of
the central meridian of UTM zone 31N, so grid north lies clockwise of true
north and the convergence is positive. UTM is conformal, so the angular
distortion is zero.

## At many points

Give one row per point, as a list of tuples or a numpy array:

```{code-cell} python
import numpy as np
import pandas as pd

eastings = np.arange(350000.0, 650001.0, 50000.0)
points = np.column_stack([eastings, np.full(len(eastings), 6650000.0)])
across = projection_factors("EPSG:32631", points)

pd.DataFrame(
    {
        "easting": eastings,
        "grid_convergence": across.grid_convergence,
        "scale_factor": across.scale_factor,
    }
).style.hide(axis="index").format(precision=6).format("{:.0f}", subset="easting")
```

The convergence changes sign at the central meridian (easting 500 000 m). The
scale factor is smallest there, 0.9996, and grows towards the zone's edges.

## From longitude and latitude

With `geographic=True` the points are longitude and latitude in the CRS's own
geographic CRS:

```{code-cell} python
at_position = projection_factors("EPSG:32631", (6.0, 60.0), geographic=True)

print("grid convergence:", at_position.grid_convergence)
print("scale factor    :", at_position.scale_factor)
```

## Turning azimuths

Grid north lies $\gamma$ clockwise of true north, so

$$\alpha_{grid} = \alpha_{true} - \gamma, \qquad \alpha_{true} = \alpha_{grid} + \gamma.$$

```{image} /figure/grid-convergence-angles.svg
:alt: Two diagrams. East of the central meridian true north lies anticlockwise of grid north by gamma, west of it clockwise. Arcs from each north to a target show the true and the grid azimuth.
:align: center
```

East of the central meridian, on the left, $\gamma$ is positive. West of it, on
the right, $\gamma$ is negative. The figure exaggerates the angle: across a UTM
zone it is never more than about 3°.

{meth}`~geodetic_engine.geodesy.ProjectionFactors.to_grid_azimuth` and
{meth}`~geodetic_engine.geodesy.ProjectionFactors.to_true_azimuth` apply this,
in degrees, and return values in $[0°, 360°)$:

```{code-cell} python
print("grid convergence:", at_position.grid_convergence)
print("true 90 on grid :", at_position.to_grid_azimuth(90.0))
print("grid 90 is true :", at_position.to_true_azimuth(90.0))
```

With factors at several points, one azimuth is turned at every point, or one
azimuth per point is turned at its own point.

To check the sign, project two points on the same meridian onto the grid. The
line between them points to true north, and on the grid it lies at azimuth
$-\gamma$:

```{code-cell} python
import math

from geodetic_engine.geodesy import Transformation

to_grid = Transformation("EPSG:4326", "EPSG:32631")
(x1, y1), (x2, y2) = to_grid.transform([(6.0, 60.0), (6.0, 60.0001)]).coordinates

bearing = math.degrees(math.atan2(x2 - x1, y2 - y1))

print(f"grid azimuth of true north: {bearing:.6f}")
print(f"minus grid convergence    : {-at_position.grid_convergence[0]:.6f}")
```

## Scaling distances

The scale factor relates a short distance on the ellipsoid to the same distance
on the grid:

$$d_{grid} = k \, d_{ellipsoid}.$$

To check it, measure a short line both ways and compare the ratio with $k$:

```{code-cell} python
from pyproj import Geod

west, east = (5.995, 60.0), (6.005, 60.0)
_, _, on_ellipsoid = Geod(ellps="WGS84").inv(*west, *east)
(xw, yw), (xe, ye) = to_grid.transform([west, east]).coordinates
on_grid = math.hypot(xe - xw, ye - yw)

print(f"grid / ellipsoid: {on_grid / on_ellipsoid:.10f}")
print(f"scale factor    : {at_position.scale_factor[0]:.10f}")
```

```{note}
The scale factor relates the grid to the **ellipsoid**, not to the ground. A
distance measured at a height above the ellipsoid must first be reduced to the
ellipsoid. This package does not apply that elevation factor.
```

## Across a zone

The factors change from point to point. The figures below show them across UTM
zone 31N (central meridian 3°E), from the equator to 84°N.

```{image} /figure/grid-convergence-map.svg
:alt: Contour map of the grid convergence over longitudes 0 to 6 degrees east and latitudes 0 to 84 degrees north. It is zero along the central meridian and the equator and reaches about 3 degrees at the zone's edges near the pole, positive in the east and negative in the west.
:align: center
```

The grid convergence is zero along the central meridian (dotted) and along the
equator. It grows with the distance from the central meridian and with
latitude, to about ±3° at the zone's edges near the pole. Mixing up grid north
and true north there puts a position about 50 m sideways for every kilometre
travelled.

```{image} /figure/scale-factor-map.svg
:alt: Contour map of the point scale factor over the same zone. It is 0.9996 along the central meridian and rises towards the zone's edges, most at the equator.
:align: center
```

The scale factor is 0.9996 on the central meridian, where a grid distance is
0.04 % shorter than on the ellipsoid. It grows towards the zone's edges, and
reaches 1 about 180 km either side of the central meridian. It depends almost
only on the distance from the central meridian, so one curve fits every
latitude:

```{image} /figure/scale-factor-profile.svg
:alt: The point scale factor at the equator against the distance from the central meridian. A U-shaped curve rises from 0.9996 at the central meridian through 1 about 180 kilometres either side to about 1.001 at the zone's edges, 334 kilometres out.
:align: center
```

Using the values from one point over a long distance carries their change
along. The grid north local method in
{doc}`/user-guide/welltrajectory/georeferencing` uses the values at the
wellhead for the whole well, which is why it drifts as the reach grows.

## Which part of the CRS is used

- A **compound CRS** is read through its horizontal part.
- A {term}`bound CRS` is read through its base CRS. Its binding changes the
  datum, not the projection.
- A **geographic CRS** has no grid. The convergence is 0, every scale is 1, and
  `projected` is False.
- Geocentric, engineering and vertical CRSs have no projection factors and are
  refused.

```{code-cell} python
compound = projection_factors("EPSG:6172", (600000.0, 7000000.0, 120.0))
geographic = projection_factors("EPSG:4326", (6.0, 60.0))

print(compound.horizontal_crs.name, compound.grid_convergence)
print(geographic.projected, geographic.grid_convergence, geographic.scale_factor)
```

The CRS's units do not change the factors. Longitude and latitude are read in
the geographic CRS's own unit and from its own prime meridian, as PROJ reads
them, so NTF (Paris) in grads from Paris works too.

## Projections that are not conformal

An equal-area projection such as `EPSG:3035` (LAEA Europe) keeps areas and
changes angles. The scales along the parallel and the meridian differ, and the
angular distortion is not zero:

```{code-cell} python
laea = projection_factors("EPSG:3035", (5500000.0, 4500000.0))

print("scale factor      :", laea.scale_factor)
print("meridional scale  :", laea.meridional_scale)
print("areal scale       :", laea.areal_scale)
print("angular distortion:", laea.angular_distortion)
```

There, turning an azimuth by the convergence alone gives the wrong answer.
{attr}`~geodetic_engine.geodesy.ProjectionFactors.conformal` says so, and the
azimuth helpers refuse:

```{code-cell} python
:tags: [raises-exception]

print("conformal:", laea.conformal)
laea.to_grid_azimuth(45.0)
```

Web Mercator (`EPSG:3857`) uses a spherical formula on the WGS 84 ellipsoid,
so it is not conformal either. Its factors are reported on the ellipsoid, and
its azimuths are refused the same way.

## As JSON

{meth}`~geodetic_engine.geodesy.ProjectionFactors.to_json_dict` gives plain
data, one entry per point, with the sign convention written out:

```{code-cell} python
import json

print(json.dumps(factors.to_json_dict(), indent=2))
```

## What is refused

{class}`~geodetic_engine.geodesy.UnsupportedCRSError` for a CRS with no
projected or geographic horizontal part:

```{code-cell} python
:tags: [raises-exception]

projection_factors("EPSG:4978", (3194419.0, 194311.0, 5470000.0))  # geocentric
```

{class}`~geodetic_engine.geodesy.CoordinateOutOfRangeError` for a point out of
range, and its base class
{class}`~geodetic_engine.geodesy.TransformationFailedError` for one that is not
finite or is at a pole:

```{code-cell} python
:tags: [raises-exception]

projection_factors("EPSG:32631", (6.0, 95.0), geographic=True)
```

A `ValueError` for points that are not two or three values each.
