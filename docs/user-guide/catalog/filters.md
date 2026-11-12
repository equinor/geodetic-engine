---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Filters

Every listing function takes keyword arguments only. Every filter given must
hold, so filters combine by narrowing. The same filter means the same thing in
every listing.

## Shared filters

| Filter | Takes | Matches |
|---|---|---|
| `authority` | A name, or several, without case | Objects of those authorities. Must be one {func}`~geodetic_engine.catalog.authorities` lists |
| `name` | Text, or a {class}`re.Pattern` | Text: a case-insensitive substring of the name or any alias. Pattern: `search()` on them |
| `code` | `"AUTH:CODE"`, a bare code, an entry, an {class}`~geodetic_engine.catalog.ObjectRef`, or several | Those objects. A bare code (`4326`, `"4326"`) matches it in any authority |
| `deprecated` | `False` (default), `True`, `None` | Current only, deprecated only, or both |
| `limit`, `offset` | Whole numbers | Paging, applied after every other filter |

Listings of objects that state where they are valid -- CRSs, operations,
datums and grids -- also take:

| Filter | Takes | Matches |
|---|---|---|
| `area_of_interest` | `(west, south, east, north)` in degrees, or a pyproj or geodesy box | Objects with an area of use intersecting it |
| `contains` | `bool` | With `area_of_interest`: only areas of use containing it entirely |
| `area_name` | Text, or a pattern | The description of an area of use, for example `"Norway"` |

## Free text and references

Free-text filters (`name`, `area_name`, `projection_method`) match a
substring, without case:

```{code-cell} python
from geodetic_engine import catalog

[str(e) for e in catalog.ellipsoids(authority="EPSG", name="clarke 1880")]
```

Reference filters (`datum`, `unit`, `ellipsoid`, `prime_meridian`, `method`,
`member_of`, `source_crs`, `target_crs`, `parameter`, `operation`) name another
object: by code, by entry, or by a name or alias matched exactly but for case.
A name may match several objects; any of them qualifies:

```{code-cell} python
same = [
    catalog.crs(authority="EPSG", kind="geographic_2d", datum=d)
    for d in ("EPSG:6258", "European Terrestrial Reference System 1989 ensemble")
]
[len(listing) for listing in same]
```

A reference that names nothing of its kind is an error, not an empty listing.
This catches the commonest slip, a CRS code given as a datum:

```{code-cell} python
:tags: [raises-exception]

catalog.crs(datum="EPSG:4326")
```

## Kinds

`kind` takes one value or several, by enumeration member or by value without
case:

```{code-cell} python
from geodetic_engine.catalog import CrsKind

len(catalog.crs(authority="EPSG", kind=[CrsKind.GEOGRAPHIC_2D, "Geographic_3D"]))
```

An unknown kind names the choices:

```{code-cell} python
:tags: [raises-exception]

catalog.crs(kind="map")
```

## Ranges

Numeric filters take one number, matched exactly, or `(low, high)` with
`None` for an open end. An object without the value never matches:

```{code-cell} python
[str(e) for e in catalog.ellipsoids(authority="EPSG", inverse_flattening=(299, None))]
```

A listing's JSON states an open end as `null`.

## Areas of interest

West greater than east is an area across the antimeridian, as in PROJ.
Whether an area of use qualifies is decided exactly as PROJ decides it in its
own CRS search, so every listing agrees with
{func}`pyproj.database.query_crs_info`.
An object with several usages qualifies when any of them does:

```{code-cell} python
fiji = catalog.crs(
    authority="EPSG", kind="projected", area_of_interest=(177.0, -19.0, -179.0, -16.0)
)
[str(e) for e in fiji][:6]
```

Between two boxes that do not cross the antimeridian, touching edges count;
when either crosses, they do not. That is PROJ's rule, kept so that the
listings agree.

## Paging

`limit` and `offset` apply after every filter, so pages never overlap or skip,
and reading stops once `limit` entries are found:

```{code-cell} python
first, second = (
    catalog.crs(authority="EPSG", kind="vertical", limit=3, offset=offset)
    for offset in (0, 3)
)
[str(e) for e in first], [str(e) for e in second]
```

## Errors

| Error | When |
|---|---|
| {class}`~geodetic_engine.catalog.UnknownAuthorityError` | `authority` names one the database does not hold |
| {class}`~geodetic_engine.catalog.InvalidFilterError` | A filter is malformed, or a reference names nothing of its kind |
| {class}`~geodetic_engine.catalog.NotFoundError` | {meth}`Listing.one <geodetic_engine.catalog.Listing.one>` found nothing |
| {class}`~geodetic_engine.catalog.NotUniqueError` | {meth}`Listing.one <geodetic_engine.catalog.Listing.one>` found more than one |
| {class}`~geodetic_engine.catalog.GridIndexUnavailableError` | `check_remote=True` and the network's list of grids could not be read |
| {class}`~geodetic_engine.catalog.CatalogError` | Their base; also no `proj.db` on the search path, or a listing read or exported after its database was switched |

The first two subclass {class}`ValueError`, and the next two
{class}`LookupError`, as well as {class}`~geodetic_engine.catalog.CatalogError`.
