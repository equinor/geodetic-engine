---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Choosing an operation

When a datum changes you must name the operation. You choose it with
{func}`~geodetic_engine.geodesy.available_operations`. It lists every operation
PROJ offers between two CRSs, including ones this package would refuse to run,
and describes each so you can decide.

## Listing candidates

```{code-cell} python
from geodetic_engine.geodesy import available_operations

candidates = available_operations("EPSG:4230", "EPSG:4326")  # ED50 -> WGS 84
print(len(candidates), "candidates")
for c in candidates[:8]:
    print(f"{c.authority_code or '(none)':14} {c.accuracy!s:>5} m  usable={c.usable!s:5}  {c.name}")
```

They are in PROJ's ranking order. Each is an
{class}`~geodetic_engine.geodesy.OperationCandidate`:

| Field | Meaning |
|---|---|
| `authority_code` | `"EPSG:1612"`, or None for an operation PROJ assembled with no code of its own |
| `name`, `method_name` | As published |
| `accuracy` | Stated accuracy in metres, or None; a ballpark always has None |
| `area_of_use` | Where the operation is valid, as an {class}`~geodetic_engine.geodesy.AreaOfUse` |
| `usable` | Whether it could be applied here: not a ballpark, and every grid installed |
| `ballpark` | Whether it is a ballpark approximation |
| `grids` | {class}`~geodetic_engine.geodesy.GridUsage` for each grid it reads |
| `requires_epoch` | Whether it reads a coordinate epoch |
| `steps`, `is_chained`, `references` | The individual operations, for a chain |

## Filtering

`available_operations` filters with the same keywords pyproj's
`TransformerGroup` uses:

```{code-cell} python
precise = available_operations(
    "EPSG:4230", "EPSG:4326",
    authority="EPSG",        # only EPSG's operations; "any" (default) searches all
    accuracy=1.0,            # stated accuracy of 1 m or better
    allow_ballpark=False,    # drop the ballpark fallback
    allow_superseded=False,  # drop operations EPSG has superseded
)
for c in precise:
    print(f"{c.authority_code:10} {c.accuracy} m  {c.area_of_use.name}")
```

PROJ never offers deprecated EPSG operations, so there is no
`allow_deprecated` option.

## Picking by area of use

A candidate is only valid inside its area of use. This picks the most accurate
usable candidate whose area contains the point, then transforms with it. The
point is in the Norwegian Sea, and the target is WGS 84 / UTM zone 31N:

```{code-cell} python
from geodetic_engine.geodesy import Transformation

lon, lat = 4.12789451, 63.58496782  # offshore Norway, north of 62°N


def covers(area, lon, lat):
    if area is None:
        return False
    west, south, east, north = area.bounds
    if east < west:  # crosses the antimeridian
        return (lon >= west or lon <= east) and south <= lat <= north
    return west <= lon <= east and south <= lat <= north


candidates = [
    c for c in available_operations("EPSG:4230", "EPSG:32631")
    if c.usable and c.accuracy is not None and covers(c.area_of_use, lon, lat)
]
best = min(candidates, key=lambda c: c.accuracy)
print("chosen:", best.name, f"({best.accuracy} m)")
print("steps :", best.references)
print("area  :", best.area_of_use.name)

result = Transformation("EPSG:4230", "EPSG:32631", operation=best).transform((lon, lat))
print(result.coordinates)
```

The bounding box is coarse. It is the rectangle around the area, so a point
inside the box can still be outside the area described in words. Read
`area_of_use.name` before trusting the box.

This package does not check that your points are inside the operation's area
of use. PROJ applies an operation anywhere, and a Helmert fitted to the North
Sea gives plausible numbers in Australia. Checking the area is your job, and
`area_of_use` is there for it.

## Passing a candidate as the operation

Towards a projected CRS, PROJ adds the projection to each datum shift, so the
candidates have no code of their own. That is why `best` above had none.
Passing the candidate object, not its code, is how such a candidate is named.
It expands to its `references`, and each is checked independently against the
pipeline:

```{code-cell} python
for c in available_operations("EPSG:4230", "EPSG:32631")[:4]:
    print(f"{c.authority_code!s:6} {c.name:45} -> {c.references}")
```

## Ballpark candidates are listed but never applied

```{code-cell} python
ballpark = [c for c in available_operations("EPSG:4230", "EPSG:4326") if c.ballpark]
for c in ballpark:
    print(c.name, "| accuracy:", c.accuracy, "| usable:", c.usable)
```

Between the Puerto Rico datum and GDA94, a ballpark is PROJ's only option. No
result can be produced for that pair:

```{code-cell} python
[(c.name, c.ballpark) for c in available_operations("EPSG:4139", "EPSG:4283")]
```

## Exporting a candidate

A candidate can be exported as WKT2 or PROJJSON for review or storage:

```{code-cell} python
candidate = available_operations("EPSG:4230", "EPSG:4326", authority="EPSG")[0]
print(candidate.to_wkt()[:400], "...")
```

{meth}`~geodetic_engine.geodesy.OperationCandidate.to_wkt` returns None when
WKT2 cannot faithfully describe the pipeline, for example when a step is
applied inverted.
