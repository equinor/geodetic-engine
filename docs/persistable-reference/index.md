---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Persistable references (OSDU)

`geodetic_engine.persistablereference` reads, writes and looks up OSDU
**persistableReference** payloads.

## What a persistableReference is

`persistableReference` is a string that fully describes the coordinate reference system (CRS) or a coordinate transformation. Rather than referencing an external registry entry, it contains the definition itself.
The value consists of a small JSON document that wraps the definition, as ESRI WKT (Well-Known Text). It is often URL-encoded and commonly stored as a string within another JSON document, such as an OSDU record.
What distinguishes a persistableReference from an identifier such as EPSG:23032 is that it is self-contained. An EPSG code is merely a reference to an external registry entry, whereas a persistableReference carries the full definition required to reconstruct the CRS, coordinate transformation, or unit, even when the original registry is unavailable.


### Self-contained, unlike a code

A code such as `EPSG:23032` is only a pointer. To use it, the reader needs the
EPSG registry, in a version that defines that code the way the sender meant. A
`persistableReference` carries everything needed to rebuild the CRS,
transformation or unit, so it still means the same thing when the registry is
missing, older or newer than the sender's.

## Anatomy of a payload

The example below is a bound CRS from the OSDU reference-data catalogue,
**ED50 / UTM zone 32N bound to WGS 84 by transformation EPSG:1612**, exactly as
the catalogue publishes it, apart from the layout. Expand the cell to see the
raw string, which is a single line.

```{code-cell} python
:tags: [hide-input]

# OSDU BoundProjected:EPSG::23032_EPSG::1612
ED50_UTM32N_VIA_1612 = r'''{"authCode":{"auth":"OSDU","code":"23032023"},"lateBoundCRS":{"authCode":{"auth":"EPSG","code":"23032"},"name":"ED_1950_UTM_Zone_32N","type":"LBC","ver":"PE_10_9_1","wkt":"PROJCS[\"ED_1950_UTM_Zone_32N\",GEOGCS[\"GCS_European_1950\",DATUM[\"D_European_1950\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],PROJECTION[\"Transverse_Mercator\"],PARAMETER[\"False_Easting\",500000.0],PARAMETER[\"False_Northing\",0.0],PARAMETER[\"Central_Meridian\",9.0],PARAMETER[\"Scale_Factor\",0.9996],PARAMETER[\"Latitude_Of_Origin\",0.0],UNIT[\"Meter\",1.0],AUTHORITY[\"EPSG\",23032]]"},"name":"ED50 * EPSG-Nor N62 2001 / UTM zone 32N [23032,1612]","singleCT":{"authCode":{"auth":"EPSG","code":"1612"},"name":"ED_1950_To_WGS_1984_23","type":"ST","ver":"PE_10_9_1","wkt":"GEOGTRAN[\"ED_1950_To_WGS_1984_23\",GEOGCS[\"GCS_European_1950\",DATUM[\"D_European_1950\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],GEOGCS[\"GCS_WGS_1984\",DATUM[\"D_WGS_1984\",SPHEROID[\"WGS_1984\",6378137.0,298.257223563]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],METHOD[\"Position_Vector\"],PARAMETER[\"X_Axis_Translation\",-116.641],PARAMETER[\"Y_Axis_Translation\",-56.931],PARAMETER[\"Z_Axis_Translation\",-110.559],PARAMETER[\"X_Axis_Rotation\",0.893],PARAMETER[\"Y_Axis_Rotation\",0.921],PARAMETER[\"Z_Axis_Rotation\",-0.917],PARAMETER[\"Scale_Difference\",-3.52],OPERATIONACCURACY[1.0],AUTHORITY[\"EPSG\",1612]]"},"type":"EBC","ver":"PE_10_9_1"}'''
```

{func}`~geodetic_engine.persistablereference.pretty_persistable_reference`
indents the JSON and keeps the members in the order the payload states them.
The result is still valid JSON:

```{code-cell} python
from geodetic_engine.persistablereference import pretty_persistable_reference

print(pretty_persistable_reference(ED50_UTM32N_VIA_1612))
```

With `wkt=True` each embedded ESRI WKT is also laid out over indented lines,
the way PROJ indents WKT. Only whitespace changes, but the result is **for
reading and is not JSON**: store or send the original string.

```{code-cell} python
print(pretty_persistable_reference(ED50_UTM32N_VIA_1612, wkt=True))
```

Reading it from the top:

| Member | Meaning |
|---|---|
| `type` | What the payload states. `EBC` is an *early bound CRS*: a CRS packaged with the one transformation to use with it. Other kinds are in the table below. |
| `name` | The name the catalogue gives it. The numbers in brackets are the CRS and the transformation. |
| `authCode` | The record's own identifier, here in OSDU's code space. Provenance, never looked up. |
| `lateBoundCRS` | The CRS on its own: ED50 / UTM zone 32N. Its `wkt` is a complete `PROJCS`, with datum, ellipsoid and projection parameters. |
| `singleCT` | The transformation to WGS 84, as an ESRI `GEOGTRAN`: a seven-parameter Position Vector shift, with its stated accuracy. |
| `ver` | The version of the ESRI WKT dictionary that wrote it. |

Because the transformation is part of the definition, nothing is left to be
chosen later. The same CRS named by its plain EPSG code would refuse to
transform until a datum operation is named, because EPSG publishes several
shifts from ED50 and the package never picks one for you. See
{doc}`/background/bound-crs`.

## The six kinds of payload

The `type` member says what a payload states. It is read as a
{class}`~geodetic_engine.persistablereference.Kind`:

| `type` | Kind | Parsed as |
|---|---|---|
| `LBC` | Late-bound CRS: a CRS on its own | {class}`~geodetic_engine.persistablereference.CrsReference` |
| `EBC` | Early-bound CRS: a CRS bound to a hub by one transformation | {class}`~geodetic_engine.persistablereference.CrsReference` |
| `ST` | Single transformation | {class}`~geodetic_engine.persistablereference.OperationReference` |
| `CT` | Concatenated transformation | {class}`~geodetic_engine.persistablereference.OperationReference` |
| `USO` | Unit by scale and offset | {class}`~geodetic_engine.persistablereference.UnitReference` |
| `UAD` | Unit by the ABCD formula $y = (A + Bx)/(C + Dx)$ | {class}`~geodetic_engine.persistablereference.UnitReference` |

## Three sources of a payload

The same CRS can reach you as a payload in three ways. They describe the same
thing, but they are not the same string, and the package keeps them apart.

| Source | Where it comes from | Use it for |
|---|---|---|
| **Received** | Inside the data you were given: an OSDU record's `persistableReference` field | Transforming with exactly what the sender stated: {doc}`usage` |
| **Generated** | {func}`~geodetic_engine.persistablereference.to_persistable_reference` writes one from a PROJ or pyproj definition | Producing a payload where none exists: {doc}`usage` |
| **Published** | {func}`~geodetic_engine.persistablereference.persistable_reference_from_catalog` reads the exact string an OSDU catalogue publishes for the object, from a `proj.db` built from that catalogue | Writing an OSDU record that names that object: {doc}`catalog` |

A generated payload is never substituted for a published one that the
catalogue does not state.

## A first example

Parse the payload above, inspect what it states, and transform with it. Both
the bound CRS and the target CRS can be payloads or plain codes. No operation is
passed, because the bound CRS carries its own:

```{code-cell} python
from geodetic_engine.geodesy import transform
from geodetic_engine.persistablereference import parse_persistable_reference

reference = parse_persistable_reference(ED50_UTM32N_VIA_1612)
print(f"{reference.kind.name}  {reference.name}")
print(f"base CRS  {reference.late_bound.authority_code}  {reference.late_bound.name}")
print(f"bound by  {reference.operation.authority_code}  {reference.operation.method_names[0]}")

result = transform(ED50_UTM32N_VIA_1612, "EPSG:32632", [(500000.0, 6650000.0)])
print(result.coordinates[0], "|", result.operation.name)
```

## What is in the package

| I want to... | Go to |
|---|---|
| Parse a payload, build a CRS, operation or unit from it, transform with it, or write one | {doc}`usage` |
| Look up, pretty-print or export the exact reference an OSDU catalogue publishes | {doc}`catalog` |
| Build the `proj.db` those lookups read | {doc}`/user-guide/osdudb` |
| Find a class or function | {doc}`/api/persistablereference` |
| Know what a refused method or payload means | {ref}`persistable-reference-errors` |

```{toctree}
:hidden:
:maxdepth: 2

usage
catalog
```
