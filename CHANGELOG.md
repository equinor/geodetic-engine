# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- An installed distribution can provide the database PROJ reads in place of
  the shipped OSDU one, by naming a directory that holds a `proj.db` under the
  entry point group `geodetic_engine.proj_data`. The import's rules,
  `use_bundled_database()` and `active_database()` treat it as the bundled
  database, and the reason names the distribution. It is passed over, with a
  warning, if it was built for another PROJ release than pyproj links, if
  PROJ would refuse its database layout, judged by the `proj.db` that came
  with that PROJ rather than the one PROJ reads, if more than one
  distribution provides one, or if it cannot be loaded, its module raising
  an exception or calling `sys.exit()`; and every one is if the installed
  distributions' entry points cannot be read, so that a malformed
  `entry_points.txt` in any of them fails no import.
  `GEODETIC_ENGINE_OSDU_DB=0` keeps the import from loading any. It is found,
  its module imported and its directory made absolute, before the search
  path lock is taken.
- `scripts/build_organisation_projdb.py` builds a Georepository register once,
  as a wheel its users `pip install` from a private package index, with no
  Georepository URL, authorities or credentials of their own. It builds the
  register, the official OSDU catalogue and the grid filename patches into
  the pinned pyproj wheel's own `proj.db`, in a new environment holding the
  dependency versions `uv.lock` pins; on Windows, where the grid patch cannot
  run, it refuses to start without `--skip-grid-patch`. Every process it
  starts, `ensurepip`, uv and `git` included, gets only what it needs
  of the caller's environment to run and to reach the network, and the
  register import alone also the two credentials. It refuses a database that
  does not open read-only, carries other metadata than the wheel's
  `proj.db`, or holds nothing of an authority it imported.
  The wheel is versioned as the PROJ release and the minute of the build, so
  `<name>==<PROJ release>.*` installs the newest build for that release, and
  holds the database alone in its directory, the build reports, and a
  manifest naming the PROJ release, EPSG version and database layout it is
  for. The new page *Sharing a built database* explains why it belongs in a
  private index and not a public one, how to avoid dependency confusion with
  uv and pip, extra indexes in pip's configuration included, and gives a
  workflow that builds and uploads it.

### Changed

- `scripts/build_bundled_osdudb.py` and `scripts/check_installed_wheel.py`
  pass their processes only what they need of the caller's environment:
  `PATH`, `HOME`, the temporary directories, the locale, proxies and
  certificate bundles, and pip alone also its `PIP_` settings. They passed
  every variable but `PROJ_` and `GEODETIC_ENGINE_` ones, and `venv` passed
  every one to the `ensurepip` it runs, so a secret such as an upload token
  reached pip, the builds and the check. `ensurepip` is now run as `venv`
  runs it, with the narrower environment.

### Fixed

- `scripts/check_package_dist.py`, and with it `scripts/build_bundled_osdudb.py`
  and `scripts/check_installed_wheel.py`, could not read a database on a UNC
  path, such as one in a temporary directory on a network share:
  `Path.as_uri()` made the server the URI's authority, which SQLite refuses.
  They now open a database as `geodetic_engine` does.

## 0.2.0 - 2026-11-04

### Added

- Geometries (#23). `Transformation.transform_geometry()` and
  `transform_geometries()` transform vector geometries read from WKT, WKB and
  hex WKB, PostGIS EWKT and EWKB, GeoJSON geometry objects, Esri JSON
  geometries, Shapely geometries and anything with a `__geo_interface__`; a
  batch may be a sequence, a numpy array, a pandas Series or a GeoPandas
  GeoSeries, and its encodings come back in the same container. Every
  coordinate goes through `transform_arrays()` in one call per number of
  values per position, so the operation, grids, epoch and range checks are
  those of points, and the coordinates are bit for bit the same. A position
  that fails fails the batch, naming its geometry, part, ring and vertex. Z is
  transformed where the CRSs have a height and carried through otherwise; M
  is carried through. An SRID or Esri `spatialReference` must state the
  source CRS; `srid_authority` says which authority an SRID is a code of.
  `axis_order="crs"` reads and writes a CRS's declared order, `densify`
  inserts vertices before transforming, along straight segments in source CRS
  units or, with `segments="geodesic"`, along geodesics on the source CRS's
  ellipsoid in metres, and `orient` winds rings, which are
  otherwise kept as transformed. In a geographic target CRS, a part crossing
  the antimeridian is cut there into parts of a multipart geometry, as RFC
  7946 has it (`antimeridian="split"`; `"unwrap"` carries its longitudes on
  past ±180° instead), and a ring round a pole is closed through it. The new
  module `geodesy.geometry` has
  `read_geometry`, `write_geometry` and `round_geometry`: `decimals` rounds
  half up by each value's shortest decimal form (`2.675` is `2.68`, `-0.125`
  is `-0.13`), before encoding, so every encoding holds the same numbers, and
  reports a polygon it made invalid with `GeometryValidityWarning`;
  `grid_size` snaps to a grid instead. WKB is written ISO, EWKB extended, and
  WKT with the shortest form of each number that reads back the same.
  `geodesy.GeometryError` is raised for geometries that cannot be read or
  written. Shapely 2.2 is a new dependency; `import geodetic_engine.geodesy`
  does not load it.
- `geodetic_engine.geojson.convert_geojson` (#21) converts RFC 7946 GeoJSON,
  OGC JSON-FG 1.0 and OSDU AnyCrs GeoJSON documents, with a
  `FeatureCollection`, a `Feature` or a geometry at the root, to another CRS,
  keeping structure, `id`, `properties`, foreign members and key order, and
  computing every `bbox` again. JSON-FG `place` is read in the nearest
  `coordRefSys`, in its declared axis order and with its epoch, and an ad hoc
  compound `coordRefSys` transforms heights between vertical datums; a
  GeoJSON 2008 `crs` member is read too. Plain GeoJSON in a CRS other than WGS
  84 is written as JSON-FG (`output="auto"`), and `"geojson"`, `"anycrs"`,
  `"jsonfg"` and `"jsonfg-plus"` choose the flavour. An AnyCrs vertical CRS
  makes heights compound rather than only scaled; `target_unit_z` converts
  heights, and `persistableReferenceUnitZ` is always written for them.
  `on_error` raises naming the position's JSON path, withdraws the feature's
  geometry, or writes NaN as the OSDU service does. `decimals` and `orient`
  round and wind as for geometries, and `densify` and `segments` insert
  vertices along straight segments or geodesics as for them, which are
  converted and counted with the others. In a geographic target CRS, a line
  or polygon crossing the antimeridian is cut there as for geometries, its
  `type` renamed to the multipart one, and a ring round a pole is closed
  through it (`antimeridian="split"`; `"unwrap"` or None as for geometries);
  a `bbox` then crosses the antimeridian, its west bound east of its east
  one, as RFC 7946 (5.2) has it, where that box is the narrower and no wider
  than half a turn.
  `convert_geojson_request` takes and
  returns the OSDU service's `convertGeoJson` request and response, its
  positions never cut.

- `geodetic_engine.units`: one reader for the length and angle units the
  package takes, from a symbol or name, an OSDU `UnitOfMeasure` record id,
  URL-encoded or not, or an OSDU unit persistableReference, `USO` or a `UAD`
  that reduces to a scale. `length_unit` and `angle_unit` return a `Unit`,
  with its symbol, quantity and scale, `to_si`, `from_si`, and `reference()`,
  which gives back the payload it was read from or writes one. A unit that
  cannot be read raises `units.UnitError`, a `ValueError`. The tables of
  symbols and of the ancestries each quantity accepts now exist only here:
  `welltrajectory.length_factor` and `angle_factor` read units through it,
  and the GeoJSON groundwork no longer imports `welltrajectory` (#30).

- The PROJ, proj-data and pyproj releases, their checksums and the pyproj
  wheel index are stated in one file, `.devcontainer/pins.env`, parsed
  strictly and never sourced. `install-proj.sh`, the Dockerfile, the
  workflows, the release scripts and the tests read it, and the wheel ships a
  copy that `geodetic_engine` reads on import. `scripts/sync_pins.py` rewrites
  the copies that must stay literal text (the `pyproj` dependency, then
  `uv lock`, and the install commands in the README, the docs and the example
  notebook), and `--check`, run by the lint workflow and the tests, fails on a
  stale one. The environment checks also fail when pyproj was compiled against
  another PROJ than it links, or when the installed proj-data is not the
  release the pinned PROJ's `proj.db` names. `runbooks/upgrading-proj-and-pyproj.md`
  is the maintainers' procedure for moving the pins.
- The wheel ships the official OSDU catalogue already built into PROJ's
  database, so OSDU CRSs, record ids and persistable references resolve right
  after `pip install`, with no build step, on Linux, macOS and Windows. The
  wheel stays `py3-none-any`; the database adds 2.8 MB to it (23.2 MB on
  disk). `pypi_release.yml` builds it at release time with
  `geodetic-osdudb build --official`, in a new environment with the pinned
  pyproj wheel, whose own `proj.db` is the base, so its EPSG content is
  exactly what users install. `scripts/build_bundled_osdudb.py` builds the
  same file for developers. It is generated and gitignored, and the build
  backend includes it in both the sdist and the wheel, as
  `geodetic_engine/osdudb/_data/proj_data/proj.db`, alone in its directory.
  `scripts/check_package_dist.py` requires it, and checks that it opens
  read-only, carries the metadata of the pinned pyproj wheel's own `proj.db`
  (PROJ release, database layout, EPSG version), holds OSDU
  objects, ids and persistable references, and records the commit and SHA-256
  of the bundled catalogue; the sdist must carry the same file. Nothing in
  these scripts or workflows states a version: the pins are read from
  `.devcontainer/pins.env`, in the checkout or as the copy in the built wheel.
  CI installs
  the built wheel into a clean environment on Linux and Windows and resolves
  `OSDU:2003001` and an OSDU record id from it, also in a spawned child
  process, with no build step and no `PROJ_` variable set. The build and the
  check leave out every `PROJ_` variable of the caller's, not only
  `PROJ_DATA`: a `PROJ_AUX_DB` would be attached to the base database, and
  could answer the check in the shipped one's place.
- `import geodetic_engine` makes the shipped database the one PROJ reads
  unless another was chosen, in the precedence of the project's pyproj
  wheels. The first rule that matches decides: `GEODETIC_ENGINE_OSDU_DB=0`
  changes nothing; nor does a `pyproj.datadir.set_data_dir()` or
  `append_data_dir()` call made before the import; nor a `PROJ_DATA`, or
  `PROJ_LIB` when `PROJ_DATA` is unset, that names a directory holding a
  `proj.db`. PROJ then searches the variable's directories, the one holding
  the `proj.db` first; a value pyproj has not read, such as one set after
  pyproj was imported, is applied, with a warning, so a process reads the
  database a child started with its environment reads; but not if that
  `proj.db` records no database layout, or an older or another one than the
  `proj.db` PROJ reads, which PROJ would refuse: then nothing changes, with a
  warning. Otherwise PROJ searches the shipped database's directory first,
  then the `PROJ_DATA`/`PROJ_LIB` directories, then pyproj's own, so grids and
  `proj.ini` are still found. The outcome is logged at `INFO`, with the
  reason when the shipped database is not used, such as
  `PROJ_LIB=C:\OSGeo4W\share\proj holds a proj.db`. A later
  `set_data_dir()` still overrides it. The new
  `geodetic_engine.osdudb.active_database()` reports which `proj.db` PROJ
  reads and why, and `use_bundled_database()` switches the shipped one on or
  off. Another thread follows a switch the next time it calls
  `active_database()` or resolves anything through the package, so what is
  reported is what that thread reads. pyproj's installed `proj.db` is never
  modified, provenance names the shipped file when it answered, and a spawned
  child process applies the same rules when it imports the package; a forked
  one inherits its parent's choice, and a fork waits for a switch in progress
  to finish. `geodetic-osdudb build` and `geodetic-projdb build` still start
  from the stock database, and `geodetic-osdudb build` is still how to get a
  newer or a custom catalogue.
  The test suite runs with `GEODETIC_ENGINE_OSDU_DB=0`, against the stock
  database, and tests each rule in a fresh interpreter.

- Packaging. `pyproj-wheels.yml` builds pyproj with pyproj's own release
  tooling against the pinned PROJ, read from `.devcontainer/install-proj.sh`,
  and releases the wheels on GitHub as `pyproj 3.8.0+proj9.9.0`. The
  documentation site serves them as a package index under `simple/`.
  `pypi_release.yml`, run by hand, publishes the package to TestPyPI or PyPI,
  as chosen, with trusted publishing, and tags a PyPI release `v<version>`
  with a GitHub release carrying its changelog section. It is published as
  `pygeodetic-engine`, since PyPI refuses `geodetic-engine` as too close to
  the existing `geodeticengine`; it is still imported as `geodetic_engine`.
  `geodetic_engine.__version__` is read from the installed package's metadata,
  so it reports the version that was published.
- Installed packages reject a pyproj linked to a different PROJ. The custom
  wheels honor `PROJ_DATA` ahead of their bundled database, and the documented
  installation commands select the exact pinned wheel even when another
  pyproj is already installed. The wheel includes the official OSDU catalogue;
  `geodetic-osdudb build --official` verifies and builds it offline.
- The source-built test suite runs on CPython 3.15 as well as 3.13, and the
  pyproj wheel workflow also builds CPython 3.15 wheels, without changing the
  minimum supported Python version.
- The pyproj wheel workflow caches native dependencies and PROJ per platform;
  wheels are still rebuilt and smoke-tested on every run. A manual clean
  rebuild can bypass the caches.
- `Transformation.transform_arrays()` returns owned NumPy axis buffers with
  provenance, without allocating Python objects per output point.
  `transform_batches()` lazily processes numeric row batches without prefetch
  or retained history. Inputs are never modified; array execution uses private
  contiguous buffers in place through PROJ, vectorised validation and cached
  pipeline text. The existing tuple-based API and geodetic rules are preserved.
  `ArrayTransformationResult.metadata_json_dict()` exports provenance separately
  from coordinates for streaming adapters. An engine benchmark compares the
  numeric and legacy paths to raw pyproj and measures bounded-stream allocations.
- `geodetic_engine.geodesy.projection_factors`: grid convergence, point scale
  factor, meridional and areal scale and angular distortion at any point of a
  projected CRS, read through a bound CRS's base and a compound CRS's
  horizontal part, with the sign convention stated. Web Mercator's scales are
  given on its ellipsoid rather than PROJ's sphere.
  `ProjectionFactors.conformal` says whether the projection preserves angles,
  and the helpers that turn azimuths between grid and true north refuse one
  that does not. A CRS with no map projection raises the new
  `UnsupportedCRSError`, which the bin grid module's own `UnsupportedCRSError`
  also is. Documented in the geodesy user guide.
- `geodetic_engine.welltrajectory`: well trajectories from directional
  surveys, by minimum curvature, georeferenced in a CRS on its own datum by
  one of four methods, with dogleg severity, interpolation along the arcs and
  3D plots. A survey whose first station is not at MD 0 is tied in there, at
  the wellhead. Azimuths against grid north need a projection that preserves
  angles at the wellhead, as does `GridNorthLocal`; azimuths against true
  north are placed in any geographic or projected CRS. LMP integrates from
  the surveyed stations, so points added between them never move them, and a
  step giving more than a million points is refused. The input is a
  `TrajectoryInput`, built from arrays, rows, a pandas DataFrame, a CSV survey
  file, or an OSDU `convertTrajectory` request body read as the service reads
  it, and the result a `WellTrajectory`. The CSV reader finds the survey
  columns by their usual names or by name given as an argument, ignores other
  columns, detects the delimiter, takes the unit from a name such as
  `MD (ft)`, and reads the wellhead and the settings from a header under
  their usual names; a unit it cannot read, or one that disagrees with a
  column's own, is refused, never defaulted. It also reads a survey report as
  it is: free text above a table lined up with spaces, with a line of units,
  in UTF-8 or Latin-1, taking only the name and the north reference from the
  free text. A synthetic survey file and the survey report of Volve F-1, a
  real well, ship in `example_data`. Plotting needs the new `plot` extra,
  which the `docs` extra includes; `open_in_browser` serves a figure at an
  unguessable local address. Documented in a user guide section of its own,
  the API reference and an example notebook.
- Documentation site built with Sphinx and published to GitHub Pages: getting
  started, a user guide for every module, executed examples, a generated API
  and command-line reference, background, and a list of known issues and
  workarounds (#1).
- `AppliedOperation.bound_operations`: the operations the CRSs themselves
  declared when the route is `bound`, so a pair of bound CRSs reports both
  codes instead of none.
- `operation=` accepts a `pyproj.crs.CoordinateOperation` as the operation
  itself, applied as stated.
- A longitude beyond a full turn is refused with `CoordinateOutOfRangeError`,
  as an impossible latitude already was.
- `geodetic_engine.bingrid`: seismic bin grids. P6 bin grid to map grid
  conversion, run as PROJ's affine operation with the coefficients PROJ gives
  EPSG methods 9666 and 1049, four-corner QC and squaring, outlines, and
  conversion to another CRS through `geodesy`. The bin grid scale factor is
  PROJ's point scale factor at the grid centre.
  `geodetic_engine.bingrid.matching` assigns a legacy dataset to a stored grid.
  The defects of the OSDU Java service's bin grid computation are not
  reproduced; each has a test marked `java_defect` stating the correct
  behaviour.
- OSDU record ids, such as
  `opendes:reference-data--CoordinateReferenceSystem:BoundProjected:EPSG::23031_EPSG::1133`
  or `opendes:reference-data--CoordinateTransformation:EPSG::1612`, resolve
  wherever an authority code does: CRS inputs, `operation=`, and the new
  `CoordinateReferenceSystem.from_osdu_id`, which also takes a bare `data.ID`.
  `geodetic-osdudb build` records the id of every catalogue record against the
  object it names, including EPSG objects the base `proj.db` already defines,
  in a new `geodetic_engine_osdu_object_identifier` table keyed by scheme and
  id.
  The data partition and any version separator are ignored. Lookup is exact
  and table-only: an id is never decoded from its spelling or matched
  approximately, so a mistyped id is refused rather than resolved to a near
  miss. A bound CRS's id resolves to the bound CRS. Ids that name nothing are
  listed in the report with the reason, an id published for two objects is
  recorded for neither, an append that would move an id to another object, or
  publish an earlier id for more than one object, is refused without
  `--overwrite-rows`, which moves or removes it, and validation checks every id
  still names an object. Each id is also written as a PROJ alias (source `OSDU_ID`)
  for tools reading the database through PROJ; PROJ matches aliases
  approximately, so `--no-osdu-id-aliases` / `osdu_id_aliases = false` leaves
  them out. `inspect` counts the ids by scheme. The OSDU user guide shows how
  to instantiate CRSs, bound CRSs and operations by id, and section 8 of the
  worked examples notebook runs those examples against a small database it
  builds first, since an id resolves only once its record is in `proj.db`.
- OSDU persistable references: `geodetic-osdudb build` records the
  `PersistableReference` every catalogue record publishes, verbatim, against
  the record's id, including for EPSG objects the base `proj.db` already
  defines, in a new `geodetic_engine_osdu_persistable_reference` table. The
  table is keyed by record id with a foreign key to the identifier table, and
  a view joins each reference to its object. Each row also records the kind,
  stated name, `authCode`, version and SHA-256. A record that states no
  reference (every geocentric CRS), or one that cannot be read or states the
  wrong kind of object, never fails the build: it is not recorded, and it is
  listed in the report's new `persistable_references` section with the
  reason. A reference that disagrees with its record, such as an `authCode`
  naming another code, is recorded as published with a warning. An append that
  would change a recorded reference is refused without `--overwrite-rows`, and
  one whose catalogue no longer states a usable reference keeps the recorded
  one, with a warning, unless `--overwrite-rows` removes it.
  Validation checks every digest and kind. `--no-persistable-references` /
  `osdu_persistable_references = false` leaves the references out.
  `geodetic_engine.persistablereference` gains
  `persistable_reference_from_catalog`, `persistable_references_from_catalog`,
  `export_persistable_references_from_catalog` (JSON, JSONL or CSV),
  `missing_reference_reason`, `pretty_persistable_reference` (indented JSON,
  optionally with the ESRI WKT laid out over lines for reading),
  `CatalogPersistableReference` and `AmbiguousCatalogReferenceError`.
  `CoordinateReferenceSystem.persistable_reference_from_catalog()` and
  `AppliedOperation.persistable_reference_from_catalog()` look up the
  reference for a CRS or for the operation applied. `esriwkt.pretty` lays ESRI
  WKT out, changing whitespace only. There is a new `geodetic-osdudb
  references` command, and `inspect` counts the references by kind.
- The official OSDU catalogue is committed and installed by default in the
  devcontainer. OSDU's public `CRS_CT.json` (Apache 2.0) is committed byte for
  byte as `catalogs/osdu/CRS_CT_osdu_official_crs_catalog.json`, with the OSDU
  commit and SHA-256 it came from in `CRS_CT_osdu_official_crs_catalog.source.json`
  and an entry in `NOTICE`. The new `scripts/install-osdu-projdb.sh` builds it
  into `build/proj.db`, recording that commit as the report's
  `source_version`, and links `local/proj-data/proj.db` to it; nothing is
  downloaded. The devcontainer runs it on creation and puts `local/proj-data`
  first on `PROJ_DATA`, so OSDU CRSs, bound CRSs, transformations, record ids
  and persistable references resolve out of the box, and the documentation
  build runs it too. A committed catalogue changed by a pull is imported again
  on the next run, and `--check`, run on every start, says when it has not
  been yet. A failure never fails container creation: it prints a warning
  saying why and keeps an earlier `build/proj.db` in use, or, without one,
  says that no OSDU objects are available and links the stock database
  instead; `--strict` makes it exit non-zero. A `build/proj.db` built for
  another PROJ release or database layout, which PROJ cannot open, is never
  linked: it is moved to `build/proj.db.stale` and the official catalogue is
  built from scratch. `--update` first
  replaces the committed catalogue with the newest OSDU publishes, for the
  change to be reviewed and committed, through the new `geodetic-osdudb
  download` and `geodetic_engine.osdudb.download_official_catalog`. These read
  it through GitLab's API, at `master` or any `--ref`, and write nothing
  unless it arrived whole, matches the digest the server states and reads as a
  manifest; otherwise the new `CatalogDownloadError` is raised and the
  committed copy is left as it was. The installed `proj.db` is still never
  modified, and the tests still run against it alone in CI.
- Custom OSDU catalogues on top of the official one. Appended with
  `--overwrite-rows`, a catalogue's own CRSs and transformations are added, a
  record it states differently replaces the earlier definition, and one it
  states identically is left untouched. Every replacement is named: the build
  summary lists each replaced definition with the columns that changed, each
  OSDU id now naming another object, and each persistableReference replaced or
  removed as a `WARNING`, and the report records them under `overrides`, with
  the counts `objects_replaced`, `identifiers_moved` and
  `persistable_references_replaced`. A collision refused without
  `--overwrite-rows` now says how to let the source replace the earlier
  definition. `scripts/install-osdu-projdb.sh` builds every catalogue in
  `local/catalogs/custom/`, and each `--custom`, on top of the official one on
  every run, importing each code space it publishes records under except
  EPSG, so they stay on top when the official catalogue is imported again; one
  that cannot be built is a warning, or an error with `--strict`. The console
  names the first 25 replacements of each kind, and the log file and report
  every one.
- `TransformationFailedError.point` and `.detail`: the index of the point a
  transformation failed for, counted in the batch or stream, and what is wrong
  with it, without the `point N` its message starts with. When the failure is
  not one point's, `point` is None and `detail` is the whole message.

### Changed

- `geodesy` and `persistablereference` no longer import each other (#29).
  proj.db access, OSDU record ids, the PROJJSON inspection payloads need, the
  `xy` axis-order model, the Helmert algebra, and `GeodesyError`,
  `UnembeddableOperationError` and `NotCollapsibleError` moved to a private
  lower layer, `geodetic_engine._referencing`. Every public import path still
  works, re-exporting the same objects, so `except` clauses match as before;
  only `geodetic_engine.geodesy.utils.helmert` and `.abridged`, never
  documented, are gone, with their names still in `geodesy.utils`. A traceback
  names the three moved exceptions by their new module. Importing
  `persistablereference` no longer loads `geodesy`, and importing `geodesy`
  now loads `persistablereference` up front, about 20 ms of a 1.2 s import.
  `persistable_reference_from_catalog` and `missing_reference_reason` accept
  any `ResolvedCrs`, an object with the `definition` it was resolved from and
  its pyproj `crs`, as a `CoordinateReferenceSystem` is. `lint-imports`
  ([import-linter](https://import-linter.readthedocs.io/)) now runs in the
  lint workflow and enforces the layering, that `welltrajectory`, `bingrid`
  and `geojson` do not import each other, and that `units` imports neither
  `geodesy` nor a domain package.

- `welltrajectory.UnitError` is also a `units.UnitError`, and so a
  `ValueError`; it is still a `WellTrajectoryError`. Some of its messages for a
  unit persistableReference are worded differently, and a unit that is not a
  string is refused as one rather than read as its text. A unit payload is
  URL-decoded as `persistablereference` decodes any payload, at most twice and
  `+` as a space, so one encoded three times, or with only its quotes encoded,
  is no longer read.

- `geodetic-osdudb build` logs a bound CRS it cannot import, such as the NTF
  (Paris) ones in the official catalogue, as a warning rather than an error.
  It is still skipped and listed in the build report.
- The default base database of a build skips a database this package built,
  so a build still starts from the official `proj.db` when an enriched one is
  ahead of it on PROJ's search path. That includes the OSDU database the wheel
  ships.
- An OSDU id the active `proj.db` cannot answer, because it records no OSDU
  ids, is refused with a message that names `active_database()` and
  `use_bundled_database()`.
- PROJ 9.9.0 with EPSG v13.102 and proj-data 1.25, up from PROJ 9.8.1 with
  EPSG v12.029 and proj-data 1.24; pyproj stays at 3.8.0. PROJ 9.9.0 refuses
  a `proj.db` with database layout 1.6, so a database built on PROJ 9.8.1 must
  be rebuilt. Every workaround on the known-issues page was re-checked and is
  still needed.
- Examples, docs and tests name `EPSG:11559` instead of `EPSG:9484`, which EPSG
  v13.102 deprecated and restated from ETRS89. The test dataset follows the
  v13.102 changes to `EPSG:28991`, `EPSG:2180` and four deprecated vertical
  operations; no expected coordinate changed.
- `available_operations` can list many more candidates for a pair whose
  datums have no area of use in common: PROJ 9.9.0 no longer drops chains
  whose steps do not overlap when extents are ignored (OSGeo/PROJ#4699). Such
  a candidate has no `area_of_use`.

### Fixed

- A unit persistableReference as the OSDU unit catalogue publishes it, a `UAD`
  whose `baseMeasurement` ancestry is the Energistics dimension, `L` for a
  length or `A` for an angle, is read. It was refused as not a length or not
  an angle, so a survey or a height unit given by such a payload failed.

- A `proj.db` whose path holds `#`, `?` or `%`, as one installed under a
  directory such as `C#` does, is read as the file PROJ reads. The package's
  read-only lookups, and the Python fallback in
  `scripts/install-osdu-projdb.sh`, put the path into a SQLite `file:` URI as
  it was, which reads those characters as a fragment, a query and an escape,
  so they opened another file: OSDU record ids, bound CRSs, skip reasons, grid
  names and persistable references were not found there,
  `default_base_proj_db()` found the search path ambiguous, and builds failed.
  After a `#` or `?`, `mode=ro` was lost as well, so SQLite could create an
  empty database named after the path up to that character.
- `geodetic-osdudb build` and `geodetic-projdb build` work on Windows.
  Validation stages the database as `proj.db` in the calling process, so the
  temporary copy is removed only after the validation process, in which PROJ
  holds it open, has exited. Publication syncs the staged database through a
  writable handle and skips the directory sync Windows does not have. A
  database that cannot be replaced, most often because another process such
  as a notebook has it open through PROJ, now raises `ProjDbBuildError` with
  the cause and is left unchanged. The
  tests now also run on Windows, against the pinned pyproj wheel, with a
  build of the official OSDU catalogue.
- The pyproj wheel build installs macOS dependencies in a writable runner
  directory and builds curl with OpenSSL. Its Windows smoke test closes the
  temporary SQLite database before cleanup.
- Engineering CRSs now honour the `xy` value order. PROJ's `always_xy` never
  normalises an engineering CRS, and PROJ reads the origin of a Similarity
  transformation stated in a northing-first projected CRS in declared order
  even where the authority stated it easting-first (EPSG's own `EPSG:1035`
  does); both transposed coordinates silently. The package now adds the
  missing swap and settles the ordinate convention from the operation's area
  of use, refusing the operation when it cannot. Documented as a workaround
  in `transformation.py` and on the known-issues page.
- A grid the compiled pipeline must read is checked on disk, so a stated
  operation (OSDU payload) or a bound-CRS chain whose grid is absent raises
  `MissingGridError` at construction rather than failing at the first point
  or being reported as an unavailable operation.
- `to_dataframe()` no longer produces duplicate column labels for a CRS whose
  axes share an abbreviation (`EPSG:3388`).
- The devcontainer no longer patches the installed `proj.db` in place or links
  grids into the installed data directory; local grids are read from
  `local/grids/` through `PROJ_DATA`, and a patched database is written only
  under the repository. `tests/test_environment.py` guards this.

### Security

- certifi 2026.10.19 or later is required, and the lock file takes certifi
  2026.10.23, up from 2026.7.22. httpx, through which the Georepository client
  and the OSDU catalogue download connect, and PROJ's network access through
  pyproj verify TLS against certifi's roots unless an environment variable such
  as `SSL_CERT_FILE` names another bundle. Releases before 2026.10.18 trust
  emSign ECC Root CA - G3, under which intermediate CA keys were compromised
  (GHSA-6q48-6c7w-9gw2); 2026.10.18 also dropped emSign's unaffected roots,
  which 2026.10.19 restored.
- filelock 4.2.1 or later is required, and the lock file takes filelock 4.2.9,
  up from 3.32.6. The OSDU database writer locks its output with `FileLock`,
  which on Linux is `UnixFileLock`; before 4.2.1 that truncates and changes the
  mode of a file another user renames onto the lock path
  (GHSA-69hj-f68j-2879), and twelve further advisories cover filelock's other
  lock types.
- The lock file takes Pillow 13.0.0, up from 12.3.0, for 41 advisories in
  image decoders and encoders. Pillow comes in only through matplotlib in the
  `dev` extra; the package does not use it.

## 0.1.0

First version.
