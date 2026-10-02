# Custom database from Georepository

`geodetic_engine.projdb` and the `geodetic-projdb` command build an **enriched
copy of PROJ's `proj.db`**. It adds the CRSs, datums and transformations a
Georepository register defines under your organisation's own authority.

**Use it when** you have geodetic objects that are not in EPSG. PROJ cannot see
them, and any attempt to use them fails with "unknown code". The enriched
database makes them usable by code, like EPSG objects, with this package or
with plain PROJ.

**You do not need it** for EPSG definitions. The official `proj.db` already
has them.

```{admonition} Not executed
:class: note

Building needs a Georepository instance and credentials, so nothing on this
page is run during the documentation build. The command reference, generated
from the real argument parser, is at {doc}`/cli/geodetic-projdb`.
```

## What a build does, and does not do

The workflow is deliberately conservative:

- The official `proj.db` is **copied, never modified in place**.
- Only objects belonging to your configured authorities are added. Any attempt
  to write a row owned by an authority you did not configure **aborts the
  build**.
- Objects the official database already defines are **not** re-imported. EPSG
  stays authoritative for EPSG.
- Every imported CRS and coordinate operation must be constructible by PROJ
  from the finished database, or the build fails.
- Nothing is published anywhere. The output is a local file that you
  distribute however you choose.

The same writer, frozen schema and validator are used by
{doc}`osdudb`, so the two sources produce the same kind of artefact and can
share one file ({doc}`combining`).

## Quick start

```bash
cp geodetic-projdb.example.toml geodetic-projdb.toml   # edit this
cp .env.example .env                                   # put the two secrets here
uv run geodetic-projdb config                          # check what was resolved
uv run geodetic-projdb build                           # build, validate, publish
```

The minimum `geodetic-projdb.toml`:

```toml
[projdb]
api_url = "https://georepository.example.com"
authorities = ["YourAuthority"]
output_db = "build/proj.db"
```

And `.env`:

```bash
GEODETIC_ENGINE_GEOREP_CLIENT_ID=<from your administrator>
GEODETIC_ENGINE_GEOREP_CLIENT_SECRET=<from your administrator>
```

## Configuration

Two files. Everything that describes *what to build* goes in the config file.
Only credentials go in `.env`, which must never be committed.

`geodetic-projdb.toml` is read from the working directory automatically. Use
`--config` or `GEODETIC_ENGINE_CONFIG` to point elsewhere. The loader
**rejects** `client_id` and `client_secret` in this file, so it holds no
secrets and can be checked in if you want the build definition reviewed.

A misspelled setting is an error, not ignored. A silently ignored typo is a
setting you think applies when it does not.

Every setting can also be given as an environment variable, which overrides
the file. Prefer the file for anything permanent so it stays reviewable.
`geodetic-projdb config` prints the resolved settings and which file each came
from, without printing credentials. Run it first when something is not being
picked up.

| Setting | Environment variable | Required | Default |
| --- | --- | --- | --- |
| `api_url` | `GEODETIC_ENGINE_GEOREP_URL` | yes | - |
| `authorities` | `GEODETIC_ENGINE_AUTHORITIES` | yes | - |
| `output_db` | `GEODETIC_ENGINE_OUTPUT_DB` | yes | - |
| - | `GEODETIC_ENGINE_GEOREP_CLIENT_ID` | yes | - |
| - | `GEODETIC_ENGINE_GEOREP_CLIENT_SECRET` | yes | - |
| `token_url` | `GEODETIC_ENGINE_GEOREP_TOKEN_URL` | no | `{api_url}/auth/connect/token` |
| `scope` | `GEODETIC_ENGINE_GEOREP_SCOPE` | no | `GeoRepositoryAPI_Scope` |
| `authority_preference` | `GEODETIC_ENGINE_AUTHORITY_PREFERENCE` | no | `custom_first` |
| `fallback_authorities` | `GEODETIC_ENGINE_FALLBACK_AUTHORITIES` | no | `PROJ,EPSG` |
| `include_deprecated` | `GEODETIC_ENGINE_INCLUDE_DEPRECATED` | no | `true` |
| `naming_systems` | `GEODETIC_ENGINE_NAMING_SYSTEMS` | no | same as `authorities` |
| `annotate_foreign_objects` | `GEODETIC_ENGINE_ANNOTATE_FOREIGN_OBJECTS` | no | `true` |
| `base_proj_db` | `GEODETIC_ENGINE_BASE_PROJ_DB` | no | the linked PROJ's `proj.db` |
| `unsupported_method_codes` | `GEODETIC_ENGINE_UNSUPPORTED_METHOD_CODES` | no | `1044,1108` |
| `page_size` | `GEODETIC_ENGINE_PAGE_SIZE` | no | `500` |
| `georepository_version` | `GEODETIC_ENGINE_GEOREP_VERSION` | no | - |

The annotated example file, with every setting, is on
{doc}`/cli/geodetic-projdb`.

What the less obvious settings do, and why the defaults are what they are:

- `authority_preference`, `fallback_authorities`:
  {ref}`authority-preference`.
- `include_deprecated`: {ref}`deprecated-objects`.
- `naming_systems`: {ref}`aliases`.
- `annotate_foreign_objects`: {ref}`annotations`.

(projdb-secrets)=

## Handling secrets safely

Credentials are only read from the environment or from a `.env` file. They are
**rejected** in the config file and cannot be given on the command line,
because both routinely end up in version control, shell history and process
listings. `ProjDbBuildConfig.__repr__` redacts them, so configurations can be
logged.

- Locally, put them in `.env`, which is gitignored. Start from `.env.example`.
- In CI, do not create a `.env`. Provide the same two variables from your
  secret store as environment variables for the build step only.
- Never commit credentials, and do not commit realistic-looking example
  values.

See {ref}`georepository-credentials` for what to ask your administrator for.

## Running it

A normal build validates the staged database before replacing the output. A
failed build leaves any existing output database unchanged.

```bash
# Build, validate, and write a provenance report next to the output.
uv run geodetic-projdb build

# Inspect the resolved settings. Credentials are reported only as present or missing.
uv run geodetic-projdb config

# Use a configuration file elsewhere. --skip-validation skips the PROJ
# round-trip check; use it only when you understand the trade-off.
uv run geodetic-projdb build --config /etc/geodetic-projdb.toml --skip-validation

# Validate an existing database for an authority.
uv run geodetic-projdb validate build/proj.db --authority YourAuthority

# Summarise a database.
uv run geodetic-projdb inspect build/proj.db
```

### Dry run

```bash
uv run geodetic-projdb build --dry-run
```

Runs the full import and validation, then discards the staged database.
Responses are fetched afresh and cached only in memory. The persistent response
cache is neither read nor written. It takes as long as a full build and gives
**no warm start** for the next one. To check settings and credential presence
without contacting the API, use `geodetic-projdb config`.

### From Python

```python
from geodetic_engine.projdb import build, load_config

config = load_config()            # same resolution as the CLI: file, env, overrides
report = build(config)            # also writes <output>.report.json; dry_run=True to rehearse
print(report.status, len(report.imported), "imported,", len(report.skipped), "skipped")
```

{func}`~geodetic_engine.projdb.load_config` takes keyword overrides that win
over every other source, for example `load_config(output_db=Path("/tmp/proj.db"))`.
{func}`~geodetic_engine.projdb.build` returns a
{class}`~geodetic_engine.projdb.BuildReport`.
{func}`~geodetic_engine.projdb.validate` checks an existing database on its
own.

## What validation checks, and what it does not

Validation asks two questions of the finished file. Is it structurally sound
(`integrity_check`, `foreign_key_check`)? Can PROJ construct every object that
was imported? CRSs go through `CRS.from_authority` and operations through
`CoordinateOperation.from_authority`. This exercises each operation's method,
parameters and units without needing a `Transformer`. Validation runs in an
isolated subprocess, so it cannot change the caller's PROJ context.

Two things are deliberately **not** build failures, because neither is a
property of the database:

- **A grid file that is not installed.** A grid transformation is a correct
  entry whether or not the grid is on the machine that built the database. It
  may well be present, or fetchable, wherever the database is used. Every
  referenced grid is listed in the build report with its availability, and
  missing ones are logged as a warning. A grid that is installed but still
  reported missing usually lacks a filename mapping; see
  {ref}`grid-alternatives`.
- **A CRS that reaches WGS 84 only by a ballpark step.** ETRS89 and WGS 84
  are separate ensembles with no operation between them, so an ETRS89-based CRS
  is legitimately ballpark-only to WGS 84. Refusing ballpark results is the job
  of the transformation layer, which refuses them for every actual
  transformation ({doc}`geodesy/errors`).

## How objects are imported

(deprecated-objects)=

### Deprecated objects are imported by default

Deprecated objects are imported with `deprecated = 1`. Where the authority
records a replacement, a row is added to `proj.db`'s `supersession` table. This
lets a caller validating user input answer "that code is deprecated, superseded
by X" rather than "CRS not found".

(authority-preference)=

### Authority preference

PROJ decides which authorities' operations are *candidates* for a CRS pair by
consulting `authority_to_authority_preference`. Without a rule naming your
authority, PROJ does not consider your transformations, and they are
invisible unless a custom CRS is named directly.

These rules change which operation is applied to a coordinate, so they are
explicit configuration, not a silent default:

| `authority_preference` | Effect |
| --- | --- |
| `custom_first` (default) | Your operations are preferred for pairs involving your authority, and added as last-resort candidates to PROJ's shipped rules for other pairs. `EPSG,EPSG` becomes `PROJ,EPSG,NKG,YourAuthority`. |
| `custom_only` | Your operations are preferred for pairs involving your authority. Selection between other authorities is left as PROJ ships it. |
| `none` | No rules are written. |

A shipped rule that already names your authority is left untouched, and no
authority is listed twice. Every rule written is recorded in the build report
under `authority_preferences`. The values are
{class}`~geodetic_engine.projdb.AuthorityPreference`.

(aliases)=

### Aliases

An alias is your organisation's own name for an object. It is stored in
`proj.db`'s `alias_name` table so the object can be looked up by either name.
Aliases are imported for every object type `proj.db` accepts one for,
including datums, and only for the naming systems listed in `naming_systems`.

(annotations)=

### Annotations on other authorities' objects

A register records more than its own objects. It also records what your
organisation calls `EPSG:32632` and what that CRS is used for in your context,
as an alias and as a usage whose scope belongs to your authority rather than
to EPSG. Those rows are imported too, because looking objects up by a local
name is much of the reason for building a custom database.

Nothing about the annotated object is rewritten. The EPSG row stays exactly as
PROJ shipped it. Only `alias_name` and `usage` gain rows pointing at it. An
object the database does not already hold is skipped rather than annotated, so
no usage row can dangle.

This pass has to enumerate every CRS in the register, because the annotation
lives on the object and the API has no server-side filter for it. It is the
slowest part of a build. Turn it off with `annotate_foreign_objects = false`.

### Bound CRSs

A register's bound CRSs are written as `BOUNDCRS` WKT in the `text_definition`
column of an ordinary CRS row, because `proj.db` has no bound CRS table. A
bound CRS over a concatenated transformation is first collapsed into one
equivalent Helmert step, and skipped with a reason if that is not possible.
{doc}`/background/bound-crs` explains both, and why reading them back by code
needs a workaround.

### Methods PROJ cannot run

Operations whose EPSG method code is in `unsupported_method_codes` are skipped
and listed in the build report. These are methods this PROJ build cannot
evaluate, so a row using one could never be applied. The default is
`[1044, 1108]`.

## The build report

Every build writes `<output>.report.json` beside the database, and records the
same report in the database's append-only `geodetic_engine_build_history`
table. The report records:

- the PROJ version, EPSG dataset version and `proj.db` layout version;
- where the definitions came from, and the register's version of each dataset;
- every object imported, and every object skipped with its reason;
- every supersession written or dropped;
- every authority preference rule applied;
- the validation result.

See {doc}`/background/provenance` for how this links a transformed coordinate
back to the register entry that defined it.

## Errors

All derive from {class}`~geodetic_engine.projdb.ProjDbBuildError`.

| Exception | Raised when |
|---|---|
| {class}`~geodetic_engine.projdb.ConfigurationError` | The configuration is missing or inconsistent, or credentials appear in the config file |
| {class}`~geodetic_engine.projdb.ForeignAuthorityCollision` | A row would overwrite an object belonging to another authority |
| {class}`~geodetic_engine.projdb.errors.OutputWouldBeDiscarded` | A fresh build would drop authorities an existing output database holds; see {doc}`combining` |
| {class}`~geodetic_engine.projdb.SchemaDriftError` | The base `proj.db` does not have the columns the writer expects |
| {class}`~geodetic_engine.projdb.MissingReferencedObjectError` | An imported object references one that is neither imported nor in the database |

{class}`~geodetic_engine.projdb.MissingGridError`,
{class}`~geodetic_engine.projdb.BallparkOnlyOperationError` and
{class}`~geodetic_engine.projdb.UnsupportedMethodError` are exported but the
current builder does not raise them. Missing grids and ballpark-only CRSs are
reported, not treated as failures (see above), and unsupported methods are
skipped. `OutputWouldBeDiscarded` is not exported from
`geodetic_engine.projdb`. Import it from `geodetic_engine.projdb.errors`.
