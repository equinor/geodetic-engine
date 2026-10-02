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
