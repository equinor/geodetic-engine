# Bound CRSs

A {term}`bound CRS` is a CRS packaged with the single transformation that ties
it to a hub, almost always WGS 84. In WKT2 it is a `BOUNDCRS` with a
`SOURCECRS`, a `TARGETCRS` and an `ABRIDGEDTRANSFORMATION`.

It is **early binding** made explicit: the operation is part of the CRS
definition, not chosen when a transformation is requested. That is why this
package accepts a bound CRS as naming the operation rather than escaping the
rule. Whoever defined the CRS named the operation, and PROJ is left with
exactly one candidate. OSDU's CRS catalogue consists mostly of bound CRSs.

## How a bound CRS is transformed

The embedded operation is read out and resolved through the same transformer
group as a named one. The bound CRS is not allowed to build a transformer by
itself. Most bound CRSs in a real register have a **projected** base, such as
`ED50 / UTM zone 32N` bound to WGS 84, not just `ED50`. The transformation then
has to unproject, apply the datum shift and reproject. Going through the group
supplies those steps and keeps the applied operation identifiable. Letting the
bound CRS resolve itself would report the map projection as the operation and
lose the datum shift's EPSG code.

## How a bound CRS is stored in `proj.db`

`proj.db` has no bound CRS table. PROJ stores a bound CRS as an ordinary
`geodetic_crs` or `projected_crs` row whose `text_definition` holds the whole
`BOUNDCRS` WKT. The coordinate system and datum columns are NULL, as that
table's CHECK constraints require. The WKT is assembled with pyproj from the
register's own WKT export of the base CRS, the transformation and the hub, so
what is embedded is a definition this package has checked, not one rebuilt from
parts.

## Looking a bound CRS up by code

PROJ discards the `BOUNDCRS` wrapper when it builds a CRS from an authority
code. It still honours the binding when selecting an operation: `EPSG:4230`
offers dozens of candidates to WGS 84, where a CRS bound to one of them offers
exactly one. But the object it returns reports `is_bound` as false, carries no
transformation, and cannot say what it is bound to. A caller naming such a CRS
by code would then be refused for an ambiguous datum change, even though the
CRS settles the question.

So the stored definition is read back out of `proj.db` and the bound CRS is
rebuilt from it. This is the only place the package reads the database
directly. Only `BOUNDCRS` definitions are read. The scan happens once per PROJ
data directory. An ordinary CRS is never affected. This is a workaround:
{doc}`/workarounds`.

## Bound CRSs over a concatenated transformation

PROJ cannot embed a chain of operations in a bound CRS. A register that defines
one over a concatenated transformation, such as `EPSG:8047` (ED50 to WGS 84
(15), two Helmert steps through ED87), would therefore be unusable as written.

The chain is first **collapsed into a single equivalent step**. Two Helmerts
compose exactly, because each is an affine map on geocentric coordinates:

$$
X_2 = T_2 + (1 + s_2) R_2 \left[ T_1 + (1 + s_1) R_1 X_0 \right]
$$

so the composition is again a Helmert, with

$$
T = T_2 + (1 + s_2) R_2 T_1, \qquad R = R_2 R_1, \qquad 1 + s = (1 + s_1)(1 + s_2)
$$

The intermediate geographic-to-geocentric conversions cancel, because the
frame between the two steps is one CRS with one ellipsoid.

The algebra is only a proposal. EPSG's rotation matrix is linearised for small
angles, so $R_2 R_1$ is not exactly a linearised matrix again, and different
operations give rotations in different units (`EPSG:1147` uses microradians,
not arc-seconds). Every collapse is therefore **checked against PROJ's own
evaluation of the original chain** over the operation's area of use, and
refused if any point moves by more than a millimetre. Across the EPSG dataset
shipped with PROJ 9.9.0, 43 of the 286 concatenated operations (including
deprecated ones; 20 of the 216 current ones) are chains of plain Helmerts and
collapse, with a worst observed residual of 0.22 mm, against operations whose
stated accuracy is in metres.

A chain that is not equivalent to one Helmert is **not approximated**. That is
the case if a step reads a grid, or is a Molodensky-Badekas, time-dependent or
full-matrix variant. The bound CRS is skipped, logged as an error, and listed in
the build report's `skipped` section with the reason.
{doc}`/user-guide/geodesy/helmert` shows the functions involved.

## Scale units in an abridged transformation

An `ABRIDGEDTRANSFORMATION` has no units: translations are metres, rotations
arc-seconds, and the scale difference is ppm. PROJ converts to these when
exporting a `BoundCRS`, but it does not convert a scale given in parts per
billion, which EPSG uses for most recent ITRF and ETRF realisations. So
`0.33` ppb is written as the literal `0.33`, read back as ppm, and positions
end up kilometres out. Every operation is restated in ppm before it is
embedded ({func}`~geodetic_engine.geodesy.utils.scale_in_parts_per_million`).
This is a workaround: {doc}`/workarounds`.
