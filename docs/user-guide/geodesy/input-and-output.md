---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Input and output formats

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

Points go in as plain Python or numpy values. There is no coordinate type to
build first. Results come back as
{class}`~geodetic_engine.geodesy.Coordinates`, a tuple of tuples that can be
exported to a list, a numpy array or a pandas DataFrame.

**Values are always `xy` order**: longitude before latitude, easting before
northing, then height, wherever the CRS has such axes to order; a CRS without
an east/north pair keeps its declared order ({doc}`/background/axis-order`).
Units are the CRS's own axis units.

## Ways to pass points

```{code-cell} python
import numpy as np

from geodetic_engine.geodesy import Transformation

tfm = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1612")

# One point, flat
print(tfm.transform((2.5, 63.5)).coordinates)

# A list of points
print(tfm.transform([(2.5, 63.5), (3.0, 64.0)]).coordinates)

# A 2D numpy array of shape (n_points, n_values)
print(tfm.transform(np.array([[2.5, 63.5], [3.0, 64.0]])).coordinates)

# Separate per-axis sequences, as pyproj's Transformer.transform takes them
print(tfm.transform([2.5, 3.0], [63.5, 64.0]).coordinates)
```

A lone scalar `z` is used for every point, so one height can be given once:

```{code-cell} python
tfm.transform([2.5, 3.0], [63.5, 64.0], 100.0).coordinates
```

## From a pandas DataFrame

Pass the columns in `xy` order, as an array or as separate series:

```{code-cell} python
import pandas as pd

wells = pd.DataFrame(
    {"well": ["A-1", "B-2"], "lon": [10.75, 5.32], "lat": [59.91, 60.39]}
)
utm = Transformation("EPSG:4258", "EPSG:25832")

result = utm.transform(wells["lon"], wells["lat"])
wells[["E", "N"]] = result.coordinates.to_numpy()
wells
```

## How many values per point

Each point must have one value per axis of the source CRS. It may have one
more: a height given with a 2D horizontal CRS, which is carried through
unchanged:

```{code-cell} python
utm.transform([(10.75, 59.91, 123.4)]).coordinates
```

Anything else is a `ValueError` naming the expected count:

```{code-cell} python
:tags: [raises-exception]

utm.transform([(10.75,)])
```

Output always has one value per axis of the **target** CRS, plus a carried
height if there was one. A vertical target gives one value per point.

## Working with `Coordinates`

`result.coordinates` behaves like a tuple of tuples: index it, iterate over it,
compare it. Three methods export it:

```{code-cell} python
result = utm.transform([(10.75, 59.91), (5.32, 60.39)])
coords = result.coordinates

print(coords[0])           # first point
print(len(coords))         # number of points
print(coords.to_list())    # list of lists
print(coords.to_numpy())   # float64 array, shape (n_points, n_values)
coords.to_dataframe()      # columns named after the target CRS's axes
```

`to_dataframe()` names its columns with the target CRS's value-order axis
abbreviations, so a geographic target gives `Lon` and `Lat`, not `x` and `y`.

## Round trip

Transform, then transform back with the same operation and swapped CRSs. The
round trip for a Helmert reproduces the input to well under a millimetre:

```{code-cell} python
forward = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1612")
inverse = Transformation("EPSG:4326", "EPSG:4230", operation="EPSG:1612")

start = np.array([[2.5, 63.5, 100.0]])
there = forward.transform(start).coordinates.to_numpy()
back = inverse.transform(there).coordinates.to_numpy()
np.abs(back - start).max()
```
