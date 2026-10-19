# Persistable references from a catalogue

An OSDU reference-data catalogue publishes a `PersistableReference` for each
CRS and transformation record. A `proj.db` built from the catalogue with
`geodetic-osdudb` keeps every one, **verbatim**, against the record's id. This
page reads them back: one at a time, as a listing, or as an export file.

**Use it when** you are writing an OSDU record that names a CRS or a
transformation, and the string in it should be the one the catalogue publishes,
not one generated from PROJ's definition. A generated payload describes the
same CRS but is not the same bytes. The two are never mixed: a reference the
catalogue does not state is never filled in with a generated one.

```{admonition} Needs a database built from a catalogue
:class: note

The examples read the database PROJ reads. In the devcontainer that is
`build/proj.db`, built from the official OSDU catalogue by default
({doc}`/getting-started/installation`). Elsewhere, build one from an OSDU
`CRS_CT.json` and put it first on PROJ's data path, as shown in
{ref}`osdu-record-ids`. The standard PROJ database records no references, so
every lookup returns `None` against it.
The outputs below were produced against a database built from a real catalogue
(2,852 references). The code is not run during the documentation build, since
the catalogue is large and usually internal. The
{doc}`worked examples notebook </examples/notebooks/geodetic-engine-examples>`
runs the same calls in section 6.
```

Every EPSG object that a catalogue record names has a reference, including the
EPSG objects the base `proj.db` already defines. So the lookup works for most of
what OSDU data names, not only for the OSDU bound CRSs the build imports.

## Looking one up

Name the object by the OSDU id of the record that publishes it, with or without
its data partition, by `AUTH:CODE`, or by a CRS object. The first `proj.db` on
PROJ's data path is read unless `database=` is given:

```python
from pathlib import Path

from geodetic_engine.persistablereference import (
    missing_reference_reason,
    persistable_reference_from_catalog,
)

# One CRS, named three ways: by code, by the record id, and by a bare data.ID.
by_code = persistable_reference_from_catalog("EPSG:4230")
by_id = persistable_reference_from_catalog(
    "opendes:reference-data--CoordinateReferenceSystem:Geographic2D:EPSG::4230"
)
bare = persistable_reference_from_catalog("Geographic2D:EPSG::4230", entity="crs")
by_code == by_id == bare  # True

by_code.identifier  # 'reference-data--CoordinateReferenceSystem:Geographic2D:EPSG::4230'
by_code.kind  # <Kind.LATE_BOUND_CRS: 'LBC'>
by_code.stated_authority  # EPSG:4230
by_code.version  # 'PE_10_9_1'
by_code.payload  # the string exactly as the catalogue publishes it
```

The result is a
{class}`~geodetic_engine.persistablereference.CatalogPersistableReference`. It
holds the verbatim `payload` and what the build recorded about it: the record
`identifier`, the object it names (`table_name`, `auth_name`, `code`),
`deprecated`, the `kind`, the `name` and `stated_authority` and `version` the
reference states, its `sha256` and the `source` catalogue. `parse()` reads the
payload into a reference object, as {doc}`usage` shows.

An `AUTH:CODE` that names a CRS and an operation alike is narrowed with
`entity="crs"` or `entity="operation"`. The same `entity=` lets a bare
`data.ID` such as `Projected:EPSG::23031` be given. If several active records
publish *different* references for one code,
{class}`~geodetic_engine.persistablereference.AmbiguousCatalogReferenceError`
is raised: name the record by its id instead.

```python
shift = persistable_reference_from_catalog("EPSG:1612", entity="operation")
shift.identifier  # 'reference-data--CoordinateTransformation:EPSG::1612'
shift.kind  # <Kind.TRANSFORMATION: 'ST'>
```

## Pretty-printing a bound CRS

{meth}`~geodetic_engine.persistablereference.CatalogPersistableReference.pretty`,
or {func}`~geodetic_engine.persistablereference.pretty_persistable_reference`
for any payload, gives indented JSON in the payload's own key order. This is
the early bound CRS for ED50 / UTM zone 31N bound by EPSG:1612. It is still
JSON:

```python
reference = persistable_reference_from_catalog(
    "opendes:reference-data--CoordinateReferenceSystem:"
    "BoundProjected:EPSG::23031_EPSG::1612"
)
print(reference.pretty())
```

```text
{
  "authCode": {
    "auth": "OSDU",
    "code": "23031023"
  },
  "lateBoundCRS": {
    "authCode": {
      "auth": "EPSG",
      "code": "23031"
    },
    "name": "ED_1950_UTM_Zone_31N",
    "type": "LBC",
    "ver": "PE_10_9_1",
    "wkt": "PROJCS[\"ED_1950_UTM_Zone_31N\",GEOGCS[\"GCS_European_1950\",...]"
  },
  "name": "ED50 * EPSG-Nor N62 2001 / UTM zone 31N [23031,1612]",
  "singleCT": { ... },
  "type": "EBC",
  "ver": "PE_10_9_1"
}
```

`pretty(wkt=True)` also lays each embedded ESRI WKT out over indented lines,
the way PROJ indents WKT. Only whitespace changes: every name and number is
kept exactly as written. The result is **for reading and is not JSON**; store
or send the verbatim payload. For the transformation EPSG:1612:

```python
print(shift.pretty(wkt=True))
```

```text
{
  "authCode": {
    "auth": "EPSG",
    "code": "1612"
  },
  "name": "ED_1950_To_WGS_1984_23",
  "type": "ST",
  "ver": "PE_10_9_1",
  "wkt":
    GEOGTRAN["ED_1950_To_WGS_1984_23",
      GEOGCS["GCS_European_1950",
        DATUM["D_European_1950",
          SPHEROID["International_1924",6378388.0,297.0]],
        PRIMEM["Greenwich",0.0],
        UNIT["Degree",0.0174532925199433]],
      GEOGCS["GCS_WGS_1984",
        DATUM["D_WGS_1984",
          SPHEROID["WGS_1984",6378137.0,298.257223563]],
        PRIMEM["Greenwich",0.0],
        UNIT["Degree",0.0174532925199433]],
      METHOD["Position_Vector"],
      PARAMETER["X_Axis_Translation",-116.641],
      PARAMETER["Y_Axis_Translation",-56.931],
      PARAMETER["Z_Axis_Translation",-110.559],
      PARAMETER["X_Axis_Rotation",0.893],
      PARAMETER["Y_Axis_Rotation",0.921],
      PARAMETER["Z_Axis_Rotation",-0.917],
      PARAMETER["Scale_Difference",-3.52],
      OPERATIONACCURACY[1.0],
      AUTHORITY["EPSG",1612]]
}
```

## From a CRS or an applied operation

A CRS and the operation a transformation applied both look their own reference
up:

```python
from geodetic_engine.geodesy import CoordinateReferenceSystem, Transformation

crs = CoordinateReferenceSystem.from_user_input("OSDU:23031023")
stored = crs.persistable_reference_from_catalog()
stored.kind  # <Kind.EARLY_BOUND_CRS: 'EBC'>

result = Transformation(
    "EPSG:23031", "EPSG:4326", operation="EPSG:1612"
).transform([(500000.0, 6600000.0)])
applied = result.operation.persistable_reference_from_catalog()
applied.identifier  # 'reference-data--CoordinateTransformation:EPSG::1612'
```

A CRS is looked up by the id or code it was resolved from. A bound CRS named by
its id therefore gets the bound CRS's reference, not the reference of the CRS it
wraps. A CRS given as WKT or PROJJSON is looked up by authority code only when
PROJ is certain which object it is. A CRS built from a payload has no authority
code and so no lookup, and an operation applied from one has none either.

## Using the stored payload

The stored string is a complete definition, so it goes wherever a CRS goes. The
difference between the early bound and the late bound reference of the same CRS
is the point:

```python
transform_bound = Transformation(stored.payload, "EPSG:4326")
transform_bound.transform([(500000.0, 6600000.0)]).coordinates[0]
# (2.9984073671068487, 59.536434448565075)

plain = CoordinateReferenceSystem.from_user_input("EPSG:23031")
late = plain.persistable_reference_from_catalog()
late.kind  # <Kind.LATE_BOUND_CRS: 'LBC'>
Transformation(late.payload, "EPSG:4326")
# AmbiguousOperationError: EPSG:23031 to EPSG:4326 involves a datum change;
# every datum operation must be named explicitly. ...
```

## Listing and exporting

```python
import io
import json
from collections import Counter

from geodetic_engine.persistablereference import (
    Kind,
    persistable_references_from_catalog,
)
from geodetic_engine.persistablereference import (
    export_persistable_references_from_catalog as export,
)

recorded = persistable_references_from_catalog(include_deprecated=False)
Counter(r.kind.value for r in recorded)
# Counter({'EBC': 1274, 'LBC': 1228, 'ST': 341, 'CT': 9})

# Every early bound CRS that carries EPSG:1612.
bound = persistable_references_from_catalog(kinds=[Kind.EARLY_BOUND_CRS])
[r.name for r in bound if r.identifier.endswith("_EPSG::1612")][:2]
# ['ED50 * EPSG-Nor N62 2001 [4230,1612]',
#  'ED50 * EPSG-Nor N62 2001 / UTM zone 31N [23031,1612]']

# Named objects, written in order. One with no reference still gets an entry.
buffer = io.StringIO()
export(buffer, pretty=True, values=["EPSG:4230", "EPSG:1612", "EPSG:99999"])  # 2
[e["query"] for e in json.loads(buffer.getvalue())]
# ['EPSG:4230', 'EPSG:1612', 'EPSG:99999']

# To a file, as JSON, JSONL or CSV.
export(Path("refs.json"), pretty=True)
export(Path("refs.csv"), format="csv", entity="operation")
```

Each entry carries the same fields as the lookup result, and
`persistable_reference`: the verbatim string, or the decoded object with
`pretty`. The formats are JSON, JSONL and CSV; `pretty` and the
WKT-laid-out display apply to JSON only. On the command line:

```bash
uv run geodetic-osdudb references build/proj.db                       # every one, JSON
uv run geodetic-osdudb references build/proj.db --kind EBC --format jsonl
uv run geodetic-osdudb references build/proj.db EPSG:23031 EPSG:1612 --pretty-wkt
uv run geodetic-osdudb references build/proj.db --format csv --output refs.csv
```

## When there is none

A missing reference never raises on lookup. The lookup returns `None`, and
{func}`~geodetic_engine.persistablereference.missing_reference_reason` says why
in a sentence. An export of named values writes
`"persistable_reference": null` with the same `reason`:

```python
persistable_reference_from_catalog("EPSG:99999")  # None
missing_reference_reason("EPSG:99999")
# 'no record of the catalogue the active proj.db was built from names EPSG:99999;
#  only an object a catalogue record names has a persistable reference'
```

Every geocentric CRS, for instance, has none, because ESRI WKT cannot state
one. {ref}`osdu-persistable-references` lists every situation, how the build
treats it, and how the references are stored in the database.
