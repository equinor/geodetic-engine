# Core concepts

The rest of the documentation uses these terms in the precise sense given
here. The {doc}`/glossary` has short definitions to link to.

## CRS

A **coordinate reference system** says what a set of numbers means: which
{term}`datum` they are relative to, which axes they are on, in what units, and
in what order. `EPSG:4326` (WGS 84 geographic), `EPSG:25832` (ETRS89 / UTM
zone 32N) and `EPSG:5941` (NN2000 height) are all CRSs. A CRS is not an
instruction to do anything. It only describes coordinates.

In this package a CRS can be given as an authority code (`"EPSG:4326"`), WKT, a
PROJ string, a {class}`pyproj.crs.CRS`, or an OSDU `persistableReference`
payload. It is resolved into a
{class}`~geodetic_engine.geodesy.CoordinateReferenceSystem`, which reports the
axes and units EPSG declares.

## Datum

A **datum** ties a coordinate system to the Earth. ED50 and WGS 84 are
different datums. The same latitude and longitude in each refers to points
about 100 to 200 m apart in the North Sea. Moving coordinates between datums is
not an exact calculation. It is a model, fitted to observations over some area,
with a stated accuracy.

## Conversion versus transformation

A {term}`coordinate operation` is how coordinates get from one CRS to another.
There are two kinds:

**Conversion**
: Stays on one datum. A map projection such as UTM, a change of units, or
  swapping axes. It is exact, and there is only one answer. This package
  applies a conversion without being asked.

**Transformation**
: Changes datum. There is usually more than one published way to do it, fitted
  to different areas and with different accuracies. `EPSG:1133` and `EPSG:1612`
  are both "ED50 to WGS 84" and give different answers. **This package requires
  you to name the transformation** (see {doc}`/background/guarantees`), unless
  the CRS already names it (a {term}`bound CRS`).

A **concatenated operation** is a published chain of transformations, for
example `EPSG:8047` = `EPSG:1147` followed by `EPSG:1146`.

## Axis order

EPSG declares `EPSG:4326` as latitude, then longitude. Most software, including
this package, passes longitude first. The two ideas are kept separate:

- **Coordinate values** you pass in and get back are always in `xy` order:
  longitude before latitude, easting before northing, then height.
- **Declared axis order** is reported separately, in
  {attr}`~geodetic_engine.geodesy.CoordinateReferenceSystem.axis_abbreviations`.
  It is never used to reorder your values without saying so.

{doc}`/background/axis-order` explains why, and the one PROJ bug this package
works around to make it true.

## Coordinate epoch

A **dynamic** reference frame, such as ITRF2014 or WGS 84 (G1762), moves with
the tectonic plates. Some transformations out of it take the time the
coordinates were observed at, as a decimal year such as `2021.5`. That time is
the **coordinate epoch**. A transformation that reads the epoch refuses to run
without one ({class}`~geodetic_engine.geodesy.MissingCoordinateEpochError`).
A transformation that does not read it needs none, even if a CRS involved is
dynamic.

## Grid

Some transformations interpolate in a **grid file** rather than applying a
formula: NTv2 and NADCON horizontal shifts, and geoid models for heights. The
grid must be installed. If it is missing, this package raises
{class}`~geodetic_engine.geodesy.MissingGridError` and names the file. PROJ
alone would quietly use a less accurate operation that does not need the grid.

## Bound CRS

A {term}`bound CRS` is a CRS packaged with the one transformation that takes it
to a hub CRS, almost always WGS 84. For example, ED50 bound to WGS 84 through
`EPSG:1612`. Whoever defined the CRS has already chosen the transformation, so
using a bound CRS counts as naming the operation. OSDU's CRS catalogue is mostly
bound CRSs. See {doc}`/background/bound-crs`.

## Ballpark

When PROJ knows no transformation between two datums, it can still produce a
**ballpark** result by treating the datums as the same. The error can be
hundreds of metres, and the result carries no accuracy. This package never
returns one
({class}`~geodetic_engine.geodesy.BallparkTransformationError`).
{func}`~geodetic_engine.geodesy.available_operations` still lists ballpark
candidates, so you can see that they are the only option.

## Provenance

Every {class}`~geodetic_engine.geodesy.TransformationResult` records the
operation applied and how it was chosen, the grids read, the coordinate epoch,
the exact PROJ pipeline, and fingerprints of the `proj.db` used. It is enough to
reproduce or audit the result later. See {doc}`/background/provenance`.
