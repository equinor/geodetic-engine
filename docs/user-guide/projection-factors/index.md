# Projection factors

{func}`~geodetic_engine.geodesy.projection_factors` calculates how a map
projection distorts directions, distances, shapes, and areas at a given
location. It reports:

- {term}`grid convergence`, the angle between grid north and true north;
- {term}`point scale factor`, the local scale applied to a short distance;
- meridional and parallel scale factors;
- areal scale; and
- angular distortion.

The factors are evaluated by PROJ for the map projection associated with the
CRS. The function determines which component of the CRS contains the
projection, interprets input coordinates in the units and axis order expected
by the CRS, and reports the sign convention used for angular values.

## When to use it

Use projection factors when converting directions or distances between the
ground and the projected grid. Typical uses include:

- converting an azimuth referenced to true north, such as one obtained from a
  gyro or astronomical observation, to a grid azimuth;
- converting a grid azimuth to an azimuth referenced to true north;
- reducing a ground distance to the grid; or
- converting a grid distance back to an approximate ground distance.

For example, {doc}`/user-guide/welltrajectory/index` uses projection factors at
the wellhead to convert survey azimuths between grid north and true north.



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
| Grid convergence $\gamma$ | The angle from true north to grid north, in degrees, positive clockwise. For a conformal projection, $\alpha_{grid} = \alpha_{true} - \gamma$. The azimuth helpers refuse projections that distort angles. |
| Point scale factor $k$ | Grid distance divided by ellipsoidal distance along the parallel. For a conformal projection it is the same in every direction, and it is the number surveying calls *the* scale factor. |
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
5. **Invalid coordinates and unevaluable factors are errors**,
   {class}`~geodetic_engine.geodesy.TransformationFailedError`, rather than
   numbers. Horizontal inputs and factors must be finite. Geographic
   longitudes must be within one full turn and latitudes within a quarter
   turn, in their own angular unit. Projected factors at a pole are undefined.

## Pages in this section

```{toctree}
:maxdepth: 1

computing
across-a-zone
azimuths-and-distances
theory
```
