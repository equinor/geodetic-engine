# Custom PROJ database

PROJ resolves every code, `EPSG:23031` as much as `YourAuthority:1234`, by
looking it up in its database, `proj.db`. The official database carries the
EPSG dataset and a few other public registries. It does not carry the CRSs and
transformations an organisation defines for itself: those in a Georepository
register under the organisation's own authority, or the bound CRSs in an OSDU
catalogue. Naming one of them fails, because PROJ has never heard of the code.

A **custom database** is a copy of the official `proj.db` with those
definitions added. Their codes then resolve exactly like EPSG codes, in this
package and in anything else built on PROJ that reads the same file.

## Do you need one?

| Your situation | What to use |
|---|---|
| Only EPSG codes | Nothing: the official `proj.db` already has them |
| Records carry a full OSDU `persistableReference` | Nothing: {doc}`persistable-reference` transforms with the payload directly |
| CRSs and transformations in a Georepository register | {doc}`projdb` |
| Data that names CRSs by OSDU code, such as `OSDU:23032023` | {doc}`osdudb` |
| An OSDU catalogue newer than the EPSG dataset PROJ ships | {doc}`osdudb`, with `EPSG` among the authorities |
| Both a register and a catalogue | Both, into one file: {doc}`combining` |

## What a build guarantees

Both sources go through the same writer and validator, so they produce the
same kind of file and can share one:

- The official `proj.db` is **copied, never modified**.
- Only the authorities you configure are written. A row owned by any other
  authority **aborts the build**, so EPSG stays authoritative for EPSG.
- Objects the official database already defines are not imported again.
- Every imported CRS and operation must be **constructible by PROJ** from the
  finished database, or the build fails.
- The build is staged and validated before it replaces the output, so a failed
  build leaves the previous database in place.
- A report beside the output lists what was imported, what was skipped and
  why, and every grid the new operations read.

## Bound CRSs

A {term}`bound CRS` is a CRS packaged with the one transformation to use with
it, almost always to WGS 84. It names its operation, so this package accepts it
where a plain datum change would be refused as ambiguous. OSDU's catalogue
consists mostly of bound CRSs, and they are the main reason to build a database
from it.

`proj.db` has no table for them, so a build and this package between them
handle four things:

Storage
: A bound CRS is written as an ordinary geodetic or projected CRS row whose
  definition holds the whole `BOUNDCRS` WKT. The WKT is assembled with pyproj
  from the source's own definitions of the base CRS and the transformation, so
  what is embedded is checked, not copied verbatim.

Lookup by code
: PROJ drops the `BOUNDCRS` wrapper when it builds a CRS from a code, so the
  CRS it returns no longer says which transformation it is bound to. This
  package reads the stored definition back and rebuilds the bound CRS, so a
  bound CRS named by code still names its operation.

Concatenated transformations
: PROJ cannot embed a chain of operations in a bound CRS. A chain of Helmert
  steps is collapsed into one equivalent Helmert, and the collapse is checked
  against PROJ's own evaluation of the chain to within a millimetre. A chain
  that does not collapse, for example one that reads a grid, is **not
  approximated**: the bound CRS is skipped and listed in the report.

Scale units
: The embedded transformation has no units, and scale is read as parts per
  million. Every operation is restated in ppm first, so a scale EPSG gives in
  parts per billion is not misread by a factor of 1000.

{doc}`/background/bound-crs` explains each of these in detail.

## Grids

A database holds operations that **name** grid files, not the grids
themselves. A grid transformation is imported whether or not its grid is
installed on the machine that builds the database. The report lists every grid
the new operations read, with whether it was found. Distribute those grids
alongside the database, or transformations using them are refused with
{class}`~geodetic_engine.geodesy.MissingGridError`. When an authority names a
grid by a filename PROJ does not map to the installed file,
`scripts/build-projdb.sh` patches the mapping ({ref}`grid-alternatives`).

## Using the database

Put the directory holding the custom `proj.db` **first** on `PROJ_DATA`,
followed by the installed PROJ directory so grids and `proj.ini` are still
found. Every result then records fingerprints of the database that answered
({doc}`/background/provenance`), so a coordinate can be traced back to the
build that defined it. {doc}`combining` has the details.

## Pages in this section

```{toctree}
:maxdepth: 1

From Georepository <projdb>
From OSDU <osdudb>
Combining sources and using the result <combining>
```
