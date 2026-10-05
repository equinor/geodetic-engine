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
| Seismic bin grids | {doc}`/user-guide/bingrid` | P6 conversion both ways, four-corner squaring with a datum change, matching a dataset to a stored grid |

```{toctree}
:hidden:

notebooks/geodesy_quickstart
notebooks/geodetic-engine-examples
```
