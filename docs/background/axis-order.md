# Axis order

Two orders are involved, and this package keeps them separate.

**Declared axis order** is part of a CRS definition in the EPSG dataset.
`EPSG:4326` is declared latitude, longitude. `EPSG:25832` is declared easting,
northing. `EPSG:2044` is declared northing, easting.

**Value order** is the order of the numbers you pass and get back. In this
package it is always `xy`: longitude before latitude, easting before northing,
then height. That is the order most data is stored in and most software
expects.

{class}`~geodetic_engine.geodesy.CoordinateReferenceSystem` reports both:
`axis_abbreviations` is the declared order, and `value_axis_abbreviations` is
the order values are passed in. Every
{class}`~geodetic_engine.geodesy.TransformationResult` has
`coordinate_order == "xy"`, and gives `source_axes`/`target_axes` in declared
order beside it, so a reader can see where the two differ.

## How it is achieved

Transformers are built with pyproj's `always_xy=True`, which asks PROJ to
normalise both ends of the pipeline to `xy`. The declared order is never used
to reinterpret your values silently.

## The gap in `always_xy`, and the workaround

`always_xy` normalises each end of a pipeline against that end's declared
horizontal axes. A **vertical CRS has no horizontal axes**, so there is nothing
to normalise against at that end. The `axisswap` PROJ inserts for the
operation's own internal geographic CRS then survives, and the horizontal
position is read latitude-first.

With stock PROJ {{ proj_version }}:

```python
from pyproj.transformer import TransformerGroup

# ETRS89 geographic 3D -> NN54 height, via a geoid grid
t = TransformerGroup("EPSG:4937", "EPSG:5776", always_xy=True).transformers[0]
t.transform(5.5, 58.5, 100.0)
# (58.5, 5.5, 57.087...)  -- the position comes back transposed
```

In the other direction, with the vertical CRS as the source, the same leftover
swap makes PROJ read the position that comes with the height latitude-first.
The geoid grid is then interpolated at the transposed position. If that
position falls outside the grid, PROJ errors. If it falls inside, you get a
plausible height for the wrong place, with no error.

This package inspects the pipeline's entry step. If a vertical source's
pipeline reads the horizontal pair swapped, the package transposes the values
before PROJ reads them, and writes the swap into the reported
{attr}`~geodetic_engine.geodesy.TransformationResult.pipeline` so that
replaying it still reproduces the result. See {doc}`/workarounds` for the code
location and when it can be removed.

## Engineering CRSs

An engineering CRS can have axes that are not east and north, such as
`EPSG:5800`, Astra Minas Grid (north, west). PROJ cannot normalise those to
`xy`, and this package does not yet correct for it. Coordinates to or from
such a CRS can come back in declared order even though the result says
`"xy"`. This is a known issue, not a workaround; see {doc}`/workarounds`.
