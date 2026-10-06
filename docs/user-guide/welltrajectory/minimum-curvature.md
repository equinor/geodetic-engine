---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Minimum curvature

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

Minimum curvature reduces a survey to positions relative to its first station.
Between two stations the hole is taken to follow the one circular arc that is
tangent to the hole's direction at both. It is the industry's standard method,
and the one {func}`~geodetic_engine.welltrajectory.compute_trajectory` uses.
{class}`~geodetic_engine.welltrajectory.MinimumCurvature` runs it on its own,
free of any CRS.

## On its own

{class}`~geodetic_engine.welltrajectory.MinimumCurvature` takes measured
depths in any length unit and angles in radians, and returns offsets in the
unit of MD. A quarter circle from vertical to horizontal over 1000 m has
radius $1000 / (\pi/2) = 636.62$ m, so the hole ends that far north and that
far down:

```{code-cell} python
import numpy as np

from geodetic_engine.welltrajectory import MinimumCurvature

model = MinimumCurvature(md=[0, 1000], inclination=[0, np.pi / 2], azimuth=[0, 0])
stations = model.stations

print("offsets (east, north, tvd):")
print(stations.offsets.round(3))
print("dogleg per interval [rad] :", model.dogleg)
print("dls [deg/30 m]            :", stations.dls(30))
```

{attr}`~geodetic_engine.welltrajectory.MinimumCurvature.stations` is a
{class}`~geodetic_engine.welltrajectory.Stations`: MD, inclination, azimuth,
offsets as `(east, north, tvd)`, and curvature at every station.
{attr}`~geodetic_engine.welltrajectory.MinimumCurvature.dogleg` is the angle
turned over each interval, one fewer than there are stations. The azimuths are
referenced to whichever north the offsets should be against: `compute_trajectory`
gives it true azimuths.

## The method

Each station's direction is its unit tangent, in a local (east, north, down)
frame:

$$t = (\sin I \sin\alpha,\ \sin I \cos\alpha,\ \cos I).$$

All the intervals are computed at once, as arrays, and each station's position
is the running sum of the steps before it.

### The step between two stations

The arc turns through the dogleg $\beta$, the angle between the two tangents:

$$\cos\beta = t_1 \cdot t_2 = \cos(I_2 - I_1) - \sin I_1 \sin I_2\,\bigl(1 - \cos(\alpha_2 - \alpha_1)\bigr),
\qquad \sin\beta = \lVert t_1 \times t_2 \rVert.$$

$\beta$ is recovered from both its sine and its cosine. The cosine alone is
flat near zero, so a small dogleg read from it loses most of its digits.

An arc of length $\Delta MD$ turning through $\beta$ has radius
$R = \Delta MD / \beta$. Its chord has length $2R\sin(\beta/2)$ and bisects the
two tangents, so it points along $t_1 + t_2$, whose length is $2\cos(\beta/2)$.
The step is therefore

$$\Delta p = R \tan\frac{\beta}{2}\,(t_1 + t_2) = \frac{\Delta MD}{2}\, RF\,(t_1 + t_2),
\qquad RF = \frac{2}{\beta}\tan\frac{\beta}{2}.$$

The ratio factor $RF$ is 1 on a straight segment, where the division by
$\beta$ is undefined, so below $\beta = 10^{-4}$ rad it is taken from its series

$$RF = 1 + \frac{\beta^2}{12} + \frac{\beta^4}{120} + O(\beta^6),$$

whose first omitted term is below $10^{-27}$ there. As $\beta \to \pi$ the two
tangents point opposite ways, the radius is unbounded and no arc joins them;
that raises {class}`~geodetic_engine.welltrajectory.DegenerateSurveyError`.

### Dogleg severity

The dogleg severity at a station is the curvature of the arc ending there,
$\beta / \Delta MD$, stated in degrees per 30 m, per 100 ft, or per any other
length. It is zero at the first station.

### Points between stations

A point a fraction $\tau$ of the way along an interval lies on that interval's
arc. Its tangent is the spherical interpolation of the two tangents, which
keeps it a unit vector turning at a constant rate,

$$t(\tau) = \frac{\sin\bigl((1 - \tau)\beta\bigr)\, t_1 + \sin(\tau\beta)\, t_2}{\sin\beta},$$

and its position is the step above over the partial arc, of length
$\tau\,\Delta MD$ and dogleg $\tau\beta$:

$$p(\tau) = p_1 + \frac{\tau\,\Delta MD}{2}\, RF(\tau\beta)\,\bigl(t_1 + t(\tau)\bigr).$$

Its inclination and azimuth are read back from $t(\tau)$, with
$\cos I = t_{down}$ and $\tan\alpha = t_{east} / t_{north}$. A depth equal to a
station's returns that station exactly.

## Checking it

Half way round the quarter circle, the interpolated point is on the circle, at
the radius from its centre, with the hole at 45°:

```{code-cell} python
radius = 1000 / (np.pi / 2)
half_way = model.interpolate([500.0])
east, north, tvd = half_way.offsets[0]

print(f"inclination        : {np.degrees(half_way.inclination[0]):.6f} deg")
print(f"distance from centre: {np.hypot(north - radius, tvd):.6f} m")
print(f"radius             : {radius:.6f} m")
```

The centre of the build is at north $R$ and TVD 0, level with the first
station. Interpolating positions or angles linearly between the stations
leaves the arc:

```{code-cell} python
straight = stations.offsets[0] + 0.5 * (stations.offsets[1] - stations.offsets[0])

print("on the arc     :", half_way.offsets[0].round(3))
print("on the straight:", straight.round(3))
print(f"apart by        {np.linalg.norm(half_way.offsets[0] - straight):.2f} m")
```

Because every interpolated point is on the arcs, a survey resampled this way
and fed back through the method lands on the same positions:

```{code-cell} python
survey = MinimumCurvature(
    md=[0, 500, 1500, 2500],
    inclination=np.radians([0, 20, 60, 70]),
    azimuth=np.radians([10, 30, 45, 50]),
)
resampled = survey.resample(10.0)
again = MinimumCurvature(resampled.md, resampled.inclination, resampled.azimuth)

print(f"{len(resampled)} points every 10 m")
print(f"largest change on re-running: {np.abs(again.stations.offsets - resampled.offsets).max():.1e} m")
```

## What is refused

Two stations pointing in opposite directions:

```{code-cell} python
:tags: [raises-exception]

MinimumCurvature([0, 100, 200], np.radians([0, 90, 90]), np.radians([0, 0, 180]))
```

Measured depth that does not strictly increase, fewer than two stations, an
inclination outside $[0, \pi]$, or values that are not finite:

```{code-cell} python
:tags: [raises-exception]

MinimumCurvature([0, 100, 100], [0, 0.1, 0.2], [0, 0, 0])
```

A depth outside the surveyed interval:

```{code-cell} python
:tags: [raises-exception]

model.interpolate([1200.0])
```
