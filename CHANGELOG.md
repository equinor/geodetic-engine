# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

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

### Changed

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

## 0.1.0

First version.
