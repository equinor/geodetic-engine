# Installation

`geodetic-engine` needs Python 3.13 or newer, **PROJ {{ proj_version }}**, and
**pyproj {{ pyproj_version }} built against that PROJ**.

## Install the released package

```bash
pip install "pygeodetic-engine" "pyproj==3.8.0+proj9.9.0" \
    --extra-index-url https://equinor.github.io/geodetic-engine/simple/
```

The explicit pyproj requirement selects the matching wheel even when another
pyproj 3.8.0 is already installed. The same arguments work with
`uv pip install`. The wheel workflow builds CPython 3.13, 3.14 and 3.15
wheels for manylinux x86-64 and aarch64, macOS arm64 and x86-64, and Windows
AMD64. If the index has no wheel for your Python or platform, build PROJ from
source as described below, then install pyproj against it:

```bash
PROJ_DIR=/usr/local PROJ_WHEEL=false python -m pip install \
    --force-reinstall --no-binary=pyproj "pyproj==3.8.0"
python -m pip install "pygeodetic-engine"
```

Set `PROJ_DIR` to the actual PROJ installation prefix. On Windows, set
`PROJ_DIR` and `PROJ_WHEEL` in the environment before running pip. The package
does not restrict newer Python versions merely because a wheel is unavailable.
The source-built test suite runs on CPython 3.13 and 3.15.

The wheel includes the committed official OSDU catalogue. To build it into a
database without a repository checkout or network access, use
`geodetic-osdudb build --official`; see {doc}`/user-guide/osdudb`.


## Why the versions are pinned

Every answer this library gives depends on the EPSG dataset inside PROJ's
`proj.db`. A different PROJ release has a different dataset: other operations,
other accuracies, sometimes other parameters. To get the same coordinates
everywhere, the PROJ version has to be the same everywhere.

The pyproj wheels on PyPI bundle their own copy of PROJ, which may not be the
pinned release. This project's index supplies wheels built against the pinned
PROJ; a source build against that PROJ works too. Importing `geodetic_engine`
checks the linked PROJ version and fails with installation guidance if it does
not match. For development, `pyproject.toml` asks `uv` to build pyproj from
source with `no-binary-package = ["pyproj"]`.

`tests/test_environment.py` checks the source-built development environment
before anything else runs:

- pyproj is {{ pyproj_version }} and PROJ is {{ proj_version }};
- PROJ's data directory is not a copy inside `site-packages`;
- the grids the test suite and these docs use (`us_nga_egm08_25.tif`,
  `no_kv_href2008a.tif`, `us_noaa_conus.tif`) are installed;
- `EPSG:4326` is declared latitude-first, so PROJ has not been configured to
  normalise axis order behind your back.

## Option 1: the devcontainer (recommended)

Open the repository in VS Code and choose **Reopen in Container**.
`.devcontainer/Dockerfile` builds PROJ from source with the `proj-data` grid
package. After the container is created, `postCreateCommand` installs the
Python dependencies, installs the official OSDU catalogue (below) and runs the
environment check. CI builds and tests in the same image, so your results match
CI's.

### The OSDU catalogue, installed by default

On creation the devcontainer runs `scripts/install-osdu-projdb.sh`. It builds
the official OSDU catalogue into `build/proj.db`: the `CRS_CT.json` OSDU
publishes, committed in this repository as
`catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json`, byte for byte, with the
OSDU commit it came from recorded beside it. Nothing is downloaded. That
database is what PROJ reads: `PROJ_DATA` lists `local/proj-data` first, and
its `proj.db` links to `build/proj.db`. OSDU CRSs, bound CRSs and
transformations, OSDU record ids and the catalogue's persistable references
then resolve with no further setup ({doc}`/user-guide/osdudb`). The installed
`proj.db` is not modified.

**A failure never fails container creation.** If the build fails, the script
prints a warning that says why. A build is published only once it succeeds,
so an earlier `build/proj.db` stays in use, with the OSDU catalogue it holds.
Without one, the warning says that no OSDU objects are available,
`local/proj-data/proj.db` links to the stock database, and everything else
works as usual. The same status is reported again on every start. To retry:

```bash
scripts/install-osdu-projdb.sh
```

A rebuilt container reuses `build/proj.db` when it already holds the committed
catalogue. When a pull brings a newer one, the next start says so and the next
run imports it. `--force` imports it again regardless. CI builds the
documentation the same way, and runs the tests with the stock database only.

A `build/proj.db` built for another PROJ release or database layout than the
installed one, after the container's PROJ is upgraded for example, is never
linked: PROJ cannot open a database of another layout at all, and every
lookup, `EPSG:4326` included, would fail. On start PROJ reads the stock
database instead and a note says so; the next run moves the old database to
`build/proj.db.stale` and builds the official catalogue from scratch. Anything
another source imported into it, such as the Georepository register, has to
be built again with `scripts/build-projdb.sh`.

To move to the newest catalogue OSDU publishes, download it over the committed
one, then review and commit the change:

```bash
scripts/install-osdu-projdb.sh --update
git diff --stat catalogs/osdu/
```

`--update` replaces the committed catalogue only once the new one has arrived
whole, matches the digest OSDU's server states and reads as a manifest. If it
fails, for example with no network, the script warns and builds the committed
catalogue instead. `--ref` fetches a given OSDU revision rather than the
newest.

An organisation's own catalogue goes in `local/catalogs/custom/`. It is built
on top of the official one on every run: its own CRSs are added, and a record
it states differently replaces the official definition, named as `REPLACED` in
the build summary. See {ref}`osdu-custom-catalogue`.

## Option 2: by hand

```bash
# 1. Build dependencies for PROJ.
sudo apt-get install -y build-essential cmake ninja-build pkg-config \
    libsqlite3-dev sqlite3 libtiff-dev libcurl4-openssl-dev zlib1g-dev

# 2. Build and install PROJ. The script pins the version and verifies the
#    tarball checksum before building.
./.devcontainer/install-proj.sh

# 3. Install the Python dependencies, building pyproj from source against it.
PROJ_DIR=/usr/local PROJ_WHEEL=false uv sync --extra dev

# 4. Verify.
uv run pytest tests/test_environment.py

# 5. Optional: the official OSDU catalogue. Put local/proj-data first on
#    PROJ_DATA so PROJ reads the database it links to.
scripts/install-osdu-projdb.sh
export PROJ_DATA="$PWD/local/proj-data:/usr/local/share/proj"
```

`install-proj.sh` configures PROJ with `-DEMBED_RESOURCE_FILES=OFF`. Do not
remove that flag. With `proj.db` embedded in `libproj`, PROJ can ignore a custom
database on disk, and {doc}`custom databases </user-guide/projdb>` would stop
working.

## Checking an existing environment

```bash
python -c "import geodetic_engine, pyproj; print(pyproj.proj_version_str, pyproj.datadir.get_data_dir())"
```

The first value should be {{ proj_version }}, the second PROJ's data directory.
The project's wheel keeps its data under `site-packages`; a source build may
use `/usr/local/share/proj`. A different PROJ version raises an error when the
package is imported.

## Grids

Grid-based operations (NTv2, NADCON, geoid models) read grid files from PROJ's
data directory. The devcontainer installs the whole `proj-data` package, about
800 MB. Elsewhere, fetch individual grids with PROJ's own tool,
`projsync --file <name>`.

The custom wheel honors `PROJ_DATA`. If it names a custom `proj.db`, put that
database's directory first and grid-only directories after it. If it names only
grid directories, the wheel keeps its bundled database and adds those
directories to the search path. An explicit `pyproj.datadir.set_data_dir`
continues to take precedence.

If a grid an operation needs is missing, this package raises
{class}`~geodetic_engine.geodesy.MissingGridError` and names the file. It never
falls back to an operation that does not use the grid; see
{doc}`/user-guide/geodesy/errors`.

To use grids that are not in `proj-data`, put them in `local/grids/`. The
devcontainer lists that directory after the installed one on `PROJ_DATA`, so a
grid dropped there is found under its own filename; the installed data
directory and its `proj.db` are never modified, and `tests/test_environment.py`
fails if they are. A grid an EPSG operation names by a legacy filename PROJ has
no `grid_alternatives` row for needs a patched database: `build/proj.db` from
`scripts/build-projdb.sh` carries the patch, and so does the OSDU database the
devcontainer installs. Alternatively, run
`.devcontainer/link-local-grids.sh --patched-copy`, which writes a patched copy
of the stock database to `local/proj-data/proj.db` in its place (see
{doc}`/workarounds`). `local/` is gitignored, so these grids are not available
in CI.

## Optional extras

| Extra | Installs | Needed for |
|---|---|---|
| `dev` | pytest, ruff, mypy, ipykernel, pandas-stubs | Running the tests and checks |
| `docs` | Sphinx, the Furo theme, myst-nb and extensions | Building these pages; see {doc}`/development/index` |
