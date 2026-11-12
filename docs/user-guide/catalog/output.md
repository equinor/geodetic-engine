---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Listings and their output

Every listing function returns a {class}`~geodetic_engine.catalog.Listing`:
a lazy, read-only sequence of entries with three ways out -- the entries
themselves, a table, and JSON.

## A lazy sequence

Nothing is read until a listing is first used, and then the entries are kept:

```{code-cell} python
from geodetic_engine import catalog

listing = catalog.crs(authority="EPSG", kind="geocentric")
print(repr(listing))
print(len(listing), "entries")
print(repr(listing))
```

It is a {class}`~collections.abc.Sequence`: index it, slice it, test it, loop
over it. {meth}`~geodetic_engine.catalog.Listing.first` returns the first
entry or None, and {meth}`~geodetic_engine.catalog.Listing.one` the only one,
raising when there is none or more:

```{code-cell} python
print(listing[0], "|", listing[-1])
print(catalog.crs(code="EPSG:4936").one())
```

```{code-cell} python
:tags: [raises-exception]

catalog.crs(authority="EPSG", name="UTM zone 32N").one()
```

A listing belongs to the database it was made for. If PROJ's search path is
switched to another database, a listing not yet read refuses to be read, and
any listing refuses to be exported as JSON, since the export names the
database that answered. Both raise
{class}`~geodetic_engine.catalog.CatalogError`; call the listing function
again. Entries already read stay as they are.

## A table

{meth}`~geodetic_engine.catalog.Listing.to_dataframe` gives one row per
entry. References become `"name (AUTH:CODE)"`, enumerations their value, and
the first area of use is spread over `area_of_use`, `west`, `south`, `east`
and `north`. The columns are the same when the listing is empty:

```{code-cell} python
catalog.operations(source_crs="EPSG:4230", target_crs="EPSG:4326", max_accuracy=2).to_dataframe()
```

## JSON

Two JSON forms, for two purposes.

### An object's definition: official PROJJSON

Each entry's `to_json_dict()` is the object's full definition as
{term}`PROJJSON`, exactly as PROJ exports it, `$schema` included. It is what
pyproj's own `to_json_dict()` returns for the same code, and PROJ can read it
back:

```{code-cell} python
import pyproj

entry = catalog.ellipsoids(code="EPSG:7019").one()
definition = entry.to_json_dict()
print(definition)
print(pyproj.crs.Ellipsoid.from_json_dict(definition).name)
```

PROJ exports a coordinate system, unit or method only inside another object.
For those, `to_json_dict()` is the form PROJ writes there, which PROJJSON's
schema defines, but which its schema does not accept as a document of its
own. A grid file has no PROJJSON form.

| Entry | `to_json_dict()` | Validates against |
|---|---|---|
| CRS, operation, datum, ellipsoid, prime meridian | PROJ's export | The PROJJSON schema |
| Coordinate system | PROJ's export from a CRS using it, else assembled the same way | PROJJSON's `coordinate_system` definition |
| Unit | PROJJSON's unit object, with its id | PROJJSON's `unit` definition |
| Method | PROJJSON's method object | PROJJSON's `method` definition |
| Grid | The listing item | The listing schema's `grid_item` |

### A listing: PROJJSON-shaped items in an envelope

PROJJSON has no place for what a listing is about: it allows no property its
schema does not name, and names no deprecation, replacement, OSDU id or grid
availability. So {meth}`~geodetic_engine.catalog.Listing.to_json_dict` wraps
items in a small envelope that records the database that answered and the
filters asked for. Each item uses PROJJSON's names and value shapes wherever
PROJJSON has the property -- `type`, `name`, `id`, `area`, `bbox`, `accuracy`,
references as `{name, id}` -- and adds the rest beside them:

```{code-cell} python
import json

document = catalog.datums(code="EPSG:6258").to_json_dict()
print(json.dumps({**document, "database": "..."}, indent=2)[:1500])
```

`include_definitions=True` adds each entry's full PROJJSON to its item as
`definition`. It builds every object through PROJ, so it takes longer on a
large listing.

`filters` holds the filters that were given; one left out was not asked for.
The exception is `deprecated`, which a listing that has the filter always
states: `false` is the default, current objects only, and `null` is
`deprecated=None`, deprecated objects as well.

### The listing schema

The envelope is described by a JSON Schema, draft-07 like PROJJSON's own,
which refers to the official PROJJSON v0.7 schema for `id`, `bbox` and every
`definition`. It ships with the package as
{func}`~geodetic_engine.catalog.listing_schema` and is published at
{data}`~geodetic_engine.catalog.LISTING_SCHEMA_URL`. To validate offline,
resolve PROJJSON's schema from PROJ's data directory:

```{code-cell} python
import os
from pathlib import Path

import jsonschema
from referencing import Registry, Resource

projjson_path = next(
    Path(directory, "projjson.schema.json")
    for directory in pyproj.datadir.get_data_dir().split(os.pathsep)
    if Path(directory, "projjson.schema.json").is_file()
)
projjson = json.loads(projjson_path.read_text())
registry = Registry().with_resource(projjson["$id"], Resource.from_contents(projjson))
validator = jsonschema.Draft7Validator(catalog.listing_schema(), registry=registry)

document = catalog.crs(code="EPSG:25832").to_json_dict(include_definitions=True)
validator.validate(document)
print("valid:", document["$schema"])
```

```{dropdown} The listing schema
:::{literalinclude} ../../../geodetic_engine/catalog/listing.schema.json
:language: json
:::
```

A change to the envelope that breaks a consumer comes with a new schema
version; the PROJJSON version follows PROJ.

## Provenance

{func}`~geodetic_engine.catalog.database` names the `proj.db` that answered,
with the versions of the datasets it holds and fingerprints of every database
on PROJ's search path. The fingerprints are those a
{class}`~geodetic_engine.geodesy.TransformationResult` records, so a listing
and a transformation can be tied to the same database:

```{code-cell} python
info = catalog.database()
print(info.proj_version, info.epsg_version)
print([str(a) for a in catalog.authorities()])
```
