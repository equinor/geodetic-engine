---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Coordinate reference systems

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

{class}`~geodetic_engine.geodesy.CoordinateReferenceSystem` wraps a
{class}`pyproj.crs.CRS` and reports what EPSG declares about it: axis names,
abbreviations, directions and units, in the declared order. You rarely need to
build one, because {func}`~geodetic_engine.geodesy.transform` and
{class}`~geodetic_engine.geodesy.Transformation` accept the same inputs. Use it
to check what a CRS expects before you transform into or out of it.

## Resolving a CRS

Anything pyproj accepts works, plus OSDU `persistableReference` payloads:

```{code-cell} python
from pyproj import CRS

from geodetic_engine.geodesy import CoordinateReferenceSystem

for value in (
    "EPSG:4326",                                  # authority code
    4326,                                         # bare integer: EPSG assumed
    "urn:ogc:def:crs:EPSG::25832",                # OGC URN
    CRS.from_epsg(5941),                          # a pyproj CRS
    "+proj=utm +zone=32 +ellps=GRS80 +units=m",   # PROJ string
):
    crs = CoordinateReferenceSystem.from_user_input(value)
    print(f"{crs.authority_code or '-':12} {crs.name}")
```

A string that is a `persistableReference` is recognised automatically. Use
{meth}`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_persistable_reference`
to require one, so any other input is refused. See
{doc}`/user-guide/persistable-reference`.

An unresolvable input raises
{class}`~geodetic_engine.geodesy.UnresolvableCRSError`, not a raw pyproj error:

```{code-cell} python
:tags: [raises-exception]

CoordinateReferenceSystem.from_user_input("EPSG:999999")
```

## Declared axes versus value order

EPSG declares `EPSG:4326` latitude-first. This package passes values
longitude-first. The CRS reports both:

```{code-cell} python
wgs84 = CoordinateReferenceSystem.from_user_input("EPSG:4326")

print("declared  :", wgs84.axis_abbreviations)        # EPSG's order
print("values    :", wgs84.value_axis_abbreviations)  # the order you pass
print("mapping   :", wgs84.value_axis_order)          # value i is declared axis mapping[i]
print("units     :", wgs84.axis_units)
print("dimension :", wgs84.dimension)
```

`value_axis_order` gives, for each value you pass, the index of the declared
axis it is. `(1, 0)` means the first value is the second declared axis
(longitude).

Each axis has full detail in
{attr}`~geodetic_engine.geodesy.CoordinateReferenceSystem.axes`:

```{code-cell} python
for axis in wgs84.axes:
    print(axis)
```

Projected CRSs are usually declared easting-first, so declared order and value
order agree:

```{code-cell} python
utm = CoordinateReferenceSystem.from_user_input("EPSG:25832")
utm.axis_abbreviations, utm.value_axis_abbreviations, utm.axis_units
```

Some are not. `EPSG:2044` (Hanoi 1972 / Gauss-Kruger zone 18) is declared
northing-first, and you still pass easting first:

```{code-cell} python
gk = CoordinateReferenceSystem.from_user_input("EPSG:2044")
gk.axis_abbreviations, gk.value_axis_abbreviations
```

## Units

Units come from the CRS. This package does not convert them. Values go in and
come out in the CRS's own axis units. `EPSG:4807` (NTF (Paris)) is in grads,
with the pole at 100:

```{code-cell} python
ntf_paris = CoordinateReferenceSystem.from_user_input("EPSG:4807")
ntf_paris.axis_units
```

A projected CRS in US survey feet reports that too:

```{code-cell} python
texas = CoordinateReferenceSystem.from_user_input("EPSG:2278")  # NAD83 / Texas South Central (ftUS)
texas.name, texas.axis_units
```

## Vertical, compound and 3D CRSs

{attr}`~geodetic_engine.geodesy.CoordinateReferenceSystem.dimension` is the
number of values each point has in this CRS:

```{code-cell} python
for code in ("EPSG:5941", "EPSG:4979", "EPSG:6172", "EPSG:4978"):
    crs = CoordinateReferenceSystem.from_user_input(code)
    print(f"{code:10} dim={crs.dimension}  {crs.value_axis_abbreviations!s:18} {crs.name}")
```

`EPSG:5941` is NN2000 height (1D). `EPSG:4979` is WGS 84 geographic 3D.
`EPSG:6172` is a compound CRS, ETRS89 / UTM 32N + NN54 height. `EPSG:4978` is
WGS 84 geocentric.

## Dynamic CRSs

{attr}`~geodetic_engine.geodesy.CoordinateReferenceSystem.is_dynamic` is true
for a datum with a frame reference epoch, such as an ITRF realisation. A
dynamic CRS does **not** by itself make a coordinate epoch mandatory. That
depends on whether the operation reads one; see
{ref}`time-dependent-transformations`.

```{code-cell} python
for code in ("EPSG:4326", "EPSG:7789", "EPSG:4258"):
    crs = CoordinateReferenceSystem.from_user_input(code)
    print(f"{code:10} dynamic={crs.is_dynamic!s:5}  {crs.name}")
```

## The underlying pyproj CRS

{attr}`~geodetic_engine.geodesy.CoordinateReferenceSystem.crs` is the
{class}`pyproj.crs.CRS`, for anything this wrapper does not expose.
{attr}`~geodetic_engine.geodesy.CoordinateReferenceSystem.definition` is the
text it was resolved from.

```{code-cell} python
wgs84.crs.datum.name, wgs84.definition
```
