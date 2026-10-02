# Combining sources and using the result

## One database from both sources

The two builders write the same schema through the same writer, so they can
share a file. `--append` opens the database already at `--output` instead of
starting from a fresh copy of the official `proj.db`. A second source then adds
its authority to what the first wrote:

```bash
# Georepository first: no output exists yet, so this starts from the base proj.db.
uv run geodetic-projdb build --output build/proj.db

# Then OSDU into the same file, adding to it rather than replacing it.
uv run geodetic-osdudb build CRS_CT.json --output build/proj.db --append
```

`scripts/build-projdb.sh` runs both in the right order:

```bash
# Fresh database from both sources.
scripts/build-projdb.sh --catalog CRS_CT.json

# One source only, somewhere else.
scripts/build-projdb.sh --source georepository --output /tmp/proj.db

# Add a source to a database an earlier run already built.
scripts/build-projdb.sh --source osdu --catalog CRS_CT.json --append
```

The script stages the whole chain, including the grid filename patches below,
and replaces the output only after every step succeeds. Without `--append` it
starts from the base database. With `--append` it stages the existing output. A
dry run validates the same chain without publishing. The script never modifies
PROJ's installed data directory: grids in `local/grids/` are read in place from
`PROJ_DATA`. Before each real build it checks that `local/grids/` is on
`PROJ_DATA` and warns if it holds grids PROJ will not find; `--dry-run` skips
the check. Run it with `--help` for all options.

Points worth knowing:

- **Appending to a path that does not exist is not an error.** The first build
  in a chain has nothing to append to, so it copies the base. That makes it
  safe to pass `--append` to every build in a script.
- **Objects the first build wrote are visible to the second.** The check for
  existing keys reads the output database, so a datum or unit the first source
  imported is reused, not re-imported or collided with.
- **A failed append leaves the earlier build intact.** Staging is discarded,
  even when validation fails after its transaction commits. The published file
  is never modified in place.
- **Each build keeps its own report and log.** An appending build writes
  `<output>.projdb.report.json` or `<output>.osdudb.report.json` beside the
  database, rather than overwriting `<output>.report.json`. The provenance of
  every source that contributed is kept.
- **Operation selection accumulates.** In `custom_first` mode, an authority
  preference rule that already names an earlier authority is extended, not
  replaced. Adding OSDU does not hide the Georepository operations.
- **A build will not silently discard another source's import.** Every
  database records which authorities each build contributed, in
  `geodetic_engine_build_history`. A build that does not append, and whose
  authorities do not cover what the existing output holds, is refused with
  `OutputWouldBeDiscarded` before anything is fetched. Pass `--append` to add
  to it, `--output` to write elsewhere, or `--replace` to discard it on
  purpose. Rebuilding a database from the same authorities that wrote it is
  unaffected. A file this package did not build is not protected, because
  nothing is known about it.

(grid-alternatives)=

## Patching grid filename mappings

An authority's coordinate operation names a grid by the authority's own
filename. PROJ's `grid_alternatives` table maps that name to the file PROJ's
tools and CDN actually ship. Occasionally that mapping is missing upstream,
even though the operation and the grid file are both fine. PROJ then reports
the grid as missing, which looks the same as a grid that really is not
available.

`scripts/patch-grid-alternatives.sh` adds the mappings known to be missing.
`scripts/build-projdb.sh` runs it on the staged database as its last step. Pass
`--skip-grid-patch` to leave it out, and the output is exactly the official
database plus this package's authority data. It can also be run on its own:

```bash
scripts/patch-grid-alternatives.sh --db build/proj.db
```

Each patch is idempotent and scoped to one authority's grid name. A name that
is already present, because a newer PROJ shipped the fix or the script already
ran, is left untouched. A backup is taken before patching and removed only once
every patch applies cleanly. Every entry records why it is still needed, and is
removed once PROJ ships the mapping. {doc}`/workarounds` lists the current
entries.

## Overwriting rather than colliding

Importers normally reuse existing objects, and duplicate rows reaching the
writer are rejected. `--overwrite-rows` explicitly updates eligible objects from
the configured authorities, including dependent axes and steps. It is useful
when re-importing a register whose definitions were corrected upstream:

```bash
uv run geodetic-projdb build --output build/proj.db --append --overwrite-rows
```

Objects in the configured base database cannot be replaced, even if their
authority is configured for import. The authority guard also prevents changes
to other authorities' objects. Use a fresh official base to update objects from
an earlier enriched output.

## Using the result

The enriched database replaces the official one. Point PROJ at the directory
that contains it, and keep the installed PROJ directory on the search path so
grids and `proj.ini` are still found. The directory holding `proj.db` must come
first: pyproj reads the database from the first entry only. Grid-only
directories, such as the devcontainer's `local/grids`, go after:

```bash
export PROJ_DATA="/path/to/build:/usr/local/share/proj"
```

```python
import os

from pyproj import datadir

datadir.set_data_dir(os.pathsep.join(["/path/to/build", datadir.get_data_dir()]))
```

With two databases on the search path a further build can no longer tell which
one is the official base, and refuses to guess; name it with
`GEODETIC_ENGINE_BASE_PROJ_DB=/usr/local/share/proj/proj.db` or `base_proj_db`
in the configuration file.

Your authority's codes then resolve like EPSG's:

```python
from geodetic_engine.geodesy import transform

transform("YourAuthority:1234", "EPSG:4326", (500000.0, 6650000.0))
```

Things to know:

- When several databases are on PROJ's search path, later builds need an
  explicit `base_proj_db`. The builder will not guess which one is the base.
- Distribute the grids the custom operations read along with the database.
  The build report lists them.
- Applications that change PROJ's search path while running must serialise
  that change against their own pyproj calls. Results record which database
  answered ({doc}`/background/provenance`), and caches are keyed by database,
  so a switch is never answered from a stale cache.
- New output databases are private by default (`0600` on POSIX). Rebuilds and
  appends keep an existing file's permission bits. Ownership and ACLs are not
  copied to the replacement file.
