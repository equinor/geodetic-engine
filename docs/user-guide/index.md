---
orphan: true
---

# User guide

Task-oriented guides, one section per module. Each starts with what the module
is for, when to use it, and when not to.

::::{grid} 1 2 2 2
:gutter: 3

:::{grid-item-card} Geodesy
:link: geodesy/index
:link-type: doc

`geodetic_engine.geodesy`: resolve CRSs, list and choose operations, transform
coordinates, and read provenance. The module most users need.
:::

:::{grid-item-card} Persistable references
:link: persistable-reference
:link-type: doc

`geodetic_engine.persistablereference`: read and write the OSDU
`persistableReference` JSON/ESRI-WKT envelope for CRSs, transformations and
units.
:::

:::{grid-item-card} Georepository client
:link: georepository
:link-type: doc

`geodetic_engine.georepository`: an authenticated, paging, caching client for
a Georepository geodetic registry.
:::

:::{grid-item-card} Custom PROJ database
:link: custom-database
:link-type: doc

`geodetic_engine.projdb`, `geodetic_engine.osdudb` and their commands: add a
Georepository register's or an OSDU catalogue's own CRSs and transformations
to a copy of PROJ's `proj.db`, and use the result.
:::

:::{grid-item-card} Seismic bin grids
:link: bingrid
:link-type: doc

`geodetic_engine.bingrid`: convert between bin grid and map grid with P6
parameters, and check, square and convert a four-corner bin grid.
:::
::::
