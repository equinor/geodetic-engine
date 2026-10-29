# Installation

`geodetic-engine` needs Python 3.13 or newer, **PROJ {{ proj_version }}**, and
**pyproj {{ pyproj_version }} built against that PROJ**.

## Install the released package

```bash
pip install "pygeodetic-engine" "pyproj==3.8.0+proj9.9.0" \
    --extra-index-url https://equinor.github.io/geodetic-engine/simple/
```

The explicit pyproj requirement selects the matching wheel even when PyPI's
build of the same pyproj release is already installed. The same arguments work with
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

The wheel includes the committed official OSDU catalogue, and the database
built from it, which PROJ reads by default; see below.

## The OSDU database the wheel ships

The wheel ships a `proj.db` built at release time with
`geodetic-osdudb build --official`: the pinned pyproj wheel's own database,
with the official OSDU catalogue's CRSs, transformations, record ids and
persistable references added. Its EPSG content is exactly the pinned wheel's.
It is about 23 MB on disk and 3 MB in the wheel, in
`geodetic_engine/osdudb/_data/proj_data/`, a directory of its own. pyproj's
installed `proj.db` is never modified.

With it, OSDU objects resolve right after installation, with no build step:

```python
import geodetic_engine
from pyproj import CRS
from geodetic_engine.geodesy.database import identified_object

CRS.from_authority("OSDU", "2003001").name
# 'Grenada 1953 * DOS-Grd / British West Indies Grid [2003,1443]'
identified_object(
    "OSDU", "reference-data--CoordinateReferenceSystem:Projected:EPSG::23031"
)
# IdentifiedObject(table_name='projected_crs', auth_name='EPSG', code='23031', ...)
```

When `geodetic_engine` is first imported, the first of these rules that
matches decides whether PROJ reads it:

1. `GEODETIC_ENGINE_OSDU_DB=0` (or `false`, `no`, `off`): nothing changes.
2. `pyproj.datadir.set_data_dir()` or `append_data_dir()` was called before
   the import: nothing changes.
3. `PROJ_DATA`, or `PROJ_LIB` when `PROJ_DATA` is unset, names a directory
   that holds a `proj.db`: PROJ reads the first such `proj.db` and searches
   the variable's directories. QGIS, OSGeo4W and conda often set one of these
   for the whole machine. pyproj reads the variable when it is imported, so
   the import usually changes nothing; it applies a variable set later, say in
   a notebook that had already imported pyproj or a library that uses it, and
   logs a warning. A later one whose `proj.db` records no database layout, or
   an older or another one than the `proj.db` PROJ reads, as another PROJ
   release's may, is not applied, since PROJ would refuse it: the import logs
   a warning and changes nothing.
4. Otherwise PROJ's search path becomes the shipped database's directory, then
   the `PROJ_DATA` or `PROJ_LIB` directories, then pyproj's own data
   directory. Only the database comes from the shipped file; grids and
   `proj.ini` are found where they were.

This is the precedence of the project's pyproj wheels: `set_data_dir()`, then
a `PROJ_DATA` holding a database, then the bundled database, with the OSDU
database in place of the wheel's. A database you chose always wins, and a
`PROJ_DATA` naming only grid directories keeps the shipped database. The import
logs the outcome at `INFO`, under the `geodetic_engine` logger, with the reason
when the shipped database is not used, for example
`PROJ_LIB=C:\OSGeo4W\share\proj holds a proj.db`. Configure logging before
the import to see it.

To see which database PROJ reads, and why:

```python
from geodetic_engine.osdudb import active_database

active = active_database()
active.path     # the proj.db PROJ reads
active.bundled  # True when it is the shipped one
active.reason   # 'it is the default, and nothing chose another database'
```

To switch it on or off explicitly, whatever the import decided:

```python
from geodetic_engine.osdudb import use_bundled_database

use_bundled_database()       # read the shipped database
use_bundled_database(False)  # back to the search path from before it
```

To turn it off for a whole process, set `GEODETIC_ENGINE_OSDU_DB=0` before
Python starts. A later `pyproj.datadir.set_data_dir()` call overrides the
import and `use_bundled_database()` alike, and `active_database()` then says
so. A result's provenance, its `database_fingerprints`, names the shipped file
when that is the database that answered.

pyproj keeps one PROJ context per thread, and a switch updates the calling
thread's at once. Another thread that has already used pyproj follows the next
time it calls `active_database()` or resolves a CRS, operation or identifier
through this package; one that only calls pyproj directly keeps the database it
had. There is one gap: if a thread sets a path of its own with
`set_data_dir()`, and another thread then sets back, also with
`set_data_dir()`, the path the first one read before, the path looks unchanged
to the first thread, and it goes on reading its own until the path changes
again or `use_bundled_database()` is called. Switch before starting threads to
avoid the question.

Child processes need nothing extra. A process started by `multiprocessing`
with the spawn method, the default on Windows and macOS, imports
`geodetic_engine` again and applies the same rules to the environment it
inherits, and so does one started with forkserver, the default on Linux from
Python 3.14. A `set_data_dir()` call under `if __name__ == "__main__":` is not
repeated in such a child. A child forked with the fork method, the default on
Linux before Python 3.14, inherits its parent's choice; a fork waits for a
switch in progress to finish.

`geodetic-osdudb build` is still how to get a newer or a custom catalogue
({doc}`/user-guide/osdudb`): put the database it writes first on `PROJ_DATA`,
or name it with `set_data_dir()`. A build starts from pyproj's stock database
even while the shipped one is active: a database this package built is never
taken as the base.

A source checkout ships no database until `python scripts/build_bundled_osdudb.py`
builds one, exactly as the release does. The devcontainer does not need it:
its `PROJ_DATA` names `build/proj.db` through `local/proj-data` (rule 3).


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

# 2. Build and install PROJ. The script reads the version and the tarball
#    checksum from .devcontainer/pins.env and verifies it before building.
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
package is imported. To see which `proj.db` PROJ reads, and why:

```bash
python -c "from geodetic_engine.osdudb import active_database; print(active_database())"
```

## Grids

Grid-based operations (NTv2, NADCON, geoid models) read grid files from PROJ's
data directory. The devcontainer installs the whole `proj-data` package, about
800 MB. Elsewhere, fetch individual grids with PROJ's own tool,
`projsync --file <name>`.

The custom wheel honors `PROJ_DATA`. If it names a custom `proj.db`, put that
database's directory first and grid-only directories after it. If it names only
grid directories, the wheel keeps its bundled database and adds those
directories to the search path, and so does this package with the OSDU
database it ships. An explicit `pyproj.datadir.set_data_dir`
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
