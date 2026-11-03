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
