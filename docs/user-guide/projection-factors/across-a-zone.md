# Across a zone

The grid convergence and the point scale factor are not constants of a CRS:
they change from point to point. This page draws them across UTM zone 31N
(WGS 84, central meridian 3°E), the zone the other pages in this section use,
from the equator to 84°N.

## The two angles

Grid north is the direction of the grid's $y$ axis, the same everywhere on the
map. True north is the direction of the meridian through the point. East of
the central meridian of a transverse Mercator grid, the meridians lean towards
the central meridian as they go north, so true north lies anticlockwise of
grid north and the convergence $\gamma$ is positive. A direction measured from
true north is $\gamma$ larger than the same direction measured from grid north:

$$\alpha_{grid} = \alpha_{true} - \gamma.$$

```{image} /figure/grid-convergence-angles.svg
:alt: Two diagrams. East of the central meridian true north lies anticlockwise of grid north by gamma, west of it clockwise. Arcs from each north to a target show the true and the grid azimuth.
:align: center
```

West of the central meridian, on the right, the meridians lean the other way:
true north lies clockwise of grid north and $\gamma$ is negative, so a grid
azimuth is larger than the true one. Both panels exaggerate the angle: across a
UTM zone it is never more than about 3°.

## Grid convergence

The maps evaluate {func}`~geodetic_engine.geodesy.projection_factors`, as
{doc}`computing` shows, at 5185 points: every 0.1° of longitude and every
degree of latitude across the zone. Over the zone the convergence runs from
−2.984° to 2.984° and the scale factor from 0.999600 to 1.000981.

```{image} /figure/grid-convergence-map.svg
:alt: Contour map of the grid convergence over longitudes 0 to 6 degrees east and latitudes 0 to 84 degrees north. It is zero along the central meridian and the equator and reaches about 3 degrees at the zone's edges near the pole, positive in the east and negative in the west.
:align: center
```

The convergence is zero along the central meridian, the dotted line, where
the meridian is a grid line, and along the equator, where the meridians are
parallel. It grows with the distance from the central meridian and with
latitude, roughly as $(\lambda - \lambda_0)\sin\varphi$ (see {doc}`theory`), to
about ±3° at the zone's edges near the pole. A survey in the north-east of the
zone that confuses grid and true north is about 3° off: 50 m sideways for
every kilometre travelled.

## Point scale factor

```{image} /figure/scale-factor-map.svg
:alt: Contour map of the point scale factor over the same zone. It is 0.9996 along the central meridian and rises towards the zone's edges, most at the equator.
:align: center
```

The scale factor is $k_0 = 0.9996$ on the central meridian, where a distance
on the grid is 0.04 % shorter than on the ellipsoid, and grows towards the
zone's edges. Where it crosses one, the two agree. Away from the equator the
zone is narrower, so its edges do not reach as far from the central meridian
and the scale factor stays smaller there.

Plotted against the distance from the central meridian, the scale factor is
the same curve at every latitude: on the grid it depends on how far east or
west a point is, hardly on its latitude. At 30°N and 60°N it differs from
the equator's by at most $3.5 \times 10^{-6}$ (3.5 mm/km), far too little to
see, so the figure draws the equator only:

```{image} /figure/scale-factor-profile.svg
:alt: The point scale factor at the equator against the distance from the central meridian. A U-shaped curve rises from 0.9996 at the central meridian through 1 about 180 kilometres either side to about 1.001 at the zone's edges, 334 kilometres out.
:align: center
```

The curve ends at the zone's edge, 334 km either side of the central meridian
at the equator. Further north the zone is narrower and the same curve stops
sooner: at 289 km at 30°N and at 167 km at 60°N. The scale factor is 1 at
179.7 km either side, the vertical lines. Poleward of 57.3°N the zone's edge
is closer than that, so there the whole zone has $k < 1$.


