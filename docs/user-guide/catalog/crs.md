---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Finding CRSs

{func}`~geodetic_engine.catalog.crs` lists the coordinate reference systems
the active database holds. Without filters, that is every CRS of every
authority:

```{code-cell} python
from geodetic_engine import catalog

everything = catalog.crs()
print(len(everything), "CRSs, for example", everything[0])
```

## By area and datum

The question this page answers most often: which projected CRSs on ETRS89
cover my area? `area_of_interest` is `(west, south, east, north)` in degrees,
and an area of use intersects it exactly when PROJ's own CRS search says so:

```{code-cell} python
found = catalog.crs(
    authority="EPSG",
    kind="projected",
    datum="ETRS89",
    area_of_interest=(4.5, 58.0, 8.0, 62.0),  # western Norway
)
found.to_dataframe()[["authority_code", "name", "projection_method", "area_of_use"]]
```

`contains=True` keeps only the CRSs whose area of use contains the whole area
of interest, rather than merely touching it:

```{code-cell} python
whole = catalog.crs(
    authority="EPSG", kind="projected", datum="ETRS89",
    area_of_interest=(4.5, 58.0, 8.0, 62.0), contains=True,
)
[entry.authority_code for entry in whole]
```

`datum` takes a code, a name, an alias or an entry. ETRS89 is a
{term}`datum ensemble`; the filter matches the datum exactly, so it does not
match a CRS on one of its member frames:

```{code-cell} python
on_etrf2000 = catalog.crs(authority="EPSG", datum="ETRF2000", kind="geographic_2d")
[str(entry) for entry in on_etrf2000]
```

## By kind

Every entry has a {class}`~geodetic_engine.catalog.CrsKind`, and `kind` takes
one or several, by member or by value:

```{code-cell} python
from collections import Counter

Counter(entry.kind.value for entry in catalog.crs(authority="EPSG"))
```

```{code-cell} python
vertical_in_norway = catalog.crs(
    authority="EPSG", kind="vertical", area_name="Norway"
)
[str(entry) for entry in vertical_in_norway]
```

Every engineering CRS -- the local CRSs of plants, sites and seismic surveys --
of every authority:

```{code-cell} python
engineering = catalog.crs(kind="engineering")
print(len(engineering), "engineering CRSs:", Counter(e.auth_name for e in engineering))
engineering.to_dataframe()[["authority_code", "name", "datums", "area_of_use"]]
```

```{note}
pyproj reports an engineering CRS as a plain `CRS`, and its own engineering
filter finds none. The kind here is read from `proj.db`, so
`kind="engineering"` finds them; see {doc}`/workarounds`.
```

## By authority

`authority` takes any authority {func}`~geodetic_engine.catalog.authorities`
lists, or several:

```{code-cell} python
[str(authority) for authority in catalog.authorities()]
```

The CRSs the OSDU catalogue defines itself are under the authority `OSDU` in
the database the released package ships (see {doc}`/user-guide/osdudb`). They
are {term}`bound CRSs <bound CRS>`, each carrying the transformation the
catalogue binds it to, and each lists the
{term}`OSDU record ids <OSDU record id>` that publish it:

```{code-cell} python
osdu = catalog.crs(authority="OSDU")
print(len(osdu), "CRSs of authority OSDU:", Counter(e.kind.value for e in osdu))
print(sum(e.bound for e in osdu), "of them bound")
osdu.to_dataframe()[["authority_code", "name", "kind", "bound", "osdu_ids"]].head(10)
```

The catalogue also publishes plain EPSG CRSs under record ids of their own,
and those stay under the authority `EPSG`. So every CRS the OSDU catalogue
publishes, whatever its authority, is every entry with `osdu_ids`:

```{code-cell} python
published = [entry for entry in catalog.crs(deprecated=None) if entry.osdu_ids]
print(len(published), "CRSs have an OSDU record id:", Counter(e.auth_name for e in published))
catalog.crs(code="EPSG:23031").one().osdu_ids
```

## By unit, coordinate system and projection

`unit` matches a CRS with any axis in that unit, and `coordinate_system_kind`
the kind of its coordinate system:

```{code-cell} python
feet = catalog.crs(
    authority="EPSG", unit="US survey foot", projection_method="Lambert Conic"
)
print(len(feet), "CRSs in US survey feet on a Lambert Conic Conformal projection")
feet.to_dataframe()[["authority_code", "name", "axis_units"]].head()
```

```{code-cell} python
[str(e) for e in catalog.crs(authority="EPSG", coordinate_system_kind="spherical")]
```

## By name

`name` is a case-insensitive substring of the name or of any alias the
database records, or a {class}`re.Pattern` for anything more precise:

```{code-cell} python
import re

zone_32 = catalog.crs(authority="EPSG", name=re.compile(r"/ UTM zone 32N$"))
[str(entry) for entry in zone_32]
```

The aliases come from the database, so old and vendor names find the current
CRS:

```{code-cell} python
tm32 = catalog.crs(name="ETRS89-TM32").one()
print(tm32)
print(tm32.aliases)
```

## Deprecated CRSs

Deprecated CRSs are left out, as PROJ leaves them out. `deprecated=True` lists
only them, and `deprecated=None` lists both. A deprecated entry names what
replaces it, where the authority states it:

```{code-cell} python
for entry in catalog.crs(authority="EPSG", deprecated=True, name="UTM zone 32N"):
    print(entry, "->", entry.replaced_by)
```

## Reading an entry

A {class}`~geodetic_engine.catalog.CrsEntry` is read from the database
without building the CRS:

```{code-cell} python
entry = catalog.crs(code="EPSG:25832").one()
print("kind:            ", entry.kind)
print("datums:          ", [str(d) for d in entry.datums])
print("coordinate system:", entry.coordinate_system)
print("axis units:      ", [u.name for u in entry.axis_units])
print("area of use:     ", entry.area_of_use.bounds)
print("usages:          ", len(entry.areas_of_use))
```

A compound CRS reports both its datums, horizontal then vertical, and the
units of all its axes:

```{code-cell} python
compound = catalog.crs(code="EPSG:9518").one()
[str(d) for d in compound.datums], [u.name for u in compound.axis_units]
```

Every reference is an {class}`~geodetic_engine.catalog.ObjectRef`, and every
`code` filter takes one, so following a reference is one more listing:

```{code-cell} python
catalog.datums(code=entry.datums).one()
```

## Using an entry

{meth}`~geodetic_engine.catalog.CrsEntry.resolve` builds the CRS the way
{mod}`geodetic_engine.geodesy` would, and the authority code goes anywhere a
CRS does:

```{code-cell} python
from geodetic_engine.geodesy import transform

crs = entry.resolve()
print(crs.axis_abbreviations, crs.axis_units)
transform("EPSG:4258", entry.authority_code, (10.7522, 59.9139)).coordinates
```

{meth}`~geodetic_engine.catalog.CrsEntry.to_json_dict` is the CRS's full
PROJJSON, exactly as PROJ exports it:

```{code-cell} python
definition = entry.to_json_dict()
definition["$schema"], definition["type"], definition["conversion"]["method"]
```
