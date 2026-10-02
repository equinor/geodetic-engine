---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Conversions and transformations

```{code-cell} python
:tags: [remove-cell]

%xmode Minimal
```

Every example on this page runs during the documentation build, with PROJ
{{ proj_version }} and the `proj-data` grids installed in the devcontainer
image.

- A **conversion** stays on one datum, such as a map projection. There is one
  answer, so no operation needs naming.
- A **transformation** changes datum. Several are usually published, so you
  must say which one ({doc}`/getting-started/concepts`).

## Conversions

### Map projection

WGS 84 geographic (lon, lat in degrees) to WGS 84 / UTM zone 32N (E, N in
metres). Same datum, so no operation is named. The result records the
conversion PROJ chose, `EPSG:16032` UTM zone 32N:

```{code-cell} python
from geodetic_engine.geodesy import transform

result = transform("EPSG:4326", "EPSG:32632", (10.7522, 59.9139))
print(result.coordinates)
print(result.operation.authority_code, result.operation.name, "/", result.operation.route)
```

### Inverse projection, and projection to projection

A projected CRS can be the source, and two projections on the same datum
convert directly, through the geographic CRS they share:

```{code-cell} python
# ETRS89 / UTM 32N -> ETRS89 geographic (lon, lat)
print(transform("EPSG:25832", "EPSG:4258", (597979.90, 6643118.99)).coordinates)

# ETRS89 / UTM 32N -> ETRS89 / UTM 33N: same datum, different zone
print(transform("EPSG:25832", "EPSG:25833", (597979.90, 6643118.99)).coordinates)
```

### Geographic to geocentric

A 3D geographic CRS (lon, lat, ellipsoidal height) to Earth-centred X, Y, Z in
metres, on the same datum:

```{code-cell} python
transform("EPSG:4979", "EPSG:4978", (10.7522, 59.9139, 100.0)).coordinates
```

### Unit change

`EPSG:2278` is NAD83 / Texas South Central in US survey feet. `EPSG:32139` is
the same projection in metres. Only the units differ:

```{code-cell} python
feet = transform("EPSG:32139", "EPSG:2278", (900000.0, 4200000.0))
print(feet.coordinates, feet.target_units)
```

## Transformations

### Named operation

ED50 to WGS 84 in Norwegian waters north of 62°N, using `EPSG:1612`, a
seven-parameter Helmert with a stated accuracy of 1 m. Input and output are
(lon, lat, h), in degrees and metres:

```{code-cell} python
result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5, 100.0), operation="EPSG:1612")
print(result.coordinates)
print(result.operation.name, "|", result.operation.method_name, "|", result.operation.accuracy, "m")
```

An operation can be named as `"EPSG:1612"`, `1612`, an OGC URN, or an
{class}`~geodetic_engine.geodesy.OperationCandidate` returned by
{func}`~geodetic_engine.geodesy.available_operations`
({doc}`choosing-operations`).

### A datum change and a projection in one call

Named operations are wrapped in whatever conversions the two CRSs need. Here
ED50 geographic goes to WGS 84 / UTM zone 32N. The result reports the datum
shift, not the projection, because the shift is what you asked for and what
determines the accuracy:

```{code-cell} python
result = transform("EPSG:4230", "EPSG:32632", (11.12789451, 63.58496782), operation="EPSG:1612")
print(result.coordinates)
print(result.operation.authority_code, result.operation.route)
print(result.operation.steps)
```

### Concatenated operations, and naming each step

`EPSG:8047` (ED50 to WGS 84 (15)) is published as two steps: `EPSG:1147` (ED50
to ED87) then `EPSG:1146` (ED87 to WGS 84). Naming the concatenated code, or
naming both steps, gives the same result:

```{code-cell} python
point = (4.12789451, 63.58496782, 100.0)

by_code = transform("EPSG:4230", "EPSG:4326", point, operation="EPSG:8047")
by_steps = transform("EPSG:4230", "EPSG:4326", point, operation=["EPSG:1147", "EPSG:1146"])

print(by_code.coordinates)
print(by_steps.coordinates)
print(by_code.operation.authority_code, "vs", by_steps.operation.steps)
```

The steps are a **set**, not a sequence. Each is checked separately against
the pipeline PROJ built, so their order does not matter:

```{code-cell} python
reordered = transform("EPSG:4230", "EPSG:4326", point, operation=["EPSG:1146", "EPSG:1147"])
reordered.coordinates == by_steps.coordinates
```

### Grid-based horizontal transformation

NAD27 to NAD83 over the conterminous US, using `EPSG:1241` (NADCON). PROJ reads
the grid `us_noaa_conus.tif`. The result names every grid it read, and whether
each was installed:

```{code-cell} python
result = transform("EPSG:4267", "EPSG:4269", (-95.0, 30.0), operation="EPSG:1241")
print(result.coordinates)
for grid in result.grids:
    print(grid.name, "available" if grid.available else "MISSING", grid.full_name)
```

The newer NADCON5 transformation `EPSG:8555` reads a different grid, and
differs by a few centimetres:

```{code-cell} python
nadcon5 = transform("EPSG:4267", "EPSG:4269", (-95.0, 30.0), operation="EPSG:8555")
print(nadcon5.coordinates, [g.name for g in nadcon5.grids])
```

If a grid is not installed, the transformation is refused and the missing file
is named ({ref}`missing-grid-error`).

### Vertical: ellipsoidal height to a geoid height

WGS 84 3D (lon, lat, ellipsoidal height in metres) to EGM2008 height, using
`EPSG:3858` and the 2.5′ geoid grid. The target is a vertical CRS, so each
point has one output value, the height:

```{code-cell} python
result = transform("EPSG:4979", "EPSG:3855", (-144.0, 72.0, 548.4082), operation="EPSG:3858")
print(result.coordinates, result.target_axes, result.target_units)
print([g.name for g in result.grids])
```

### Compound target: position and height together

`EPSG:6172` is ETRS89 / UTM zone 32N + NN54 height. From ETRS89 3D
(`EPSG:4937`), only the vertical part needs a transformation, `EPSG:9484`,
which reads the Norwegian grid `no_kv_href2008a.tif`. Output is (E, N, H) in
metres:

```{code-cell} python
result = transform(
    "EPSG:4937", "EPSG:6172", (11.12789451, 63.58496782, 100.0), operation="EPSG:9484"
)
print(result.coordinates, result.target_axes)
```

From WGS 84 3D (`EPSG:4979`) there is also a horizontal datum change, so **both**
operations must be named. PROJ merges the horizontal and vertical steps into
one unidentified operation, so naming only one would leave the other chosen by
PROJ:

```{code-cell} python
result = transform(
    "EPSG:4979", "EPSG:6172", (11.12789451, 63.58496782, 100.0),
    operation=["EPSG:11028", "EPSG:9484"],
)
print(result.coordinates)
print(result.operation.name)
```

Naming just the vertical one is refused:

```{code-cell} python
:tags: [raises-exception]

transform("EPSG:4979", "EPSG:6172", (11.12789451, 63.58496782, 100.0), operation="EPSG:9484")
```

(time-dependent-transformations)=

### Time-dependent: a coordinate epoch

`EPSG:6277` (ITRF2005 to GDA94) is a 14-parameter Helmert with rates of
change, so its result depends on when the coordinates were observed. Pass the
coordinate epoch as a decimal year. Input and output are geocentric X, Y, Z in
metres:

```{code-cell} python
from geodetic_engine.geodesy import Transformation

tfm = Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277")
print("requires epoch:", tfm.requires_epoch)

point = [(-2593197.524, 5656917.6189, -1394397.8828)]
for epoch in (1994.0, 2024.0):
    print(epoch, tfm.transform(point, coordinate_epoch=epoch).coordinates)
```

Over 30 years the result moves by about 2 m, which is the Australian plate's
motion relative to ITRF. Leaving the epoch out is refused
({ref}`missing-epoch-error`).

A dynamic CRS alone does not require an epoch. WGS 72 to WGS 84 (`EPSG:1238`)
is a static Helmert and gives the same answer at every epoch:

```{code-cell} python
Transformation("EPSG:4322", "EPSG:4326", operation="EPSG:1238").requires_epoch
```

### Engineering CRS

Engineering CRSs are local plant or site grids. `EPSG:5817` is the Tombak LNG
plant grid, with axes x and y in metres. `EPSG:15747` is a similarity
transformation to Nakhl-e Ghanem / UTM zone 39N:

```{code-cell} python
result = transform("EPSG:5817", "EPSG:3307", (20000.0, 10000.0), operation="EPSG:15747")
print(result.coordinates, result.source_axes, "->", result.target_axes)
print(result.operation.method_name)

back = transform("EPSG:3307", "EPSG:5817", result.coordinates[0], operation="EPSG:15747")
print(back.coordinates)
```

A plant grid's axes need not be east and north. `EPSG:5800`, the Astra Minas
grid, declares X north and Y west, so its values are given in that declared
order -- there is no easting to put first -- while the projected result is
`xy` as always. PROJ on its own gets this pair wrong (see {doc}`/workarounds`);
the point below lands where it should, at Comodoro Rivadavia:

```{code-cell} python
result = transform("EPSG:5800", "EPSG:22192", (10000.0, 20000.0), operation="EPSG:1035")
print(result.coordinates, result.source_axes, "->", result.target_crs.value_axis_abbreviations)
print(transform("EPSG:5800", "EPSG:4221", (10000.0, 20000.0), operation="EPSG:1035").coordinates)
```

### Bound CRS: the CRS names its own transformation

A {term}`bound CRS` carries its transformation to a hub, so no operation needs
naming. It can be built with pyproj, parsed from an OSDU payload
({doc}`/user-guide/persistable-reference`), or looked up by code in a
{doc}`custom database </user-guide/projdb>`:

```{code-cell} python
from pyproj import CRS
from pyproj.crs import CoordinateOperation
from pyproj.crs.crs import BoundCRS

ed50_utm31_via_1612 = BoundCRS(
    source_crs=CRS.from_epsg(23031),               # ED50 / UTM zone 31N
    target_crs=CRS.from_epsg(4326),                # hub: WGS 84
    transformation=CoordinateOperation.from_epsg(1612),
)
result = transform(ed50_utm31_via_1612, "EPSG:4326", (500000.0, 7000000.0))
print(result.coordinates)
print(result.operation.authority_code, result.operation.route)
```

The route is `bound`: the operation came from the CRS definition, not from a
search. {doc}`/background/bound-crs` covers bound CRSs over concatenated
operations and why looking them up by code needs a workaround.

### A stated operation: OSDU payloads and ESRI GEOGTRAN

Instead of a code, the operation can be given in full as an OSDU
`persistableReference` payload, an ESRI `GEOGTRAN` WKT string, or a parsed
{class}`~geodetic_engine.persistablereference.OperationReference`. It is then
applied exactly as written, parameters and all, not looked up in PROJ's
database. See {ref}`persistable-reference-stated-operation`.

## Choosing between `transform` and `Transformation`

{func}`~geodetic_engine.geodesy.transform` caches its resolved transformations,
so repeating a call with the same CRSs and operation does not resolve again.
{class}`~geodetic_engine.geodesy.Transformation` makes this explicit. It
resolves when constructed, so a bad operation, missing grid or ambiguous datum
change is raised before any coordinates are passed. The object can then be
reused:

```{code-cell} python
tfm = Transformation("EPSG:4230", "EPSG:4326", operation="EPSG:1612")
print(tfm)
print(tfm.source_crs, "->", tfm.target_crs, "| grids:", tfm.grids, "| epoch:", tfm.requires_epoch)

for batch in ([(2.5, 63.5)], [(3.0, 64.0), (4.0, 65.0)]):
    print(tfm.transform(batch).coordinates)
```

Inverting a transformation means swapping source and target and naming the
same operation. PROJ applies it in reverse.
