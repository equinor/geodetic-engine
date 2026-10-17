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
payloads, {doc}`persistable-reference` transforms with them directly, with no
database build.

```{admonition} Not executed
:class: note

A build needs an OSDU `CRS_CT.json` catalogue, which is large and usually
internal, so nothing on this page is run during the documentation build. The
command reference is at {doc}`/cli/geodetic-osdudb`.
```

## The catalogue

OSDU publishes CRSs and transformations as one manifest, usually called
`CRS_CT.json`. Its `ReferenceData` array holds
`reference-data--CoordinateReferenceSystem` and
`reference-data--CoordinateTransformation` records.
{class}`~geodetic_engine.osdudb.OsduCatalog` reads it.

Keep the manifest in `local/osdu/`. That directory is gitignored, and a
catalogue is large and often internal to whoever published it. Give its path on
the command line, as `catalog` in `geodetic-osdudb.toml`, or in
`GEODETIC_ENGINE_OSDU_CATALOG`.

## Running it

```{important}
If you already built a database from Georepository, **add `--append`**. Both
commands default to `build/proj.db`. Without `--append` this build starts from
a fresh copy of the official `proj.db`, and is refused rather than discard the
earlier import. See {doc}`combining`.
```

```bash
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
use. After a build, those ids work wherever an authority code does:

```python
from geodetic_engine.geodesy import CoordinateReferenceSystem, transform

bound = CoordinateReferenceSystem.from_user_input(
    "opendes:reference-data--CoordinateReferenceSystem:"
    "BoundProjected:EPSG::23031_EPSG::1133"
)
bound.crs.is_bound  # True: the CRS carries EPSG:1133

result = transform(
    "opendes:reference-data--CoordinateReferenceSystem:Projected:EPSG::23031",
    "EPSG:4326",
    [(500000.0, 6600000.0)],
    operation="opendes:reference-data--CoordinateTransformation:EPSG::1612",
)
```

{meth}`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_osdu_id` also
accepts a record's bare `data.ID`, such as `Projected:EPSG::23031`.

How an id is matched:

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

The report accounts for every record:

- each id that names nothing in the database is listed with the reason:
  skipped by the build, not in the base `proj.db` (`--authority EPSG` imports
  it), or the record states no id;
- an id the catalogue publishes for two different objects is recorded for
  neither;
- where the catalogue and `proj.db` disagree about an object, usually an EPSG
  rename, it is reported and the id still names the object.

Appending the same catalogue again changes nothing. Appending one that would
move an id to a different object, because OSDU renumbered a bound CRS for
example, is refused unless `--overwrite-rows` is given, so an id never silently
starts resolving to something else. The superseded object itself stays in the
database.

The ids are stored in a table this package adds,
`geodetic_engine_object_identifier`, keyed by scheme (`OSDU`) and id, and
naming each object by table, authority and code in the vocabulary of PROJ's
`object_view`. It joins with PROJ's own tables for listing and searching, for
example every OSDU CRS within an extent. `geodetic-osdudb inspect` counts the
ids by scheme.

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

## Errors

On top of the {doc}`projdb` errors it shares:

| Exception | Raised when |
|---|---|
| {class}`~geodetic_engine.osdudb.OsduCatalogError` | The catalogue file cannot be read as an OSDU manifest |
| {class}`~geodetic_engine.osdudb.UnreadableDefinitionError` | A record carries no WKT, or WKT PROJ will not parse |
