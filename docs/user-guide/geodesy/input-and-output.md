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

## Numeric buffers and streaming batches

For a service or a large dataset, reuse one `Transformation` and call
`transform_arrays()` to avoid creating Python objects for every output point:

```{code-cell} python
numeric = utm.transform_arrays(
    np.array([10.75, 5.32]),
    np.array([59.91, 60.39]),
)
eastings, northings = numeric.columns
print(numeric.count)
print(eastings, northings)
```

The result is an {class}`~geodetic_engine.geodesy.ArrayTransformationResult`.
Its `columns` are contiguous, writable `float64` arrays, one per output axis
in coordinate value order. It carries the same provenance as the ordinary
result. Separate axes can be one-dimensional arrays or scalars, with scalars
broadcast across the batch. A row array of shape `(n_points, n_values)` or a
single flat point also works. Unlike `transform()`, this numeric interface
does not accept generators of individual points.

**Ownership:** inputs are never modified, even when a transformation fails.
Each axis is copied into a private working buffer once and PROJ executes
in place through the pipeline. Non-contiguous, read-only and other numeric
dtypes are accepted and converted at this boundary. Result buffers remain
valid after subsequent calls; there is no reusable scratch buffer that
silently overwrites earlier results. The result's attributes are frozen,
but callers may modify its arrays. Complex and masked arrays are rejected
rather than silently discarding imaginary components or masks.

Use `numeric.to_numpy()` only when a row-oriented array is needed: it allocates
and combines the columns. `numeric.to_result()` explicitly materialises the
ordinary tuple-based result for its JSON or DataFrame exporters.
`numeric.metadata_json_dict()` exports provenance without touching coordinate
buffers. A streaming adapter can encode this once for batches with the same
CRSs, operation and epoch, rather than repeating WKT and operation definitions.

`transform_batches()` accepts a lazy iterable of numeric **row batches**:

```{code-cell} python
def incoming_batches():
    yield np.array([[10.75, 59.91], [5.32, 60.39]])
    yield np.array([[7.99, 58.15]])

for batch in utm.transform_batches(incoming_batches()):
    print(batch.count, batch.columns[0])
```

There is no prefetch, queue, retained result history or network dependency.
Only advancing the iterator reads the next input batch. The producer controls
batch size and, for a live source, how long to wait before flushing a partial
batch. Memory is proportional to the current batch, not total stream length,
unless the caller retains the results. Avoid `list(...)` around a long stream.
Closing the iterator stops further reads; the adapter remains responsible for
closing its upstream connection.

All existing operation, grid, epoch, axis-order and finite-result checks still
apply. Range and non-finite-result errors identify the point by its index in
the whole stream; PROJ execution errors retain their existing error messages.
An error stops iteration, but earlier results have already been yielded:
an HTTP adapter must signal incomplete streams explicitly.

The engine remains synchronous. A future asynchronous API should incrementally
decode and encode bounded batches, schedule computation outside its event
loop, propagate cancellation and apply backpressure. Do not collect a whole
request into memory or schedule an unbounded number of transformation tasks.

### Reproducible engine benchmark

**The array API was approximately 5 times faster than the tuple-output API
in a local benchmark of 100,000 projection points.** The measured speedup
was 4.6 times for separate input axes. This compares `transform_arrays()`
with the current, optimised `transform()` API, not with an older engine
release or an HTTP endpoint.

The warmed WGS 84 to UTM zone 32N benchmark (`EPSG:4326` to `EPSG:32632`)
produced these median times over seven repetitions:

| Path | Median time for 100,000 points |
|---|---:|
| Direct pyproj, NumPy axes | 18.6 ms |
| `transform_arrays()`, NumPy axes | 18.9 ms |
| `transform_arrays()`, NumPy rows | 20.3 ms |
| `transform()`, NumPy rows and tuple output | 86.7 ms |

Measured with Python 3.13.16, NumPy 2.5.2, pyproj 3.8.0 and PROJ 9.9.0.
The array paths return numeric columns without materialising Python output
tuples; converting those results to tuples adds work. Actual speedups depend
on hardware, batch size, operation and input/output layout. These local
measurements are illustrative, not a universal 5-times speed guarantee.

From the repository root:

```bash
uv run python -m scripts.benchmark_transformations --memory
uv run python -m scripts.benchmark_transformations --case chained --memory
uv run python -m scripts.benchmark_transformations --case vertical --memory
```

Cases include projection, Helmert, chained, vertical-grid and dynamic operations.
Grid cases require the operation's grids to be installed. The harness checks
coordinates against the reported pipeline replayed directly through pyproj,
then reports warmed median/p95 timings and points/s. Resolution and input
creation are outside the timed region. Allocation tracing runs separately;
its peak is not total process RSS or all native allocations. Compare the
10-batch and 100-batch stream measurements at fixed batch size to check that
allocation peaks do not grow with stream length. These are engine measurements,
not HTTP throughput or latency guarantees.

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
