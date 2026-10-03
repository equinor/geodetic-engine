---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Quickstart

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

This page runs during every documentation build, so the output below is what
the code actually produces with PROJ {{ proj_version }}.

## Transform one point

Transform a point in Oslo from ETRS89 geographic coordinates (`EPSG:4258`) to
ETRS89 / UTM zone 32N (`EPSG:25832`). Input is **longitude, latitude** in
degrees. Output is **easting, northing** in metres.

```{code-cell} python
from geodetic_engine.geodesy import transform

result = transform("EPSG:4258", "EPSG:25832", (10.7522, 59.9139))
result.coordinates
```

Both CRSs use the same datum, so this is a map projection and nothing had to be
chosen. Coordinate values are always given in `xy` order: longitude before
latitude, easting before northing, then height. This holds even though EPSG
declares `EPSG:4258` latitude-first. See {doc}`/background/axis-order`.

## Change datum, and say how

A **datum change** can be done more than one way. PROJ's EPSG dataset holds dozens of
transformations from ED50 to WGS 84, and they disagree by metres. This package
will not pick one for you:

```{code-cell} python
:tags: [raises-exception]

transform("EPSG:4230", "EPSG:4326", (2.5, 63.5))
```

Name the operation instead. `EPSG:1612` is ED50 to WGS 84 (23), a
seven-parameter Helmert for Norwegian waters north of 62°N:

```{code-cell} python
result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation="EPSG:1612")
result.coordinates
```

## Read how the answer was produced

Every result records which operation was applied, how it was chosen, its stated
accuracy, and the PROJ pipeline that ran:

```{code-cell} python
op = result.operation
print("operation :", op.authority_code, "-", op.name)
print("method    :", op.method_name)
print("accuracy  :", op.accuracy, "m")
print("route     :", op.route)
print("values    :", result.coordinate_order, result.target_crs.value_axis_abbreviations)
print("declared  :", result.target_axes, result.target_units)
```

The values are `xy` (`Lon`, `Lat`). `target_axes` gives the order EPSG
*declares* for the target CRS, latitude first, so you can see that the two
differ.

```{code-cell} python
print(result.pipeline)
```

The pipeline can be given to `pyproj.Transformer.from_pipeline` to reproduce
the result without this package. `result.to_json()` serialises all of this,
including SHA-256 fingerprints of the `proj.db` that answered. See
{doc}`/user-guide/geodesy/results`.

## Many points, one resolution

To transform many batches, resolve the transformation once and reuse it:

```{code-cell} python
import numpy as np

from geodetic_engine.geodesy import Transformation

tfm = Transformation("EPSG:4258", "EPSG:25832", operation="EPSG:16032")
points = np.array([[10.75, 59.91], [5.32, 60.39], [7.99, 58.15]])  # lon, lat
tfm.transform(points).coordinates.to_dataframe()
```

`tfm.transform` accepts a single point, a list of tuples, a 2D numpy array, or
separate `x, y[, z]` sequences as pyproj does. See
{doc}`/user-guide/geodesy/input-and-output`.

## Where next

- {doc}`concepts`: the vocabulary used in the rest of the documentation.
- {doc}`/user-guide/geodesy/index`: everything the `geodesy` module does.
- {doc}`/examples/index`: longer worked examples.
