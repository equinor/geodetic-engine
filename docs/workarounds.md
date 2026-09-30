# Known issues and workarounds

This page lists every place where the package works around a defect or a gap
in PROJ, pyproj, the EPSG dataset, the Georepository API, OSDU or ESRI WKT.
Each entry gives the symptom, the cause, what the package does, where the code
is, and when the workaround can be removed. An upstream issue is linked only
where the code cites one. "None referenced" means no upstream report is
recorded in this repository, not that the problem is unknown upstream.

Versions checked: PROJ {{ proj_version }}, pyproj {{ pyproj_version }},
proj-data 1.24.

```{contents}
:local:
:depth: 2
```

## PROJ and pyproj

### `always_xy` leaves an axis swap at a vertical end

Symptom
: With `always_xy=True`, a pipeline between a geographic 3D CRS and a vertical
  CRS reads or writes the horizontal position latitude-first. `EPSG:4937` to
  `EPSG:5776` returns `(58.5, 5.5, 57.087)` for input `(5.5, 58.5, 100)`. With
  a vertical source, a geoid grid is interpolated at the transposed position,
  giving a plausible wrong height if that position is inside the grid.

Cause
: `always_xy` normalises each end against that end's declared horizontal axes.
  A vertical CRS has none, so the `axisswap` belonging to the operation's own
  internal geographic CRS is never stripped.

What the package does
: Before running a pipeline from a vertical source, it checks whether the entry
  step swaps the horizontal pair. If so, it transposes the values first, and
  adds the swap to the reported `pipeline` so that replaying it reproduces the
  result.

Code
: `_entry_step`, `_swaps_horizontal`, `_order_horizontal_for_pipeline` and
  `_report_horizontal_swap` in `geodetic_engine/geodesy/transformation.py`,
  and `_Pipeline.reads_declared_horizontal`. The call is in
  {meth}`Transformation.transform <geodetic_engine.geodesy.Transformation.transform>`.

Upstream
: None referenced.

Remove when
: PROJ strips the leftover `axisswap` itself. The block of functions above can
  then be deleted outright, together with its call in `Transformation.transform`.
  Nothing else depends on it.
  `test_vertical_source_reads_its_position_in_xy_order` in
  `tests/geodesy/test_axis_order.py` covers the behaviour.

### A bound CRS looked up by code comes back unbound

Symptom
: `CRS.from_authority` on a CRS stored as `BOUNDCRS` WKT returns the base CRS
  with `is_bound == False` and no transformation. Naming it would be refused as
  an ambiguous datum change, even though the CRS defines its operation.

Cause
: PROJ discards the `BOUNDCRS` wrapper when building a CRS from a code. It
  still uses the binding for operation selection.

What the package does
: Reads the stored `text_definition` back out of `proj.db` and rebuilds the
  bound CRS from it, by code first and then by name. Only `BOUNDCRS` rows are
  read, once per PROJ data directory. This is the only direct read of `proj.db`
  at runtime.

Code
: `_rebound` in `geodetic_engine/geodesy/crs.py`; `bound_definition` and
  `bound_definition_by_name` in `geodetic_engine/geodesy/database.py`.

Upstream
: None referenced.

Remove when
: `tests/geodesy/test_bound_from_database.py` fails because PROJ keeps the
  wrapper.

### A bound CRS cannot carry a concatenated operation

Symptom
: A register's bound CRS over a chain, such as `EPSG:8047` (two Helmerts
  through ED87), cannot be written as a `BOUNDCRS`.

Cause
: A `BOUNDCRS` holds exactly one `ABRIDGEDTRANSFORMATION`. This is a limit of
  the WKT2 model, not a PROJ bug.

What the package does
: Composes the Helmert steps into one
  ({func}`~geodetic_engine.geodesy.utils.collapse_concatenated`), then checks
  the result against PROJ's evaluation of the chain over the area of use to
  1 mm. A chain that does not compose is skipped with a reason, never
  approximated. See {doc}`/background/bound-crs`.

Code
: `geodetic_engine/geodesy/utils/helmert.py`, used by
  `geodetic_engine/projdb/bound.py`, `geodetic_engine/osdudb/bound.py` and
  `geodetic_engine/persistablereference/reference.py`.

Upstream
: Not a defect. None referenced.

Remove when
: Permanent, unless the bound CRS model gains support for chains.

### A parts-per-billion scale is exported unconverted in a bound CRS

Symptom
: A bound CRS whose transformation gives its scale difference in ppb (most
  recent ITRF/ETRF transformations) is exported with the literal value. On
  reading it back, `0.33` ppb becomes a scale factor, and positions are
  kilometres out.

Cause
: PROJ converts to the abridged transformation's fixed units only from the
  units it expects. A ppb scale is written unchanged.

What the package does
: Restates every scale difference in ppm before embedding
  ({func}`~geodetic_engine.geodesy.utils.scale_in_parts_per_million`).

Code
: `geodetic_engine/geodesy/utils/abridged.py`.

Upstream
: None referenced.

Remove when
: A `BoundCRS` built over `EPSG:10586` exports and re-reads with the correct
  scale.

### Exporting an inverted datum step reverses it silently

Symptom
: When PROJ applies a Helmert backwards, it marks the step with an authority of
  `INVERSE(EPSG)`. WKT2 and PROJJSON cannot express that, so re-reading the
  export gives the *forward* operation: the datum shift with its sign reversed.

Cause
: No "apply backwards" flag exists in the export formats. PROJ keeps the
  forward parameters.

What the package does
: Detects inverted datum steps (`has_inverted_step` in
  `geodetic_engine/geodesy/operation.py`). In that case
  {meth}`AppliedOperation.to_wkt <geodetic_engine.geodesy.AppliedOperation.to_wkt>`
  and {meth}`OperationCandidate.to_wkt <geodetic_engine.geodesy.OperationCandidate.to_wkt>`
  return None, `to_persistable_reference` refuses, and chain collapse refuses.
  When reading ESRI chains, a reversed Helmert is written out as the forward
  Helmert of its exact inverse.

Code
: `geodetic_engine/geodesy/operation.py`,
  `geodetic_engine/persistablereference/emit.py`,
  `_reversed` in `geodetic_engine/persistablereference/reference.py`.

Upstream
: [OSGeo/PROJ#4866](https://github.com/OSGeo/PROJ/issues/4866).

Remove when
: PROJ exports an inverted step in a form that reads back inverted.

### EPSG's reversible polynomial is not implemented

Symptom
: EPSG method 9651, "Reversible polynomial of degree 4", used for example by
  ED50 to ED87 (1), `EPSG:15753`, cannot be built by PROJ.

Cause
: PROJ implements no EPSG polynomial method.

What the package does
: When reading an ESRI `Reversible_polynomial_of_degree_4`, it states the
  series as PROJ's `horner` operation. This reproduces EPSG's worked example.

Code
: `_polynomial_method` in `geodetic_engine/persistablereference/reference.py`.

Upstream
: [OSGeo/PROJ#4867](https://github.com/OSGeo/PROJ/issues/4867).

Remove when
: PROJ implements method 9651. With PROJ 9.8.1,
  `CoordinateOperation.from_epsg(15753)` fails with "coordinate operation not
  found".

### A grid is reported missing when PROJ reads a renamed copy

Symptom
: `EPSG:15851` cites `conus.las`/`conus.los`, which PROJ no longer ships.
  PROJ reads `us_noaa_conus.tif` instead, but reports the published names as
  unavailable. Believing that would refuse a transformation that runs
  correctly.

Cause
: PROJ's grid availability is reported for the name the authority published,
  not for the file `grid_alternatives` substitutes.

What the package does
: Treats a grid reported missing as installed only when the compiled pipeline
  reads a *different* file in its place. The reverse check still applies, so a
  genuinely missing grid is still refused.

Code
: `_confirm_installed` in `geodetic_engine/geodesy/transformation.py`.

Upstream
: None referenced.

Remove when
: PROJ reports availability for the substituted file.
  `test_a_superseded_grid_filename_is_not_reported_missing` in
  `tests/geodesy/test_rules.py` covers it.

### PROJ reads no ESRI `GEOGTRAN`, and writes none

Symptom
: PROJ parses ESRI WKT for CRSs, but not the `GEOGTRAN` element that states a
  transformation. It will not write a transformation as ESRI WKT, and for a
  bound CRS its ESRI WKT output silently drops the datum shift.

What the package does
: Parses and writes ESRI WKT itself, translating method and parameter names and
  units through fixed tables. Anything that cannot be stated exactly is
  refused ({doc}`/user-guide/persistable-reference`).

Code
: `geodetic_engine/persistablereference/esriwkt.py`,
  `geodetic_engine/persistablereference/methods.py`,
  `geodetic_engine/persistablereference/emit.py`.

Upstream
: Not a defect. None referenced.

Remove when
: Permanent.

### PROJJSON drops nested identifiers and unit codes

Symptom
: A CRS's PROJJSON export omits the codes of its datum, ellipsoid and prime
  meridian, and gives units by name and factor with no code.

What the package does
: When importing OSDU records, reads nested codes from the pyproj sub-objects,
  and matches units against the `unit_of_measure` table. A unit that cannot be
  matched is refused, not guessed.

Code
: `geodetic_engine/osdudb/definition.py` (`identifier_of`, `UnitResolver`).

Upstream
: None referenced.

Remove when
: PROJJSON export includes these identifiers.

### Build configuration

`-DEMBED_RESOURCE_FILES=OFF`
: With `proj.db` embedded in `libproj`, PROJ can ignore a custom database on
  disk. `.devcontainer/install-proj.sh` builds PROJ without embedding.

pyproj built from source
: PyPI wheels bundle their own PROJ, which would replace the pinned version
  silently. `pyproject.toml` sets `no-binary-package = ["pyproj"]`, and the
  image sets `PROJ_WHEEL=false`. `tests/test_environment.py` checks this.

## PROJ data

### Missing `grid_alternatives` mapping for the 1′ EGM2008 geoid

Symptom
: `EPSG:3859` / `EPSG:9618` (WGS 84 to EGM2008 height, 1′ grid) report their
  grid as missing even when `us_nga_egm2008_1.tif` is installed.

Cause
: PROJ's `proj.db` has a `grid_alternatives` row for the 2.5′ variant only.

What the package does
: `scripts/patch-grid-alternatives.sh` adds the row
  `Und_min1x1_egm2008_isw=82_WGS84_TideFree → us_nga_egm2008_1.tif` (and its
  `.gz` twin, plus `NNTrans2018B.gtx → no_kv_HREF2018B_NN54_NN2000.tif`). It
  runs as the last step of `scripts/build-projdb.sh`, and the devcontainer's
  `postStartCommand` (`.devcontainer/link-local-grids.sh`) also runs it against
  the **installed** `proj.db`, so inside the devcontainer the stock database is
  modified. It is idempotent and leaves an existing row untouched.

Upstream
: None referenced.

Remove when
: `SELECT * FROM grid_alternatives WHERE original_grid_name='Und_min1x1_egm2008_isw=82_WGS84_TideFree'`
  returns a row in a stock PROJ database. Each entry in the script states why it
  is still needed.

## Georepository API

These are behaviours of the API as implemented, worked around in
`geodetic_engine/georepository/client.py`. The code documents each one where it
is handled.

Export exists only on the generic CRS collection
: `GeodeticCoordRefSystem/{code}/export` returns HTTP 404, while
  `CoordRefSystem/{code}/export` returns the WKT. CRS export URLs are rewritten
  to the generic collection (`_export_url`).

`formatVersion` is not usable
: The parameter is a small enum, not a year. Passing a year is rejected, and
  `2019` returns HTTP 500. The default already gives WKT2, so it is not sent.

No server-side authority filter
: Every collection is enumerated in full and filtered on `DataSource` on the
  client side. This makes annotation of other authorities' objects the slowest
  part of a build.

Version history entries state no `DataSource`
: The register's own version series is told apart from the EPSG dataset's by
  code: the register's own codes start at 40,000,000.

Detail responses may lack a self link
: A `Links` entry pointing at the fetched URL is added, so per-object
  sub-resources such as aliases can still be reached.

Pagination is verified
: Every page is followed until the advertised `TotalResults` is collected. A
  shortfall raises
  {class}`~geodetic_engine.georepository.PaginationTruncatedError`, never a
  silently short list.

## OSDU catalogue

No WKT for bound CRSs
: OSDU bound CRSs are assembled with pyproj from their parts, so the embedded
  transformation is one this package has checked
  ({doc}`/user-guide/osdudb`).

Bound CRS extent has no code
: OSDU gives the extent as the intersection of the CRS and transformation
  extents, with no code. It is recorded under the importing authority, not
  dropped.

## ESRI WKT

`Bursa_Wolf` read as Coordinate Frame
: ESRI documents the Coordinate Frame and Bursa-Wolf methods as the same in its
  Projection Engine (ArcSDE 10.0 SDK), so both map to EPSG method 9607.

## Known issues not worked around

### Engineering CRSs whose axes are not east and north

`always_xy` cannot normalise an engineering CRS declared, for example, north
and west (`EPSG:5800`, Astra Minas Grid). With PROJ {{ proj_version }} the
result can come back in the target's **declared** order, while the result still
says `coordinate_order == "xy"`:

```python
from geodetic_engine.geodesy import transform

result = transform("EPSG:5800", "EPSG:22192", (10000.0, 20000.0), operation="EPSG:1035")
result.coordinates        # ((4915661.95..., 2590394.63...),)  northing first
result.coordinate_order   # 'xy'
result.target_crs.value_axis_abbreviations  # ('Y', 'X'): easting expected first
result.coordinates.to_dataframe()  # column 'Y' holds the northing
```

`EPSG:22192` declares `X` = northing and `Y` = easting, so the values above
are northing, easting. Read back as `xy` they land thousands of kilometres from
Astra Minas. Check the order yourself whenever the source or target is an
engineering CRS whose axes are not east and north. Engineering CRSs with
east/north axes, such as `EPSG:5817` on
{doc}`/user-guide/geodesy/transformations`, are not affected. The same gap
affects some custom-register engineering CRSs.

### Projected CRSs with south or west axes keep their declared order

`EPSG:5513` (S-JTSK / Krovak) and `EPSG:2065` declare axes pointing south
and west. PROJ's `always_xy` leaves such axes alone, and so does this package:
values go in and come out as (southing, westing), the declared order, while
`coordinate_order` still says `"xy"`. `value_axis_abbreviations` reports the
order actually used (`('X', 'Y')`), so read it for these CRSs.

### Area of use is not enforced

An operation is applied wherever PROJ can compute it. Check points against the
operation's `area_of_use` yourself
({doc}`/user-guide/geodesy/choosing-operations`).

### Deprecated operations cannot be listed

PROJ's operation search always excludes deprecated EPSG operations, so
{func}`~geodetic_engine.geodesy.available_operations` has no option to include
them. A deprecated operation can still be named explicitly by code.
