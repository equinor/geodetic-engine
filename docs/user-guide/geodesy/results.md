---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Results and provenance

Every call returns a {class}`~geodetic_engine.geodesy.TransformationResult`
containing the coordinates and a record of how they were produced. The record
is enough to reproduce or audit the result later.

```{code-cell} python
from geodetic_engine.geodesy import transform

result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation="EPSG:1612")
```

## What the result records

```{code-cell} python
print("coordinates     :", result.coordinates)
print("count           :", result.count)
print("coordinate order:", result.coordinate_order)          # always "xy"
print("source          :", result.source_crs, result.source_axes, result.source_units)
print("target          :", result.target_crs, result.target_axes, result.target_units)
print("epoch           :", result.coordinate_epoch)
print("grids           :", result.grids, "missing:", result.missing_grids)
```

`coordinate_order` is always `"xy"`, the order of the values. `source_axes`,
`target_axes` and the unit tuples are in EPSG's *declared* order, which for a
geographic CRS is latitude first. They are reported side by side so the two
cannot be confused. The value order of each axis is
`result.target_crs.value_axis_abbreviations`.

## The applied operation

{attr}`~geodetic_engine.geodesy.TransformationResult.operation` is an
{class}`~geodetic_engine.geodesy.AppliedOperation`:

```{code-cell} python
op = result.operation
print("requested     :", op.requested)
print("applied       :", op.authority_code, "-", op.name)
print("method        :", op.method_name)
print("accuracy      :", op.accuracy, "m")
print("route         :", op.route)
print("steps         :", op.steps)
print("ballpark      :", op.ballpark)
print("requires epoch:", op.requires_epoch)
print("direction     :", op.execution_direction)
```

`route` records how the transformer was obtained
({class}`~geodetic_engine.geodesy.OperationRoute`):

| Route | Meaning |
|---|---|
| `transformer_group` | Your named operation, found among the candidates PROJ offers for the pair |
| `chained` | Your named or stated operation, wrapped in same-datum conversions to fit the pair |
| `bound` | Taken from a bound CRS's own definition |
| `proj_default` | Nothing named, no datum change; PROJ's conversion, recorded |
| `any_operation` | Legacy value in old serialised results; never produced now |

## Replaying the pipeline

{attr}`~geodetic_engine.geodesy.TransformationResult.pipeline` is the exact
PROJ pipeline that ran, including axis swaps, unit conversions and inversions.
Plain pyproj can replay it without this package:

```{code-cell} python
print(result.pipeline)
```

```{code-cell} python
from pyproj import Transformer

Transformer.from_pipeline(result.pipeline).transform(2.5, 63.5)
```

Use the pipeline, not the operation's WKT, to reproduce a result.
{meth}`~geodetic_engine.geodesy.AppliedOperation.to_wkt` gives the WKT2 of the
applied operation, but returns None when WKT2 cannot state it faithfully, for
example when a step was applied inverted:

```{code-cell} python
print(result.operation.to_wkt()[:300], "...")
```

## Which database answered

The same EPSG code can mean different parameters in different `proj.db`
files. Every result stores SHA-256 fingerprints of the databases PROJ was
reading:

```{code-cell} python
result.database_fingerprints
```

A result produced from a {doc}`custom database </user-guide/projdb>` can
therefore be traced back to that database, and through its build report to
the register entries it was built from ({doc}`/background/provenance`).

## Serialising

{meth}`~geodetic_engine.geodesy.TransformationResult.to_json` gives a JSON
string and
{meth}`~geodetic_engine.geodesy.TransformationResult.to_json_dict` gives the
same as a dict. Both include the full WKT of both CRSs, so the record does not
depend on a database being present when it is read:

```{code-cell} python
import json

record = result.to_json_dict()
print(sorted(record))
print(json.dumps(record["operation"], indent=2)[:800])
```
