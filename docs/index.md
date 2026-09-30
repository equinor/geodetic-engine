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

```{mermaid}
flowchart LR
    A["source CRS · target CRS<br/><b>named</b> operation"] --> B{"checks"}
    B -->|"unnamed datum change · ballpark<br/>missing grid · missing epoch"| R["refused, with the reason"]
    B -->|"ok"| P["PROJ"] --> C["coordinates<br/>+ provenance"]
    classDef bad fill:#f8d7da,stroke:#b02a37,color:#58151c;
    classDef good fill:#d1ecf1,stroke:#0b6b86,color:#0b3d4c;
    class R bad;
    class C good;
```

A wrong coordinate that looks right is worse than an error, because nobody
checks it again. So a datum change must name its operation, a ballpark
approximation is refused, a missing grid is an error, and a time-dependent
operation needs a coordinate epoch.

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

Task-oriented guides for every module: transformations, OSDU
persistableReferences, the Georepository client, and custom `proj.db` builds.
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
| Read or write an OSDU `persistableReference` | {doc}`user-guide/persistable-reference` |
| Add my organisation's CRSs and transformations to PROJ | {doc}`user-guide/projdb` or {doc}`user-guide/osdudb` |
| Know what the package does differently from plain pyproj | {doc}`background/guarantees` |
| Look up a term such as *bound CRS* or *ballpark* | {doc}`glossary` |

```{toctree}
:hidden:
:caption: Getting started
:maxdepth: 1

getting-started/installation
getting-started/quickstart
getting-started/concepts
```

```{toctree}
:hidden:
:caption: User guide
:maxdepth: 2

user-guide/geodesy/index
user-guide/persistable-reference
user-guide/georepository
user-guide/projdb
user-guide/osdudb
user-guide/combining
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
:maxdepth: 1

api/index
cli/index
glossary
changelog
```

```{toctree}
:hidden:
:caption: Background
:maxdepth: 1

background/architecture
background/guarantees
background/axis-order
background/bound-crs
background/provenance
workarounds
```

```{toctree}
:hidden:
:caption: Project
:maxdepth: 1

development/index
GitHub repository <https://github.com/equinor/geodetic-engine>
```
