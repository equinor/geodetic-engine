# Projection factors

{func}`~geodetic_engine.geodesy.projection_factors` reports how a map
projection distorts at a point: how far grid north is turned from true north,
the {term}`grid convergence`, and how much a short distance is stretched, the
{term}`point scale factor`, with the meridional scale, the areal scale and the
angular distortion beside them. PROJ evaluates them. This function decides
which part of the CRS they describe, reads the points in the CRS's own units,
and returns the sign convention with the numbers.

**Use it when** a measurement made on the ground has to be put on the grid, or
the reverse: an azimuth measured against true north, from a gyro or an
astronomic observation, turned onto grid north, or a ground distance scaled
onto the grid. {doc}`/user-guide/welltrajectory/index` uses it to turn a
survey's grid azimuths onto true north at the wellhead.

**It is not a transformation.** The factors describe one CRS's map projection,
so nothing changes datum and no operation has to be named. To move coordinates
into another CRS, see {doc}`/user-guide/geodesy/transformations`.

## The API in one table

| You want to | Use |
|---|---|
| Evaluate the factors at one point or many | {func}`~geodetic_engine.geodesy.projection_factors` |
| Read the factors, the points and the CRS they describe | {class}`~geodetic_engine.geodesy.ProjectionFactors` |
| Turn grid azimuths onto true north | {meth}`ProjectionFactors.to_true_azimuth <geodetic_engine.geodesy.ProjectionFactors.to_true_azimuth>` |
| Turn true azimuths onto grid north | {meth}`ProjectionFactors.to_grid_azimuth <geodetic_engine.geodesy.ProjectionFactors.to_grid_azimuth>` |
| Get plain data with the sign convention stated, for JSON | {meth}`ProjectionFactors.to_json_dict <geodetic_engine.geodesy.ProjectionFactors.to_json_dict>` |

## Conventions

| Quantity | Convention |
|---|---|
| Grid convergence $\gamma$ | The angle from true north to grid north, in degrees, positive clockwise: positive where grid north lies east of true north. So $A_{grid} = A_{true} - \gamma$. |
| Point scale factor $k$ | The scale along the parallel: grid distance $= k \times$ ellipsoidal distance. For a conformal projection it is the same in every direction, and it is the number surveying calls *the* scale factor. |
| Meridional scale $h$ | The scale along the meridian. Equal to $k$ for a conformal projection. |
| Areal scale | Area on the grid over area on the ellipsoid. 1 for an equal-area projection. |
| Angular distortion | The largest amount by which the projection changes an angle, in degrees. 0 for a conformal projection. |
| Points | `xy` order in the CRS's own units, or longitude and latitude with `geographic=True`. A third value per point, such as a height, is ignored. |
| Datum | Always the CRS's own. A projected point is unprojected onto its own base CRS, which is a conversion, not a transformation. |

## Rules this function follows

1. **The part of the CRS that holds the projection is the one described.** A
   {term}`bound CRS` is read through its base CRS and a compound CRS through its
   horizontal part, because neither a binding transformation nor a height
   changes the map projection.
2. **A geographic CRS has no grid.** Its convergence is 0 and every scale is 1,
   reported with `projected` set to False, so a caller needs no special case.
3. **A CRS with no horizontal position is refused.** Geocentric, engineering
   and vertical CRSs raise
   {class}`~geodetic_engine.geodesy.UnsupportedCRSError`.
4. **Longitude is read as PROJ reads it**: from the CRS's own prime meridian
   and in its own unit, so a CRS such as NTF (Paris), in grads from Paris, gets
   the right factors.
5. **A point PROJ cannot evaluate is an error**,
   {class}`~geodetic_engine.geodesy.TransformationFailedError`, rather than a
   number.

## Pages in this section

```{toctree}
:maxdepth: 1

computing
across-a-zone
azimuths-and-distances
theory
```
