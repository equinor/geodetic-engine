# Custom database from OSDU

`geodetic_engine.osdudb` and the `geodetic-osdudb` command build the same
enriched `proj.db` as {doc}`projdb`, from an OSDU coordinate reference
catalogue instead of a Georepository API. No credentials or network are needed.

**Use it when** your data names CRSs by OSDU code, for example
`OSDU:23032023`, an OSDU bound CRS, or by the id of the OSDU record that
publishes it, for example
`opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031`
({ref}`osdu-record-ids`), and you want those to resolve.

**You may not need it**: if your records carry full `persistableReference`
payloads, {doc}`/persistable-reference/usage` transforms with them directly, with no
database build.

```{admonition} Not executed
:class: note

Nothing on this page is run during the documentation build. The devcontainer
and the documentation build both build the official catalogue into
`build/proj.db` ({doc}`/getting-started/installation`), so there the examples
work as shown. The command reference is at {doc}`/cli/geodetic-osdudb`.
```

## The catalogue

OSDU publishes CRSs and transformations as one manifest, usually called
`CRS_CT.json`. Its `ReferenceData` array holds
`reference-data--CoordinateReferenceSystem` and
`reference-data--CoordinateTransformation` records.
{class}`~geodetic_engine.osdudb.OsduCatalog` reads it.

The official one is public, under the Apache License 2.0, in the
[OSDU Data Definitions](https://community.opengroup.org/osdu/data/data-definitions)
repository, at `ReferenceValues/Manifests/reference-data/LOCAL/CRS_CT.json`.
It is committed in this repository, byte for byte, as
`catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json`. Beside it,
`CRS_CT_osdu_official_crs_catalog.source.json` records the OSDU commit it came
from and its SHA-256, and a build records that commit as its `source_version`.
A build therefore needs no network, and is reproducible from the repository
alone. The released wheel includes the same catalogue and provenance;
`geodetic-osdudb build --official` verifies their SHA-256 and records the
source commit. `scripts/install-osdu-projdb.sh` builds and activates it in one
step from a repository checkout, and is what the devcontainer runs.

`geodetic-osdudb download`, or
{func}`~geodetic_engine.osdudb.download_official_catalog`, replaces the
committed copy with the newest OSDU publishes, for the change to be reviewed
and committed; `scripts/install-osdu-projdb.sh --update` does that and then
builds it. The repository's web pages sit behind a bot check that a script
cannot pass, so the file is read through GitLab's API instead. Nothing is
written unless the whole file arrived, matches the digest the server states
and reads as a manifest.

An organisation's own catalogue works the same way. Keep it in `local/`. That
directory is gitignored, and such a catalogue is often internal to whoever
published it. Give its path on the command line, as `catalog` in
`geodetic-osdudb.toml`, or in `GEODETIC_ENGINE_OSDU_CATALOG`.

## Running it

From an installed package, build the bundled official catalogue and put the
result first on PROJ's data path:

```bash
previous_proj_data="$(python -c 'import pyproj; print(pyproj.datadir.get_data_dir())')"
geodetic-osdudb build --official --output "$PWD/build/proj.db"
export PROJ_DATA="$PWD/build:$previous_proj_data"
```

Set `PROJ_DATA` before starting the process that uses the database. On
Windows, separate the directories with `;` instead of `:`. A Python process can
also call `pyproj.datadir.set_data_dir` as shown in {doc}`combining`.

```{important}
If you already built a database from Georepository, **add `--append`**. Both
commands default to `build/proj.db`. Without `--append` this build starts from
a fresh copy of the official `proj.db`, and is refused rather than discard the
earlier import. See {doc}`combining`.
```

From a repository checkout:

```bash
# Build the committed official catalogue into build/proj.db and make it the
# database PROJ reads.
scripts/install-osdu-projdb.sh

# Replace the committed catalogue with the newest OSDU publishes, then build it.
scripts/install-osdu-projdb.sh --update

# Only replace it, without building.
uv run geodetic-osdudb download

# Everything is defaulted; the catalogue is the only thing that must be named.
uv run geodetic-osdudb build CRS_CT.json

# Choose where to write the validated database.
uv run geodetic-osdudb build CRS_CT.json --output build/proj.db

# Add to a database an earlier build already wrote, instead of replacing it.
uv run geodetic-osdudb build CRS_CT.json --output build/proj.db --append

# Also import the catalogue's EPSG records that this proj.db does not yet have.
uv run geodetic-osdudb build CRS_CT.json --authority OSDU --authority EPSG

# Check, summarise, and show the resolved settings.
uv run geodetic-osdudb validate build/proj.db --authority OSDU
uv run geodetic-osdudb inspect build/proj.db
uv run geodetic-osdudb config CRS_CT.json

# Look up or export the persistableReference strings the catalogue publishes.
uv run geodetic-osdudb references build/proj.db EPSG:23031 --pretty
uv run geodetic-osdudb references build/proj.db --format csv --output refs.csv
```

From Python:

```python
from pathlib import Path

from geodetic_engine.osdudb import build, load_config

report = build(load_config(catalog=Path("local/osdu/CRS_CT.json")))
print(report.status, len(report.imported), "imported,", len(report.skipped), "skipped")
```

`geodetic-osdudb.toml` is optional. The annotated example is on
{doc}`/cli/geodetic-osdudb`.

(osdu-custom-catalogue)=
## Your own catalogue on top of the official one

An organisation often publishes CRSs and transformations the official OSDU
catalogue does not have, and sometimes states an official record differently.
Build such a catalogue **after** the official one, into the same database, with
`--append --overwrite-rows`:

- a record the official catalogue does not have is **added**;
- a record it states differently, under the same code or OSDU id, **replaces**
  the official definition, and its persistableReference replaces the official
  one;
- a record it states exactly as the official catalogue does is left
  **unchanged**, so a catalogue that repeats the official records changes only
  what it really changes, and building it again changes nothing.

Every replacement is named. The build summary counts each kind as a `WARNING`
and names the first 25 of each with the columns that changed; the log file
beside the database names every one, and the report records them under
`overrides`, with the counts `objects_replaced`, `identifiers_moved` and
`persistable_references_replaced`. An area of use or alias a record restates is
refreshed with its object and not listed:

```text
WARNING 1 definition(s) an earlier build wrote were REPLACED by this source's:
WARNING   geodetic_crs OSDU:4100 Example 2020 -> Example 2020 (corrected)
WARNING       changed: name
WARNING 1 persistableReference(s) an earlier build recorded were REPLACED:
WARNING   reference-data--CoordinateReferenceSystem:Geographic2D:OSDU::4100
INFO 4 object(s) were stated exactly as an earlier build wrote them and left unchanged
```

Without `--overwrite-rows`, a record stated differently is refused, the build
is aborted, and the error says which record and how to let it replace the
earlier one. Objects of PROJ's own EPSG dataset are never replaced, by either
catalogue. Records under a code space of your own are imported only when it is
named with `--authority`, or in `authorities` in `geodetic-osdudb.toml`.

A custom catalogue often publishes the official record ids for objects under
its own code space, for example `BoundProjected:EPSG::23031_EPSG::1133` for a
bound CRS it numbers itself. Each such id then names the custom catalogue's
object, not the official one, and is listed as an id that now names a
different object. The official object is still in the database under its own
code.

The simplest way to keep the order right is to let
`scripts/install-osdu-projdb.sh` do it. It builds every `*.json` in
`local/catalogs/custom/`, in name order, and every `--custom` it is given, on
top of the official catalogue each time it runs, including in the
devcontainer on creation. It imports every code space a custom catalogue
publishes records under, except EPSG. Importing the official catalogue again,
with `--force`, `--update` or after a pull that changed it, is followed by the
custom ones again, so they stay on top:

```bash
# Every run, and the devcontainer on creation, builds these on top.
cp our-crs.json local/catalogs/custom/
scripts/install-osdu-projdb.sh

# Or name one for this run only.
scripts/install-osdu-projdb.sh --custom path/to/our-crs.json

# By hand, after the official catalogue, naming your code space.
uv run geodetic-osdudb build our-crs.json --output build/proj.db --append --overwrite-rows --authority ACME
```

A custom catalogue that cannot be built leaves the database as it was, with a
warning naming it; the official catalogue and any other custom one are still
installed.

## Which authorities to import

A catalogue has two kinds of record:

- Records under the `OSDU` code space are OSDU's own. In practice they are
  bound CRSs: an EPSG CRS packaged with the one named transformation to WGS 84
  that should be used with it.
- Records under `EPSG` are the EPSG dataset, republished.

The default, `authorities = ["OSDU"]`, imports only the first kind. Adding
`EPSG` also imports the catalogue's EPSG records that the base `proj.db` does
not already define. This fills the gap when the catalogue is newer than the
EPSG dataset PROJ ships. EPSG objects PROJ already has are never rewritten.

## What has to be recovered from the WKT

A catalogue names each CRS's coordinate system, datum and projection by
authority code, but defines none of them. An ellipsoid's axes, a prime
meridian's longitude, an axis order and an operation's parameters are stated
only in the record's own `OGCWellKnownText2`. Importing one CRS means taking
its WKT apart and producing everything it references that the base database
does not already have.

PROJ's PROJJSON export drops two things, which are recovered elsewhere. In
both cases a plausible guess would produce wrong coordinates:

- **Nested identifiers.** Codes for a datum, ellipsoid and prime meridian are
  read from the pyproj sub-objects, which keep them.
- **Unit identifiers.** A unit is exported by name and conversion factor, with
  no code, so units are matched against the `unit_of_measure` table already
  in the database. A unit that cannot be matched is **refused**. An operation
  with the wrong rotation unit is wrong by a plausible-looking amount, not
  obviously broken.

A record is imported whole or not at all. A CRS is skipped and reported, not
written in a weakened form, if its declared datum is not the one its own WKT
defines, if its axis unit cannot be resolved, or if it needs an object from an
authority the build may not write.

## Bound CRSs

OSDU's bound CRSs are the main reason to build this database. They pin one
transformation to a CRS instead of leaving the choice to operation selection.
They are assembled with pyproj, not taken from the catalogue, which publishes
no WKT for them. So the transformation embedded is one this package has
checked. A bound CRS over a concatenated operation is first collapsed to a
single equivalent step, and refused if it does not collapse
({doc}`/background/bound-crs`).

OSDU gives a bound CRS's extent as the intersection of the CRS's and the
transformation's extents, with no code of its own. It is recorded under the
importing authority rather than dropped, because it is the area the bound CRS
is actually valid in.

(osdu-record-ids)=
## OSDU record ids

OSDU data rarely names a CRS by authority code. It names the reference data
record that publishes the CRS:

```text
opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031
opendes:reference-data--CoordinateReferenceSystem:BoundProjected:EPSG::23031_EPSG::1133
opendes:reference-data--CoordinateTransformation:EPSG::1612
```

A build records the id of **every** record in the catalogue against the object
it names. That includes the EPSG records the base `proj.db` already defines,
which the build does not import but which are most of the ids a caller will
use. After a build, those ids work wherever an authority code does.

```{important}
**The record has to be in `proj.db`.** An id resolves only against a database
built by `geodetic-osdudb` from a catalogue that publishes the record, and only
when that database is first on PROJ's data path. The standard PROJ database
records no OSDU ids, so against it every id is refused with
{class}`~geodetic_engine.geodesy.UnresolvableCRSError`, even one as plain to
read as `Projected:EPSG::23031`:

    UnresolvableCRSError: could not resolve 'opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031'
    as a CRS: the active proj.db records no OSDU ids; build one from the OSDU catalogue that publishes
    the record with geodetic-osdudb and put it on PROJ's data path (scripts/install-osdu-projdb.sh
    does both for the official catalogue)

The devcontainer installs the official catalogue this way by default
({doc}`/getting-started/installation`). Elsewhere, build and activate one
first:

    scripts/install-osdu-projdb.sh
    export PROJ_DATA="$PWD/local/proj-data:$(uv run python -c 'import pyproj; print(pyproj.datadir.get_data_dir())')"
```

### Instantiating objects by id

The examples below assume a database built as above. Section 8 of the
{doc}`worked examples notebook </examples/notebooks/geodetic-engine-examples>`
runs the same steps end to end against the official catalogue, which the
documentation build installs first, so its output is what the code produces.

A CRS, from the record's id with or without its partition, or as a reference
from another record, which ends in a version separator:

```python
from geodetic_engine.geodesy import CoordinateReferenceSystem

crs = CoordinateReferenceSystem.from_user_input(
    "opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031"
)
crs.authority_code  # 'EPSG:23031'
crs.axis_abbreviations  # ('E', 'N')

# The partition is ignored, and so is a version separator.
CoordinateReferenceSystem.from_user_input(
    "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031:"
).authority_code  # 'EPSG:23031'

# from_osdu_id() takes only OSDU ids, and also a record's bare data.ID.
CoordinateReferenceSystem.from_osdu_id("Projected:EPSG::23031").authority_code
# 'EPSG:23031'
```

A bound CRS, which resolves to the bound CRS with its transformation, not to
the CRS it wraps:

```python
bound = CoordinateReferenceSystem.from_user_input(
    "opendes:reference-data--CoordinateReferenceSystem:"
    "BoundProjected:EPSG::23031_EPSG::1133"
)
bound.crs.is_bound  # True
bound.crs.source_crs.name  # 'ED50 / UTM zone 31N'
bound.crs.to_json_dict()["transformation"]["id"]
# {'authority': 'EPSG', 'code': 1133}
```

A transformation with both ends named by id. The bound CRS settles the datum
change itself, so no operation is named:

```python
from geodetic_engine.geodesy import Transformation

BOUND = (
    "opendes:reference-data--CoordinateReferenceSystem:"
    "BoundProjected:EPSG::23031_EPSG::1133"
)
WGS84 = "opendes:reference-data--CoordinateReferenceSystem:Geographic2D:EPSG::4326"

result = Transformation(BOUND, WGS84).transform([(500000.0, 6600000.0)])
result.operation.authority_code  # 'EPSG:1133'
result.operation.route  # bound
result.coordinates[0]  # (2.9983507609032416, 59.536415666522224)
```

An operation named by the id of its `CoordinateTransformation` record, through
`operation=` on {class}`~geodetic_engine.geodesy.Transformation` or
{func}`~geodetic_engine.geodesy.transform`. It is applied and checked exactly
as if named by its code:

```python
from geodetic_engine.geodesy import transform

result = transform(
    "opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031",
    "EPSG:4326",
    [(500000.0, 6600000.0)],
    operation="opendes:reference-data--CoordinateTransformation:EPSG::1612",
)
result.operation.authority_code  # 'EPSG:1612'
result.coordinates[0]  # (2.9984073671068487, 59.536434448565075)
```

What is refused, rather than resolved to something close:

```python
# A near miss: one digit short of EPSG::1133.
CoordinateReferenceSystem.from_user_input(
    "opendes:reference-data--CoordinateReferenceSystem:"
    "BoundProjected:EPSG::23031_EPSG::113"
)  # UnresolvableCRSError: ... records no OSDU id ...

# A transformation's id where a CRS is needed, and the reverse.
CoordinateReferenceSystem.from_user_input(
    "opendes:reference-data--CoordinateTransformation:EPSG::1612"
)  # UnresolvableCRSError: ... is the OSDU id of a coordinate transformation, not of a CRS
transform(
    "EPSG:23031", "EPSG:4326", [(500000.0, 6600000.0)],
    operation="opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031",
)  # ValueError: ... is the OSDU id of a coordinate reference system, not of a coordinate transformation

# A transformation id the database does not record.
transform(
    "EPSG:23031", "EPSG:4326", [(500000.0, 6600000.0)],
    operation="opendes:reference-data--CoordinateTransformation:EPSG::161",
)  # OperationNotAvailableError: ... names no coordinate operation: ...
```

### How an id is matched

- **The data partition is ignored.** `opendes:` above differs between
  operators, and OSDU builds the rest of the id the same way in every partition
  precisely so the same CRS is recognised in all of them. The manifest's
  `{{NAMESPACE}}` placeholder is accepted too.
- **A version separator is ignored.** A reference from one record to another
  ends in `:` or `:<version>`. The database holds one definition per id, the
  one in the catalogue it was built from. The id as given is tried first, so an
  id that genuinely ends in digits is never cut short.
- **Matching is exact.** The id is looked up in the identifiers the build
  recorded, and nothing else. It is not decoded from its spelling, since
  operators curate these records and OSDU's spelling convention is good
  practice rather than a guarantee. It is never matched approximately: an id
  one character off, such as `…_EPSG::113` for `…_EPSG::1133`, is refused
  rather than resolved to a near miss.
- **The database must have been built from a catalogue.** A `proj.db` no build
  of this kind has written records no ids, so every id is refused, with a
  message saying so.

### What a build records

The report accounts for every record:

- each id that names nothing in the database is listed with the reason:
  skipped by the build, not in the base `proj.db` (`--authority EPSG` imports
  it), or the record states no id;
- an id the catalogue publishes for two different objects is recorded for
  neither; one object published under one id both active and inactive is not
  ambiguous, and is recorded from its active record;
- where the catalogue and `proj.db` disagree about an object, usually an EPSG
  rename, it is reported and the id still names the object.

Appending the same catalogue again changes nothing. Appending one that would
move an id to a different object, because OSDU renumbered a bound CRS for
example, is refused unless `--overwrite-rows` is given, so an id never silently
starts resolving to something else. The superseded object itself stays in the
database. A persistable reference recorded for a moved id stated the earlier
object, so it goes with it, even with persistable references turned off; the
build lists it under the overrides as removed, or as replaced when the
catalogue publishes a different one for the id. The same holds for an append
that publishes an earlier id for more than one object: it is refused, and with
`--overwrite-rows` the id is removed with its alias and persistable reference,
so it stops resolving rather than going on naming the earlier object.

With `include_deprecated` off, inactive records are left out, but an id an
earlier build recorded as active whose record the catalogue now marks inactive
is marked inactive rather than left resolving as active. That is a change to
the id, so it follows the same `--overwrite-rows` rule. The report counts these
as `marked_inactive`.

The ids are stored in a table this package adds,
`geodetic_engine_osdu_object_identifier`, keyed by scheme (`OSDU`) and id, and
naming each object by table, authority and code in the vocabulary of PROJ's
`object_view`. It joins with PROJ's own tables for listing and searching, for
example every OSDU CRS within an extent. An append build migrates the former
table name in an existing database. `geodetic-osdudb inspect` counts the ids by
scheme.

```{admonition} PROJ aliases
:class: warning

Each id is also written as a PROJ alias, source `OSDU_ID`, so a tool reading
the database through PROJ directly, such as `projinfo`, QGIS or plain pyproj,
finds an id without its partition. PROJ matches aliases **approximately**, so
such a tool can resolve a mistyped id to a different CRS: `…_EPSG::113` to the
bound CRS over EPSG:1133. This package never resolves ids through the aliases.
Turn them off with `--no-osdu-id-aliases`, or `osdu_id_aliases = false` in
`geodetic-osdudb.toml`, if other software reads the database.
```

(osdu-persistable-references)=
## Persistable references

Every catalogue record also publishes a `PersistableReference`. This is the
JSON envelope around ESRI WKT that OSDU data carries wherever it names a CRS or
a transformation (see {doc}`/persistable-reference/index`). A build records it
**verbatim** against the record's id, for **every** record whose id it records.
That includes the EPSG records the base `proj.db` already defines and the build
does not import. So for any object a catalogue record names, the exact string
the catalogue publishes can be looked up, listed or exported. Use it when
writing an OSDU record that names that object.

How to look one up, pretty-print it, and list or export them is on
{doc}`/persistable-reference/catalog`. This section covers what a build does
with them.

### When there is none

A missing reference never fails a build and never raises on lookup:

| Situation | Build | Recorded | Lookup |
|---|---|---|---|
| The record states no `PersistableReference`, or an empty one (all geocentric CRSs, which ESRI cannot state) | Listed under `missing`; one `WARNING` with the count | No row | `None` |
| The reference cannot be read, has malformed WKT, or states a transformation for a CRS (or the reverse) | Listed under `invalid` with the reason; `WARNING` | No row | `None` |
| One id is published with two different references | Listed under `invalid` | No row | `None` |
| The reference disagrees with its record: an `authCode` naming another code, an early bound reference on an unbound CRS, no `authCode` | Listed under `warnings`; `WARNING` | Verbatim | The reference |
| The record's id names nothing in the database | Already listed under the identifiers; counted as `unlinked` | No row | `None` |
| The database was built before references were recorded, or with them turned off | — | No table | `None` |
| An append whose catalogue no longer states a usable reference for a record that has one recorded | Listed under `warnings`; with `--overwrite-rows`, listed under `missing` or `invalid` | Kept; with `--overwrite-rows`, removed | The earlier reference; with `--overwrite-rows`, `None` |

Where a lookup returns `None`, an export of named values writes
`"persistable_reference": null` with a `reason`.
{func}`~geodetic_engine.persistablereference.missing_reference_reason` gives the
same sentence, read from the build history where the build recorded one:

```python
persistable_reference_from_catalog("EPSG:4978")  # None
missing_reference_reason("EPSG:4978")
# "the catalogue record 'reference-data--CoordinateReferenceSystem:Geocentric:EPSG::4978'
#  states no persistableReference"
```

The build report has a `persistable_references` section with `recorded`,
`written`, `unchanged`, `kept`, `removed`, `unlinked`, and the `missing`,
`invalid` and `warnings` lists. Its counts include
`persistable_references_recorded`, `_missing` and `_invalid`. Appending the same
catalogue again changes nothing. An append that would change a recorded
reference, for example a newer catalogue with a newer `ver`, is refused unless
`--overwrite-rows` is given, so a reference never silently changes. For the
same reason, an append whose catalogue states no usable reference keeps the
recorded one unless `--overwrite-rows` is given. When `--overwrite-rows` moves
an id to another object, the old reference goes with it.

Recording references adds about one and a half kilobytes per record: the
2,900-record catalogue this was measured on grows the database from 18 MB to
23 MB. Turn it off with `--no-persistable-references` or
`osdu_persistable_references = false`. An append with it off leaves the
references an earlier build recorded as they are.

### How they are stored

```sql
CREATE TABLE geodetic_engine_osdu_persistable_reference(
    scheme TEXT NOT NULL,             -- 'OSDU'
    identifier TEXT NOT NULL,         -- the record id, without its partition
    kind TEXT NOT NULL CHECK (kind IN ('LBC','EBC','ST','CT','USO','UAD')),
    name TEXT,                        -- the name the reference states
    stated_auth_name TEXT,            -- its authCode: provenance, may disagree
    stated_code TEXT,
    version TEXT,                     -- its "ver", e.g. PE_10_9_1
    sha256 TEXT NOT NULL,             -- of the verbatim payload
    persistable_reference TEXT NOT NULL,
    source TEXT,                      -- the catalogue it was read from
    PRIMARY KEY (scheme, identifier),
    FOREIGN KEY (scheme, identifier)
        REFERENCES geodetic_engine_osdu_object_identifier(scheme, identifier)
        ON DELETE CASCADE
);
-- and geodetic_engine_osdu_persistable_reference_view, joining each reference
-- to the object (table_name, auth_name, code) its id names
```

- **Keyed by record id, not by object.** A `persistableReference` belongs to
  the catalogue record that publishes it, not to the PROJ object. Two records,
  or two catalogue releases, can state one object differently. The object is
  reached through the id's row in `geodetic_engine_osdu_object_identifier`
  rather than named again, so there is one place that says which object an id
  names.
- **A real foreign key.** Unlike `object_view`, which PROJ's annotation tables
  check with a trigger, the identifier table can be referenced by a foreign key.
  SQLite refuses a reference for an id that is not recorded. The cascade
  removes a reference when its id is moved or removed. Validation runs SQLite's
  foreign key check, recomputes each `sha256`, and checks each kind fits the
  object its id names.
- **A table of its own.** The identifier table stays small for id resolution,
  absence of a row is what "no reference" means, existing databases need no
  migration, and the whole feature can be turned off. It is an ordinary rowid
  table, because SQLite stores a `WITHOUT ROWID` row whole in its key's b-tree,
  which suits rows far smaller than a kilobyte.
- **Joins like PROJ's own tables.** The view gives each reference the
  `object_view` vocabulary that `alias_name` and `usage` use, so references can
  be listed with `crs_view`, filtered by extent, and so on. Unit references
  (`USO`, `UAD`) are already allowed for when units are catalogued.

`geodetic-osdudb inspect` counts the references by kind.

## Errors

On top of the {doc}`projdb` errors it shares:

| Exception | Raised when |
|---|---|
| {class}`~geodetic_engine.osdudb.OsduCatalogError` | The catalogue file cannot be read as an OSDU manifest |
| {class}`~geodetic_engine.osdudb.UnreadableDefinitionError` | A record carries no WKT, or WKT PROJ will not parse |
