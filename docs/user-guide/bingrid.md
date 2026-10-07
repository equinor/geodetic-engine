---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Seismic bin grids

A 3D seismic survey numbers its traces by inline and crossline (I, J). A bin
grid maps those numbers to easting and northing in a projected CRS.
`geodetic_engine.bingrid` does that arithmetic, and the checks around it,
following:

- EPSG Guidance Note 7-2, methods 9666 (right-handed, "P6 I=J+90") and 1049
  (left-handed, "P6 I=J-90"), as written in IOGP P6/11 files;
- the OSDU four-corner definition, in which corners A, B, C and D sit at
  (min I, min J), (min I, max J), (max I, min J) and (max I, max J);
- the SDU note *Geometric aspects of bin grids*, for deriving P6 parameters
  from four corners and measuring how far the corners are from a rectangle.

The package works with plain Python values and has no OSDU JSON in it. Values
are in `xy` order (easting, then northing; longitude, then latitude), as in the
rest of this library. Nothing in it is rounded, and every result has a
`to_json_dict()`.

## Bin grid and map grid coordinates

{class}`~geodetic_engine.bingrid.P6Parameters` holds a grid as P6/11 states
it, and converts in both directions with PROJ's implementation of the two EPSG
methods (PROJ 9.9 or later). It accepts single points or arrays, the same
shapes that {func}`~geodetic_engine.geodesy.transform` accepts. EPSG's own
9666 example:

```{code-cell} python
from geodetic_engine.bingrid import Handedness, P6Parameters, corners_from_p6

grid = P6Parameters(
    origin_i=1, origin_j=1,
    origin_easting=456781.0, origin_northing=5836723.0,
    bin_width_i=25.0, bin_width_j=12.5,
    bearing_j=20.0,                    # map grid bearing of the J (crossline) axis
    handedness=Handedness.RIGHT,       # EPSG method 9666
    scale_factor=0.99984,
)
grid.to_map(300, 247)
```

```{code-cell} python
grid.to_bin(464855.62, 5837055.90)
```

```{code-cell} python
corners = corners_from_p6(grid, inline_range=(1, 301), crossline_range=(1, 401))
corners.coordinates                    # corners A, B, C, D
```

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

## Scale factor and node increments

P6 bin widths are *ground* distances. EPSG's formulas multiply them by the bin
grid scale factor k to get map grid distances. So a grid squared from map grid
corners has bin width = increment × map grid distance / (node span × k). This
makes deriving, applying and inverting the parameters round-trip exactly for
any k, and k moves no position: only k × bin width enters the formulas.

`convert_bin_grid` derives k as EPSG defines it: the point scale factor of the
CRS the grid is squared in, taken at the centre of the grid, from PROJ. The bin
widths are then the ground spacing of the bins at the centre, the same in any
CRS. Where the projection's scale varies across the grid, the spacing elsewhere
in the grid differs from it: averaged over the grid, by about 1e-5 (1 cm per
km) for a 100 km grid and 4e-7 for a 20 km one. A projection that is not
conformal, such as Cassini-Soldner or an equal-area one, has a scale that
depends on direction, and no single k makes both bin widths ground distances.
Where the scale at the grid centre differs by more than 1e-6 between
directions, `convert_bin_grid` raises `UnsupportedCRSError` rather than derive
k. Pass `scale_factor` to state k yourself; `square_up` and `derive_p6` take k
as given, 1.0 by default.

Node increments are how the inline and crossline numbers step between adjacent
nodes: a grid numbered 1, 5, 9, ... in crossline has `increment_j=4`, and its
crossline bin width is the distance between those nodes. Corners that are not
whole increments apart cannot all be nodes, and are refused with
`InvalidCornersError`. Mis-location is reported in inline and crossline
numbers (`di`, `dj`), in bins (`di_bins`, `dj_bins`), and as a map grid
distance.

## Matching a legacy dataset to a stored grid

The SDU note describes how to assign a legacy dataset, which arrives with only
its own corners, to a bin grid that is already stored, instead of storing yet
another definition. {mod}`geodetic_engine.bingrid.matching` implements it. It
is an optional module, not part of the `bingrid` API:

```{code-cell} python
from geodetic_engine.bingrid.matching import StoredBinGrid, match_bin_grid
from geodetic_engine.geodesy import transform

# The SDU note's Table 2 grid, stored in NAD27 / UTM zone 15N (metres).
table_2 = P6Parameters(
    origin_i=14100, origin_j=5161,
    origin_easting=423081.91, origin_northing=3227689.59,
    bin_width_i=30.0, bin_width_j=25.0, bearing_j=2.48019694,
    handedness=Handedness.RIGHT, increment_j=4,
)
store = [StoredBinGrid("table-2", table_2, "EPSG:26715")]

# A dataset loaded from it, delivered in NAD27 / BLM 15N (ftUS).
metres = corners_from_p6(
    table_2, inline_range=(14200, 15000), crossline_range=(5201, 7201)
)
dataset_corners = metres.with_coordinates(
    transform("EPSG:26715", "EPSG:32065", metres.coordinates).coordinates
)

result = match_bin_grid(dataset_corners, "EPSG:32065", store, increment_j=4)
result.best.grid.key   # result.best: the BinGridMatch to assign, or None to define a new grid
```

A stored grid matches if it puts every dataset corner within half of the
smaller real spacing between the dataset's loaded traces. When several match,
the order of preference is: a grid on the dataset's own map grid (the same
datum, projection and linear unit, whatever the axis order and whether or not
bound to WGS 84), then a grid stored at the dataset's increments, then the
smallest distance.

The note makes one exception to comparing in the dataset's own CRS: NAD27 data
in US survey feet against grids stored in metres. This module allows the
general form of it, a stored grid in any projected CRS on the dataset's datum,
compared after an exact conversion. It never compares across a datum change,
because that needs a transformation, and a match must not choose one.

(bingrid-java-service)=

## Where this differs from the OSDU Java service

The OSDU crs-conversion-service's acceptance test grid, without and with a
`toCRS`, is this package's regression case (`tests/bingrid/test_conversion.py`).
The with-toCRS case reproduces the Java service's Apache SIS results: the P6
origin to within 1e-5 ftUS, and the corners to the 3 decimals the Java service
writes.

The Java implementation's defects in the computation are not reproduced here;
those in its JSON handling are outside this package. Each defect has a test
that states the correct behaviour and is marked `java_defect` (`uv run pytest
-m java_defect`). `tests/bingrid/data/java_defects.json` records where each
defect is in the Java source.

| | Java service | Here |
| --- | --- | --- |
| D2 | Bin widths ignore k, but the formulas apply it | Exact round trips for any k |
| D3, D4 | Corner layout and geometry unchecked: division by zero, NaN | `InvalidCornersError`, `DegenerateBinGridError` |
| D5 | Grids in geographic CRSs accepted | `UnsupportedCRSError` |
| D13 | No outline; the documented ring is clockwise | Counterclockwise outline, in the map CRS and in WGS 84 |
| D14 | Request mutated; input corners lost | Input, converted and squared corners kept |
| D15 | Converts even when toCRS is the source CRS | No conversion |
| D16 | Corners rounded, P6 origin not | Nothing rounded |
| D18 | The request's k is written back, even into another CRS | k derived for the output CRS |

## Sources

The package is an independent implementation. PROJ runs the P6 conversion as
its affine operation, with the coefficients it documents for EPSG methods 9666
and 1049, and supplies the bin grid scale factor; the squaring follows the SDU
note. Third-party material is used in the test suite only, as references:

- the OSDU crs-conversion-service's test cases and expected outcomes, and a
  port of its Java squaring to compare with (Apache License 2.0);
- the SDU note *Geometric aspects of bin grids* and its workbook, by Bert
  Kampes (Shell), as published in that service's repository;
- the worked examples of EPSG Guidance Note 7-2 (IOGP).

Each data file in `tests/bingrid/data` names its source, and the repository's
`NOTICE` file lists them.
