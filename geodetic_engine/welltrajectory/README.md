# geodetic_engine.welltrajectory

Positions along a wellbore from a directional survey: measured depth (MD),
inclination and azimuth at each station, a wellhead, and a CRS. The survey is
reduced to offsets from the wellhead by the minimum curvature method, and the
offsets are placed in the CRS by one of four methods. Every coordinate change
goes through `geodetic_engine.geodesy`, on the CRS's own datum, so no datum
shift is ever applied silently.

The full guide, with executed examples, is the documentation's
[Well trajectories](../../docs/user-guide/welltrajectory/index.md) section.

- [Quickstart](#quickstart)
- [Building the input](#building-the-input)
  - [The survey file format](#the-survey-file-format)
- [Package layout](#package-layout)
- [Conventions](#conventions)
- [Minimum curvature](#minimum-curvature)
  - [The step between two stations](#the-step-between-two-stations)
  - [Points between stations](#points-between-stations)
- [Placing the offsets in a CRS](#placing-the-offsets-in-a-crs)
- [How the methods differ](#how-the-methods-differ)
- [Not covered](#not-covered)

## Quickstart

```python
from importlib.resources import files

from geodetic_engine.welltrajectory import TrajectoryInput, open_in_browser

well = TrajectoryInput.from_arrays(
    md=[0, 500, 1500, 2500],
    inclination=[0, 20, 60, 70],
    azimuth=[10, 30, 45, 50],
    wellhead=(500000.0, 6600000.0, 25.0),  # x, y in the CRS; z = elevation
    crs="EPSG:32631",  # or WKT, PROJJSON, an OSDU persistableReference, a bound CRS
    north_reference="GN",  # azimuths against grid north; "TN" for true north
    md_unit="m",  # optional from here on, defaults shown
    angle_unit="degree",
    method="AzimuthalEquidistant",  # or "GridNorthLocal", "ENU", "LMP"; see Method
    z_unit="m",
    md_step=None,  # e.g. 30: also a point every 30 m of MD, on the arcs
    md_points=None,  # e.g. [1234.5]: also a point at these MDs
    name=None,  # labels the well in plots
)
trajectory = well.compute()

trajectory.to_dataframe()  # md, angles, offsets, x, y, z, dls
trajectory.interpolate([1234.5, 2000])  # on the arcs, not linearly
trajectory.to_geographic()  # WGS 84 / UTM to WGS 84 needs no datum change
trajectory.plot(color_by="dls")  # a plotly figure, needs the plot extra
open_in_browser(trajectory.plot())  # the same, full screen in a browser tab

# The synthetic survey file that ships with the package.
example = (
    files("geodetic_engine.welltrajectory") / "example_data" / "synthetic_well.csv"
)
TrajectoryInput.from_csv(example).compute()
```

`compute_trajectory()` does the same from a `Survey`, a `Wellhead` and a CRS
given separately. `MinimumCurvature` works on its own, without a CRS, for
offsets, dogleg severity and interpolation.

Plotting needs the `plot` extra: `pip install 'geodetic-engine[plot]'`, which
brings plotly and kaleido. `plot_trajectory()`, or `WellTrajectory.plot()`,
builds a 3D figure with every axis at one scale, to rotate, pan, zoom and hover
for MD, angles, TVD and dogleg severity. The survey stations are marked on each
path, to show it passing through them; `stations=False` leaves the markers
out, and `projections=False` the shadows. A notebook shows it in place, at a
fixed size; `open_in_browser()` serves it from a local web server on
`127.0.0.1` and opens it in a browser tab, which in a dev container is the
host's browser through VS Code's port forwarding. `figure.write_html(path)`
keeps it as a self-contained file, and `figure.write_image(path)` as a still
PNG, SVG or PDF; kaleido renders those with Chromium or Chrome, which the dev
container installs.

When wells use different MD or vertical units, all plotted values, including
hover text and colour scales, use the first well's units. The input data is
not changed.

## Building the input

`TrajectoryInput` holds everything a trajectory is computed from. Four
settings are required: the survey, the wellhead, the CRS and
`north_reference`. The others have the defaults shown above. Everything that
can be checked without the CRS is checked when the input is made; the CRS is
resolved by `compute()`. An input is frozen: `dataclasses.replace(well,
method="LMP")` gives a checked copy with a setting changed.

| Constructor | The survey as |
| --- | --- |
| `from_arrays(md, inclination, azimuth, ...)` | One numpy array, list or Series per quantity. |
| `from_records(rows, ...)` | One row per station: `(md, inclination, azimuth)` tuples or lists, or mappings with those keys. |
| `from_dataframe(frame, md_column=..., ...)` | A pandas DataFrame, with any column names; other columns are ignored. |
| `from_csv(path, ...)` | A CSV survey file in the format below, or a survey report with free text above a table lined up with spaces. Keyword arguments fill in or override its header; `md_column`, `inclination_column` and `azimuth_column` name columns it does not recognise, and `delimiter` the separator. |
| `from_osdu_payload(body)` | An OSDU `convertTrajectory` request body, as a mapping or JSON text. `MD_i.md_i` maps onto `md_points` and `MD_i.md_interval` onto `md_step`. |

`to_csv(path)` writes an input in the survey file format, every setting
included, and `to_dataframe()` gives its stations.

### The survey file format

```text
# Synthetic survey. A line starting with # that is not "# key: value" is a comment.
# name: Synthetic-1
# crs: EPSG:23031
# wellhead_x: 455000.0
# wellhead_y: 6785000.0
# wellhead_z: 32.0
# north_reference: GN
# md_unit: m
md,inclination,azimuth
0.0,0.0,0.0
30.0,0.31,210.01
...
```

- UTF-8 text; a byte order mark is skipped, and other text is read as
  Latin-1.
- Header lines first. `# key: value` states a setting. Keys are read in any
  case, with or without spaces, hyphens or underscores between their words,
  and under other usual names: `Coordinate system` for `crs`, `Easting` and
  `Northing` for `wellhead_x` and `wellhead_y`, `KB` or `Elevation` for
  `wellhead_z`, `Well name` for `name`, and so on. `crs`, the wellhead and
  `north_reference` are required, unless given as arguments. The wellhead is
  `wellhead_x`, `wellhead_y` and optionally `wellhead_z`, or one line
  `# wellhead: x, y, z` (also `# origin: ...`). `md_unit`, `angle_unit`,
  `z_unit`, `method`, `md_step`, `md_points` (comma separated) and `name` are
  optional. Any other `#` line is a comment, unless its key is a likely
  misspelling of a known one, which is refused. Other lines above the table
  are free text, as in a survey report, and skipped, except a `key: value`
  line giving the name or the north reference, such as
  `North Reference: Grid`.
- Then the table, from the first line naming the md and inclination columns,
  separated by commas, semicolons or tabs, or lined up with spaces, two or
  more ending a name. Measured depth, inclination and azimuth are found by
  their usual names (`MD`, `Measured Depth`, `Inc`, `Incl`, `Azi`, `AZIM_GN`,
  ...) in any case, or named with `md_column`, `inclination_column` and
  `azimuth_column`.
  Other columns are ignored; without an azimuth column the survey is
  inclination-only. A unit in brackets, `MD (ft)`, is the column's unit, as
  is one in a line of units below the names, such as `m RKB  deg  deg`. Then
  one line per station, plain numbers with `.` as the decimal point. Blank
  lines are skipped.
- Errors name the line at fault.
- Unsupported units in column names or unit rows raise `UnitError`, including
  misspellings and units of the wrong quantity; they never become defaults.

## Package layout

| Module | Holds |
| --- | --- |
| `datamodels/trajectory_input.py` | `TrajectoryInput`, the input, and its constructors. |
| `datamodels/well_trajectory.py` | `WellTrajectory`, the result. |
| `trajectory.py` | `compute_trajectory()`. |
| `survey.py` | `Survey`, `Wellhead`, `NorthReference` and the unit resolution. |
| `csv_parser.py` | The survey file format: read for `from_csv`, written for `to_csv`. |
| `minimum_curvature.py` | `MinimumCurvature`. |
| `methods/` | The four georeferencing methods, one module each. |
| `plot.py` | The 3D view. |
| `example_data/` | `synthetic_well.csv`, a synthetic survey file, and `volve_f1_survey.txt`, the survey report of Volve F-1, a real well. |

## Conventions

| Quantity | Convention |
| --- | --- |
| Coordinate values | `xy` order, as everywhere in this package: easting then northing, longitude then latitude, in the CRS's own units. |
| Inclination $I$ | From vertical: 0 is straight down, 90 horizontal. |
| Azimuth $\alpha$ | Clockwise from north, against grid north (`GN`) or true north (`TN`). |
| Grid convergence | $\gamma$ from true north to grid north, clockwise positive: $\alpha_{grid} = \alpha_{true} - \gamma$. This needs a conformal projection. See [Projection factors](../../docs/user-guide/geodesy/projection-factors.md). |
| Offsets | `east`, `north`, `tvd` from the wellhead, against true north, TVD positive down, in `z_unit`. |
| Elevation | $z = z_0 - \mathrm{TVD}$, with $z_0$ the wellhead elevation. The same for every method. |
| Dogleg severity | Degrees per 30 m of MD, or per 100 ft for a survey in feet; `dls(per_length)` for any other length. |
| Units | Symbols (`m`, `ft`, `ftUS`), OSDU unit ids, or OSDU unit persistableReferences. An unknown unit raises `UnitError`; nothing defaults. |

Grid azimuths are turned onto true north with the convergence at the wellhead,
so they assume it does not change across the well. Projected trajectory CRSs
must preserve angles at the wellhead; grid convergence alone is not enough
when the projection distorts them. Grid azimuths in a geographic CRS are
refused, since there is no grid.

## Minimum curvature

Each station's direction is its unit tangent, in a local (east, north, down)
frame at the wellhead:

$$t = (\sin I \sin\alpha,\ \sin I \cos\alpha,\ \cos I).$$

Between two stations the hole is taken to follow the one circular arc that is
tangent to both. All intervals are computed at once as arrays, and each
station's position is the running sum of the steps before it.

### The step between two stations

The arc turns through the dogleg $\beta$, the angle between the two tangents:

$$\cos\beta = t_1 \cdot t_2 = \cos(I_2 - I_1) - \sin I_1 \sin I_2\,\bigl(1 - \cos(\alpha_2 - \alpha_1)\bigr),
\qquad \sin\beta = \lVert t_1 \times t_2 \rVert.$$

$\beta$ is recovered from both its sine and its cosine. The cosine alone is
flat near zero, so a small dogleg read from it loses most of its digits.

An arc of length $\Delta MD$ turning through $\beta$ has radius
$R = \Delta MD / \beta$. Its chord has length $2R\sin(\beta/2)$ and bisects the
two tangents, so it points along $t_1 + t_2$, whose length is
$2\cos(\beta/2)$. The step is therefore

$$\Delta p = R \tan\frac{\beta}{2}\,(t_1 + t_2) = \frac{\Delta MD}{2}\, RF\,(t_1 + t_2),
\qquad RF = \frac{2}{\beta}\tan\frac{\beta}{2}.$$

The ratio factor $RF$ is 1 on a straight segment, where the division by
$\beta$ is undefined, so below $\beta = 10^{-4}$ rad it is taken from its
series $RF = 1 + \beta^2/12 + \beta^4/120 + O(\beta^6)$, whose first omitted
term is below $10^{-27}$ there. As $\beta \to \pi$ the radius tends to
$\Delta MD / \pi$ and the chord stays finite. At an exact reversal the two
tangents do not determine a unique arc plane; that raises
`DegenerateSurveyError`, as does a numerically indistinguishable near-reversal.

The dogleg severity at a station is the curvature of the arc ending there,
$\beta / \Delta MD$, stated in degrees per 30 m or per 100 ft. It is zero at the
first station.

### Points between stations

A point a fraction $\tau$ of the way along an interval lies on that interval's
arc. Its tangent is the spherical interpolation of the two tangents, which
keeps it a unit vector turning at a constant rate,

$$t(\tau) = \frac{\sin\bigl((1 - \tau)\beta\bigr)\, t_1 + \sin(\tau\beta)\, t_2}{\sin\beta},$$

and its position is the step above over the partial arc, of length
$\tau\,\Delta MD$ and dogleg $\tau\beta$:

$$p(\tau) = p_1 + \frac{\tau\,\Delta MD}{2}\, RF(\tau\beta)\,\bigl(t_1 + t(\tau)\bigr).$$

Its inclination and azimuth are read back from $t(\tau)$, with
$\cos I = t_{down}$ and $\tan\alpha = t_{east} / t_{north}$. Because every point is
on the arcs, a survey resampled this way and fed back through the method lands
on the same positions, to $10^{-9}$ m. Linear interpolation of positions or
angles leaves the arcs and does not have this property.

## Placing the offsets in a CRS

The offsets $(E, N, \mathrm{TVD})$ are metres against true north at the
wellhead. Each method is a different model of how that local frame sits on the
curved earth; all four stay on the CRS's own ellipsoid.

**`AzimuthalEquidistant`** (default). An azimuthal equidistant projection
centred on the wellhead keeps every distance and azimuth *from the wellhead*
true, which is exactly what the offsets are. A station is placed at the
geodesic distance $\rho = \sqrt{E^2 + N^2}$ from the wellhead, leaving it at
azimuth $\alpha$ with $\tan\alpha = E / N$, and the result is converted to the CRS. The
target projection's own scale factor and convergence then apply exactly at
every station, not only at the wellhead.

**`GridNorthLocal`**. The offsets are turned onto grid north by $\gamma$ and
scaled by the point scale factor $k$, both taken at the wellhead:

$$E_{grid} = k\,(E\cos\gamma - N\sin\gamma), \qquad N_{grid} = k\,(E\sin\gamma + N\cos\gamma).$$

This needs a conformal projected CRS, so that one scale factor applies in
every direction. Axis order, directions and units are respected.

**`ENU`**. The offsets are coordinates in the topocentric frame at the
wellhead, with $U = -\mathrm{TVD}$. With the wellhead at $(\varphi_0, \lambda_0, h_0)$
and geocentric position $X_0$, a station is at

$$X = X_0 + \begin{pmatrix}
-\sin\lambda_0 & -\sin\varphi_0\cos\lambda_0 & \cos\varphi_0\cos\lambda_0 \\
\cos\lambda_0 & -\sin\varphi_0\sin\lambda_0 & \cos\varphi_0\sin\lambda_0 \\
0 & \cos\varphi_0 & \sin\varphi_0
\end{pmatrix}
\begin{pmatrix} E \\ N \\ U \end{pmatrix},$$

which is then read back as latitude and longitude on the same ellipsoid and
converted to the CRS. The wellhead elevation stands in for $h_0$. An error
$\delta h$ in it moves a station at reach $d$ by $d\,\delta h / R$: 3 cm for a
40 m geoid separation at 5 km.

**`LMP`**. Each step between stations is laid on the ellipsoid with the radii
of curvature at its own latitude and elevation:

$$\Delta\varphi = \frac{\Delta N}{M(\bar\varphi) + \bar h}, \qquad
\Delta\lambda = \frac{\Delta E}{\bigl(N(\bar\varphi) + \bar h\bigr)\cos\bar\varphi},$$

$$M = \frac{a(1 - e^2)}{W^3}, \qquad N = \frac{a}{W}, \qquad W = \sqrt{1 - e^2\sin^2\bar\varphi},$$

with $\bar\varphi$ and $\bar h = z_0 - \overline{\mathrm{TVD}}$ taken at the
middle of the step. The latitudes depend on each other only through
$\bar\varphi$, so they are solved for the whole well at once by fixed-point
iteration; each pass shrinks the error by the reach over the earth's radius,
and three passes are far more than enough. Each step's azimuth is thereby
counted from the meridian where it was drilled, not the wellhead's.

The original survey stations remain the integration anchors. Each interpolated
point is evaluated from its preceding survey station; changing the query
order, adding points or resampling never moves an existing station.

## How the methods differ

All four agree to centimetres near the wellhead. Further out they differ by
amounts that follow from their geometry, which the tests pin:

| Method | Differs from `AzimuthalEquidistant` by | Example |
| --- | --- | --- |
| `GridNorthLocal` | The change of $k$ and $\gamma$ across the reach. | 5 mm over 2.5 km, off the central meridian of UTM. |
| `ENU` | A flat plane leaving a curved earth: a station at depth $T$ and reach $d$ lands $dT/R$ further out. | 1.4 m at 3 km reach and 3 km depth. |
| `LMP` | The same $dT/R$ at depth, and a line of constant azimuth rather than a geodesic: heading due east it stays $d^2\tan\varphi / 2N$ north of the geodesic. | 1.2 m at 3 km, due east at 60°N. |

Moving a result to another datum is `to_geographic()`, which goes through
`Transformation` and so needs the operation named, or a bound trajectory CRS.
Bindings in the horizontal component of a compound CRS are preserved too.

## Example data

The Volve F-1 report is from the [Volve field data set](https://www.equinor.com/energy/volve-data-sharing),
provided by Equinor and the Volve licence partners under the Equinor Open Data
Licence. The synthetic survey is generated example data, not an observed well.

## Not covered

Survey error models and uncertainty (ISCWSA), anti-collision, magnetic
declination, a convergence that varies across the well for grid azimuths, and
vertical datums beyond the stated assumption that the wellhead elevation stands
in for its ellipsoidal height in `ENU`.
