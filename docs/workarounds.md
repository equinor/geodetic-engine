# Known issues and workarounds

This page lists every place where the package works around a defect or a gap
in PROJ, pyproj, the EPSG dataset, OSDU or ESRI WKT.
Each entry gives the symptom, the cause, what the package does, where the code
is, and when the workaround can be removed. An upstream issue is linked only
where the code cites one. "None referenced" means no upstream report is
recorded in this repository, not that the problem is unknown upstream.

Versions checked: PROJ {{ proj_version }} with EPSG {{ epsg_version }},
pyproj {{ pyproj_version }}, proj-data {{ proj_data_version }}.

## Overview

Each issue links to its full entry below.

**PROJ and pyproj**

| Issue | What the package does |
|---|---|
| [`always_xy` leaves an axis swap at a vertical end](#always_xy-leaves-an-axis-swap-at-a-vertical-end) | Detects the leftover swap and transposes the values first |
| [`always_xy` leaves an engineering CRS in declared order](#always_xy-leaves-an-engineering-crs-in-declared-order) | Adds the missing axis swap to the pipeline |
| [PROJ reads a Similarity transformation's ordinates in declared axis order](#proj-reads-a-similarity-transformations-ordinates-in-declared-axis-order) | Picks the reading that lands inside the area of use |
| [A bound CRS looked up by code comes back unbound](#a-bound-crs-looked-up-by-code-comes-back-unbound) | Rebuilds the bound CRS from its stored definition |
| [A bound CRS cannot carry a concatenated operation](#a-bound-crs-cannot-carry-a-concatenated-operation) | Collapses a chain of Helmert steps into one, checked to 1 mm |
| [A parts-per-billion scale is exported unconverted in a bound CRS](#a-parts-per-billion-scale-is-exported-unconverted-in-a-bound-crs) | Restates every scale in ppm before embedding |
| [Exporting an inverted datum step reverses it silently](#exporting-an-inverted-datum-step-reverses-it-silently) | Detects inverted steps and returns no export for them |
| [EPSG's reversible polynomial is not implemented](#epsgs-reversible-polynomial-is-not-implemented) | Restates the series as PROJ's `horner` operation |
| [A grid is reported missing when PROJ reads a renamed copy](#a-grid-is-reported-missing-when-proj-reads-a-renamed-copy) | Checks the grid files the compiled pipeline will read |
| [PROJ reads no ESRI `GEOGTRAN`, and writes none](#proj-reads-no-esri-geogtran-and-writes-none) | Parses and writes ESRI WKT itself |
| [PROJJSON drops nested identifiers and unit codes](#projjson-drops-nested-identifiers-and-unit-codes) | Reads the codes from pyproj's sub-objects instead |
| [Build configuration](#build-configuration) | Builds PROJ and pyproj from source so the pinned database is used |

**PROJ data**

| Issue | What the package does |
|---|---|
| [Missing `grid_alternatives` mapping for the 1′ EGM2008 geoid](#missing-grid_alternatives-mapping-for-the-1-egm2008-geoid) | Adds the mapping to a copy of `proj.db`, never the installed one |

**OSDU catalogue**

| Issue | What the package does |
|---|---|
| [No WKT for bound CRSs](#no-wkt-for-bound-crss) | Assembles each bound CRS with pyproj from its parts |
| [Bound CRS extent has no code](#bound-crs-extent-has-no-code) | Records the extent under the importing authority |

**ESRI WKT**

| Issue | What the package does |
|---|---|
| [`Bursa_Wolf` read as Coordinate Frame](#bursa_wolf-read-as-coordinate-frame) | Maps both methods to EPSG method 9607 |

**Known issues not worked around**

| Issue | What to do |
|---|---|
| [Projected CRSs with south or west axes keep their declared order](#projected-crss-with-south-or-west-axes-keep-their-declared-order) | Read `value_axis_abbreviations` for the order used |
| [A Similarity transformation into a northing-first engineering CRS](#a-similarity-transformation-into-a-northing-first-engineering-crs) | State the operation out of the grid instead |
| [Area of use is not enforced](#area-of-use-is-not-enforced) | Check points against `area_of_use` yourself |
| [Deprecated operations cannot be listed](#deprecated-operations-cannot-be-listed) | Name a deprecated operation by its code |

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
  `tests/geodesy/test_axis_order.py` covers the behaviour. PROJ still leaves
  the swap, and the example above still returns `(58.5, 5.5, 57.087)`.

### `always_xy` leaves an engineering CRS in declared order

Symptom
: PROJ's `always_xy` never normalises an engineering CRS, whatever its axes.
  A plant grid declared (northing, easting) is read and written in that order
  while every other end of the pipeline is east-first, so a point given in
  this package's `xy` order lands hundreds of metres off. Nothing raises; the
  first point of a symmetric test set (`7000, 7000`) even agrees.

Cause
: PROJ's `mustAxisOrderBeSwitchedForVisualization` considers geographic,
  projected and derived projected CRSs only.

What the package does
: For an engineering end declared northing-first and used by anything other
  than Cartesian Grid Offsets, it adds the `axisswap` PROJ omits to the core
  of the pipeline and rebuilds it. Cartesian Grid Offsets (EPSG:9656) is left
  alone because PROJ renders it as an east-first affine and does not adapt an
  engineering end to it, so the `xy` values already are what the step
  consumes. A grid with no east/north pair at all (`EPSG:5800` declares north
  and west) keeps its declared order, which is what
  {attr}`~geodetic_engine.geodesy.CoordinateReferenceSystem.value_axis_order`
  reports for it. The rebuilt pipeline is what runs and what
  {attr}`~geodetic_engine.geodesy.TransformationResult.pipeline` reports, so
  replaying it reproduces the result from the caller's own values.

Code
: `_correct_engineering_axes` and `_add_engineering_swaps` in
  `geodetic_engine/geodesy/transformation.py`; the block comment above them
  is the full account.

Upstream
: None referenced.

Remove when
: PROJ normalises engineering CRSs under `always_xy`.
  `test_proj_still_leaves_engineering_axes_alone` in
  `tests/geodesy/test_engineering_axes.py` fails the day it does, at which
  point the swap must be removed rather than adapted, or it is applied twice.
  The test still passes.

### PROJ reads a Similarity transformation's ordinates in declared axis order

Symptom
: `EPSG:1035` (Astra Minas Grid to Campo Inchauspe / Argentina 2) returns
  `(4915661.95, 2590394.63)` for the grid point `(10000, 20000)`: northing
  first, under an `xy` label. Chained into the geographic CRS the same point
  lands in Paraguay, 1 500 km from the site. The same happens to plant grids
  in registers that state their origin easting-first.

Cause
: PROJ takes "Ordinate 1/2 of evaluation point in target CRS" (method 9621)
  and A0/B0 (method 9624) as the target CRS's first and second **declared**
  axes, then appends an `axisswap` to normalise a northing-first target.
  EPSG's own data for `EPSG:1035` is authored the other way: read in
  declared order the stated origin `(2610200.48, 4905282.73)` lies in
  Antarctica; read easting-first it lies at Comodoro Rivadavia, the
  operation's area of use. The two conventions coexist in published data.

What the package does
: For a Similarity or Affine parametric transformation whose evaluation point
  is stated in a northing-first projected CRS, it unprojects the point under
  both readings and keeps the one that lands inside the operation's area of
  use (the CRS's when the operation has none, allowing 1° of slack). When the
  ordinates turn out east-first, PROJ's appended `axisswap` -- which then
  transposes a result that was already `xy` -- is removed from the pipeline
  before it is rebuilt. When neither or both readings are plausible, the
  operation is refused with
  {class}`~geodetic_engine.geodesy.OperationNotAvailableError` rather than
  run under a guess. An evaluation point stated in an engineering CRS cannot
  be placed on the map, so PROJ's reading is kept there.

Code
: `_drop_spurious_ordinate_swap` and `_ordinate_convention` in
  `geodetic_engine/geodesy/transformation.py`.

Upstream
: None referenced. The convention question itself (whether EPSG methods
  9621/9624 refer to declared axes or to an easting-first frame) is open
  between EPSG's guidance and its data.

Remove when
: EPSG and PROJ agree on the convention and `EPSG:1035` is restated or read
  accordingly. `test_epsg_1035_states_its_evaluation_point_easting_first` in
  `tests/geodesy/test_engineering_axes.py` fails the day EPSG restates it.
  The origin is still stated easting-first and PROJ still reads it in
  declared order.

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
  wrapper. It still discards it.

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
: Permanent, unless the bound CRS model gains support for chains. A
  `BoundCRS` over `EPSG:8047` is still refused.

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
  scale. The scale is still written as `2.25`.

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
: PROJ exports an inverted step in a form that reads back inverted. The
  issue's own example, `EPSG:4837`, still re-imports from PROJJSON about
  176 m off.

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
: PROJ implements method 9651. `CoordinateOperation.from_epsg(15753)` still
  fails with "coordinate operation not found".

### A grid is reported missing when PROJ reads a renamed copy

Symptom
: `EPSG:15851` cites `conus.las`/`conus.los`, which PROJ no longer ships.
  PROJ reads `us_noaa_conus.tif` instead, but reports the published names as
  unavailable. Believing that would refuse a transformation that runs
  correctly. The opposite failure exists too: PROJ keeps opened grids in
  memory, so a pipeline compiled while `us_noaa_conus.tif` was present still
  compiles once it is gone and fails at the first coordinate; and an
  operation stated outright (an OSDU payload) whose grid is absent fails to
  build with "Bad step definition", which says nothing about a grid.

Cause
: PROJ's grid availability is reported for the name the authority published,
  not for the file `grid_alternatives` substitutes, and compiling a pipeline
  is not proof that its files are on disk.

What the package does
: Reads the `grids=` of the compiled pipeline -- the complete statement of
  what will be read -- and checks each file on disk, across PROJ's search
  path and the user-writable directory. Every file present: the registry's
  "missing" names are taken as satisfied. Any file absent: it is reported
  under PROJ's own name in
  {class}`~geodetic_engine.geodesy.MissingGridError`, at construction. A
  stated operation PROJ cannot build for want of a file is refused the same
  way, naming the grids its definition cites. With PROJ's network access
  enabled a file may be fetched on demand, so its absence is then not held
  against it.

Code
: `_confirm_installed`, `_installed_grid` and `_stated_transformer` in
  `geodetic_engine/geodesy/transformation.py`.

Upstream
: None referenced.

Remove when
: PROJ reports availability for the substituted file and refuses to compile a
  step whose file is gone. `test_a_superseded_grid_filename_is_not_reported_missing`
  and the three `*_names_its_missing_grid` tests in
  `tests/geodesy/test_rules.py` cover both directions. `conus.las` and
  `conus.los` are still reported unavailable, and a compiled pipeline still
  outlives its grid file.

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
: Permanent. PROJ still neither reads nor writes a `GEOGTRAN`.

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
: PROJJSON export includes these identifiers. They are still omitted.

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
  `.gz` twin, plus `NNTrans2018B.gtx → no_kv_HREF2018B_NN54_NN2000.tif`) to a
  database **under the repository**: it runs as the last step of
  `scripts/build-projdb.sh` on `build/proj.db`, and
  `.devcontainer/link-local-grids.sh --patched-copy` writes a patched copy of
  the stock database to `local/proj-data/proj.db` for the developer to put
  first on `PROJ_DATA` by hand. The installed `proj.db` is never modified;
  `tests/test_environment.py` fails if it is. The patch is idempotent and
  leaves an existing row untouched.

Upstream
: None referenced.

Remove when
: `SELECT * FROM grid_alternatives WHERE original_grid_name='Und_min1x1_egm2008_isw=82_WGS84_TideFree'`
  returns a row in a stock PROJ database. Each entry in the script states why it
  is still needed. None of the three rows is there yet, and every grid name
  is still cited by a current operation.


## OSDU catalogue

### No WKT for bound CRSs

OSDU bound CRSs are assembled with pyproj from their parts, so the embedded
transformation is one this package has checked
({doc}`/user-guide/osdudb`).

### Bound CRS extent has no code

OSDU gives the extent as the intersection of the CRS and transformation
extents, with no code. It is recorded under the importing authority, not
dropped.

## ESRI WKT

### `Bursa_Wolf` read as Coordinate Frame

ESRI documents the Coordinate Frame and Bursa-Wolf methods as the same in its
Projection Engine (ArcSDE 10.0 SDK), so both map to EPSG method 9607.

## Known issues not worked around

### Projected CRSs with south or west axes keep their declared order

`EPSG:5513` (S-JTSK / Krovak) and `EPSG:2065` declare axes pointing south
and west, and the engineering `EPSG:5800` declares north and west. PROJ's
`always_xy` leaves such axes alone, and so does this package: values go in
and come out in the declared order, while `coordinate_order` still says
`"xy"` -- which this package defines as easting-first *wherever there is an
easting to put first*. `value_axis_abbreviations` reports the order actually
used (`('X', 'Y')`), so read it for these CRSs.

### A Similarity transformation into a northing-first engineering CRS

The ordinate-convention check above needs to place the evaluation point on
the map, which an engineering CRS cannot do. An operation defined *into* a
northing-first plant grid (rare: EPSG's are all defined out of the grid) is
read as PROJ reads it, ordinates in declared order. State such an operation
out of the grid instead, or with the grid declared east-first.

### Area of use is not enforced

An operation is applied wherever PROJ can compute it. Check points against the
operation's `area_of_use` yourself
({doc}`/user-guide/geodesy/choosing-operations`).

### Deprecated operations cannot be listed

PROJ's operation search always excludes deprecated EPSG operations, so
{func}`~geodetic_engine.geodesy.available_operations` has no option to include
them. A deprecated operation can still be named explicitly by code.
