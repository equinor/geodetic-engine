---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Geometries

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

{meth}`Transformation.transform_geometry <geodetic_engine.geodesy.Transformation.transform_geometry>`
and {meth}`~geodetic_engine.geodesy.Transformation.transform_geometries`
transform vector geometries, and
{mod}`geodetic_engine.geodesy.geometry` reads, writes and rounds them. They
read and write the encodings databases, GDAL and web services exchange:

| Encoding | Read | Written by | CRS stated |
|---|---|---|---|
| WKT | text | `wkt()` | none |
| WKB, hex WKB | bytes, hex in either case | `wkb()`, `wkb_hex()` (ISO) | none |
| PostGIS EWKT | `SRID=<n>;...` | `ewkt()` | SRID |
| PostGIS EWKB, hex EWKB | bytes, hex | `ewkb()`, `ewkb_hex()` (extended) | SRID |
| GeoJSON geometry object | mapping or JSON text | `geojson()` | none |
| Esri JSON geometry | mapping or JSON text | `esrijson()` | `spatialReference` |
| Shapely geometry, `__geo_interface__` | object | `geometry` | SRID, if set |

Every coordinate goes through
{meth}`~geodetic_engine.geodesy.Transformation.transform_arrays`, in one call
per number of values per position, so geometries have the guarantees points
have: a named operation for a datum change, no ballpark results, required
grids and a coordinate epoch where the operation reads one. The coordinates
are bit for bit those `transform_arrays` gives for the same points.

**Use it when** geometries come from or go to a database, a file or a web
service in one of these encodings. **Use
{func}`~geodetic_engine.geojson.convert_geojson` instead** for whole GeoJSON
documents, with features and properties; see {doc}`/user-guide/geojson`.

## Transform a geometry

```{code-cell} python
from geodetic_engine.geodesy import Transformation

tfm = Transformation("EPSG:4230", "EPSG:32631", operation="EPSG:1612")
result = tfm.transform_geometry(
    "SRID=4230;POLYGON ((2.5 63.5, 3 63.5, 3 64, 2.5 63.5))"
)
result.wkt(decimals=2)
```

The result holds the geometry in the target CRS at full precision, and
writes it in any encoding:

```{code-cell} python
print(result.ewkt(decimals=2))
print(result.ewkb_hex(decimals=2))
print(result.geojson(decimals=2))
print(result.esrijson(decimals=2))
```

and its provenance, as for points:

```{code-cell} python
result.transformation["operation"]["name"], result.transformation["operation"]["applied"]
```

An SRID or Esri `spatialReference` must name the transformation's source CRS;
a geometry stating another is refused with
{class}`~geodetic_engine.geodesy.GeometryError`. SRID 0 means unknown. An
SRID is a bare integer, so `srid_authority` says which authority it is a code
of: `"EPSG"` by default, or any other in the active `proj.db`, such as
`"ESRI"` or one a custom database adds. EWKT and EWKB are written with the
target CRS's integer code in its own authority; a CRS without one, such as an
early-bound CRS, cannot be written as EWKT or EWKB.

## Many geometries

```{code-cell} python
import pandas as pd

shapes = pd.Series(
    ["POINT (2.5 63.5)", None, "LINESTRING (2.5 63.5, 3 64)"],
    index=["well", "none", "pipeline"],
)
tfm.transform_geometries(shapes).wkt(decimals=1)
```

A sequence, a numpy array, a pandas Series or a GeoPandas GeoSeries goes in,
and the encodings come back in the same container, with the same index. A
GeoSeries comes back as one, with the target CRS set, and its own CRS must be
the source CRS. GeoPandas is not a dependency.

One position that cannot be transformed fails the whole batch, naming the
geometry, part, ring and vertex:

```{code-cell} python
:tags: [raises-exception]

tfm.transform_geometries(["POINT (2.5 63.5)", "LINESTRING (2.5 63.5, 3 95)"])
```

## Heights and measures

A Z value is transformed where the CRSs have a height, as in a compound CRS
with a vertical transformation, and carried through unchanged otherwise. M
values are carried through unchanged and written to every encoding but
GeoJSON, which has no place for them. None and empty geometries come back as
they are. Curved geometries (`CIRCULARSTRING` and the like) are refused.

## Rounding

`decimals` rounds each coordinate **half up**, by the decimal a reader sees:
with 2 decimals, `2.675` is `2.68` and `-0.125` is `-0.13`. Python's `round()`
and numpy's `round` give `2.67` and `-0.12`. Rounding is applied to the values
before they are written, so WKT, WKB, hex and GeoJSON hold the same numbers,
`-0.0` becomes `0.0`, and rounding twice gives the same as rounding once. One
number applies to every axis, or give one per axis, in the order x, y, z, m:

```{code-cell} python
from geodetic_engine.geodesy.geometry import round_geometry, write_geometry
import shapely

point = shapely.Point(10.123456789, 60.987654321, 2.345)
write_geometry(point, "wkt", decimals=(8, 8, 2))
```

Rounding keeps every vertex, which can make a polygon self-intersect or a ring
collapse. That is reported with a
{class}`~geodetic_engine.geodesy.GeometryValidityWarning`, or raised with
`validate="raise"`. `grid_size` snaps to a grid instead, which keeps polygons
valid but may remove vertices:

```{code-cell} python
notch = shapely.from_wkt("POLYGON ((0 0, 10 0, 10 1, 5 0.004, 0 1, 0 0))")
print(round_geometry(notch, 2, validate="off"))  # touches itself at (5 0)
round_geometry(notch, grid_size=0.01)
```

WKT is written at full precision with the shortest form of each number that
reads back as the same value, so nothing is cut that was not asked to be.

## Axis order, densifying and orientation

Coordinates are `xy`: longitude or easting first, as WKT, WKB and Esri JSON
are in practice. For data written in a CRS's declared order, such as latitude
first for EPSG:4326 from some WFS 2.0 or Oracle sources, give
`axis_order="crs"`: geometries are read in the source CRS's declared order and
written in the target's.

A straight edge in one CRS is not straight in another. `densify` inserts
vertices first, so that no segment is longer than the given length in source
CRS units.

Ring orientation is kept as transformed by default, which follows the
standards: neither Simple Features nor RFC 7946 rejects either winding. A
left-handed target CRS or an axis swap reverses it. `orient="ccw"` winds
exterior rings counterclockwise (RFC 7946's right-hand rule) and `orient="cw"`
clockwise.

## Without a CRS change

{func}`~geodetic_engine.geodesy.geometry.read_geometry` reads any encoding
above and the CRS it states, and
{func}`~geodetic_engine.geodesy.geometry.write_geometry` writes one:

```{code-cell} python
from geodetic_engine.geodesy.geometry import read_geometry

geometry, crs = read_geometry("0101000020E6100000C7726EBA353F24405B39F0746B7E4E40")
print(geometry, crs)
write_geometry(geometry, "ewkt", crs=crs, decimals=3)
```
