# geodetic-engine

<p class="lead">Coordinate transformations you can defend.</p>

`geodetic-engine` is a Python library built on [PROJ](https://proj.org) and
[pyproj](https://pyproj4.github.io/pyproj/stable/). PROJ does the arithmetic.
This package decides whether PROJ's answer can be trusted, refuses it when it
cannot, and records exactly how every coordinate was produced.

```python
from geodetic_engine.geodesy import transform

result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation="EPSG:1612")
result.coordinates      # ((2.49818..., 63.49961...),)  lon, lat in degrees
result.operation.name   # 'ED50 to WGS 84 (23)'
result.pipeline         # the exact PROJ pipeline that ran
```

A wrong coordinate that looks right is worse than an error, because nobody
checks it again. So a datum change must name its operation, a ballpark
approximation is refused, a missing grid is an error, and a time-dependent
operation needs a coordinate epoch.

## What the package does

- **Transforms coordinates with a stated operation.** Between geographic,
  projected, vertical, compound and engineering CRSs, from EPSG or from your
  own definitions. A datum change names its transformation, and the one named
  is the one applied.
- **Refuses results it cannot vouch for.** Ballpark approximations, a missing
  grid, a time-dependent operation without a coordinate epoch: each raises an
  error that says what was wrong, instead of returning a plausible number.
- **Records provenance.** Every result carries the applied operation, its
  accuracy, the grids used, the coordinate epoch, the exact PROJ pipeline, and
  fingerprints of the `proj.db` that answered, so a result can be reproduced
  and audited later.
- **Reads and writes OSDU `persistableReference`s**, and transforms with
  exactly the CRS or operation a payload states.
- **Evaluates projection factors.** Grid convergence and point scale factor at
  any point of a projected CRS, with the sign convention stated.
- **Positions wellbores.** A directional survey, by minimum curvature,
  georeferenced in a CRS on its own datum, with dogleg severity and a 3D view.
- **Builds custom PROJ databases.** Adds an organisation's CRSs and
  transformations, from a Georepository register or an OSDU catalogue, to a
  validated copy of PROJ's `proj.db`.
- **Works around known PROJ and EPSG problems**, such as engineering CRS axis
  order, and documents each workaround with the condition for removing it.

## Modules

| Module | Purpose |
|---|---|
| {mod}`geodetic_engine.geodesy` | Transformations, CRS inspection, operation lookup, results and provenance, projection factors |
| {mod}`geodetic_engine.welltrajectory` | Well trajectories from directional surveys: minimum curvature, georeferencing in a CRS, 3D plots |
| {mod}`geodetic_engine.persistablereference` | Parse and emit OSDU `persistableReference` payloads |
| {mod}`geodetic_engine.georepository` | Authenticated client for a Georepository API |
| {mod}`geodetic_engine.projdb` | Build a `proj.db` from a Georepository register (`geodetic-projdb`) |
| {mod}`geodetic_engine.osdudb` | Build a `proj.db` from an OSDU catalogue (`geodetic-osdudb`) |

PROJ does all the numerical work. The package is a layer over pyproj that
decides which operation runs, checks the result, and records how it was
produced. It is a library, not a service: transformations run locally and never
contact the Georepository. See {doc}`background/architecture`.

## When to use it

Use it when you must be able to say which operation produced a coordinate,
with what accuracy, and from which database. Use plain pyproj when PROJ
may pick the operation for you and an unstated accuracy is acceptable, for
example when drawing a map. {doc}`background/guarantees` lists what this
package does differently from pyproj.

## Requirements

- Python 3.13 or later.
- PROJ {{ proj_version }} and pyproj {{ pyproj_version }}, built against each
  other. The EPSG dataset in `proj.db` is part of every answer, so the versions
  are pinned. The devcontainer installs both; see
  {doc}`getting-started/installation`.
- Licensed under Apache 2.0.

## Start here

::::{grid} 1 2 2 3
:gutter: 3

:::{grid-item-card} {octicon}`rocket` Getting started
:link: getting-started/index
:link-type: doc

Install the pinned PROJ and pyproj, transform your first point, and learn the
ideas the rest of the documentation assumes you know.
:::

:::{grid-item-card} {octicon}`book` User guide
:link: user-guide/index
:link-type: doc

Task-oriented guides for every module: transformations, projection factors,
well trajectories, OSDU persistableReferences, the Georepository client, and
custom `proj.db` builds.
:::

:::{grid-item-card} {octicon}`beaker` Examples
:link: examples/index
:link-type: doc

Worked notebooks that run during every documentation build, so every number
shown is real output.
:::

:::{grid-item-card} {octicon}`code` API reference
:link: api/index
:link-type: doc

Every public class and function, generated from the docstrings.
:::

:::{grid-item-card} {octicon}`terminal` Command-line tools
:link: cli/index
:link-type: doc

`geodetic-projdb` and `geodetic-osdudb`: every option, with the example
configuration files.
:::

:::{grid-item-card} {octicon}`alert` Known issues and workarounds
:link: workarounds
:link-type: doc

What this package works around in PROJ and the EPSG dataset, why, and when
each workaround can be removed.
:::
::::

## Where to find what

| I want to... | Go to |
|---|---|
| Transform coordinates between two CRSs | {doc}`user-guide/geodesy/transformations` |
| Find out which operations exist between two CRSs | {doc}`user-guide/geodesy/choosing-operations` |
| Understand why my transformation was refused | {doc}`user-guide/geodesy/errors` |
| Turn an azimuth between true and grid north, or scale a distance onto the grid | {doc}`user-guide/projection-factors/azimuths-and-distances` |
| Position a well from its directional survey | {doc}`user-guide/welltrajectory/input` |
| Read or write an OSDU `persistableReference` | {doc}`user-guide/persistable-reference` |
| Add my organisation's CRSs and transformations to PROJ | {doc}`user-guide/custom-database` |
| Know what the package does differently from plain pyproj | {doc}`background/guarantees` |
| Look up a term such as *bound CRS* or *ballpark* | {doc}`glossary` |

```{toctree}
:hidden:
:maxdepth: 2

getting-started/index
```

```{toctree}
:hidden:
:caption: User guide
:maxdepth: 2

user-guide/geodesy/index
user-guide/projection-factors/index
user-guide/welltrajectory/index
user-guide/persistable-reference
user-guide/georepository
user-guide/custom-database
```

```{toctree}
:hidden:
:caption: Examples
:maxdepth: 2

Gallery <examples/index>
```

```{toctree}
:hidden:
:caption: Reference
:maxdepth: 2

api/index
cli/index
background/index
glossary
changelog
```

```{toctree}
:hidden:
:caption: Project
:maxdepth: 1

development/index
GitHub repository <https://github.com/equinor/geodetic-engine>
```
