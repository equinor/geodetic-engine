---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Finding operations

{func}`~geodetic_engine.catalog.operations` lists the coordinate operations
the active database holds: conversions such as map projections,
transformations between datums, and the chains an authority publishes as
concatenated operations.

## Transformations between two CRSs

`source_crs` and `target_crs` find the operations registered between two
CRSs. Each entry states its method, accuracy, version and area of use:

```{code-cell} python
from geodetic_engine import catalog

ed50_to_wgs84 = catalog.operations(source_crs="EPSG:4230", target_crs="EPSG:4326")
print(len(ed50_to_wgs84), "transformations from ED50 to WGS 84")
ed50_to_wgs84.to_dataframe()[
    ["authority_code", "name", "method", "accuracy", "version", "area_of_use"]
].head(10)
```

`max_accuracy` keeps only those stating an accuracy, in metres, no worse
than the bound. One that states none is left out, since a bound is a promise:

```{code-cell} python
for entry in catalog.operations(
    source_crs="EPSG:4230", target_crs="EPSG:4326",
    max_accuracy=1.0, area_of_interest=(2.0, 60.0, 6.0, 64.0),
):
    print(f"{entry.authority_code:10} {entry.accuracy} m  {entry.version}  {entry.area_of_use}")
```

PROJ applies a transformation in either direction, but registers it in one.
`either_direction=True` finds it whichever way round you ask:

```{code-cell} python
backwards = catalog.operations(source_crs="EPSG:4326", target_crs="EPSG:4230")
either = catalog.operations(
    source_crs="EPSG:4326", target_crs="EPSG:4230", either_direction=True
)
len(backwards), len(either)
```

The CRSs can be given as anything
{class}`~geodetic_engine.geodesy.CoordinateReferenceSystem` resolves: a code,
a name, an entry, a pyproj CRS, a WKT string or an OSDU record id.

## Registered operations, or what PROJ would run

The catalog lists what the registry publishes. EPSG registers ED50 to WGS 84
transformations between the geographic CRSs, not between the projected CRSs
built on them. Asked for those, the catalog finds nothing registered, while
{func}`~geodetic_engine.geodesy.available_operations` shows the chains PROJ
would assemble, each projection included:

```{code-cell} python
from geodetic_engine.geodesy import available_operations

registered = catalog.operations(source_crs="EPSG:23031", target_crs="EPSG:32631")
assembled = available_operations("EPSG:23031", "EPSG:32631")
print("registered:", len(registered), " assembled by PROJ:", len(assembled))
for candidate in assembled[:3]:
    print(" ", candidate.references, candidate.accuracy)
```

| | {func}`catalog.operations <geodetic_engine.catalog.operations>` | {func}`geodesy.available_operations <geodetic_engine.geodesy.available_operations>` |
|---|---|---|
| Answers | What does the registry publish? | What would PROJ run between these two CRSs? |
| Between | The CRSs it was registered for | Any two CRSs |
| Chains | Only those published as concatenated operations | Also those PROJ assembles |
| Deprecated operations | On request | Never |
| Ballpark operations | Never | Listed, flagged |
| Without a CRS pair | Yes: by method, area, accuracy, grid, ... | No |

Either way, an entry's authority code is a valid `operation=`:

```{code-cell} python
from geodetic_engine.geodesy import transform

chosen = catalog.operations(code="EPSG:1612").one()
transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation=chosen.authority_code).coordinates
```

## By method, kind and grid

`method` takes a method's code, name or entry:

```{code-cell} python
ntv2_in_europe = catalog.operations(
    method="NTv2", area_of_interest=(-10, 35, 30, 72)
)
[str(entry) for entry in ntv2_in_europe][:8]
```

`kind` picks conversions, transformations, concatenated operations or point
motion operations. A conversion is registered without source and target; a
concatenated operation lists its steps:

```{code-cell} python
utm = catalog.operations(code="EPSG:16032").one()
print(utm.kind, utm.method, utm.source_crs)
chain = catalog.operations(code="EPSG:8047").one()
print(chain.kind, [str(step) for step in chain.steps])
```

`grid_based` keeps the operations that read grid files, and `grids_available`
asks PROJ whether every grid each reads can be found now. It builds each
grid-based operation, so it is the one slow filter and is applied last:

```{code-cell} python
norway_grids = catalog.operations(grid_based=True, area_name="Norway")
for entry in norway_grids:
    print(f"{entry.authority_code:10} {entry.grids_available()!s:5}  {entry.grid_names}")
```

```{code-cell} python
missing = catalog.operations(authority="EPSG", grids_available=False, area_name="Norway")
[str(entry) for entry in missing]
```

{meth}`~geodetic_engine.catalog.OperationEntry.grids` says which grid PROJ
found where, as {class}`~geodetic_engine.geodesy.GridUsage`. The grid files
themselves are listed by {func}`~geodetic_engine.catalog.grids`; see
{doc}`grids`.

## Deprecated and superseded operations

PROJ never offers a deprecated operation, so
{func}`~geodetic_engine.geodesy.available_operations` cannot list one. The
catalog can:

```{code-cell} python
deprecated = catalog.operations(
    source_crs="EPSG:4230", target_crs="EPSG:4326", deprecated=True
)
for entry in deprecated:
    print(entry, "-> replaced by", entry.replaced_by or "nothing stated")
```

A {term}`superseded` operation is still valid and still offered. `superseded`
filters on it, and each entry names its successors:

```{code-cell} python
for entry in catalog.operations(
    source_crs="EPSG:4230", target_crs="EPSG:4326", superseded=True
):
    print(entry, "-> superseded by", entry.superseded_by)
```

## Exporting an operation

{meth}`~geodetic_engine.catalog.OperationEntry.to_json_dict` is the
operation's PROJJSON as PROJ exports it from the registry, parameters
included:

```{code-cell} python
definition = chosen.to_json_dict()
[(p["name"], p["value"]) for p in definition["parameters"]]
```
