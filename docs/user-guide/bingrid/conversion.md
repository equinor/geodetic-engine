---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
language_info:
  name: python
---

# Converting seismic bin grids

A bin grid can be described by four corners, or by an origin, spacing and
rotation. The first example checks and squares a four-corner grid; the second
transforms every node of an already defined grid to WGS 84.

## Checking, squaring and converting a four-corner grid

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

## Seismic bin grid to WGS84

A project grid can be described by an origin, column and row spacings,
dimensions, a rotation and a projected coordinate reference system (CRS).
Convert its grid indices to map coordinates with P6 parameters, then transform
those coordinates to WGS 84 longitude and latitude.

This example uses an early-bound OSDU CRS for a grid in the North Sea. The CRS
contains its transformation to WGS 84, so the caller does not need to pass a
separate transformation code.

```{note}
The official OSDU catalogue ships with the engine, but an OSDU CRS record ID
needs a `proj.db` built from that catalogue and active on PROJ's data path. See
{doc}`/user-guide/osdudb` for building and activating it. The standard PROJ
database alone does not contain the OSDU record IDs.
```

### Define the project grid

The origin and spacings below are in metres in an early-bound ED50 / UTM zone
31N CRS. Its OSDU record ID combines the projected CRS `EPSG:23031` and the
bound transformation `EPSG:1613`. The rotation is in degrees anticlockwise
from the map's easting axis to the direction of increasing columns.

```{code-cell} python
import numpy as np

from geodetic_engine.bingrid import Handedness, P6Parameters
from geodetic_engine.geodesy import CoordinateReferenceSystem, Transformation

project = {
  "crs": "BoundProjected:EPSG::23031_EPSG::1613",
  "origin_easting": 470000.0,
  "origin_northing": 6680000.0,
  "column_spacing": 2500.0,
  "row_spacing": 2000.0,
  "ncol": 21,
  "nrow": 17,
  "rotation": 30.0,
}
```

### Position the grid nodes

Columns are inline indices and rows are crossline indices, both starting at
zero. This grid is right-handed: its row axis is 90 degrees anticlockwise
from its column axis. P6 measures the J-axis bearing clockwise from north, so
the bearing is the negative of the project grid's rotation.

The spacings are distances on the map grid. Setting the P6 scale factor to 1
makes the P6 bin widths those same map distances; it does not assert that the
projection's physical scale factor is 1. Adjust the handedness and bearing if
the project's grid uses different axis conventions.

```{code-cell} python
grid = P6Parameters(
  origin_i=0,
  origin_j=0,
  origin_easting=project["origin_easting"],
  origin_northing=project["origin_northing"],
  bin_width_i=project["column_spacing"],
  bin_width_j=project["row_spacing"],
  bearing_j=-project["rotation"],
  handedness=Handedness.RIGHT,
  scale_factor=1.0,
)
columns, rows = np.meshgrid(np.arange(project["ncol"]), np.arange(project["nrow"]))
nodes = grid.to_map(columns.ravel(), rows.ravel()).reshape(
  project["nrow"], project["ncol"], 2
)
print("Origin (easting, northing):", nodes[0, 0])
print("Next column:", nodes[0, 1])
print("Next row:", nodes[1, 0])
```

The resulting array has shape `(nrow, ncol, 2)`: the first index is the row,
the second the column, and the final pair is easting and northing.

### Transform to WGS 84

Resolve the project CRS by its record ID from the OSDU database. The ID must
exist in the catalogue; the engine does not construct a CRS from its spelling.
Its bound definition contains the transformation to WGS 84, which the engine
reads and applies without a separate `operation` argument:

```{code-cell} python
source_crs = CoordinateReferenceSystem.from_osdu_id(project["crs"])
print("Source:", source_crs.name)
print("Early-bound:", source_crs.crs.is_bound)

to_wgs84 = Transformation(source_crs, "EPSG:4326")
node_result = to_wgs84.transform(nodes[..., 0].ravel(), nodes[..., 1].ravel())
lonlat = node_result.coordinates.to_numpy().reshape(nodes.shape)
print("Operation:", node_result.operation.authority_code, node_result.operation.name)
print("Origin (longitude, latitude):", lonlat[0, 0])
```

`lonlat[row, column]` is the node's longitude and latitude in degrees. The
result reports the operation the CRS supplied; for this CRS it is EPSG:1613,
ED50 to WGS 84 (24). The engine uses the operation already bound to the CRS,
not an automatically guessed datum transformation. A late-bound CRS on a
different datum requires an explicitly named operation; see
{doc}`/background/bound-crs`.

An OSDU `persistableReference` payload can also be passed directly to
`Transformation`, or resolved with `CoordinateReferenceSystem.from_user_input`;
it carries its own definition and does not require the OSDU database. See
{doc}`/persistable-reference/usage`. For large grids, reuse `to_wgs84` and
transform the flattened nodes in batches, preserving their order before
reshaping the result.