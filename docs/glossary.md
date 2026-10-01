# Glossary

```{glossary}
Area of use
  The region an operation is published as valid for, as a bounding box and
  a description. Reported on every
  {class}`~geodetic_engine.geodesy.OperationCandidate`. Not enforced.

Authority code
  An object's identifier in a register, such as `EPSG:4326`. In this package,
  an authority code in an OSDU payload is provenance. The payload's WKT is the
  definition.

Ballpark
  PROJ's fallback when it knows no transformation between two datums. It treats
  them as the same and states no accuracy. Errors can be hundreds of metres.
  Never returned by this package.

Bound CRS
  A CRS packaged with the single transformation that takes it to a hub CRS,
  usually WGS 84. `BOUNDCRS` in WKT2, `EBC` in an OSDU payload. Using one
  counts as naming the operation. See {doc}`/background/bound-crs`.

Concatenated operation
  A published chain of operations applied in sequence, such as `EPSG:8047` =
  `EPSG:1147` + `EPSG:1146`.

Conversion
  A coordinate operation that stays on one datum: a map projection, a unit
  change, an axis swap. Exact, and applied without being named.

Coordinate epoch
  The time coordinates were observed at, as a decimal year. Required by
  operations that model motion over time.

Coordinate operation
  Any conversion or transformation between two CRSs.

CRS
  Coordinate reference system: what a set of coordinate numbers means. Datum,
  axes, units, order.

Datum
  What ties a coordinate system to the Earth. Moving between datums is a
  transformation.

Declared axis order
  The axis order in a CRS's definition, such as latitude-first for `EPSG:4326`.
  Reported, never used to reorder values silently. Compare *value order*.

Dynamic CRS
  A CRS whose datum has a frame reference epoch, such as an ITRF realisation.
  Does not by itself require a coordinate epoch.

Early binding
  Fixing the transformation as part of the CRS definition, as a bound CRS
  does. The opposite of late binding.

Grid
  A file of shifts that an operation interpolates in: NTv2, NADCON, geoid
  models.

Helmert
  A seven-parameter similarity transformation between geocentric frames: three
  translations, three rotations, one scale difference.

Hub
  The CRS a bound CRS is bound to, almost always WGS 84.

Late binding
  Choosing the transformation when a transformation is requested, not in the
  CRS definition. `LBC` in an OSDU payload.

persistableReference
  OSDU's JSON envelope around ESRI WKT, stating a CRS, transformation or unit.
  See {doc}`/user-guide/persistable-reference`.

Pipeline
  PROJ's step-by-step description of a transformation. Reported on every
  result, and replayable with plain pyproj.

Route
  How a result's operation was arrived at: `transformer_group`, `chained`,
  `bound` or `proj_default`. See {class}`~geodetic_engine.geodesy.OperationRoute`.

Stated operation
  An operation given in full (an OSDU payload, an ESRI `GEOGTRAN`, a parsed
  reference) instead of by code. Applied exactly as stated.

Transformation
  A coordinate operation that changes datum. Usually several are published for
  the same pair, so this package requires one to be named.

Value order
  The order of the numbers you pass and receive. Always `xy` in this package:
  longitude before latitude, easting before northing, then height, wherever
  the CRS has such axes; a CRS without an east/north pair keeps its declared
  order.
```
