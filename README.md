<h1>
  <img src="docs/_static/logo.svg" alt="geodetic-engine" width="360">
</h1>

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
- **OSDU `persistableReference` support.** Read and write the JSON/ESRI WKT
  envelope, and transform with exactly what it states.
- **Custom PROJ databases.** Add a Georepository register's or an OSDU
  catalogue's own CRSs and transformations to a copy of PROJ's `proj.db`.
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
`available_operations("EPSG:4230", "EPSG:4326")` to choose one.

## Installation

The library needs **PROJ 9.9.0 built from source** and **pyproj 3.8.0 built
against it**, because the EPSG dataset inside `proj.db` is part of every answer.
The simplest way is the devcontainer: open the repository in VS Code and choose
*Reopen in Container*. By hand:

```bash
./.devcontainer/install-proj.sh                          # build PROJ 9.9.0
PROJ_DIR=/usr/local PROJ_WHEEL=false uv sync --extra dev # pyproj from source
uv run pytest tests/test_environment.py                  # verify
```

See [Installation](https://equinor.github.io/geodetic-engine/dev/getting-started/installation.html)
for details.

## Documentation

| | |
|---|---|
| [Getting started](https://equinor.github.io/geodetic-engine/dev/getting-started/index.html) | Installation, quickstart, core concepts |
| [User guide](https://equinor.github.io/geodetic-engine/dev/user-guide/index.html) | Transformations, OSDU payloads, the Georepository client, custom `proj.db` builds, seismic bin grids |
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
```

See [Development](https://equinor.github.io/geodetic-engine/dev/development/index.html).

## License

See [LICENSE](LICENSE).
