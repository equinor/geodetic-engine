# Examples

Every example here is executed on each documentation build against PROJ
{{ proj_version }}, so the output shown is what the code produces. The
notebooks are the files in the repository's
[`examples/`](https://github.com/equinor/geodetic-engine/tree/main/examples)
directory.

## Notebooks

::::{grid} 1 1 2 2
:gutter: 3

:::{grid-item-card} Quickstart
:link: notebooks/geodesy_quickstart
:link-type: doc

A tour of `geodetic_engine.geodesy`: a projection, a named datum shift, a
geoid height, a dynamic frame with a coordinate epoch, axis inspection, batch
and numpy input, provenance, reuse, and bound CRSs including a collapsed chain
and an OSDU payload.
:::

:::{grid-item-card} Worked examples
:link: notebooks/geodetic-engine-examples
:link-type: doc

Longer cases: ED50 to WGS 84, OSDU bound CRS round trips through UTM, chained
operations, Norwegian NN54 heights with and without a datum change, an
engineering CRS, operation discovery by area of use, coordinate export, and
ten OSDU `persistableReference` scenarios.
:::

:::{grid-item-card} Well trajectories
:link: notebooks/welltrajectory_examples
:link-type: doc

Read the synthetic survey file, compute the trajectory, add points between the
stations, convert to WGS 84 and plot it in 3D. Then one short example of each
other way to build the input: arrays, rows, a DataFrame and an OSDU request
body. Last, a real well, Volve F-1, checked against its survey report.
:::
::::

## Examples by topic

The user guide pages are executed too. Each is a set of short, self-contained
examples:

| Topic | Page | Shows |
|---|---|---|
| CRSs | {doc}`/user-guide/geodesy/crs` | Resolving inputs, declared vs value axis order, units, dimension, dynamic frames |
| Conversions | {doc}`/user-guide/geodesy/transformations` | Projection, inverse projection, zone change, geocentric, unit change |
| Datum transformations | {doc}`/user-guide/geodesy/transformations` | Named Helmert, concatenated operations, NADCON grids, geoid heights, compound CRSs, epoch-dependent Helmert, engineering CRS, bound CRS |
| Choosing operations | {doc}`/user-guide/geodesy/choosing-operations` | Listing, filtering, selecting by area of use, unnamed chains, ballpark candidates |
| Input and output | {doc}`/user-guide/geodesy/input-and-output` | Tuples, lists, numpy, per-axis, pandas, carried heights, exports, round trips |
| Provenance | {doc}`/user-guide/geodesy/results` | Applied operation, route, pipeline replay in plain pyproj, database fingerprints, JSON |
| Refusals | {doc}`/user-guide/geodesy/errors` | Triggering every `GeodesyError` subclass, including a simulated missing grid |
| Helmert algebra | {doc}`/user-guide/geodesy/helmert` | Reading, composing and collapsing Helmerts; restating ppb scales |
| OSDU payloads | {doc}`/user-guide/persistable-reference` | All six payload kinds, transforming with payloads, stated operations, units, writing payloads, method support tables |
| Projection factors | {doc}`/user-guide/projection-factors/computing` | One point and many, from longitude and latitude, bound and compound CRSs, units and prime meridians, a non-conformal projection, JSON |
| Projection factors across a zone | {doc}`/user-guide/projection-factors/across-a-zone` | Figures of the grid convergence and the point scale factor over a UTM zone, and of how the angles relate |
| Azimuths and distances | {doc}`/user-guide/projection-factors/azimuths-and-distances` | Grid and true azimuths, the sign checked against transformed points, the scale factor checked against a geodesic |
| Building the input | {doc}`/user-guide/welltrajectory/input` | A trajectory input from arrays, rows, a DataFrame, a survey file and an OSDU request body; the survey file format; checks; changing and saving an input |
| Surveys | {doc}`/user-guide/welltrajectory/surveys` | Stations, units in every accepted form, inclination-only surveys, the wellhead, malformed surveys |
| Trajectories | {doc}`/user-guide/welltrajectory/trajectories` | Results and their units, dogleg severity, interpolation, grid azimuths, a geographic CRS, moving to WGS 84 |
| Minimum curvature | {doc}`/user-guide/welltrajectory/minimum-curvature` | The method on its own, points on the arcs, resampling that reproduces the survey |
| Georeferencing methods | {doc}`/user-guide/welltrajectory/georeferencing` | The four methods side by side, and their geometric differences at depth |
| Plotting | {doc}`/user-guide/welltrajectory/plotting` | One well, several wells, colouring and shadows |

```{toctree}
:hidden:

notebooks/geodesy_quickstart
notebooks/geodetic-engine-examples
notebooks/welltrajectory_examples
```
