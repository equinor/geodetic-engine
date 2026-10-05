---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# How the factors are defined

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

A map projection is a function from the ellipsoid to the plane,

$$(x, y) = f(\varphi, \lambda),$$

and every factor in this section comes from its partial derivatives at a point.
On an ellipsoid with semi-major axis $a$ and eccentricity $e$, the radii of
curvature in the meridian and in the prime vertical at latitude $\varphi$ are

$$M = \frac{a(1 - e^2)}{(1 - e^2\sin^2\varphi)^{3/2}}, \qquad
N = \frac{a}{(1 - e^2\sin^2\varphi)^{1/2}},$$

so a step $d\varphi$ north covers $M\,d\varphi$ on the ellipsoid, and a step
$d\lambda$ east covers $N\cos\varphi\,d\lambda$.

## Grid convergence

Moving north along the meridian moves the point on the grid in the direction
$(\partial x/\partial\varphi,\ \partial y/\partial\varphi)$. That is true north
as drawn on the grid, at grid azimuth
$\operatorname{atan2}(\partial x/\partial\varphi,\ \partial y/\partial\varphi)$.
Grid north lies $\gamma$ clockwise of true north, so true north lies at grid
azimuth $-\gamma$:

$$\tan\gamma = -\frac{\partial x / \partial\varphi}{\partial y / \partial\varphi}.$$

This is the sign `grid_convergence` is reported with, and the sign
{doc}`azimuths-and-distances` checks against transformed points.

## Scale along the parallel and the meridian

The scale in a direction is the length of the grid step over the length of the
ellipsoidal step it comes from. Along the parallel and along the meridian:

$$k = \frac{\sqrt{(\partial x/\partial\lambda)^2 + (\partial y/\partial\lambda)^2}}{N\cos\varphi},
\qquad
h = \frac{\sqrt{(\partial x/\partial\varphi)^2 + (\partial y/\partial\varphi)^2}}{M}.$$

`scale_factor` is $k$ and `meridional_scale` is $h$. In a conformal projection
the two are equal, and equal to the scale in every other direction, which is
why surveying can speak of *the* scale factor.

## Areal scale and angular distortion

The areal scale is the area of a small patch on the grid over its area on the
ellipsoid: the Jacobian of the projection over the ellipsoid's own area element,

$$s = \frac{1}{MN\cos\varphi}\left|\frac{\partial(x, y)}{\partial(\varphi, \lambda)}\right|.$$

A small circle on the ellipsoid is drawn on the grid as an ellipse, Tissot's
indicatrix. Its semi-axes $a_T \ge b_T$ are the largest and smallest scales at
the point, their product is $s$, and the largest change the projection makes to
any angle there is

$$\omega = 2\arcsin\frac{a_T - b_T}{a_T + b_T},$$

which is `angular_distortion`, in degrees. A conformal projection draws the
circle as a circle, so $a_T = b_T$ and $\omega = 0$. An equal-area projection
keeps $a_T b_T = 1$.

PROJ also reports the indicatrix's axes, which pyproj exposes. They reproduce
the two factors:

```{code-cell} python
import math

from pyproj import Proj

from geodetic_engine.geodesy import projection_factors

raw = Proj("EPSG:3035").get_factors(25.0, 60.0)
laea = projection_factors("EPSG:3035", (25.0, 60.0), geographic=True)
a_t, b_t = raw.tissot_semimajor, raw.tissot_semiminor

print(f"2 asin((a - b) / (a + b)): {math.degrees(2 * math.asin((a_t - b_t) / (a_t + b_t))):.10f}")
print(f"angular_distortion       : {laea.angular_distortion[0]:.10f}")
print(f"a b                      : {a_t * b_t:.10f}")
print(f"areal_scale              : {laea.areal_scale[0]:.10f}")
```

## How PROJ evaluates them

PROJ takes the derivatives by finite differences, so the factors carry a small
numerical error: about $10^{-10}$ in a scale, and about $10^{-6}$ degrees in an
angular distortion that should be zero. On UTM's central meridian, where the
scale factor is exactly $k_0 = 0.9996$:

```{code-cell} python
central = projection_factors("EPSG:32631", (500000.0, 6600000.0))

print(f"scale factor      : {central.scale_factor[0]:.12f}")
print(f"angular distortion: {central.angular_distortion[0]:.1e} degrees")
```

Both are far below anything a survey can measure, but a test or a comparison
should allow for them.

## Against closed forms

A few projections have closed forms for their factors, and PROJ's values agree
with them.

Mercator is conformal, with $k = h = \sqrt{1 - e^2\sin^2\varphi} / \cos\varphi$:

```{code-cell} python
import numpy as np
import pandas as pd
from pyproj import CRS

ellipsoid = CRS("EPSG:3395").ellipsoid
e2 = 1 - (ellipsoid.semi_minor_metre / ellipsoid.semi_major_metre) ** 2
latitudes = np.array([0.0, 30.0, 60.0, 80.0])
phi = np.radians(latitudes)
closed_form = np.sqrt(1 - e2 * np.sin(phi) ** 2) / np.cos(phi)

mercator = projection_factors(
    "EPSG:3395", np.column_stack([np.full(4, 10.0), latitudes]), geographic=True
)
pd.DataFrame(
    {
        "latitude": latitudes,
        "closed form": closed_form,
        "scale_factor": mercator.scale_factor,
        "meridional_scale": mercator.meridional_scale,
    }
)
```

On a sphere, the transverse Mercator convergence is exactly
$\tan\gamma = \tan(\lambda - \lambda_0)\sin\varphi$. On the ellipsoid that is a
first approximation, good to about $10^{-5}$ degrees within a UTM zone, and
the sign is the one reported:

```{code-cell} python
points = [(6.0, 60.0), (0.0, 60.0), (6.0, -40.0), (0.5, -10.0)]
utm = projection_factors("EPSG:32631", points, geographic=True)
longitude, latitude = np.radians(np.array(points)).T
sphere = np.degrees(
    np.arctan(np.tan(longitude - np.radians(3.0)) * np.sin(latitude))
)

pd.DataFrame(
    {
        "longitude": utm.longitude,
        "latitude": utm.latitude,
        "on a sphere": sphere,
        "grid_convergence": utm.grid_convergence,
    }
)
```
