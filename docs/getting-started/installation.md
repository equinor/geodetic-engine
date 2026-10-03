# Installation

`geodetic-engine` needs Python 3.13 or newer, **PROJ 9.9.0 built from source**,
and **pyproj 3.8.0 built against that PROJ**. 


## Why the versions are pinned

Every answer this library gives depends on the EPSG dataset inside PROJ's
`proj.db`. A different PROJ release has a different dataset: other operations,
other accuracies, sometimes other parameters. To get the same coordinates
everywhere, the PROJ version has to be the same everywhere.

The pyproj wheels on PyPI bundle their own copy of PROJ. Installing one would
silently replace the pinned PROJ with whatever the wheel ships, so pyproj is
built from source instead. `pyproject.toml` enforces this for `uv` with
`no-binary-package = ["pyproj"]`.

`tests/test_environment.py` checks the environment before anything else runs:

- pyproj is 3.8.0 and PROJ is 9.9.0;
- PROJ's data directory is not a copy inside `site-packages`;
- the grids the test suite and these docs use (`us_nga_egm08_25.tif`,
  `no_kv_href2008a.tif`, `us_noaa_conus.tif`) are installed;
- `EPSG:4326` is declared latitude-first, so PROJ has not been configured to
  normalise axis order behind your back.

## Option 1: the devcontainer (recommended)

Open the repository in VS Code and choose **Reopen in Container**.
`.devcontainer/Dockerfile` builds PROJ from source with the `proj-data` grid
package. After the container is created, `postCreateCommand` installs the
Python dependencies and runs the environment check. CI builds and tests in the
same image, so your results match CI's.

## Option 2: by hand

```bash
# 1. Build dependencies for PROJ.
sudo apt-get install -y build-essential cmake ninja-build pkg-config \
    libsqlite3-dev sqlite3 libtiff-dev libcurl4-openssl-dev zlib1g-dev

# 2. Build and install PROJ 9.9.0. The script pins the version and verifies
#    the tarball checksum before building.
./.devcontainer/install-proj.sh

# 3. Install the Python dependencies, building pyproj from source against it.
PROJ_DIR=/usr/local PROJ_WHEEL=false uv sync --extra dev

# 4. Verify.
uv run pytest tests/test_environment.py
```

`install-proj.sh` configures PROJ with `-DEMBED_RESOURCE_FILES=OFF`. Do not
remove that flag. With `proj.db` embedded in `libproj`, PROJ can ignore a custom
database on disk, and {doc}`custom databases </user-guide/projdb>` would stop
working.

## Checking an existing environment

```bash
uv run python -c "import pyproj; print(pyproj.proj_version_str, pyproj.datadir.get_data_dir())"
# 9.9.0 /usr/local/share/proj
```

If the second value is under `site-packages`, pyproj was installed from a wheel
and is using its own bundled PROJ. Reinstall it from source as in step 3.

## Grids

Grid-based operations (NTv2, NADCON, geoid models) read grid files from PROJ's
data directory. The devcontainer installs the whole `proj-data` package, about
800 MB. Elsewhere, fetch individual grids with PROJ's own tool,
`projsync --file <name>`.

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
`scripts/build-projdb.sh` carries the patch, or run
`.devcontainer/link-local-grids.sh --patched-copy` and put the copy it writes
first on `PROJ_DATA` as it tells you to (see {doc}`/workarounds`). `local/` is
gitignored, so these grids are not available in CI.

## Optional extras

| Extra | Installs | Needed for |
|---|---|---|
| `dev` | pytest, ruff, mypy, ipykernel, pandas-stubs | Running the tests and checks |
| `docs` | Sphinx, the Furo theme, myst-nb and extensions | Building these pages; see {doc}`/development/index` |
