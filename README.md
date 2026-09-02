# geodetic-engine

``geodetic-engine`` is an open-source Python library for coordinate reference system (CRS) management and coordinate transformations.


Built on top of PROJ, it provides a unified interface for transforming coordinates between geographic, projected, engineering, and vertical reference systems. The library supports custom CRS definitions and transformation catalogs, making it suitable for enterprise, scientific, and geospatial workflows.


Features:

- High-accuracy coordinate transformations using PROJ

- Custom CRS and transformation database support

- Geographic, projected, engineering, and vertical CRS support

- WKT, EPSG, and PROJ string interoperability

- Integration-friendly Python API

- Extensible architecture for organization-specific geodetic definitions


## Development environment

The environment is pinned deliberately, because the EPSG dataset baked into
`proj.db` is part of every answer this library gives. Two properties must hold,
and both are asserted by `tests/test_environment.py`:

- PROJ is **9.8.1**, built from source rather than installed from a package
  manager.
- `pyproj` is built against *that* PROJ. The PyPI wheels bundle their own copy
  of PROJ, which would silently override the pin.

### Using the devcontainer

Open the repository in the devcontainer and everything below is done for you.
`.devcontainer/Dockerfile` builds PROJ, and `postCreateCommand` installs the
Python dependencies and runs the environment checks.

### Reproducing it by hand

```bash
# 1. Build dependencies for PROJ.
sudo apt-get install -y build-essential cmake ninja-build pkg-config \
    libsqlite3-dev sqlite3 libtiff-dev libcurl4-openssl-dev zlib1g-dev

# 2. Build and install PROJ 9.8.1. The script pins the version and verifies the
#    tarball checksum before building.
./.devcontainer/install-proj.sh

# 3. Install Python dependencies, building pyproj from source against it.
PROJ_DIR=/usr/local PROJ_WHEEL=false uv sync --extra dev

# 4. Verify.
uv run pytest tests/test_environment.py
```

`install-proj.sh` configures PROJ with `-DEMBED_RESOURCE_FILES=OFF`. That flag
is load-bearing: when `proj.db` is embedded into `libproj`, a custom database on
disk can be silently ignored, which would defeat the workflow below.

`pyproj` is pinned by commit SHA in `[tool.uv.sources]` rather than in
`[project.dependencies]`, because PyPI rejects direct reference URLs in
published package metadata.

Verify an existing environment at any time:

```bash
uv run python -c "import pyproj; print(pyproj.proj_version_str, pyproj.datadir.get_data_dir())"
# 9.8.1 /usr/local/share/proj
```

## Building a custom PROJ database

### What this is, and when you need it

PROJ ships an official `proj.db` built from the EPSG dataset. If your
organisation maintains its own CRSs, datums or transformations in a
Georepository instance, PROJ cannot see them, and any attempt to use them fails
with "unknown code".

`geodetic-engine` builds an **enriched** copy of `proj.db` that adds your
authority's objects to the official database. You need it if, and only if, you
have authority-specific geodetic objects that are not in EPSG.

The workflow is deliberately conservative:

- The official `proj.db` is **copied, never modified in place**.
- Only objects belonging to your configured authorities are added. Any attempt
  to write a row owned by EPSG, PROJ or ESRI **aborts the build**.
- Objects the official database already defines are **not** re-imported. EPSG
  stays authoritative for EPSG.
- Every imported CRS and coordinate operation must be constructible by PROJ from
  the finished database, or the build fails.
- Nothing is published anywhere. The output is a local file that you distribute
  however you choose.

### What validation checks, and what it does not

Validation asks two questions of the finished file: is it structurally sound
(`integrity_check`, `foreign_key_check`), and can PROJ construct every object
that was imported? CRSs go through `CRS.from_authority` and operations through
`CoordinateOperation.from_authority`, which exercises each operation's method,
parameters and units without needing a `Transformer`.

Two things are deliberately **not** treated as build failures, because neither
is a property of the database:

- **A grid file that is not installed.** A grid transformation is a correct
  entry whether or not the grid is on the machine that built the database, and
  it may well be present, or fetchable, wherever the database is used. Every
  referenced grid is listed in the build report with its availability, and
  missing ones are logged as a warning.
- **A CRS that only reaches WGS 84 by a ballpark step.** ETRS89 and WGS 84 are
  separate ensembles with no operation between them, so an ETRS89-based CRS is
  legitimately ballpark-only to WGS 84. Refusing ballpark results belongs at
  transformation time, in the layer that serves coordinates, where
  `allow_ballpark=False` applies to an actual requested transformation.

The package installed from PyPI uses **stock PROJ with the EPSG dataset only**.
Building a custom database is an explicit, opt-in step.

### Configuration

There are two files. Everything that describes *what to build* goes in a config
file; only credentials go in `.env`, which must never be committed.

```bash
cp geodetic-projdb.example.toml geodetic-projdb.toml   # edit this
cp .env.example .env                                   # put the two secrets here
uv run geodetic-projdb config                          # check what was resolved
```

`geodetic-projdb.toml` is picked up automatically from the working directory, so
no flag is needed. Use `--config` or `GEODETIC_ENGINE_CONFIG` to point elsewhere.
It is a local file; keeping it is entirely up to you. Because the loader
**rejects** `client_id` and `client_secret` if it finds them there, it holds no
secrets, so it is safe to check in if you automate the rebuild and want the
definition reviewable. Nothing requires you to.

`geodetic-projdb config` prints the resolved settings, which file each came
from, and whether credentials were found, without printing the credentials
themselves. Run it first when something is not being picked up.

A misspelled setting is an error rather than being ignored, because a silently
ignored typo is a setting you believe is applied when it is not.

The minimum needed in `geodetic-projdb.toml`:

```toml
[projdb]
api_url = "https://georepository.example.com"
authorities = ["YourAuthority"]
output_db = "build/proj.db"
```

And in `.env`:

```bash
GEODETIC_ENGINE_GEOREP_CLIENT_ID=<from your administrator>
GEODETIC_ENGINE_GEOREP_CLIENT_SECRET=<from your administrator>
```

See geodetic-projdb.example.toml for every setting, what it does, and its
default. Any of them can also be set as an environment variable prefixed with
`GEODETIC_ENGINE_`, which takes precedence over the file; prefer the file for
anything permanent so the setting stays reviewable.

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
| `base_proj_db` | `GEODETIC_ENGINE_BASE_PROJ_DB` | no | the linked PROJ's `proj.db` |
| `unsupported_method_codes` | `GEODETIC_ENGINE_UNSUPPORTED_METHOD_CODES` | no | `1044,1108` |
| `page_size` | `GEODETIC_ENGINE_PAGE_SIZE` | no | `500` |
| `georepository_version` | `GEODETIC_ENGINE_GEOREP_VERSION` | no | - |

### Why deprecated objects are imported by default

Deprecated objects are imported with `deprecated = 1` and, where the authority
records a replacement, a row in proj.db's `supersession` table. This is what
lets a caller validating user input answer "that code is deprecated, superseded
by X" rather than "CRS not found".

### Authority preference, and why it matters

PROJ decides which authorities' coordinate operations are even *candidates* for
a CRS pair by consulting `authority_to_authority_preference`. Without a rule
naming your authority, PROJ will not consider your transformations, and they are
effectively invisible unless a custom CRS is named directly.

Because these rules change which operation is applied to a coordinate, the
behaviour is explicit configuration rather than a silent default:

| `GEODETIC_ENGINE_AUTHORITY_PREFERENCE` | Effect |
| --- | --- |
| `custom_first` (default) | Your operations are preferred for pairs involving your authority, and appended as last-resort candidates to PROJ's shipped rules for other pairs. `EPSG,EPSG` becomes `PROJ,EPSG,NKG,YourAuthority`. |
| `custom_only` | Your operations are preferred for pairs involving your authority. Selection between other authorities is left exactly as PROJ ships it. |
| `none` | No rules are written at all. |

A shipped rule that already names your authority is left untouched, and no
authority is ever listed twice. Every rule written is recorded in the build
report under `authority_preferences`.

### Aliases

An alias is your organisation's own name for an object, stored in proj.db's
`alias_name` so the object can be looked up by either name. Aliases are
imported for every object type proj.db accepts one for, including datums, and
only for the naming systems listed in `GEODETIC_ENGINE_NAMING_SYSTEMS`.

### Obtaining OAuth2 credentials

Ask your Georepository administrator for a **client credentials** registration.
You need:

- a client id and client secret for the machine account,
- the client granted the API scope, `GeoRepositoryAPI_Scope` unless your
  instance differs,
- the token endpoint URL, if it is not `{your-instance}/auth/connect/token`.

The Georepository OpenAPI document advertises an *implicit* flow, which is for
interactive browser use. Server-to-server callers such as this one use the
client credentials grant against the identity server.

### Handling secrets safely

Credentials are only ever read from the environment or from a `.env` file. They
are **rejected** if found in the config file, and they cannot be passed as
command line arguments, because both routinely end up in version control, shell
history and process listings. `ProjDbBuildConfig.__repr__` redacts them so
configurations can be logged.

- Locally, put them in `.env`, which is gitignored. Start from `.env.example`.
- In CI, do not create a `.env`. Provide the same two variables from your secret
  store as environment variables for the build step only.
- Never commit credentials, and do not commit realistic-looking example values.

### Running it

```bash
# Run the whole build and report what it would write, keeping nothing.
uv run geodetic-projdb build --dry-run

# Build, validate, and write a provenance report next to the database.
uv run geodetic-projdb build

# Show the resolved settings and where each file was found.
uv run geodetic-projdb config

# Build from a config file elsewhere, skipping the PROJ round-trip checks.
uv run geodetic-projdb build --config /etc/geodetic-projdb.toml --skip-validation

# Check an existing database.
uv run geodetic-projdb validate build/proj.db --authority YourAuthority

# Summarise a database.
uv run geodetic-projdb inspect build/proj.db
```

`--dry-run` performs the entire build, including every foreign key, collision
and authority check, and then discards it rather than committing. It exercises
the same code as a real build rather than approximating it, so a dry run that
succeeds means a real build would too.

Or from Python:

```python
from geodetic_engine.projdb import build, load_config, validate

config = load_config()
report = build(config)
report.validation = validate(config.output_db, authorities=config.authorities)
print(report.to_json())
```

### Using the result

The enriched database is a drop-in replacement for the official one. Point PROJ
at the directory containing it, keeping the installed PROJ directory on the
search path so grids and `proj.ini` are still found:

```bash
export PROJ_DATA="/path/to/build:/usr/local/share/proj"
```

```python
import os
from pyproj import datadir

datadir.set_data_dir(os.pathsep.join(["/path/to/build", datadir.get_data_dir()]))
```

### Provenance

Every build writes `<output>.report.json` recording the PROJ version, the EPSG
dataset version, the proj.db layout version, the Georepository instance and
version, every imported object, every skipped object with its reason, every
supersession that was written or dropped, and every authority preference rule
applied. A transformation produced from this database can therefore be traced
back to the inputs that defined it.

## Talking to a Georepository instance directly

The API client is a package in its own right, so it can be used without
building a database at all:

```python
from geodetic_engine.georepository import GeorepositoryClient, GeorepositoryConfig

config = GeorepositoryConfig(
    api_url="https://georepository.example.com",
    client_id=...,
    client_secret=...,
)
with GeorepositoryClient(config) as client:
    for datum in client.iter_collection("Datum", authorities={"YourAuthority"}):
        print(datum["Code"], datum["Name"])
```

Paging is handled for you and is verified: if the server advertises more results
than it returns, or ignores the `page` parameter, the client raises
`PaginationTruncatedError` rather than returning a silently incomplete list.

