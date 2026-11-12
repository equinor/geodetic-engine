---
file_format: mystnb
kernelspec:
  name: python3
  display_name: Python 3
---

# Grid files

{func}`~geodetic_engine.catalog.grids` lists the grid files the operations in
the active database read, and those PROJ's `grid_alternatives` table knows that
no operation reads. Each says whether it is installed on this machine:

```{code-cell} python
from geodetic_engine import catalog

catalog.grids(area_name="Norway").to_dataframe()[
    ["name", "kind", "format", "installed", "used_by"]
]
```

## One entry per file PROJ reads

Authorities name grids their own way, and PROJ maps each name to the file it
reads. An entry is that file, with the names operations use for it:

```{code-cell} python
conus = catalog.grids(operation="EPSG:15851").one()
print(conus.name)
print(conus.original_names)
print(conus.kind, conus.format, conus.proj_method)
print(conus.url)
```

NADCON publishes a grid as two files, one of latitude and one of longitude
shifts, where PROJ reads one file holding both. Both names are listed under
that file, as above. A grid PROJ has no file of its own for is listed under
the name the database states.

`used_by` lists the operations reading a grid, and `operation` turns that
round:

```{code-cell} python
len(conus.used_by), "operations read", conus.name
```

## Installed, or missing

`installed` is whether PROJ finds the file on this machine: under `name`, or
under `old_proj_name`, the name of PROJ's older release of the grid, which
PROJ falls back to. It is looked for in the directories on PROJ's search path
and in PROJ's user data directory. A file under one of `original_names` does
not count: PROJ asks for its own file, and cannot read NADCON's `conus.las`
and `conus.los` at all. It is checked each time a listing is read, so a grid
installed since shows at once:

```{code-cell} python
from collections import Counter

Counter(grid.installed for grid in catalog.grids(used=True))
```

```{code-cell} python
missing_vertical = catalog.grids(kind="vertical", installed=False, area_name="Norway")
[(g.name, g.url) for g in missing_vertical]
```

```{note}
{meth}`OperationEntry.grids_available <geodetic_engine.catalog.OperationEntry.grids_available>`
applies the same test to every file an operation reads, with one difference:
with PROJ networking enabled, a grid PROJ can download counts as available
there while it is not `installed` here.
```

## On the content delivery network

`check_remote=True` asks PROJ's content delivery network which grids it
publishes, and fills in `remote_available`. It is the only listing that uses
the network, and reads the list once per process:

```python
for grid in catalog.grids(kind="vertical", installed=False, check_remote=True):
    print(grid.name, grid.remote_available)
```

`remote_available=True` keeps only the grids it publishes, and implies
`check_remote`. A network failure raises
{class}`~geodetic_engine.catalog.GridIndexUnavailableError`.

## Kinds

{class}`~geodetic_engine.catalog.GridKind` says what a grid corrects, from how
PROJ applies it or, where PROJ does not say, from the method of an operation
reading it:

```{code-cell} python
Counter(grid.kind.value for grid in catalog.grids())
```
