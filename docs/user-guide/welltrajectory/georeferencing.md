---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Georeferencing methods

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal

import pandas as pd

pd.set_option("display.precision", 4)
```

Minimum curvature gives each station's offsets $(E, N, \mathrm{TVD})$ from the
wellhead, in metres against true north: positions in a flat frame of the
well's own. Georeferencing turns them into coordinates in the CRS, and that
takes a model of how the flat, local frame sits on the curved earth.
{class}`~geodetic_engine.welltrajectory.Method` names four. They all stay on
the CRS's own ellipsoid, and they all report elevation as
$z_0 - \mathrm{TVD}$; they differ only in where the stations land
horizontally.

| Method | Model | CRS |
|---|---|---|
| `AzimuthalEquidistant` (default) | The offsets are coordinates of an azimuthal equidistant projection centred on the wellhead. | Geographic or conformal projected |
| `GridNorthLocal` | The offsets turned by the grid convergence and scaled by the point scale factor, both taken at the wellhead. | Conformal projected |
| `ENU` | The offsets are a local tangent plane at the wellhead, through geocentric coordinates. | Geographic or conformal projected |
| `LMP` | Each step laid on the ellipsoid with the radii of curvature at its own latitude and elevation. | Geographic or conformal projected |

A projected CRS must be conformal, preserving angles, for every method: each
trajectory reports grid azimuths, and turning true azimuths onto the grid with
the grid convergence alone needs a conformal projection. Other projections are
refused with {class}`~geodetic_engine.geodesy.UnsupportedCRSError`.

Choose with `method=` on any
{class}`~geodetic_engine.welltrajectory.TrajectoryInput` constructor or on
{func}`~geodetic_engine.welltrajectory.compute_trajectory`: a
{class}`~geodetic_engine.welltrajectory.Method` member, such as
`Method.LMP`, or its name as text, such as `"LMP"`. The parameter is typed
with the four names, so an editor offers them and a type checker refuses any
other.

## `AzimuthalEquidistant`

An azimuthal equidistant projection centred on the wellhead keeps every
distance and azimuth *from the wellhead* true, which is exactly what the
offsets are. A station is placed at the geodesic distance
$\rho = \sqrt{E^2 + N^2}$ from the wellhead, leaving it at azimuth $\alpha$ with
$\tan\alpha = E / N$, and the result is converted to the CRS. The projection is
built on the CRS's own datum, so the conversion is a change of projection only.

This is the default. The target projection's own scale factor and convergence
then apply exactly at every station, not only at the wellhead, and nothing is
needed but PROJ. The projection is kept as the trajectory's `local_crs`:

```{code-cell} python
from geodetic_engine.welltrajectory import Method, Survey, compute_trajectory

survey = Survey([0, 500, 1500, 2500], [0, 20, 60, 70], [10, 30, 45, 50])
trajectory = compute_trajectory(
    survey, (666000.0, 6660000.0, 25.0), "EPSG:32631", method=Method.AZIMUTHAL_EQUIDISTANT
)

print(trajectory.local_crs.name)
for parameter in trajectory.local_crs.crs.coordinate_operation.params:
    print(f"  {parameter.name}: {parameter.value} {parameter.unit_name}")
```

## `GridNorthLocal`

The offsets are turned onto grid north by the grid convergence $\gamma$ and
scaled by the point scale factor $k$, both taken at the wellhead:

$$E_{grid} = k\,(E\cos\gamma - N\sin\gamma), \qquad N_{grid} = k\,(E\sin\gamma + N\cos\gamma).$$

This is $\alpha_{grid} = \alpha_{true} - \gamma$ and $d_{grid} = k\, d_{ground}$ in
vector form; see {doc}`/user-guide/geodesy/projection-factors`.
Holding $\gamma$ and $k$ constant over the whole well is the approximation:
exact at the wellhead, and off further out by how much they change across the
reach. The scale factor applies in every direction because the projection is
conformal. Axis order, directions and units are respected.

## `ENU`

The offsets are coordinates in the topocentric frame at the wellhead, with
$U = -\mathrm{TVD}$. With the wellhead at $(\varphi_0, \lambda_0, h_0)$ and
geocentric position $X_0$, a station is at

$$X = X_0 + \begin{pmatrix}
-\sin\lambda_0 & -\sin\varphi_0\cos\lambda_0 & \cos\varphi_0\cos\lambda_0 \\
\cos\lambda_0 & -\sin\varphi_0\sin\lambda_0 & \cos\varphi_0\sin\lambda_0 \\
0 & \cos\varphi_0 & \sin\varphi_0
\end{pmatrix}
\begin{pmatrix} E \\ N \\ U \end{pmatrix},$$

which is then read back as latitude and longitude on the same ellipsoid and
converted to the CRS. Unlike the other methods the frame stays flat as it
leaves the wellhead, so "up" stays the wellhead's up.

The wellhead's elevation stands in for its ellipsoidal height $h_0$, which it
is not. An error $\delta h$ in it moves a station at reach $d$ by
$d\,\delta h / R$: 3 cm for a 40 m geoid separation at 5 km.

## `LMP`

Each step between stations is laid on the ellipsoid with the radii of
curvature at its own latitude and elevation:

$$\Delta\varphi = \frac{\Delta N}{M(\bar\varphi) + \bar h}, \qquad
\Delta\lambda = \frac{\Delta E}{\bigl(N(\bar\varphi) + \bar h\bigr)\cos\bar\varphi},$$

$$M = \frac{a(1 - e^2)}{W^3}, \qquad N = \frac{a}{W}, \qquad W = \sqrt{1 - e^2\sin^2\bar\varphi},$$

with $\bar\varphi$ and $\bar h = z_0 - \overline{\mathrm{TVD}}$ taken at the
middle of the step. A step drilled deep sweeps a larger angle than the same
step at the surface. The latitudes depend on each other only through
$\bar\varphi$, so they are solved for the whole well at once by fixed-point
iteration. Each pass shrinks the error by the reach over the earth's radius,
and three passes are far more than enough. Each step's azimuth is thereby
counted from the meridian where it was drilled, not the wellhead's.

Integration is anchored to the original survey stations. An interpolated
point is evaluated from the preceding original station, regardless of other
requested points. Resampling, adding points or changing their order does not
move any surveyed station.

## How the methods differ

All four agree to centimetres near the wellhead. Further out they differ by
amounts that follow from their geometry, which the test suite pins:

| Method | Differs from `AzimuthalEquidistant` by | Example |
|---|---|---|
| `GridNorthLocal` | The change of $k$ and $\gamma$ across the reach. | Millimetres over 2.5 km, off the central meridian of UTM. |
| `ENU` | A flat plane leaving a curved earth: a station at depth $T$ and reach $d$ lands $dT/R$ further out. | 1.4 m at 3 km reach and 3 km depth. |
| `LMP` | The same $dT/R$ at depth, and a line of constant azimuth rather than a geodesic: heading due east it stays $d^2\tan\varphi / 2N$ north of the geodesic. | 1.2 m at 3 km, due east at 60°N. |

The same survey through all four, off the central meridian of UTM zone 31N:

```{code-cell} python
import numpy as np

by_method = {
    method: compute_trajectory(survey, (666000.0, 6660000.0, 25.0), "EPSG:32631", method=method)
    for method in Method
}
reference = by_method[Method.AZIMUTHAL_EQUIDISTANT]

pd.DataFrame(
    {
        "x at TD": [t.x[-1] for t in by_method.values()],
        "y at TD": [t.y[-1] for t in by_method.values()],
        "largest separation [m]": [
            np.hypot(t.x - reference.x, t.y - reference.y).max() for t in by_method.values()
        ],
    },
    index=[str(method) for method in by_method],
)
```

A step at depth shows the curvature. Down 3 km, then 3 km due east, near
60°N:

```{code-cell} python
deep = Survey([0, 3000, 3000.001, 6000], [0, 0, 90, 90], [90, 90, 90, 90])
wellhead = (500000.0, 6650000.0, 0.0)
at_depth = {
    method: compute_trajectory(deep, wellhead, "EPSG:32631", north="TN", method=method)
    for method in Method
}
base = at_depth[Method.AZIMUTHAL_EQUIDISTANT]
radius = 6.39e6  # the prime vertical radius near 60°N

print(f"d T / R             : {3000 * 3000 / radius:.3f} m")
print(f"d^2 tan(phi) / 2N   : {3000**2 * np.tan(np.radians(base.frame.latitude)) / (2 * radius):.3f} m")
pd.DataFrame(
    {
        "east of AzimuthalEquidistant [m]": [t.x[-1] - base.x[-1] for t in at_depth.values()],
        "north of AzimuthalEquidistant [m]": [t.y[-1] - base.y[-1] for t in at_depth.values()],
    },
    index=[str(method) for method in at_depth],
)
```

`ENU` lands the well $dT/R$ further out, east. `LMP` does too, and also stays
on the parallel while the geodesic bends towards the equator, so it ends north
of it. `GridNorthLocal` agrees with the projection on the central meridian.

## Which to use

- **`AzimuthalEquidistant`** unless there is a reason not to. It is exact for
  the offsets as minimum curvature defines them, uses the projection's own
  factors at every station, and records the projection it read them in.
- **`GridNorthLocal`** to match a grid-based workflow that applies one scale
  factor and one convergence to the whole well. It agrees closely near the
  wellhead, and the difference grows with the reach.
- **`ENU`** and **`LMP`** to reproduce systems built on those models. Their
  differences from the others are geometric, not errors, and grow with depth
  and reach as tabulated above.
