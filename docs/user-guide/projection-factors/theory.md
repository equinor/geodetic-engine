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
For a projected CRS, {func}`~geodetic_engine.geodesy.projection_factors` reads
the factors from PROJ's
[`proj_factors()`](https://proj.org/en/stable/development/reference/functions.html#c.proj_factors),
through pyproj's
[`Proj.get_factors()`](https://pyproj4.github.io/pyproj/stable/api/proj.html#pyproj.Proj.get_factors).
The formulas on this page are the ones PROJ evaluates. Its
[`PJ_FACTORS`](https://proj.org/en/stable/development/reference/datatypes.html#c.PJ_FACTORS)
names them as follows:

| This package | PROJ | Symbol |
|---|---|---|
| `grid_convergence` | `meridian_convergence` | $\gamma$ |
| `scale_factor` | `parallel_scale` | $k$ |
| `meridional_scale` | `meridional_scale` | $h$ |
| `areal_scale` | `areal_scale` | $s$ |
| `angular_distortion` | `angular_distortion` | $\omega$ |

PROJ returns the two angles in radians; pyproj, and this package, give them in
degrees.

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

$$\gamma = -\operatorname{atan2}\!\left(\frac{\partial x}{\partial\varphi},\ \frac{\partial y}{\partial\varphi}\right).$$

This is how PROJ computes `meridian_convergence`, and the sign
`grid_convergence` is reported with. An azimuth $\alpha$, measured clockwise
from north, is turned from true north onto grid north by

$$\alpha_{grid} = \alpha_{true} - \gamma,$$

which {doc}`azimuths-and-distances` checks against transformed points.

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

$$s = \frac{1}{MN\cos\varphi}\left(\frac{\partial x}{\partial\lambda}\frac{\partial y}{\partial\varphi}
- \frac{\partial x}{\partial\varphi}\frac{\partial y}{\partial\lambda}\right).$$

A small circle on the ellipsoid is drawn on the grid as an ellipse, Tissot's
indicatrix. Its semi-axes $a_T \ge b_T$ are the largest and smallest scales at
the point. They satisfy $a_T^2 + b_T^2 = h^2 + k^2$ and $a_T b_T = s$, which
PROJ solves for them:

$$a_T + b_T = \sqrt{h^2 + k^2 + 2s}, \qquad a_T - b_T = \sqrt{h^2 + k^2 - 2s}.$$

The largest change the projection makes to any angle there is

$$\omega = 2\arcsin\frac{a_T - b_T}{a_T + b_T},$$

which is `angular_distortion`, in degrees. A conformal projection draws the
circle as a circle, so $a_T = b_T$ and $\omega = 0$. An equal-area projection
keeps $a_T b_T = 1$.

