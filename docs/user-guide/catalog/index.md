# Catalog

`geodetic_engine.catalog` lists what the active PROJ database holds:
coordinate reference systems, coordinate operations, datums and datum
ensembles, ellipsoids, prime meridians, units, operation methods, coordinate
systems and grid files. Every listing takes the same filters, can be combined
with any other filter, and returns entries you can tabulate, export as JSON, or
pass straight on to {mod}`geodetic_engine.geodesy`.

```python
from geodetic_engine import catalog

catalog.crs(
    authority="EPSG",
    kind="projected",
    datum="ETRS89",
    area_of_interest=(-10, 50, 30, 72),  # west, south, east, north
).to_dataframe()
```

**Use it when** you need to find a code: the projected CRSs on a datum in your
area, the transformations an authority publishes between two datums and how
accurate they are, the grids those transformations read and whether they are
installed, or the ellipsoid, unit or method behind a definition.

**Use {func}`~geodetic_engine.geodesy.available_operations` instead when** you
already have two CRSs and want to know what PROJ would run between them. The
catalog lists what the registry *publishes*, as it publishes it, including
deprecated objects. PROJ *assembles* operations between any two CRSs, chaining
registered ones through geographic CRSs and hubs, and never offers a
deprecated one. The two answer different questions; see
{doc}`operations`.

## The API in one table

| You want to list | Use | Each entry is |
|---|---|---|
| Coordinate reference systems | {func}`~geodetic_engine.catalog.crs` | {class}`~geodetic_engine.catalog.CrsEntry` |
| Conversions, transformations and concatenated operations | {func}`~geodetic_engine.catalog.operations` | {class}`~geodetic_engine.catalog.OperationEntry` |
| Datums and datum ensembles | {func}`~geodetic_engine.catalog.datums` | {class}`~geodetic_engine.catalog.DatumEntry` |
| Ellipsoids | {func}`~geodetic_engine.catalog.ellipsoids` | {class}`~geodetic_engine.catalog.EllipsoidEntry` |
| Prime meridians | {func}`~geodetic_engine.catalog.prime_meridians` | {class}`~geodetic_engine.catalog.PrimeMeridianEntry` |
| Units of measure | {func}`~geodetic_engine.catalog.units` | {class}`~geodetic_engine.catalog.UnitEntry` |
| Operation methods | {func}`~geodetic_engine.catalog.methods` | {class}`~geodetic_engine.catalog.MethodEntry` |
| Coordinate systems | {func}`~geodetic_engine.catalog.coordinate_systems` | {class}`~geodetic_engine.catalog.CoordinateSystemEntry` |
| Grid files | {func}`~geodetic_engine.catalog.grids` | {class}`~geodetic_engine.catalog.GridEntry` |
| Authorities and their dataset versions | {func}`~geodetic_engine.catalog.authorities` | {class}`~geodetic_engine.catalog.Authority` |
| The database that answered | {func}`~geodetic_engine.catalog.database` | {class}`~geodetic_engine.catalog.DatabaseInfo` |

## How it works

```{mermaid}
flowchart LR
    Call["catalog.crs(...)"] --> Check["Filters checked<br/>at the call"]
    Check --> Listing["Listing<br/>(nothing read yet)"]
    Listing -- "first use" --> Read["Read once per database:<br/>pyproj where it answers,<br/>proj.db for the rest"]
    Read --> Entries["Entries, in order"]
    Entries --> Table["to_dataframe()"]
    Entries --> JSON["to_json_dict():<br/>PROJJSON-shaped items"]
    Entries --> Def["entry.to_json_dict():<br/>official PROJJSON"]
    Entries --> Geodesy["entry.authority_code<br/>to geodesy"]
```

- **pyproj first.** CRS rows, their kinds, areas and deprecation come from
  {func}`pyproj.database.query_crs_info`, and an area of interest is tested
  against those areas by PROJ's own rule, without asking PROJ again for every
  listing. Units come from {func}`pyproj.database.get_units_map`, and every
  full definition is exported by PROJ. What pyproj does not hand back -- the
  datum of a CRS, aliases, replacements, operation endpoints and accuracies,
  coordinate system axes, grid files -- is read from the same `proj.db` that
  PROJ resolves codes against.
- **Filters are checked when you call.** A misspelt authority, a range the
  wrong way round, or `datum="EPSG:4326"` (a CRS, not a datum) raises at the
  call that gave it, not at first use.
- **Listings are lazy and kept.** Nothing is read until the listing is first
  iterated, indexed, measured or exported. Each table is read once per
  database and kept, so the first listing of a kind takes a moment and the
  next takes milliseconds. Switching database with
  {func}`~geodetic_engine.osdudb.use_bundled_database` or
  `pyproj.datadir.set_data_dir()` is seen on the next call.
- **Exports follow PROJJSON.** An entry's `to_json_dict()` is the object's
  official PROJJSON, untouched. A listing's JSON uses PROJJSON's names and
  shapes and adds only what PROJJSON has no place for. See {doc}`output`.

## Pages in this section

```{toctree}
:maxdepth: 1

crs
operations
datums
building-blocks
grids
filters
output
```
