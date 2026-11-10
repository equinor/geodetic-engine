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
coordinates, read provenance, and get the grid convergence and scale factor.
The module most users need.
:::

:::{grid-item-card} Catalog
:link: catalog/index
:link-type: doc

`geodetic_engine.catalog`: find CRSs, transformations, datums, ellipsoids,
units, methods, coordinate systems and grid files in the PROJ database, filter
them by area, datum, accuracy and more, and export them as a table or
PROJJSON.
:::

:::{grid-item-card} GeoJSON documents
:link: geojson
:link-type: doc

`geodetic_engine.geojson`: convert RFC 7946 GeoJSON, OGC JSON-FG and OSDU
AnyCrs GeoJSON documents to another CRS, as the OSDU `convertGeoJson` does.
:::

:::{grid-item-card} Well trajectories
:link: welltrajectory/index
:link-type: doc

`geodetic_engine.welltrajectory`: position a wellbore from its directional
survey by minimum curvature, place it in a CRS, and plot it in 3D.
:::

:::{grid-item-card} Seismic bin grids
:link: bingrid
:link-type: doc

`geodetic_engine.bingrid`: convert between bin grid and map grid with P6
parameters, and check, square and convert a four-corner bin grid.
:::

:::{grid-item-card} Persistable references
:link: /persistable-reference/index
:link-type: doc

`geodetic_engine.persistablereference`: read, write and look up the OSDU
`persistableReference` JSON/ESRI-WKT envelope for CRSs, transformations and
units. Documented in the Reference section.
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
::::
