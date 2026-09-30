# Provenance

Every coordinate this package produces can be traced back through three
records: the result, the database, and, for a custom database, its build
report.

## On every result

A {class}`~geodetic_engine.geodesy.TransformationResult` records:

- the source and target CRSs, with their full WKT in the serialised form;
- the operation requested and the operation applied, its method, accuracy and
  route ({class}`~geodetic_engine.geodesy.AppliedOperation`);
- every grid read, with its path and availability;
- the coordinate epoch, if one was used;
- the exact PROJ pipeline, which plain pyproj can replay;
- SHA-256 fingerprints of every `proj.db` PROJ was reading.

The fingerprints are what make a result auditable after the database changes.
The same EPSG code can have different parameters in two `proj.db` files, and
the fingerprint says which one answered.
{doc}`/user-guide/geodesy/results` shows all of this with real output.

Operation metadata includes `execution_direction`, relative to the raw
operation definition rather than the registry entry. A chained operation run in
reverse keeps its forward definition with `execution_direction="INVERSE"`, and
its `operation.to_wkt()` returns None rather than exporting the wrong
direction. Use the result's `pipeline` to replay the whole transformation,
including inversions and surrounding conversions.

## In a custom database

Builds use a locked staging database and validate it in an isolated subprocess
before atomically replacing the published database. A failed build or a dry run
leaves the previous database unchanged. Python
{func}`~geodetic_engine.projdb.build` calls follow the same workflow as the
command line. `skip_validation=True` is an explicit opt-out, and it is
recorded.

Each database contains an append-only `geodetic_engine_build_history` table
with the report of every build that wrote to it. The sidecar
`<output>.report.json` files are convenience exports of the same reports. A
report records:

- the PROJ version, EPSG dataset version and `proj.db` layout version;
- where the definitions came from, and at which register version;
- every imported object, and every skipped object with its reason;
- every supersession written or dropped;
- every authority preference rule applied;
- the validation outcome, including grids that were not installed.

So a transformation result produced from a custom database can be traced back
to the register entries that defined it: the result's fingerprint identifies
the database, and the database's history identifies the inputs.

## Caching and the search path

Resolution caches are keyed by database path and generation, so a changed
search path is never answered from a stale cache. Applications that change
PROJ's global search path must still serialise that change against their own
pyproj calls. Validation runs in a subprocess and does not change the caller's
PROJ context.

Publishing keeps an existing output file's permission bits on rebuilds and
appends. New output databases are private by default (`0600` on POSIX). Grant
access explicitly when sharing one with another account. Ownership and ACLs are
not copied to the replacement file.
