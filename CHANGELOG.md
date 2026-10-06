# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- `geodetic_engine.geodesy.projection_factors`: grid convergence, point scale
  factor, meridional and areal scale and angular distortion at any point of a
  projected CRS, with the sign convention stated, and helpers that turn
  azimuths between grid and true north. Documented in the geodesy user guide.
- `geodetic_engine.welltrajectory`: well trajectories from directional
  surveys, by minimum curvature, georeferenced in a CRS on its own datum by
  one of four methods, with dogleg severity, interpolation along the arcs and
  3D plots. The input is a `TrajectoryInput`, built from arrays, rows, a
  pandas DataFrame, a CSV survey file, or an OSDU `convertTrajectory` request
  body, and the result a `WellTrajectory`. The CSV reader finds the survey
  columns by their usual names or by name given as an argument, ignores other
  columns, detects the delimiter, takes the unit from a name such as
  `MD (ft)`, and reads the wellhead and the settings from a header under
  their usual names. It also reads a survey report as it is: free text above
  a table lined up with spaces, with a line of units, in UTF-8 or Latin-1.
  A synthetic survey file and the survey report of Volve F-1, a real well,
  ship in `example_data`.
  Documented in a user guide section of its own, the API reference and an
  example notebook.
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

- Web Mercator scale factors are corrected from the sphere to the base
  ellipsoid. GridNorthLocal retains scale factor and grid convergence, with
  native axis order and directions respected. Angular distortion that these
  calculations cannot handle is refused, as are non-finite and out-of-range inputs.
- LMP interpolation and resampling retain the original survey anchors;
  query order and extra points no longer change surveyed positions.
- CSV unit annotations no longer silently default when unsupported. Shared
  trajectory plots normalize MD and vertical units to the first well's units.
- Horizontal conversion preserves bindings inside compound CRSs. Corrected
  the trajectory README quickstart and the explanation of reversal geometry.
- Every georeferencing method refuses a projected CRS that does not preserve
  angles at the wellhead, not only GridNorthLocal. OSDU request bodies with a
  malformed nested field or a zero `md_interval`, and units with a non-positive
  scale, raise the module's own errors. `plot_trajectory` refuses an unknown
  `color_by` instead of labelling it as dogleg severity.
- An OSDU payload that is not a JSON object, or whose `interpolate` is not a
  boolean, raises `InvalidInputError`. `compute_trajectory` reads method and
  north reference names in any case and refuses a wellhead that is not finite.
  Interpolated and resampled trajectories keep the provenance of their model.
  `ProjectionFactors.to_json_dict()` names the CRS of its coordinates. The
  `docs` extra installs plotly.
- A survey whose first station is not at MD 0 is placed with that station at
  the wellhead, as a tie-in point; the documentation now says so and the
  provenance records it.
- An OSDU payload's `unitXY` is checked against an angular unit for a
  geographic CRS, so `degree` with `EPSG:4326` is accepted; `MD_i.md_i` must be
  a list. An unknown `inputKind` is refused, and `unitMD` falls back to `unitZ`
  only when it is absent: an empty or null `unitMD` or `unitXY` raises
  `UnitError`.
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
