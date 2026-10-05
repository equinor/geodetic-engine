# Glossary

```{glossary}

Coordinate system (CS)
  A coordinate system (CS) defines how coordinates are expressed by specifying
  the coordinate axes, their order, orientation, and the units used to measure
  positions within the system. A coordinate system is an abstract mathematical
  construct and, by itself, is not physically anchored to the Earth or any other
  real-world object.

Datum
  A datum defines the relationship of a
  {term}`coordinate system <Coordinate system (CS)>` to the Earth by specifying
  the origin and orientation of the coordinate system relative to the Earth.
  This makes the resulting coordinates unambiguous and allows them to represent
  real-world positions.

CRS
  A Coordinate Reference System (CRS) defines the meaning of a set of
  coordinates by combining a {term}`coordinate system <Coordinate system (CS)>`,
  which specifies the axes, axis order, and units, with a {term}`datum` that
  anchors the coordinate system to the Earth.

Conversion
  A coordinate operation that stays on one datum: a map projection, a unit
  change, an axis swap. Exact, and applied without being named.

Coordinate operation
  Any conversion or transformation between two CRSs.

Transformation
  A coordinate operation that changes the datum. Multiple transformations may
  exist between the same source and target CRS, depending on the area of use, required
  accuracy, coordinate epoch, and available transformation parameters or grids.


Authority code
  A unique identifier assigned to an object in an authoritative register, such
  as `EPSG:4326`. It consists of an authority name and a code, which together
  uniquely identify the object.

Bound CRS
  A CRS that is explicitly linked to another CRS, known as the hub CRS, through
  a predefined coordinate {term}`transformation`. Because the transformation is selected
  and included as part of the CRS definition, this is known as early binding.
  It helps users without specialised geodetic knowledge apply the intended
  transformation without having to choose between multiple available operations
  at runtime. The hub CRS is commonly WGS 84. A bound CRS is represented as
  `BOUNDCRS` in WKT2 and `EBC` in an OSDU payload. See
  {doc}`/background/bound-crs`.


Concatenated operation
  A published chain of operations applied in sequence, such as the
  datum transformation `EPSG:8047` =
  `EPSG:1147` + `EPSG:1146`.

Area of use
  The region an operation is published as valid for, as a bounding box and
  a description. Reported on every
  {class}`~geodetic_engine.geodesy.OperationCandidate`. Not enforced.


Accuracy of a transformation
  The expected absolute difference, expressed in metres, between a transformed
  position and the position that would be obtained by directly determining the
  same physical point in the target CRS.

  The calculation itself is exact, deterministic, and repeatable. The stated
  accuracy describes the limitations of the transformation model, not its
  arithmetic or numerical precision.

  For example, a seven-parameter
  {term}`Helmert transformation <Helmert transformation>` applies a uniform
  translation, rotation, and scale to the entire reference frame. It cannot
  correct local distortions within a geodetic network, whereas a grid-based
  transformation such as NTv2 can model spatially varying corrections.
   
  The accuracy is generally a representative value without a formal confidence
  level and applies only within the transformation's
  {term}`area of use <Area of use>`. Differences between two transformations
  do not indicate their accuracy, since neither result is necessarily the
  reference value.


Early binding
  Selecting a specific transformation in advance and including it as part of the
  CRS definition, as is done with a {term}`bound CRS <Bound CRS>`. This ensures
  that the predefined transformation is used without requiring a transformation
  to be selected at runtime. The opposite of
  {term}`late binding <Late binding>`.

Late binding
  Selecting the most appropriate transformation at runtime, based on the source
  and target CRSs and the context of the operation. The selection may consider
  factors such as the area of use, required accuracy, coordinate epoch, and
  available transformation grids. Represented as `LBC` in an OSDU payload. The
  opposite of {term}`early binding <Early binding>`.


Declared axis order
  The order in which the coordinate axes are defined by a CRS, such as latitude
  followed by longitude in `EPSG:4326`. The declared axis order is an integrated
  part of the CRS definition.



Coordinate epoch
  The time coordinates were observed at, as a decimal year. Required by
  operations that model motion over time.


Dynamic CRS
  A CRS based on a dynamic datum or reference frame that accounts for changes
  in the Earth over time, including tectonic plate motion. Its datum has a frame
  reference epoch at which the reference frame is defined. This is distinct from
  the coordinate epoch, which specifies when an individual position applies.

  For example, coordinates in an ITRF realisation change over time as the
  Eurasian tectonic plate moves, while ETRS89 moves with the stable part of the
  Eurasian plate so that coordinates remain approximately fixed relative to
  Europe. Consequently, coordinates for the same physical location expressed
  in ITRF and ETRS89 increasingly differ as time passes. Compare
  {term}`static CRS <Static CRS>`.

Static CRS
  A CRS based on a static datum or reference frame that does not explicitly
  account for changes in the Earth over time. It can be understood as a snapshot
  of a dynamic reference frame at a specified epoch. Coordinates are treated as
  constant, even though the physical location may move because of tectonic plate
  motion, land uplift, or local deformation.

  For example, ETRS89 was defined to coincide with ITRS at epoch 1989.0,
  approximately 1 January 1989, and was then fixed to the stable part of the
  Eurasian plate. ETRS89 coordinates therefore remain approximately constant
  relative to Europe, while coordinates in an ITRF realisation change over time
  as the tectonic plate moves. Consequently, ETRS89 and ITRF coordinates for the
  same physical location increasingly differ as time passes. Compare
  {term}`dynamic CRS <Dynamic CRS>`.


Ballpark
  PROJ's fallback when it knows no transformation between two datums. It treats
  them as the same and states no accuracy. Errors can be hundreds of metres.
  Never returned by this package.


Grid
  A file containing position-dependent correction values used by a coordinate
  operation. The correction applied at a given location is interpolated from
  the surrounding grid values. Grids may provide horizontal shifts, vertical
  corrections, deformation values, or velocities. Examples include NTv2 and
  NADCON grids for horizontal transformations and geoid models for converting
  between ellipsoidal and gravity-related heights.

Helmert transformation
  A transformation between source and target geocentric CRSs using seven
  parameters: 
    - three translations {math}`(t_X, t_Y, t_Z)`
    - three rotations {math}`(r_X, r_Y, r_Z)`
    - one scale difference {math}`dS`.

  The transformation can be expressed as:

  :::{math}
  \begin{bmatrix}
  X_t \\
  Y_t \\
  Z_t
  \end{bmatrix}
  =
  (1 + dS)
  \begin{bmatrix}
  1 & -r_Z & r_Y \\
  r_Z & 1 & -r_X \\
  -r_Y & r_X & 1
  \end{bmatrix}
  \begin{bmatrix}
  X_s \\
  Y_s \\
  Z_s
  \end{bmatrix}
  +
  \begin{bmatrix}
  t_X \\
  t_Y \\
  t_Z
  \end{bmatrix}.
  :::

Hub CRS
  The intermediate CRS to which a {term}`bound CRS <Bound CRS>` is linked
  through its predefined transformation. The hub CRS provides a common reference
  through which coordinates can be transformed to other CRSs. It is commonly
  WGS 84.


persistableReference
  OSDU's JSON envelope around ESRI WKT, stating a CRS, transformation or unit.
  See {doc}`/user-guide/persistable-reference`.

Pipeline
  PROJ's explicit, step-by-step description of a coordinate operation. Each step
  represents an individual operation, such as an axis swap, unit conversion, map
  projection, grid shift, or datum transformation, and the steps are applied
  sequentially. A pipeline records exactly how coordinates are transformed and
  can be reused with PROJ or pyproj to reproduce the operation.

Route
  How the transformer that produced a result was arrived at. It is recorded on
  every result as {class}`~geodetic_engine.geodesy.OperationRoute` and is one
  of:

  - `transformer_group`: the operation was selected from the candidates PROJ
    offers for the source and target CRS pair.
  - `chained`: the operation was built from the one requested, wrapped in
    {term}`conversions <Conversion>` that stay on the same datum.
  - `bound`: the operation was taken from the transformation that a
    {term}`bound CRS <Bound CRS>` carries in its own definition.
  - `proj_default`: no operation was requested, so PROJ chose one and the
    choice is recorded.

  See {doc}`/user-guide/geodesy/results`.

Stated operation
  A coordinate operation given in full, with its method and parameters,
  instead of being named by an authority code or a name. Examples are an OSDU
  {term}`persistableReference` payload, an ESRI `GEOGTRAN` string and a pyproj
  `CoordinateOperation`.

  A named operation is looked up in the database, so the parameters used are
  those the database holds under that code. A stated operation is applied
  exactly as stated, even when its code is also in the database with different
  parameters. It is applied alone and cannot be combined with other named
  operations in a list.

  See {ref}`persistable-reference-stated-operation`.


Value order
  The order of the numbers you pass and receive. Always `xy` in this package:
  longitude before latitude, easting before northing, then height, wherever
  the CRS has such axes; a CRS without an east/north pair keeps its declared
  order.

Grid convergence
  The angle between true north and grid north at a point of a projected CRS.
  In this package it is measured from true north to grid north, positive
  clockwise, so a grid azimuth is the true azimuth less the convergence. See
  {doc}`/user-guide/projection-factors/index`.

Point scale factor
  The ratio of a short distance on the grid to the same distance on the
  ellipsoid, at a point of a projected CRS. For a conformal projection it is
  the same in every direction. See {doc}`/user-guide/projection-factors/index`.

Measured depth (MD)
  The length along the wellbore from its reference point, as drilled. It is
  what a survey station is located by.

True vertical depth (TVD)
  The vertical distance of a point of the wellbore below its reference point,
  positive down.

Wellhead
  The reference point of a well's survey, from which measured depth and true
  vertical depth count: its position in the trajectory CRS and its elevation.

Inclination
  The angle of the wellbore from vertical at a survey station: 0 is straight
  down, 90 degrees horizontal.

Survey azimuth
  The direction of the wellbore in the horizontal plane at a survey station,
  clockwise from north. It is measured against grid north or true north, which
  differ by the {term}`grid convergence`.

Dogleg severity (DLS)
  How sharply the wellbore turns: the angle between the directions at two
  survey stations, per length of measured depth, usually in degrees per 30 m
  or per 100 ft.

Minimum curvature
  The standard way of reducing a directional survey to positions: between two
  stations, the wellbore follows the one circular arc tangent to both
  stations' directions. See {doc}`/user-guide/welltrajectory/minimum-curvature`.
```
