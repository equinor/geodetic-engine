# Sharing a built database

Only the build needs the register. `GEODETIC_ENGINE_GEOREP_URL`,
`GEODETIC_ENGINE_AUTHORITIES` and the two credentials,
`GEODETIC_ENGINE_GEOREP_CLIENT_ID` and `GEODETIC_ENGINE_GEOREP_CLIENT_SECRET`,
configure `geodetic-projdb build` ({doc}`projdb`). Transformations read the
`proj.db` it writes, never the register. Build it once, centrally, as a wheel
in a private package index, and everyone else installs your organisation's
CRSs and transformations like any other dependency: no Georepository URL, no
authorities to configure, no credentials for the register, and no build that
takes as long as fetching the whole register.

```{admonition} Not executed
:class: note

Building needs a Georepository instance and credentials, and publishing needs
a private package index, so nothing on this page is run during the
documentation build.
```

## Where to publish it

The wheel holds your register's definitions. Publish it only to a package
index that the register's users, and no one else, can read, such as an Azure
Artifacts feed visible to your organisation alone. Not to PyPI, and not from a
public repository, this one included: anyone can read a public repository's
Actions logs, which name objects from the register, and any GitHub user can
download its workflow artifacts.

Give the wheel a name no public index serves, and have installers look for it
in your index alone. Given an extra index, on the command line or in its
configuration, pip takes the highest version it finds on any index, so
whoever publishes the same name on PyPI with a higher version would have their
code installed instead: a dependency confusion attack. Install it as
{ref}`installing-the-database` shows.

## Building it

`scripts/build_organisation_projdb.py` builds the database the way the release
wheel's OSDU database is built ({doc}`/development/index`), checks it before
it writes anything, and writes a wheel:

1. A new virtual environment gets this checkout and the pyproj wheel it pins,
   so the base is that wheel's own `proj.db`: the EPSG content your users
   install.
2. `geodetic-projdb build` imports the register your configuration file
   describes, every response fetched afresh. `geodetic-osdudb build --official
   --append` adds the official OSDU catalogue, because the installed database
   is read in place of the one the wheel ships, and your users would
   otherwise lose it. `scripts/patch-grid-alternatives.sh` then adds the grid
   filename mappings it knows to be missing ({ref}`grid-alternatives`), as
   `scripts/build-projdb.sh` does.
3. The result must open read-only, carry the metadata of the wheel's own
   `proj.db` (PROJ release, database layout, EPSG version), record both
   builds as validated and started from that `proj.db`, and hold CRSs or
   operations of every authority they imported.
4. The wheel is named `--name` and versioned as the PROJ release followed by
   the UTC minute of the build, {{ proj_version }}.202611030405 say, so that
   `<name>==<PROJ release>.*` installs the newest build for one PROJ release.

```bash
uv run --env-file .env python scripts/build_organisation_projdb.py \
    --config geodetic-projdb.toml --name example-projdb --output dist
```

The credentials come from the environment, here read from your `.env`; in CI,
from your secret store. Only the register import gets them. Of the rest of
your environment, the build gets only what Python, pip, `bash` and `sqlite3`
need to run and to reach the network: `PATH`, `HOME`, the temporary
directory, the locale, proxies and certificate bundles, and for pip alone its
`PIP_` settings. Every other setting comes from the configuration file, so
neither a local `PROJ_DATA` nor another setting or secret in `.env` reaches
the build. The script needs Python 3.13 or newer; network access to PyPI, this
project's pyproj index and the register; and `bash` and `sqlite3` for the grid
patch, which `--skip-grid-patch` leaves out.

The wheel installs a package that holds:

| File | What it is |
|---|---|
| `proj_data/proj.db` | The database, alone in its directory, as PROJ reads the `proj.db` of each directory on its search path |
| `proj.db.report.json`, `proj.db.osdudb.report.json` | The register's and the catalogue's build reports ({doc}`projdb`) |
| `proj.db.manifest.json` | The PROJ release, EPSG version and database layout it is for, the pyproj wheel to use it with, the commit of this repository that built it, the SHA-256 of `proj.db`, and how many CRSs and operations each authority contributed |

The wheel names `proj_data` under the entry point group
`geodetic_engine.proj_data`, which is how `geodetic_engine` finds it
({ref}`installed-database`). It has no dependencies, and no code but the two
lines that name the directory.

## Publishing from a workflow

A workflow for the private or internal repository that publishes the wheel.
That repository holds your `geodetic-projdb.toml`, which has no secrets in
it. The workflow rebuilds weekly and on demand, and uploads every build to
your package index.

```yaml
name: proj.db

on:
  schedule:
    - cron: "0 4 * * 1"
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}

jobs:
  build:
    runs-on: ubuntu-latest
    # Holds the secrets; allow only the default branch to deploy to it.
    environment: georepository
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false

      # The credentials reach this code: move the commit deliberately.
      - uses: actions/checkout@v4
        with:
          repository: equinor/geodetic-engine
          ref: <full commit SHA>
          path: geodetic-engine
          persist-credentials: false

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Build the wheel
        env:
          GEODETIC_ENGINE_GEOREP_CLIENT_ID: ${{ secrets.GEODETIC_ENGINE_GEOREP_CLIENT_ID }}
          GEODETIC_ENGINE_GEOREP_CLIENT_SECRET: ${{ secrets.GEODETIC_ENGINE_GEOREP_CLIENT_SECRET }}
        run: >-
          python geodetic-engine/scripts/build_organisation_projdb.py
          --config geodetic-projdb.toml --name example-projdb --output dist

      - name: Upload it to the package index
        env:
          TWINE_REPOSITORY_URL: ${{ vars.PACKAGE_INDEX_UPLOAD_URL }}
          TWINE_USERNAME: ${{ secrets.PACKAGE_INDEX_USERNAME }}
          TWINE_PASSWORD: ${{ secrets.PACKAGE_INDEX_TOKEN }}
        run: |
          python -m pip install --quiet twine
          python -m twine upload --non-interactive dist/*.whl
```

Set it up once:

- Create the environment `georepository`, and limit its deployment branches
  to the default branch. Its secrets are the two Georepository credentials,
  and `PACKAGE_INDEX_USERNAME` and `PACKAGE_INDEX_TOKEN`: an account, and a
  token of it that may publish to the index.
- Set the repository variable `PACKAGE_INDEX_UPLOAD_URL`. For an Azure
  Artifacts feed it is
  `https://pkgs.dev.azure.com/<organisation>/<project>/_packaging/<feed>/pypi/upload/`.
- If your register answers only from your own network, run the job on a
  self-hosted runner there.

When you move the commit to one that pins another PROJ release, the next
build's version starts with that release. The builds for the earlier one stay
in the index, for whoever still runs it.

(installing-the-database)=

## Installing it

Install it beside geodetic-engine and the pyproj wheel it pins
({doc}`/getting-started/installation`), from your index alone. Replace
`<PROJ release>` below with the release pyproj links, {{ proj_version }} for
this release.

With uv, pin it to your index in the `pyproject.toml` of the project that
uses it. An index marked `explicit` serves only the packages pinned to it:

```toml
[project]
dependencies = [
    "pygeodetic-engine",
    "example-projdb==<PROJ release>.*",
]

[[tool.uv.index]]
name = "organisation"
url = "https://pkgs.dev.azure.com/<organisation>/<project>/_packaging/<feed>/pypi/simple/"
explicit = true

[tool.uv.sources]
example-projdb = { index = "organisation" }
```

uv reads the index's credentials from `UV_INDEX_ORGANISATION_USERNAME` and
`UV_INDEX_ORGANISATION_PASSWORD`, or from a keyring; see uv's guides to
[Azure Artifacts](https://docs.astral.sh/uv/guides/integration/azure/) and to
[index authentication](https://docs.astral.sh/uv/concepts/indexes/#authentication).
In CI, set the two variables from a secret. In a container image, give the
build the token as a build secret, never as a build argument, which the image
records.

With pip, install it on its own, from your index only:

```bash
PIP_CONFIG_FILE=/dev/null pip install --isolated --no-deps --index-url \
    https://pkgs.dev.azure.com/<organisation>/<project>/_packaging/<feed>/pypi/simple/ \
    "example-projdb==<PROJ release>.*"
```

`--index-url` replaces only pip's main index: pip still searches an extra
index or `find-links` location set in a `PIP_` variable or in any pip
configuration file, and would take a higher version from there. `--isolated`
ignores the variables and your user configuration, and
`PIP_CONFIG_FILE=/dev/null` (`nul` on Windows) every configuration file.
Proxies in `HTTPS_PROXY`, and credentials in a keyring or `.netrc`, still
apply; give anything else your index needs, such as `--cert`, on the command
line.

The wheel holds no grid files. `validation.grids` in its `proj.db.report.json`
lists every grid the register's transformations read, with the operations that
read it (`used_by`). Install those your transformations need where PROJ finds
them ({doc}`/getting-started/installation`), or set `PROJ_NETWORK=ON` to let
PROJ download those it can fetch; a transformation whose grid is missing raises
{class}`~geodetic_engine.geodesy.MissingGridError`. Your authority's codes
resolve like EPSG's, and the official OSDU catalogue's still do:

```python
from geodetic_engine.geodesy import transform
from geodetic_engine.osdudb import active_database

active_database().reason  # 'example-projdb ... provides it, and nothing chose another database'
transform("YourAuthority:1234", "EPSG:4326", (500000.0, 6650000.0))
```

(installed-database)=

## How geodetic_engine finds it

When `geodetic_engine` is imported, a distribution that names a directory
under the entry point group `geodetic_engine.proj_data` provides the bundled
database: PROJ reads the `proj.db` in that directory in place of the OSDU
database the wheel ships. Everything {doc}`/getting-started/installation` says
of the shipped database applies to it. A database you chose with
`pyproj.datadir.set_data_dir()` or `PROJ_DATA` still wins,
`GEODETIC_ENGINE_OSDU_DB=0` turns it off, and keeps the import from loading
the distribution's module at all,
{func}`~geodetic_engine.osdudb.use_bundled_database` switches it, and
{func}`~geodetic_engine.osdudb.active_database` names it and the distribution
that provides it. Every result's provenance names the file
({doc}`/background/provenance`).

The import passes it over, logs a warning, and reads the shipped database if
it was built for another PROJ release than pyproj links, if PROJ would refuse
its database layout, judged by the `proj.db` that came with that PROJ, if more
than one installed distribution provides a database, or if the entry point
cannot be loaded, its module raising an exception or calling `sys.exit()`, or
names no directory holding a `proj.db`. It passes over every one if the
installed distributions' entry points cannot be read, as when any of them,
however unrelated, has a malformed `entry_points.txt`.

### Keeping it in step with PROJ

A database belongs to the PROJ release whose `proj.db` it was built on. PROJ
cannot open one of another database layout at all, and one of the same layout
but an older EPSG dataset would answer with that dataset without complaint.
So the wheel's version starts with the PROJ release, the pin above selects
the builds for one release, and `geodetic_engine` reads a database only when
it was built for the release pyproj links. When you move to a geodetic-engine
release that pins another PROJ, move the pin with it.
