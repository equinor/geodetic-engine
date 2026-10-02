# Getting started

`geodetic-engine` is a Python library for transforming coordinates between
coordinate reference systems (CRSs) under explicit, verifiable rules. It is
built on [PROJ](https://proj.org) and
[pyproj](https://pyproj4.github.io/pyproj/stable/): PROJ performs the numerical
computation, while `geodetic-engine` decides which coordinate operation is
applied, checks the result before returning it, and records how it was
produced.

## What the package does

`geodetic-engine` is a layer over pyproj. It:

- applies the coordinate operation the caller names, and raises an error if
  that operation cannot be applied as named;
- refuses a {term}`ballpark` result, a missing {term}`grid`, and a
  time-dependent operation without a {term}`coordinate epoch`;
- records the applied operation, its accuracy, the grids, the epoch, the PROJ
  {term}`pipeline` and a fingerprint of the `proj.db` used, on every result;
- reads and writes OSDU `persistableReference` payloads;
- builds custom `proj.db` files from a Georepository register or an OSDU
  catalogue.

## Why a wrapper around PROJ

PROJ is designed to return a result for any pair of CRSs. That suits
visualisation. It does not suit work where the operation, its accuracy and the
data behind it must be stated. The gaps this package addresses are:

- **Operation selection.** Through pyproj, PROJ chooses an operation by its own
  ranking, which can differ between machines and releases. It falls back to a
  ballpark or to the next candidate when a grid is missing, and evaluates a
  time-dependent operation at its reference epoch when no epoch is given.
- **Organisation-specific definitions.** CRSs and transformations defined in an
  organisation's own register, or in an OSDU catalogue, are not in PROJ's
  `proj.db`, so PROJ cannot resolve their codes. A custom `proj.db` adds them to
  a copy of PROJ's database.
- **Bound CRSs.** A {term}`bound CRS` carries the transformation to use, and OSDU
  catalogues consist mostly of them. PROJ drops that binding when it builds a CRS
  from a code, and cannot embed a chain of operations in one. The package
  restores the binding and collapses chains of Helmert steps where that is exact.
- **Provenance.** PROJ does not always report which operation was applied in a robust way. The package
  records it.

{doc}`/background/guarantees` gives the full comparison, and
{doc}`/background/bound-crs` the handling of bound CRSs.

## A first look

```python
from geodetic_engine.geodesy import transform

# ED50 to WGS 84 in Norwegian waters, with a named EPSG transformation.
result = transform("EPSG:4230", "EPSG:4326", (2.5, 63.5), operation="EPSG:1612")

result.coordinates     # ((2.49818..., 63.49961...),)  longitude, latitude
result.operation.name  # 'ED50 to WGS 84 (23)'
result.pipeline        # the exact PROJ pipeline that ran
```

Leave out `operation=` and the call raises an error instead, because several
ED50 to WGS 84 transformations exist and they disagree by metres. The
{doc}`quickstart` runs this and more during every documentation build.

## What you need

- Python 3.13 or later.
- PROJ {{ proj_version }} built from source, and pyproj {{ pyproj_version }}
  built against it. The EPSG dataset inside PROJ is part of every answer, so
  the versions are pinned. The devcontainer installs both.

## Read these pages in order

1. {doc}`installation`: why the PROJ version is pinned, and how to get an
   environment that gives the same answers as CI.
2. {doc}`quickstart`: transform a point, name a datum change, and read how the
   result was produced. Every output on the page is real.
3. {doc}`concepts`: CRS, datum, operation, axis order, epoch, grid, bound CRS
   and ballpark, in the precise sense the rest of the documentation uses them.

## Where to go next

| I want to... | Go to |
|---|---|
| Transform coordinates and choose an operation | {doc}`/user-guide/geodesy/index` |
| Understand why a transformation was refused | {doc}`/user-guide/geodesy/errors` |
| Read or write an OSDU `persistableReference` | {doc}`/user-guide/persistable-reference` |
| Add my organisation's CRSs and transformations to PROJ | {doc}`/user-guide/custom-database` |
| See worked examples | {doc}`/examples/index` |
| Know what the package does differently from plain pyproj | {doc}`/background/guarantees` |
| Look up a term | {doc}`/glossary` |

```{toctree}
:hidden:

installation
quickstart
concepts
```
