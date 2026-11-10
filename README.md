<h1>
  <img src="https://raw.githubusercontent.com/equinor/geodetic-engine/main/docs/_static/logo.svg" alt="geodetic-engine" width="360">
</h1>

[![PyPI](https://img.shields.io/pypi/v/pygeodetic-engine)](https://pypi.org/project/pygeodetic-engine/)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fequinor%2Fgeodetic-engine%2Fmain%2Fpyproject.toml)](https://pypi.org/project/pygeodetic-engine/)
[![Tests](https://github.com/equinor/geodetic-engine/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/equinor/geodetic-engine/actions/workflows/tests.yml)
[![Lint and type check](https://github.com/equinor/geodetic-engine/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/equinor/geodetic-engine/actions/workflows/lint.yml)
[![Documentation](https://github.com/equinor/geodetic-engine/actions/workflows/docs.yml/badge.svg?branch=main)](https://equinor.github.io/geodetic-engine/)
[![License](https://img.shields.io/github/license/equinor/geodetic-engine)](https://github.com/equinor/geodetic-engine/blob/main/LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

`geodetic-engine` is a Python library for transforming coordinates between
coordinate reference systems (CRSs) under explicit, verifiable rules. It is
built on [PROJ](https://proj.org) and
[pyproj](https://pyproj4.github.io/pyproj/stable/): PROJ performs the numerical
computation, while `geodetic-engine` determines which coordinate operation is
applied, validates the result before returning it, and records the provenance
required to reproduce and audit it. A transformation that cannot be carried out
exactly as specified is refused with an error rather than approximated.

**Documentation: <https://equinor.github.io/geodetic-engine/>**

- **Explicit operations.** A datum change must name its transformation, or use
  a bound CRS that names it. The operation named is the one applied.
- **Refuses untrustworthy results.** No ballpark approximations, no silent
  fallback when a grid is missing, no time-dependent transformation without a
  coordinate epoch.
- **Provenance on every result.** The applied operation, grids, epoch, the exact
  PROJ pipeline, and fingerprints of the `proj.db` that answered.
- **Numeric batches and lazy streams.** Reuse a `Transformation` with
  `transform_arrays()` for owned NumPy output buffers, or `transform_batches()`
  for lazy bounded batches, without constructing Python objects per output point.
- **OSDU `persistableReference` support.** Read and write the JSON/ESRI WKT
  envelope, and transform with exactly what it states.
- **Geometries.** Transform, convert and round geometries in WKT, WKB, hex
  WKB, PostGIS EWKT/EWKB, GeoJSON and Esri JSON with
  `Transformation.transform_geometry()`, with exact half-up rounding.
- **GeoJSON documents.** Convert RFC 7946 GeoJSON, OGC JSON-FG and OSDU
  AnyCrs GeoJSON to another CRS with `geodetic_engine.geojson.convert_geojson`,
  keeping structure and properties; a drop-in for the OSDU `convertGeoJson`.
- **Custom PROJ databases.** Add a Georepository register's or an OSDU
  catalogue's own CRSs and transformations to a copy of PROJ's `proj.db`.
- **Catalog of the database.** List and filter the CRSs, transformations,
  datums, ellipsoids, prime meridians, units, methods, coordinate systems and
  grid files `proj.db` holds, by area, datum, accuracy, deprecation and more,
  with `geodetic_engine.catalog`; export them as DataFrames or PROJJSON.
- **Projection factors.** Grid convergence and scale factor of any projected
  CRS, with `geodetic_engine.geodesy.projection_factors`.
- **Well trajectories.** Minimum-curvature trajectories from directional
  surveys, placed in a CRS; see the
  [`welltrajectory` README](https://github.com/equinor/geodetic-engine/blob/main/geodetic_engine/welltrajectory/README.md).
- **Seismic bin grids.** Convert between bin grid and map grid with P6
  parameters, check and square up a four-corner grid, convert it to another
  CRS, and match a legacy dataset to a stored grid.

## Quickstart

```python
from geodetic_engine.geodesy import transform

# ED50 -> WGS 84 in Norwegian waters, with a named EPSG transformation.
result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation="EPSG:1612")

result.coordinates        # ((2.49818..., 63.49961...),)   lon, lat: always xy order
result.operation.name     # 'ED50 to WGS 84 (23)'
result.operation.accuracy # 1.0 (metres)
result.pipeline           # the PROJ pipeline that ran, replayable in plain pyproj
```

Leave out `operation=` and the call raises `AmbiguousOperationError`, because
several ED50 to WGS 84 transformations exist and they disagree by metres. Use
`available_operations("EPSG:4230", "EPSG:4326")` to choose one, or list what
EPSG publishes with the catalog:

```python
from geodetic_engine import catalog

catalog.operations(
    source_crs="EPSG:4230", target_crs="EPSG:4326", max_accuracy=1.0, area_name="Norway"
).to_dataframe()
```

## Installation

The library needs **PROJ 9.9.0** and **pyproj 3.8.0 built against it**,
because the EPSG dataset inside `proj.db` is part of every answer. Install the
released package and the matching wheel together:

```bash
pip install "pygeodetic-engine" "pyproj==3.8.0+proj9.9.0" \
    --extra-index-url https://equinor.github.io/geodetic-engine/simple/
```

The explicit wheel requirement also replaces a previously installed PyPI
pyproj wheel. If a matching wheel is unavailable, build pyproj from source
against the pinned PROJ instead. Importing the package rejects another PROJ
version rather than silently using its EPSG dataset. Wheels do not include the
full grid package; install the grids needed by your operations separately.

The wheel ships PROJ's database with the official OSDU catalogue already built
into it, and PROJ reads it once `geodetic_engine` is imported. OSDU CRSs,
record ids and persistable references resolve with no build step:

```python
import geodetic_engine
from pyproj import CRS

CRS.from_authority("OSDU", "2003001").name
# 'Grenada 1953 * DOS-Grd / British West Indies Grid [2003,1443]'
```

A database you chose yourself always wins: a `pyproj.datadir.set_data_dir()`
call before the import, or a `PROJ_DATA` (or `PROJ_LIB`) directory holding a
`proj.db`. `GEODETIC_ENGINE_OSDU_DB=0` turns the shipped database off, and
`geodetic_engine.osdudb.active_database()` reports which database PROJ reads
and why. `geodetic-osdudb build` still builds a newer or custom catalogue. See
[Installation](https://equinor.github.io/geodetic-engine/dev/getting-started/installation.html#the-osdu-database-the-wheel-ships).

For development, open the repository in VS Code and choose *Reopen in
Container*. By hand:

```bash
./.devcontainer/install-proj.sh                          # build the pinned PROJ
PROJ_DIR=/usr/local PROJ_WHEEL=false uv sync --extra dev # pyproj from source
uv run pytest tests/test_environment.py                  # verify
```

See [Installation](https://equinor.github.io/geodetic-engine/dev/getting-started/installation.html)
for details.

## Documentation

| | |
|---|---|
| [Getting started](https://equinor.github.io/geodetic-engine/dev/getting-started/index.html) | Installation, quickstart, core concepts |
| [User guide](https://equinor.github.io/geodetic-engine/dev/user-guide/index.html) | Transformations, the catalog, OSDU payloads, the Georepository client, custom `proj.db` builds, seismic bin grids |
| [Examples](https://equinor.github.io/geodetic-engine/dev/examples/index.html) | Executed notebooks |
| [API reference](https://equinor.github.io/geodetic-engine/dev/api/index.html) | Every public class and function |
| [Command-line tools](https://equinor.github.io/geodetic-engine/dev/cli/index.html) | `geodetic-projdb`, `geodetic-osdudb` |
| [Known issues and workarounds](https://equinor.github.io/geodetic-engine/dev/workarounds.html) | What is worked around in PROJ, EPSG data, and the APIs, and when each can be removed |

To build the documentation locally:

```bash
uv run --extra docs sphinx-build -W --keep-going -b html docs docs/_build/html
```

## Development

```bash
uv run pytest                  # the usual run
scripts/run_all_tests.sh       # including the exhaustive dataset sweep
uv run ruff check geodetic_engine tests && uv run mypy geodetic_engine
uv run lint-imports            # the import layering contracts
```

See [Development](https://equinor.github.io/geodetic-engine/dev/development/index.html).
The PROJ, proj-data and pyproj releases are pinned in one file,
`.devcontainer/pins.env`; to move them, follow
[runbooks/upgrading-proj-and-pyproj.md](https://github.com/equinor/geodetic-engine/blob/main/runbooks/upgrading-proj-and-pyproj.md).

## License

See [LICENSE](https://github.com/equinor/geodetic-engine/blob/main/LICENSE).
