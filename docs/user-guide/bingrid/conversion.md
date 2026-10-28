---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
language_info:
  name: python
---

# Checking, squaring and converting a four-corner grid

{func}`~geodetic_engine.bingrid.convert_bin_grid` is the computation behind
the OSDU service's `POST v3/convertBinGrid`. It checks four corners,
optionally converts them to another CRS, fits the SDU rectangle through them
there, reports how far the corners were from it, and gives the squared corners
in WGS 84:

```{code-cell} python
from geodetic_engine.bingrid import convert_bin_grid

corners = [  # (inline, crossline, easting, northing), in any order
    (1, 1000, 500000.0, 3000000.0),
    (1, 2000, 500000.0, 3100000.0),
    (101, 1000, 600000.0, 3000000.0),
    (101, 2000, 600000.0, 3100000.0),
]
result = convert_bin_grid(
    corners,
    "EPSG:32615",                  # WGS 84 / UTM zone 15N
    target_crs="EPSG:32064",       # NAD27 / BLM 14N (ftUS)
    operation="EPSG:15851",        # NAD27 to WGS 84 (79): the datum change, named
    wgs84_operation="EPSG:15851",
)
result.max_mislocation.dj_bins     # the corners are bent off a rectangle
```

```{code-cell} python
result.parameters                  # P6 parameters, anchored at squared corner A
```

```{code-cell} python
result.squared_corners.coordinates     # corners of the fitted rectangle, in ftUS
```

```{code-cell} python
result.converted_corners.coordinates   # the input corners converted, before squaring
```

```{code-cell} python
result.wgs84_corners               # ((lon, lat), ...) of the squared corners
```

```{code-cell} python
result.outline.labels              # counterclockwise
```

```{code-cell} python
result.applied_operations()        # what was done, step by step, with EPSG codes
```

The conversion goes through {class}`~geodetic_engine.geodesy.Transformation`,
so it follows the same rules: a datum change needs a named operation or a
bound CRS, ballpark results and missing grids are refused, an operation that
reads the coordinate epoch needs `coordinate_epoch`, and both conversions are
kept on the result as {class}`~geodetic_engine.geodesy.TransformationResult`
objects. A target CRS on the grid's own map grid -- the same datum, projection
and linear unit, whatever its axis order and whether or not it is bound to WGS
84 -- means no conversion; a bound target then names the operation the WGS 84
step applies, which is how an OSDU `BoundProjected` reference of the grid's CRS
is meant to be used. An EPSG CRS that declares northing first and its ESRI WKT,
which declares easting first, are one map grid: values are in `xy` order
whichever is given.

`result.wgs84_outline` visits the corners in the order of `result.outline`,
each edge the short way round in longitude. For a grid across the antimeridian
its longitudes continue past 180° (-179.9 becomes 180.1), so that the ring goes
round the grid and not round the rest of the world; GeoJSON (RFC 7946) asks for
such a polygon to be cut in two at the antimeridian. A ring round a pole closes
along the pole's latitude, through two points labelled `pole`.

Everything that cannot be a bin grid is refused with a specific error, all
subclasses of {class}`~geodetic_engine.bingrid.BinGridError`:

| Error | Raised when |
| --- | --- |
| {class}`~geodetic_engine.bingrid.InvalidCornersError` | There are not four corners, their numbers are not the four combinations of two inlines and two crosslines, or they are not whole node increments apart. |
| {class}`~geodetic_engine.bingrid.DegenerateBinGridError` | Coordinates are not finite, corners coincide, or the outline A-B-D-C is not convex (swapped or collinear corners). |
| {class}`~geodetic_engine.bingrid.InvalidParameterError` | A P6 parameter is out of range, for example a scale factor that is not positive. |
| {class}`~geodetic_engine.bingrid.UnsupportedCRSError` | A CRS is not a 2D projected CRS with easting and northing axes in one linear unit, or k is to be derived from a projection that is not conformal at the grid. It is a {class}`geodetic_engine.geodesy.UnsupportedCRSError` too. |

{func}`~geodetic_engine.bingrid.derive_p6` and
{func}`~geodetic_engine.bingrid.square_up` expose the fit itself. They take a
{class}`~geodetic_engine.bingrid.BinGridCorners`, which
{meth}`~geodetic_engine.bingrid.BinGridCorners.from_corners` labels from
corners given in any order.