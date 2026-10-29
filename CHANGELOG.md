# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- Packaging. `pyproj-wheels.yml` builds pyproj with pyproj's own release
  tooling against the pinned PROJ, read from `.devcontainer/install-proj.sh`,
  and releases the wheels on GitHub as `pyproj 3.8.0+proj9.9.0`. The
  documentation site serves them as a package index under `simple/`.
  `pypi_release.yml`, run by hand, publishes the package to TestPyPI or PyPI,
  as chosen, with trusted publishing. It is published as `pygeodetic-engine`,
  since PyPI refuses `geodetic-engine` as too close to the existing
  `geodeticengine`; it is still imported as `geodetic_engine`.
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

### Changed

- `geodetic-osdudb build` logs a bound CRS it cannot import, such as the NTF
  (Paris) ones in the official catalogue, as a warning rather than an error.
  It is still skipped and listed in the build report.
- The default base database of a build skips a database this package built,
  so a build still starts from the official `proj.db` when an enriched one is
  ahead of it on PROJ's search path.
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

## 0.1.0

First version.
