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

:::{grid-item-card} Custom database from Georepository
:link: projdb
:link-type: doc

`geodetic_engine.projdb` and `geodetic-projdb`: add a register's own CRSs and
transformations to a copy of PROJ's `proj.db`.
:::

:::{grid-item-card} Custom database from OSDU
:link: osdudb
:link-type: doc

`geodetic_engine.osdudb` and `geodetic-osdudb`: the same, from an OSDU
`CRS_CT.json` catalogue.
:::

:::{grid-item-card} Combining sources and using the result
:link: combining
:link-type: doc

Build one database from both sources, patch grid mappings, and point PROJ at
the result.
:::
::::
