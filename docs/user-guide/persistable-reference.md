---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Persistable references (OSDU)

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

`geodetic_engine.persistablereference` reads and writes OSDU
**persistableReference** payloads. These are JSON envelopes around ESRI WKT
that OSDU records use to say which CRS, transformation or unit their numbers
are in.

**Use it when** your data comes from OSDU, or anywhere else that states CRSs
and transformations as ESRI WKT, and you need to transform with exactly what
the payload states.

**What is different about it**: a payload is read as a definition, not as a
code to look up. The WKT parameters are what gets built. The authority code
next to them is kept as provenance only. A payload whose code is wrong, or
names a register this machine has never heard of, still means exactly what its
WKT says.

## The payloads used on this page

All are verbatim from an OSDU reference-data catalogue. Expand the cell to
copy them.

```{code-cell} python
:tags: [hide-input]

# OSDU BoundProjected:EPSG::23032_EPSG::1612 -- ED50 / UTM 32N bound to WGS 84 by EPSG:1612
ED50_UTM32N_VIA_1612 = r'''{"authCode":{"auth":"OSDU","code":"23032023"},"lateBoundCRS":{"authCode":{"auth":"EPSG","code":"23032"},"name":"ED_1950_UTM_Zone_32N","type":"LBC","ver":"PE_10_9_1","wkt":"PROJCS[\"ED_1950_UTM_Zone_32N\",GEOGCS[\"GCS_European_1950\",DATUM[\"D_European_1950\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],PROJECTION[\"Transverse_Mercator\"],PARAMETER[\"False_Easting\",500000.0],PARAMETER[\"False_Northing\",0.0],PARAMETER[\"Central_Meridian\",9.0],PARAMETER[\"Scale_Factor\",0.9996],PARAMETER[\"Latitude_Of_Origin\",0.0],UNIT[\"Meter\",1.0],AUTHORITY[\"EPSG\",23032]]"},"name":"ED50 * EPSG-Nor N62 2001 / UTM zone 32N [23032,1612]","singleCT":{"authCode":{"auth":"EPSG","code":"1612"},"name":"ED_1950_To_WGS_1984_23","type":"ST","ver":"PE_10_9_1","wkt":"GEOGTRAN[\"ED_1950_To_WGS_1984_23\",GEOGCS[\"GCS_European_1950\",DATUM[\"D_European_1950\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],GEOGCS[\"GCS_WGS_1984\",DATUM[\"D_WGS_1984\",SPHEROID[\"WGS_1984\",6378137.0,298.257223563]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],METHOD[\"Position_Vector\"],PARAMETER[\"X_Axis_Translation\",-116.641],PARAMETER[\"Y_Axis_Translation\",-56.931],PARAMETER[\"Z_Axis_Translation\",-110.559],PARAMETER[\"X_Axis_Rotation\",0.893],PARAMETER[\"Y_Axis_Rotation\",0.921],PARAMETER[\"Z_Axis_Rotation\",-0.917],PARAMETER[\"Scale_Difference\",-3.52],OPERATIONACCURACY[1.0],AUTHORITY[\"EPSG\",1612]]"},"type":"EBC","ver":"PE_10_9_1"}'''

# OSDU Geographic2D:EPSG::4326 -- WGS 84 geographic, late bound
WGS84 = r'''{"authCode":{"auth":"EPSG","code":"4326"},"name":"GCS_WGS_1984","type":"LBC","ver":"PE_10_9_1","wkt":"GEOGCS[\"GCS_WGS_1984\",DATUM[\"D_WGS_1984\",SPHEROID[\"WGS_1984\",6378137.0,298.257223563]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433],AUTHORITY[\"EPSG\",4326]]"}'''

# OSDU Projected:EPSG::32632 -- WGS 84 / UTM zone 32N, late bound
WGS84_UTM32N = r'''{"authCode":{"auth":"EPSG","code":"32632"},"name":"WGS_1984_UTM_Zone_32N","type":"LBC","ver":"PE_10_9_1","wkt":"PROJCS[\"WGS_1984_UTM_Zone_32N\",GEOGCS[\"GCS_WGS_1984\",DATUM[\"D_WGS_1984\",SPHEROID[\"WGS_1984\",6378137.0,298.257223563]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],PROJECTION[\"Transverse_Mercator\"],PARAMETER[\"False_Easting\",500000.0],PARAMETER[\"False_Northing\",0.0],PARAMETER[\"Central_Meridian\",9.0],PARAMETER[\"Scale_Factor\",0.9996],PARAMETER[\"Latitude_Of_Origin\",0.0],UNIT[\"Meter\",1.0],AUTHORITY[\"EPSG\",32632]]"}'''

# EPSG:1612 as a single transformation (ST)
ED50_TO_WGS84_1612 = r'''{"authCode":{"auth":"EPSG","code":"1612"},"name":"ED_1950_To_WGS_1984_23","type":"ST","ver":"PE_10_9_1","wkt":"GEOGTRAN[\"ED_1950_To_WGS_1984_23\",GEOGCS[\"GCS_European_1950\",DATUM[\"D_European_1950\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],GEOGCS[\"GCS_WGS_1984\",DATUM[\"D_WGS_1984\",SPHEROID[\"WGS_1984\",6378137.0,298.257223563]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],METHOD[\"Position_Vector\"],PARAMETER[\"X_Axis_Translation\",-116.641],PARAMETER[\"Y_Axis_Translation\",-56.931],PARAMETER[\"Z_Axis_Translation\",-110.559],PARAMETER[\"X_Axis_Rotation\",0.893],PARAMETER[\"Y_Axis_Rotation\",0.921],PARAMETER[\"Z_Axis_Rotation\",-0.917],PARAMETER[\"Scale_Difference\",-3.52],OPERATIONACCURACY[1.0],AUTHORITY[\"EPSG\",1612]]"}'''

# EPSG:8517 as a concatenated transformation (CT): two geocentric translations
CHOS_MALAL_TO_WGS84 = r'''{"authCode":{"auth":"EPSG","code":"8517"},"cts":[{"authCode":{"auth":"EPSG","code":"1528"},"name":"Chos_Malal_1914_To_Campo_Inchauspe","type":"ST","ver":"PE_10_9_1","wkt":"GEOGTRAN[\"Chos_Malal_1914_To_Campo_Inchauspe\",GEOGCS[\"GCS_Chos_Malal_1914\",DATUM[\"D_Chos_Malal_1914\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],GEOGCS[\"GCS_Campo_Inchauspe\",DATUM[\"D_Campo_Inchauspe\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],METHOD[\"Geocentric_Translation\"],PARAMETER[\"X_Axis_Translation\",160.0],PARAMETER[\"Y_Axis_Translation\",26.0],PARAMETER[\"Z_Axis_Translation\",41.0],OPERATIONACCURACY[10.0],AUTHORITY[\"EPSG\",1528]]"},{"authCode":{"auth":"EPSG","code":"1527"},"name":"Campo_Inchauspe_To_WGS_1984_2","type":"ST","ver":"PE_10_9_1","wkt":"GEOGTRAN[\"Campo_Inchauspe_To_WGS_1984_2\",GEOGCS[\"GCS_Campo_Inchauspe\",DATUM[\"D_Campo_Inchauspe\",SPHEROID[\"International_1924\",6378388.0,297.0]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],GEOGCS[\"GCS_WGS_1984\",DATUM[\"D_WGS_1984\",SPHEROID[\"WGS_1984\",6378137.0,298.257223563]],PRIMEM[\"Greenwich\",0.0],UNIT[\"Degree\",0.0174532925199433]],METHOD[\"Geocentric_Translation\"],PARAMETER[\"X_Axis_Translation\",-154.5],PARAMETER[\"Y_Axis_Translation\",150.7],PARAMETER[\"Z_Axis_Translation\",100.4],OPERATIONACCURACY[0.5],AUTHORITY[\"EPSG\",1527]]"}],"name":"Chos Malal 1914 to WGS 84 (1)","policy":"Concatenated","type":"CT","ver":"PE_10_9_1"}'''

# EPSG:9225, a time-specific Helmert -- refused, see below
WGS84_TO_ETRS89_TIME_SPECIFIC = r'''{"authCode":{"auth":"EPSG","code":"9225"},"name":"WGS_84_to_ETRS89_2","type":"ST","ver":"PE_10_9_1","wkt":"GEOGTRAN[\"WGS_84_to_ETRS89_2\",GEOGCS[\"WGS_84\",DATUM[\"World_Geodetic_System_1984_ensemble\",SPHEROID[\"WGS_84\",6378137,298.257223563]],PRIMEM[\"Greenwich\",0],UNIT[\"Meter\",1]],GEOGCS[\"ETRS89\",DATUM[\"European_Terrestrial_Reference_System_1989_ensemble\",SPHEROID[\"GRS_1980\",6378137,298.257222101]],PRIMEM[\"Greenwich\",0],UNIT[\"metre\",1]],METHOD[\"Time-specific_Position_Vector_transform_geocen\"],PARAMETER[\"X_Axis_Translation\",0.054],PARAMETER[\"Y_Axis_Translation\",0.051],PARAMETER[\"Z_Axis_Translation\",-0.085],PARAMETER[\"X_Axis_Rotation\",0.0021],PARAMETER[\"Y_Axis_Rotation\",0.0126],PARAMETER[\"Z_Axis_Rotation\",-0.0204],PARAMETER[\"Scale_Difference\",0.0025],PARAMETER[\"Transformation_reference_epoch\",2014.81],OPERATIONACCURACY[OPERATIONACCURACY[0.1]],AUTHORITY[\"EPSG\",9225]]"}'''

# Units given as a scale and an offset (USO)
FOOT = '{"type":"USO","name":"foot","symbol":"ft","scaleOffset":{"scale":0.3048,"offset":0.0},"baseMeasurement":{"ancestry":"Length"}}'
METRE = '{"type":"USO","name":"metre","symbol":"m","scaleOffset":{"scale":1.0,"offset":0.0},"baseMeasurement":{"ancestry":"Length"}}'
```

## The six kinds of payload

The `type` member says what a payload states. It is read as a
{class}`~geodetic_engine.persistablereference.Kind`:

| `type` | Kind | Parsed as | Build with |
|---|---|---|---|
| `LBC` | Late-bound CRS: a CRS on its own | {class}`~geodetic_engine.persistablereference.CrsReference` | `to_crs()` |
| `EBC` | Early-bound CRS: a CRS bound to a hub by one transformation | {class}`~geodetic_engine.persistablereference.CrsReference` | `to_crs()` gives a {class}`pyproj.crs.BoundCRS` |
| `ST` | Single transformation | {class}`~geodetic_engine.persistablereference.OperationReference` | `to_operation()` |
| `CT` | Concatenated transformation | {class}`~geodetic_engine.persistablereference.OperationReference` | `to_operation()` |
| `USO` | Unit by scale and offset | {class}`~geodetic_engine.persistablereference.UnitReference` | `to_si()`, `from_si()`, `convert_to()` |
| `UAD` | Unit by the ABCD formula $y = (A + Bx)/(C + Dx)$ | {class}`~geodetic_engine.persistablereference.UnitReference` | same |

## Parsing

{func}`~geodetic_engine.persistablereference.parse_persistable_reference`
reads a payload and returns the matching reference type:

```{code-cell} python
from geodetic_engine.persistablereference import parse_persistable_reference

for payload in (ED50_UTM32N_VIA_1612, WGS84, ED50_TO_WGS84_1612, CHOS_MALAL_TO_WGS84, FOOT):
    ref = parse_persistable_reference(payload)
    print(f"{type(ref).__name__:19} {ref.kind.value:4} {str(ref.authority_code):16} {ref.name}")
```

Every reference keeps the original payload (`raw`), its `name`, `version` (the
producer's `ver`, such as `PE_10_9_1`) and `authority_code`, an
{class}`~geodetic_engine.persistablereference.AuthorityCode` or None.

### Encoded and embedded payloads

Payloads are often URL-encoded, and URL encoding is decoded automatically:

```{code-cell} python
import json
from urllib.parse import quote

parse_persistable_reference(quote(WGS84)).name
```

A payload is also often stored as a string inside another JSON document, such
as an OSDU record's `persistableReference` field. Take the string out of the
document first. The package does not search inside other documents, and a
payload that is itself JSON-encoded a second time is refused as malformed:

```{code-cell} python
record = json.loads(json.dumps({"kind": "osdu:wks:...", "persistableReference": WGS84}))
parse_persistable_reference(record["persistableReference"]).name
```

### Recognising a payload cheaply

{func}`~geodetic_engine.persistablereference.looks_like_reference` decides
from the shape of a string whether it is a payload, without parsing it. It is
cheap enough to run on every CRS argument, which is how
{func}`~geodetic_engine.geodesy.transform` accepts payloads and codes in the
same argument:

```{code-cell} python
from geodetic_engine.persistablereference import looks_like_reference

for value in (WGS84, quote(WGS84), "EPSG:4326", 'GEOGCS["WGS 84",...]'):
    print(looks_like_reference(value), "|", value[:40])
```

## CRS references

An early-bound payload builds a {class}`pyproj.crs.BoundCRS` that carries the
transformation:

```{code-cell} python
ref = parse_persistable_reference(ED50_UTM32N_VIA_1612)
print("bound:", ref.is_bound)
print("late-bound part:", ref.late_bound.name, ref.late_bound.authority_code)
print("bound operation:", ref.operation.name, ref.operation.authority_code)

crs = ref.to_crs()
print(type(crs).__name__, "|", crs.source_crs.name, "->", crs.target_crs.name, "via", crs.coordinate_operation.name)
```

The package's own CRS type can be built from a payload directly:

```{code-cell} python
from geodetic_engine.geodesy import CoordinateReferenceSystem

crs = CoordinateReferenceSystem.from_persistable_reference(ED50_UTM32N_VIA_1612)
crs.name, crs.value_axis_abbreviations, crs.axis_units
```

## Transforming with payloads

Payloads can go wherever a CRS is accepted. Here both ends are payloads, so
nothing is looked up by code. The bound source CRS names its own datum shift,
so no operation is passed. Input is ED50 / UTM 32N (E, N in metres). Output is
WGS 84 / UTM 32N:

```{code-cell} python
from geodetic_engine.geodesy import transform

result = transform(ED50_UTM32N_VIA_1612, WGS84_UTM32N, (500000.0, 6650000.0))
print(result.coordinates)
print(result.operation.route, "|", result.operation.name)
```

The same CRS named by its plain EPSG code has no binding, so the datum change
is ambiguous and refused:

```{code-cell} python
:tags: [raises-exception]

transform("EPSG:23032", WGS84_UTM32N, (500000.0, 6650000.0))
```

(persistable-reference-stated-operation)=

### A stated operation

An `ST` or `CT` payload, a bare ESRI `GEOGTRAN` string, or a parsed
{class}`~geodetic_engine.persistablereference.OperationReference` can be the
`operation=`. It is applied **exactly as stated**: its parameters are used,
not the parameters PROJ's database has under the same code.

```{code-cell} python
result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation=ED50_TO_WGS84_1612)
print(result.coordinates)
print(result.operation.route, "|", result.operation.name)

# The bare GEOGTRAN inside the payload works the same way.
geogtran_wkt = json.loads(ED50_TO_WGS84_1612)["wkt"]
print(transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation=geogtran_wkt).coordinates)
```

A stated operation is applied alone and cannot be mixed with other named
operations in a list.

## Operation references

```{code-cell} python
chain = parse_persistable_reference(CHOS_MALAL_TO_WGS84)
print("concatenated:", chain.is_concatenated)
print("methods     :", chain.method_names)

operation = chain.to_operation()   # a pyproj CoordinateOperation
print(operation.name, "|", operation.type_name)
for step in operation.operations:
    print("  ", step.name, step.method_name)
```

{func}`~geodetic_engine.persistablereference.operation_from_geogtran` builds a
{class}`pyproj.crs.CoordinateOperation` from a bare `GEOGTRAN` string with no
envelope. PROJ cannot read `GEOGTRAN`, so this package parses it itself.

## Units

A unit payload converts values to and from SI, or directly to another unit of
the same quantity:

```{code-cell} python
foot = parse_persistable_reference(FOOT)
metre = parse_persistable_reference(METRE)

print(foot.symbol, foot.measurement, "scale:", foot.scale, "offset:", foot.offset)
print("1000 ft in m:", foot.to_si(1000.0))
print("100 m in ft :", foot.from_si(100.0))
print("1000 ft -> m:", foot.convert_to(metre, 1000.0))
```

Converting between units that measure different things, such as length and
temperature, raises
{class}`~geodetic_engine.persistablereference.UnsupportedReferenceError`.

## Writing payloads

{func}`~geodetic_engine.persistablereference.to_persistable_reference` writes a
CRS (bound or not) or a transformation as a payload. PROJ writes ESRI WKT for
CRSs only. For a bound CRS it silently drops the transformation. So the
`GEOGTRAN` is assembled by this package from PROJ's definition, with ESRI's
method names and each parameter in ESRI's units:

```{code-cell} python
from pyproj import CRS
from pyproj.crs import CoordinateOperation

from geodetic_engine.persistablereference import AuthorityCode, to_persistable_reference

payload = to_persistable_reference(CoordinateOperation.from_epsg(1612))
print(payload[:260], "...")
```

No authority code or version is written unless you supply one. A code is a
claim about a register, and the package will not make it for you:

```{code-cell} python
payload = to_persistable_reference(
    CRS.from_epsg(23032), authority=AuthorityCode("EPSG", "23032")
)
print(json.loads(payload)["authCode"], json.loads(payload)["type"])
```

Anything written can be read back to the same definition:

```{code-cell} python
round_trip = parse_persistable_reference(to_persistable_reference(CRS.from_epsg(23032)))
round_trip.to_crs().equals(CRS.from_epsg(23032), ignore_axis_order=True)
```

{func}`~geodetic_engine.persistablereference.geogtran` builds just the
`GEOGTRAN` element as a tree, and
{func}`~geodetic_engine.persistablereference.esriwkt.write` serialises it:

```{code-cell} python
from geodetic_engine.persistablereference import geogtran
from geodetic_engine.persistablereference.esriwkt import write

print(write(geogtran(CoordinateOperation.from_epsg(1612)))[:200], "...")
```

## Supported and refused methods

A payload is translated exactly, or refused. Several ESRI methods differ from
a supported one only by a convention, or by a parameter ESRI does not state.
Translating such a near miss would give coordinates that look right and are
metres out. The tables below are generated from the package's own method
tables at build time.

```{code-cell} python
:tags: [hide-input]

import pandas as pd

from geodetic_engine.persistablereference import methods

supported = pd.DataFrame(
    [
        {"ESRI method": name, "EPSG code": m.code, "EPSG method": m.name, "parameters": ", ".join(m.parameters)}
        for name, m in methods.METHODS.items()
    ]
)
supported.style.hide(axis="index")
```

Grid methods (`NADCON`, `NTv2`, `HARN`) are also supported. The grid file's
method and real filename are taken from PROJ's `grid_alternatives` table. The
reversible polynomial of degree 4 (EPSG method 9651) is also supported: PROJ
has no implementation, so this package evaluates it with PROJ's `horner`
operation.

```{code-cell} python
:tags: [hide-input]

pd.DataFrame(
    [{"refused ESRI method": name, "why": why} for name, why in methods.REFUSED.items()]
).style.hide(axis="index")
```

A refused method raises
{class}`~geodetic_engine.persistablereference.UnsupportedMethodError` with the
reason:

```{code-cell} python
:tags: [raises-exception]

parse_persistable_reference(WGS84_TO_ETRS89_TIME_SPECIFIC).to_operation()
```

## Errors

| Exception | Raised when |
|---|---|
| {class}`~geodetic_engine.persistablereference.MalformedReferenceError` | The payload is not valid JSON, states no type and nothing that implies one, or has WKT PROJ will not read |
| {class}`~geodetic_engine.persistablereference.UnsupportedReferenceError` | A well-formed payload states something this package cannot represent, such as a chain whose steps do not join |
| {class}`~geodetic_engine.persistablereference.UnsupportedMethodError` | The method or a parameter has no exact equivalent (table above) |
| {class}`~geodetic_engine.persistablereference.UnresolvableGridError` | A grid-based transformation names a grid PROJ's database has no mapping for |

All derive from
{class}`~geodetic_engine.persistablereference.PersistableReferenceError`.
When a payload is passed to {func}`~geodetic_engine.geodesy.transform` as a
CRS, these are reported as
{class}`~geodetic_engine.geodesy.UnresolvableCRSError`.
