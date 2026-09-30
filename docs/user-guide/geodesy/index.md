# Geodesy

`geodetic_engine.geodesy` transforms coordinates. It is a layer over
pyproj's `Transformer`: PROJ does the computation, and this module checks the
result can be trusted before returning it.

**Use it when** you need coordinates in another CRS and have to be able to say
which operation produced them, with what accuracy, from which database.

**Use plain pyproj instead when** you want PROJ to pick an operation for you and
an unstated accuracy is acceptable, for example drawing a map. This module
refuses to do that. See {doc}`/background/guarantees`.

## The API in one table

| You want to | Use |
|---|---|
| Transform points once | {func}`~geodetic_engine.geodesy.transform` |
| Transform many batches with one operation | {class}`~geodetic_engine.geodesy.Transformation` |
| See every operation PROJ offers between two CRSs | {func}`~geodetic_engine.geodesy.available_operations` |
| Inspect a CRS's axes, units, dimension, dynamism | {class}`~geodetic_engine.geodesy.CoordinateReferenceSystem` |
| Read coordinates and provenance from a result | {class}`~geodetic_engine.geodesy.TransformationResult` |
| Compose or collapse Helmert transformations | {mod}`geodetic_engine.geodesy.utils` |

## Rules this module enforces

1. **A datum change must name its operation**, as an EPSG code, a candidate from
   `available_operations`, a stated OSDU payload, or through a {term}`bound CRS`.
   Otherwise: {class}`~geodetic_engine.geodesy.AmbiguousOperationError`.
2. **The operation you name is the operation applied.** If PROJ builds something
   else: {class}`~geodetic_engine.geodesy.OperationNotAvailableError`.
3. **No ballpark results**:
   {class}`~geodetic_engine.geodesy.BallparkTransformationError`.
4. **No silent fallback when a grid is missing**:
   {class}`~geodetic_engine.geodesy.MissingGridError`.
5. **A time-dependent operation needs a coordinate epoch**:
   {class}`~geodetic_engine.geodesy.MissingCoordinateEpochError`.
6. **Values are `xy` in and out**: longitude before latitude, easting before
   northing, then height, whatever axis order the CRS declares.

## Pages in this section

```{toctree}
:maxdepth: 1

crs
transformations
choosing-operations
input-and-output
results
errors
helmert
```
