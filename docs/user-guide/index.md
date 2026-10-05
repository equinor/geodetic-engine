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

:::{grid-item-card} Projection factors
:link: projection-factors/index
:link-type: doc

`geodetic_engine.geodesy.projection_factors`: grid convergence, point scale
factor and distortion at any point of a projected CRS, to turn azimuths and
scale distances between the ground and the grid.
:::

:::{grid-item-card} Well trajectories
:link: welltrajectory/index
:link-type: doc

`geodetic_engine.welltrajectory`: position a wellbore from its directional
survey by minimum curvature, place it in a CRS, and plot it in 3D.
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
::::
