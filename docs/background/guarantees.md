# Design guarantees

**A wrong coordinate that looks right is worse than no coordinate.** A loud
failure gets investigated. A plausible wrong number gets stored, passed
downstream and built on, and nobody checks it again. Every rule below follows
from this.

## What plain PROJ does, and what this package does instead

PROJ is built to compute. Given two CRSs, it builds the best transformation it
can find and returns numbers. That is right for many uses, and wrong where
someone later has to defend the result.

| Situation | PROJ via pyproj | `geodetic-engine` |
|---|---|---|
| Datum change, no operation named | Picks one by its own ranking, possibly different on another machine or PROJ version | {class}`~geodetic_engine.geodesy.AmbiguousOperationError`: name it |
| Named operation cannot be applied to this pair | May build a working transformer with a different operation | {class}`~geodetic_engine.geodesy.OperationNotAvailableError`: the built pipeline is checked to contain what was asked for |
| No real transformation exists | Ballpark: treats the datums as identical, no accuracy stated | {class}`~geodetic_engine.geodesy.BallparkTransformationError`, or the ambiguity error first |
| Best operation's grid not installed | Falls back to the next candidate, usually less accurate | {class}`~geodetic_engine.geodesy.MissingGridError` naming the file |
| Time-dependent operation, no epoch | Runs, evaluating the operation as if at its reference epoch | {class}`~geodetic_engine.geodesy.MissingCoordinateEpochError` |
| Projected metres given to a geographic CRS | "Invalid latitude", with no CRS, unit or order named | {class}`~geodetic_engine.geodesy.CoordinateOutOfRangeError` naming all three |
| Axis order | Declared order unless `always_xy`, which has gaps at vertical and engineering ends | Always `xy`; the gaps are worked around ({doc}`axis-order`) |
| What ran | You reconstruct it yourself | Recorded on every result ({doc}`provenance`) |

## Why "name the operation" and not "pick the most accurate"

Picking the candidate with the best stated accuracy sounds safe, but it is not.
The most accurate operation is usually the most local, and can be wrong outside
its area. Two candidates with the same stated accuracy can differ by metres.
And PROJ's ranking changes between releases. The choice depends on where the
data came from and what it will be used for, which the library cannot know. It
belongs to the caller, who can use
{func}`~geodetic_engine.geodesy.available_operations` to make it with full
information.

A {term}`bound CRS` counts as naming the operation: whoever defined the CRS
already chose it.

## Compatibility keyword

`allow_any_operation=True` used to allow automatic selection. It is still
accepted so existing calls do not break, but it no longer bypasses any rule.

## What is *not* checked

- **Area of use.** An operation is applied anywhere PROJ can compute it. Check
  your points against the operation's
  {attr}`~geodetic_engine.geodesy.OperationCandidate.area_of_use` yourself
  ({doc}`/user-guide/geodesy/choosing-operations`).
- **Projection validity far from the central meridian.** A Transverse Mercator
  evaluated far outside its zone returns numbers, not an error.
- **Plausibility of your inputs** beyond the latitude range.
