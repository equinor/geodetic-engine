---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# GeoJSON documents

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

{func}`~geodetic_engine.geojson.convert_geojson` converts every position of a
GeoJSON-family document to another CRS. The structure, `id`, `properties`,
foreign members and key order are kept, every `bbox` there was is computed
again, and the members that state the CRS are written for the target. The
input is never modified.

It reads three flavours:

| Flavour | Told by | CRS of the positions | Axis order |
|---|---|---|---|
| RFC 7946 GeoJSON | plain `type` names | CRS84, or CRS84h with heights; `source_crs` for a prior arrangement | longitude, latitude |
| GeoJSON 2008 `crs` member | `crs.properties.name` | the URN or URL named | `xy`, as GDAL wrote it |
| OGC JSON-FG 1.0 | `conformsTo`, `place` or `coordRefSys` | a `place` is in the nearest `coordRefSys` (geometry, feature, collection), a `geometry` in CRS84 | the CRS's declared order |
| OSDU AnyCrs GeoJSON | `AnyCrs` type names | `CoordinateReferenceSystemID`, else `persistableReferenceCrs` | `xy` |

The positions of each source CRS, with and without a height, are transformed
in one call to
{meth}`~geodetic_engine.geodesy.Transformation.transform_arrays`, so the
package's rules hold: a datum change names its operation, no ballpark results,
required grids, and a coordinate epoch where the operation reads one.

**Use it when** you have a whole document. For single geometries in WKT, WKB
and other encodings, see {doc}`/user-guide/geodesy/geometries`.

## Convert a document

```{code-cell} python
from geodetic_engine.geojson import convert_geojson

document = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": "well-1",
            "properties": {"name": "A-1"},
            "geometry": {"type": "Point", "coordinates": [3.0, 60.0]},
        }
    ],
}
result = convert_geojson(document, "EPSG:32631", decimals=2)
result.document
```

Plain GeoJSON in a CRS other than WGS 84 longitude and latitude is written as
JSON-FG, the open standard for it: the positions go in `place`, in the
target CRS's declared axis order, `coordRefSys` names the CRS by its OGC URI,
and `geometry` is null. `output="jsonfg-plus"` keeps a WGS 84 `geometry` for
readers that only know GeoJSON:

```{code-cell} python
convert_geojson(document, "EPSG:32631", output="jsonfg-plus", decimals=2).document["features"][0]
```

`output` chooses the flavour: `"auto"` keeps AnyCrs as AnyCrs and writes plain
GeoJSON as such when the target is WGS 84, `"geojson"`, `"anycrs"`,
`"jsonfg"` and `"jsonfg-plus"` choose one. JSON-FG needs a target with an OGC
URI, which EPSG and OGC CRSs have; for others, write `"anycrs"`.

The result also counts the positions and says how they were converted:

```{code-cell} python
result.success_count, result.total_count, result.operations_applied
```

## OSDU AnyCrs GeoJSON

```{code-cell} python
import json

anycrs = {
    "type": "AnyCrsFeatureCollection",
    "CoordinateReferenceSystemID": "EPSG:23031",
    "VerticalUnitID": "osdu:reference-data--UnitOfMeasure:m:",
    "features": [
        {
            "type": "AnyCrsFeature",
            "properties": {},
            "geometry": {"type": "AnyCrsPoint", "coordinates": [500000.0, 6650000.0, 10.0]},
        }
    ],
}
converted = convert_geojson(
    anycrs, "EPSG:32631", operation="EPSG:1612", target_unit_z="ft", decimals=3
)
print(converted.document["features"][0]["geometry"])
print(json.loads(converted.document["persistableReferenceUnitZ"])["symbol"])
```

Heights are converted to `target_unit_z`, and `persistableReferenceUnitZ` is
always written for them. A vertical CRS stated in
`VerticalCoordinateReferenceSystemID` or `persistableReferenceVerticalCrs`
makes positions with heights compound, so heights are transformed between
vertical datums, not only scaled; give a compound or three-dimensional target
for that, and an operation where a geoid is involved.

{func}`~geodetic_engine.geojson.convert_geojson_request` takes and returns the
OSDU CRS conversion service's `convertGeoJson` request and response, so
results can be compared with the service or used in its place:

```{code-cell} python
from geodetic_engine.geojson import convert_geojson_request

response = convert_geojson_request(
    {"toCRS": "EPSG:4326", "transformation": "EPSG:1612", "featureCollection": anycrs}
)
response["featureCollection"]["features"][0]["geometry"], response["successCount"]
```

## Positions that fail

`on_error="raise"`, the default, raises the error with the position's JSON
path. `"withdraw"` writes the feature with a null geometry, which RFC 7946
allows, and lists the failures in `result.failures`. `"nan"` writes NaN in the
position, as the OSDU service does; JSON has no NaN, so the document is then
not valid JSON.

```{code-cell} python
failing = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [3.0, 60.0]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [93.0, 0.0]}},
    ],
}
withdrawn = convert_geojson(failing, "EPSG:32631", output="anycrs", on_error="withdraw")
withdrawn.document["features"][1]["geometry"], withdrawn.failures[0][0]
```

## Rounding and orientation

`decimals` rounds converted values half up, as
{func}`~geodetic_engine.geodesy.geometry.round_geometry` does: one number for
all, or one per value in `xy` order and then the height. `bbox` is computed
from the rounded values.

Ring orientation is kept as transformed by default; `orient="ccw"` applies
RFC 7946's right-hand rule, and `orient="cw"` the opposite.

## Densifying

RFC 7946 draws a segment straight in longitude and latitude, and only its ends
are converted, so in a projected target a segment is the chord between them:
an edge of a licence block along a parallel is 100 m off it at 60°N in UTM.
`densify` inserts vertices first, so that no segment is longer than the given
length, in source CRS units. `segments="geodesic"` inserts them along the
geodesic on the source CRS's ellipsoid instead, with `densify` in metres, for
boundaries defined by geodesics:

```{code-cell} python
block = {
    "type": "Polygon",
    "coordinates": [[[2, 60], [3, 60], [3, 61], [2, 61], [2, 60]]],
}
dense = convert_geojson(block, "EPSG:32631", output="anycrs", densify=0.05)
len(dense.document["coordinates"][0]), dense.total_count
```

The positions inserted are converted, counted and written like the others. A
failed one is named by the position before it, as `coordinates[3] (vertex 2
densify inserted after it)`.

## The antimeridian and the poles

Converted into a geographic CRS, a line or polygon that crosses the
antimeridian has positions at both ends of the longitude range, and a segment
between them, read straight in longitude and latitude, would go the long way
round. Such a part is cut at the antimeridian into parts of a multipart
geometry, as RFC 7946 (3.1.9) has it, and its `type` is renamed to match. A ring
round a pole, as a polar projection draws one, is closed along the antimeridian
through the pole:

```{code-cell} python
crossing = {
    "type": "AnyCrsFeatureCollection",
    "CoordinateReferenceSystemID": "EPSG:32660",
    "features": [
        {
            "type": "AnyCrsFeature",
            "properties": {},
            "geometry": {
                "type": "AnyCrsLineString",
                "coordinates": [[600000, 6650000], [800000, 6680000]],
            },
        }
    ],
}
cut = convert_geojson(crossing, "EPSG:4326", output="geojson", decimals=4)
cut.document["features"][0]["geometry"]
```

`antimeridian="unwrap"` keeps the part whole, its longitudes carried on past
±180°, and `antimeridian=None` keeps it as transformed, as
`convert_geojson_request` does for the OSDU service. A JSON-FG `place` is cut
by its longitude whatever its axis order, and the WGS 84 `geometry` that
`jsonfg-plus` writes beside it is cut on its own. A segment that does go the
long way round in a geographic source is kept, as is a part with a position
that failed. No position moves: a cut part gains vertices on the antimeridian,
rounded like the others and not counted. The cut is that of
{doc}`geometries </user-guide/geodesy/geometries>`, bit for bit.
