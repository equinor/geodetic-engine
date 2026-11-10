---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Units, methods and coordinate systems

The building blocks every definition refers to. Each listing works like the
others; see {doc}`filters`.

## Units of measure

{func}`~geodetic_engine.catalog.units` lists the units PROJ knows, through
{func}`pyproj.database.get_units_map`, with the factor to the SI unit of the
same quantity:

```{code-cell} python
from geodetic_engine import catalog

catalog.units(authority="EPSG", kind="linear", name="foot").to_dataframe()[
    ["authority_code", "name", "conversion_factor", "proj_short_name"]
]
```

`conversion_factor` filters on the factor, exactly or as a range. A
sexagesimal unit has no factor, and never matches:

```{code-cell} python
[str(u) for u in catalog.units(kind="angular") if u.conversion_factor is None]
```

A unit's `to_json_dict()` is PROJJSON's unit object, the form PROJ writes
inside every definition that uses it:

```{code-cell} python
catalog.units(code="EPSG:9003").one().to_json_dict()
```

```{tip}
To read a length or angle unit from a symbol, an OSDU unit id or an OSDU unit
`persistableReference`, use {mod}`geodetic_engine.units`. This listing shows
what PROJ knows.
```

## Operation methods

{func}`~geodetic_engine.catalog.methods` lists the methods the database lists
and those its operations apply, with the parameters PROJ states for an
operation that applies each, and how many operations do:

```{code-cell} python
tm = catalog.methods(code="EPSG:9807").one()
print(tm, tm.kind, tm.operation_count, "operations")
[str(p) for p in tm.parameters]
```

`parameter` finds the methods taking a parameter:

```{code-cell} python
[str(m) for m in catalog.methods(parameter="Scale difference", authority="EPSG")]
```

The operations applying a method are one listing away:

```{code-cell} python
ntv2 = catalog.methods(code="EPSG:9615").one()
len(catalog.operations(method=ntv2)), "current NTv2 operations"
```

`name` matches a substring, so `name="NTv2"` also finds the velocity-grid
methods named after the format; a pattern pins it down:

```{code-cell} python
import re

[str(m) for m in catalog.methods(name="NTv2")], catalog.methods(name=re.compile("^NTv2$")).one()
```

## Coordinate systems

{func}`~geodetic_engine.catalog.coordinate_systems` lists the coordinate
systems with their axes. proj.db keeps no name for a coordinate system, so the
name is composed in the pattern EPSG uses:

```{code-cell} python
cs = catalog.coordinate_systems(code="EPSG:4400").one()
print(cs.name)
[(axis.name, axis.abbreviation, axis.direction, axis.unit.name) for axis in cs.axes]
```

`axes` finds coordinate systems by their axis abbreviations, in order. This
finds the northing-first Cartesian systems, the ones whose CRSs are easiest to
get wrong:

```{code-cell} python
[str(c) for c in catalog.coordinate_systems(authority="EPSG", axes=("N", "E"))]
```

An axis pointing along a meridian, as on a polar stereographic projection,
states that meridian:

```{code-cell} python
polar = catalog.coordinate_systems(code="EPSG:1026").one()
[(axis.abbreviation, axis.direction, axis.meridian) for axis in polar.axes]
```

`to_json_dict()` is PROJ's export of the coordinate system from a CRS that
uses it, and, for one no CRS uses, assembled from the database in the same
form:

```{code-cell} python
cs.to_json_dict()
```
