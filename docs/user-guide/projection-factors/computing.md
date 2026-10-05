---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Computing projection factors

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

{func}`~geodetic_engine.geodesy.projection_factors` takes a CRS and one or more
points and returns a {class}`~geodetic_engine.geodesy.ProjectionFactors`: the
factors at each point, the points themselves, and the CRSs the factors
describe.

## At one point

Give the CRS, and a point in its own values in `xy` order:

```{code-cell} python
from geodetic_engine.geodesy import projection_factors

factors = projection_factors("EPSG:32631", (600000.0, 6700000.0))

print("grid convergence  :", factors.grid_convergence)
print("scale factor      :", factors.scale_factor)
print("meridional scale  :", factors.meridional_scale)
print("areal scale       :", factors.areal_scale)
print("angular distortion:", factors.angular_distortion)
```

Every factor is an array with one entry per point, even for a single point.
This point is 100 km east of the central meridian of UTM zone 31N, so grid
north lies east of true north and the convergence is positive. Transverse
Mercator is conformal: the scale is the same along the parallel and the
meridian, and the angular distortion is zero.

The result also states where the point is, and which CRSs the factors
describe:

```{code-cell} python
print("crs           :", factors.crs.name)
print("horizontal_crs:", factors.horizontal_crs.name)
print("geographic_crs:", factors.geographic_crs.name)
print("projected     :", factors.projected)
print("coordinates   :", factors.coordinates)
print("longitude     :", factors.longitude)
print("latitude      :", factors.latitude)
```

`horizontal_crs` is the part of `crs` that carries the projection, and
`geographic_crs` its own geodetic CRS, which `longitude` and `latitude` are
expressed in.

## At many points

Give one row per point, as a sequence of tuples or a numpy array of shape
`(n, 2)` or `(n, 3)`:

```{code-cell} python
import numpy as np
import pandas as pd

eastings = np.arange(300000.0, 700001.0, 100000.0)
points = np.column_stack([eastings, np.full(len(eastings), 6650000.0)])
across = projection_factors("EPSG:32631", points)

pd.DataFrame(
    {
        "easting": eastings,
        "longitude": across.longitude,
        "grid_convergence": across.grid_convergence,
        "scale_factor": across.scale_factor,
    }
).round(6)
```

Across the zone the convergence changes sign at the central meridian, and the
scale factor is smallest there, at the projection's $k_0 = 0.9996$, and grows
towards the edges. {doc}`across-a-zone` draws both over the whole zone.

## From longitude and latitude

With `geographic=True` the points are longitude and latitude in the CRS's own
geodetic CRS, in its own unit and from its own prime meridian:

```{code-cell} python
at_position = projection_factors("EPSG:32631", (6.0, 60.0), geographic=True)

print("coordinates     :", at_position.coordinates)
print("grid convergence:", at_position.grid_convergence)
print("scale factor    :", at_position.scale_factor)
```

`coordinates` holds the points as they were given. `longitude` and `latitude`
hold the position in either case.

## Which part of the CRS is described

A compound CRS is read through its horizontal part. The height in the point is
ignored:

```{code-cell} python
compound = projection_factors("EPSG:6172", (600000.0, 7000000.0, 120.0))

print(compound.crs.name)
print(compound.horizontal_crs.name, compound.grid_convergence)
```

A {term}`bound CRS` is read through its base CRS, since its binding
transformation changes the datum, not the projection:

```{code-cell} python
from pyproj import CRS
from pyproj.crs import BoundCRS, CoordinateOperation

bound = BoundCRS(
    CRS("EPSG:23032"), CRS("EPSG:4326"), CoordinateOperation.from_epsg(1133)
)
through_base = projection_factors(bound, (600000.0, 6643000.0))
on_base = projection_factors("EPSG:23032", (600000.0, 6643000.0))

print(through_base.horizontal_crs.name)
print(through_base.grid_convergence, on_base.grid_convergence)
```

The CRS can be anything
{meth}`CoordinateReferenceSystem.from_user_input <geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input>`
accepts, including an OSDU `persistableReference`; see
{doc}`/user-guide/persistable-reference`.

## A geographic CRS

A geographic CRS has no grid, so there is nothing to distort. The convergence
is zero, every scale is one, and `projected` says so:

```{code-cell} python
no_grid = projection_factors("EPSG:4326", [(6.0, 60.0), (7.0, 61.0)])

print("projected       :", no_grid.projected)
print("grid convergence:", no_grid.grid_convergence)
print("scale factor    :", no_grid.scale_factor)
```

## Units and prime meridians

The factors are ratios and angles, so the CRS's unit does not change them.
NAD83 / California zone 1 in US survey feet and in metres, at the same place:

```{code-cell} python
in_feet = projection_factors("EPSG:2225", (6561666.667, 1640416.667))
in_metres = projection_factors(
    "EPSG:26941",
    (in_feet.longitude[0], in_feet.latitude[0]),
    geographic=True,
)

print(in_feet.scale_factor, in_metres.scale_factor)
print(in_feet.grid_convergence, in_metres.grid_convergence)
```

NTF (Paris) / Lambert zone II counts longitude in grads from the Paris
meridian. `longitude` and `latitude` come back in that unit and from that
meridian, which is also how PROJ reads them:

```{code-cell} python
paris = projection_factors("EPSG:27572", (700000.0, 2200000.0))

print(paris.geographic_crs.name, paris.geographic_crs.axis_units)
print("longitude, latitude:", paris.longitude, paris.latitude)
print("grid convergence   :", paris.grid_convergence)
```

The grid convergence and the angular distortion are in degrees whatever the
CRS's unit.

## A projection that is not conformal

Away from its origin, the Lambert azimuthal equal-area projection of
`EPSG:3035` (ETRS89-extended / LAEA Europe) keeps areas and gives up angles.
The scale along the parallel and along the meridian differ, the areal scale is
one, and the angular distortion is not zero:

```{code-cell} python
laea = projection_factors("EPSG:3035", (5500000.0, 4500000.0))

pd.DataFrame(
    {
        name: getattr(laea, name)
        for name in (
            "longitude",
            "latitude",
            "scale_factor",
            "meridional_scale",
            "areal_scale",
            "angular_distortion",
        )
    }
)
```

## As JSON

{meth}`~geodetic_engine.geodesy.ProjectionFactors.to_json_dict` gives plain
data, one entry per point, with the sign convention written out so the numbers
can be read without this page:

```{code-cell} python
import json

print(json.dumps(factors.to_json_dict(), indent=2))
```

## What is refused

A CRS with no horizontal position has no projection factors:

```{code-cell} python
:tags: [raises-exception]

projection_factors("EPSG:4978", (3194419.0, 194311.0, 5470000.0))  # geocentric
```

Each point must be two or three values:

```{code-cell} python
:tags: [raises-exception]

projection_factors("EPSG:32631", [1.0, 2.0, 3.0, 4.0])
```

A point PROJ cannot evaluate raises
{class}`~geodetic_engine.geodesy.TransformationFailedError`:

```{code-cell} python
:tags: [raises-exception]

projection_factors("EPSG:32631", (6.0, 95.0), geographic=True)
```
