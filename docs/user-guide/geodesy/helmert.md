---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Helmert utilities

{mod}`geodetic_engine.geodesy.utils` is the algebra behind bound CRSs over
concatenated operations. It is public so it can be used and checked on its
own.

**Use it when** you need a single transformation where a register publishes a
chain. A bound CRS can carry only one step, so `EPSG:8047` (ED50 to WGS 84
(15), two Helmerts through ED87) cannot be embedded as published.

**You do not need it** to transform through a chain. Name the chain or its steps
as the operation ({doc}`transformations`). PROJ applies chains directly.

## Reading a Helmert's parameters

{func}`~geodetic_engine.geodesy.utils.helmert_parameters` reads a plain
Helmert's seven parameters into SI units, always in the position-vector
rotation convention:

```{code-cell} python
from pyproj.crs import CoordinateOperation

from geodetic_engine.geodesy.utils import helmert_parameters

ed50_to_wgs84_23 = CoordinateOperation.from_epsg(1612)
p = helmert_parameters(ed50_to_wgs84_23)
print(p)
print("rotations (arc-seconds):", p.rotations_arc_seconds())
print("scale (ppm):", p.scale_ppm())
print(p.proj_string())
```

Each parameter is converted with its own stated unit factor. EPSG gives some
rotations in microradians and others in arc-seconds. Reading microradians as
arc-seconds would make a rotation about five times too large. A coordinate-frame Helmert has its rotation
signs flipped so that two sets can be composed without tracking conventions.

It returns None for anything that is not a plain Helmert: Molodensky-Badekas,
time-dependent and full-matrix variants, and grid or offset methods:

```{code-cell} python
print(helmert_parameters(CoordinateOperation.from_epsg(1241)))  # NADCON: a grid
```

## Composing two Helmerts

Two Helmerts compose exactly, because each is an affine map on geocentric
coordinates:

$$
X_2 = T_2 + (1 + s_2) R_2 \left[ T_1 + (1 + s_1) R_1 X_0 \right]
$$

so the composition is again a Helmert:

$$
T = T_2 + (1 + s_2) R_2 T_1, \qquad R = R_2 R_1, \qquad 1 + s = (1 + s_1)(1 + s_2)
$$

{func}`~geodetic_engine.geodesy.utils.compose` computes it:

```{code-cell} python
from geodetic_engine.geodesy.utils import compose

ed50_to_ed87 = helmert_parameters(CoordinateOperation.from_epsg(1147))
ed87_to_wgs84 = helmert_parameters(CoordinateOperation.from_epsg(1146))
compose(ed50_to_ed87, ed87_to_wgs84)
```

## Collapsing a concatenated operation

{func}`~geodetic_engine.geodesy.utils.collapse_concatenated` composes every
step, then **checks** the result. EPSG's rotation matrix is linearised for
small angles, so $R_2 R_1$ is not exactly a linearised matrix again. The
collapsed operation is compared with PROJ's own evaluation of the original
chain at `samples` points over the area of use, and refused if any point moves
more than `tolerance_m` (default 1 mm):

```{code-cell} python
from geodetic_engine.geodesy.utils import collapse_concatenated, is_collapsible

chain = CoordinateOperation.from_epsg(8047)
print("collapsible:", is_collapsible(chain))

single = collapse_concatenated(chain)
print(single.name)
print(single.method_name)
print("towgs84:", [round(v, 4) for v in single.towgs84])
```

The collapsed step is what a bound CRS can carry. Bound to ED50 and used to
transform, it gives the same coordinates as the published chain, to well under
the millimetre tolerance:

```{code-cell} python
from pyproj import CRS
from pyproj.crs.crs import BoundCRS

from geodetic_engine.geodesy import transform

ed50_via_8047 = BoundCRS(CRS.from_epsg(4230), CRS.from_epsg(4326), single)

point = (4.12789451, 63.58496782)
by_chain = transform("EPSG:4230", "EPSG:4326", point, operation="EPSG:8047").coordinates[0]
by_bound = transform(ed50_via_8047, "EPSG:4326", point).coordinates[0]
print(by_chain)
print(by_bound)
```

{func}`~geodetic_engine.geodesy.utils.is_collapsible` is a cheap structural
check: every step is a plain Helmert, and all share one domain. It does not
prove that the collapse passes the numerical check.

A chain is **not** collapsed, and
{class}`~geodetic_engine.geodesy.NotCollapsibleError` is raised, if:

- any step is not a plain Helmert (it reads a grid, or is Molodensky-Badekas,
  time-dependent, time-specific or full-matrix);
- the steps mix domains (geog2D, geog3D and geocentric Helmerts treat
  ellipsoidal height differently);
- a step is applied inverted, which PROJJSON cannot state faithfully;
- the composed parameters do not reproduce the chain within `tolerance_m`;
- it is not a chain of at least two steps.

## Restating a scale in parts per million

{func}`~geodetic_engine.geodesy.utils.scale_in_parts_per_million` rewrites an
operation's scale difference in parts per million. PROJ exports a bound CRS's
abridged transformation assuming ppm. A scale given in parts per billion, as
EPSG gives most recent ITRF/ETRF transformations, is exported unconverted and
then read back as a scale factor, which puts positions kilometres out. See
{doc}`/workarounds`.

```{code-cell} python
from geodetic_engine.geodesy.utils import scale_in_parts_per_million

itrf = CoordinateOperation.from_epsg(10586)
restated = scale_in_parts_per_million(itrf)

for label, operation in (("as published", itrf), ("restated", restated)):
    scale = next(p for p in operation.params if p.code == "8611")
    print(f"{label:13} {scale.name}: {scale.value} {scale.unit_name}")
```
