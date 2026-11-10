---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Datums, ellipsoids and prime meridians

## Datums and datum ensembles

{func}`~geodetic_engine.catalog.datums` lists geodetic, vertical and
engineering datums, and datum ensembles:

```{code-cell} python
from geodetic_engine import catalog

catalog.datums(authority="EPSG", area_name="Norway").to_dataframe()[
    ["authority_code", "name", "kind", "is_ensemble", "is_dynamic", "ellipsoid"]
]
```

`ensemble=True` keeps the {term}`datum ensembles <datum ensemble>`. Each
names its members and states its accuracy:

```{code-cell} python
etrs89 = catalog.datums(code="EPSG:6258").one()
print(etrs89.projjson_type, etrs89.ensemble_accuracy, "m")
print(len(etrs89.members), "members, the first", etrs89.members[0])
```

`member_of` turns that round, listing the members of an ensemble:

```{code-cell} python
[str(d) for d in catalog.datums(member_of="WGS 84", deprecated=None)]
```

A dynamic reference frame has a reference epoch; `dynamic` and
`frame_reference_epoch` filter on it:

```{code-cell} python
recent = catalog.datums(authority="EPSG", frame_reference_epoch=(2015, None))
[(str(d), d.frame_reference_epoch) for d in recent]
```

`ellipsoid` and `prime_meridian` find the datums using one:

```{code-cell} python
[str(d) for d in catalog.datums(authority="EPSG", prime_meridian="Paris")]
```

A datum's PROJJSON is PROJ's export, so an ensemble's lists its members:

```{code-cell} python
definition = etrs89.to_json_dict()
definition["type"], definition["accuracy"], definition["members"][:2]
```

## Ellipsoids

{func}`~geodetic_engine.catalog.ellipsoids` gives every ellipsoid in metres,
whatever unit the database states it in, with both the semi-minor axis and the
inverse flattening, whichever one is stated:

```{code-cell} python
catalog.ellipsoids(authority="EPSG", semi_major_axis=(6378000, 6378400)).to_dataframe()[
    ["authority_code", "name", "semi_major_axis", "semi_minor_axis", "inverse_flattening"]
]
```

A sphere has no inverse flattening. `IAU_2015` models other bodies;
`celestial_body` keeps one:

```{code-cell} python
[str(e) for e in catalog.ellipsoids(celestial_body="Mars")]
```

The datums on an ellipsoid are one listing away:

```{code-cell} python
clarke = catalog.ellipsoids(authority="EPSG", name="Clarke 1880 (RGS)").one()
len(catalog.datums(ellipsoid=clarke)), "datums on", str(clarke)
```

## Prime meridians

{func}`~geodetic_engine.catalog.prime_meridians` gives each longitude as
stated, in its unit, and in decimal degrees. EPSG states several in
sexagesimal degrees, which PROJ reads as decimal degrees, and Paris in grads:

```{code-cell} python
catalog.prime_meridians(authority="EPSG").to_dataframe()[
    ["authority_code", "name", "longitude", "unit", "longitude_degrees"]
]
```
