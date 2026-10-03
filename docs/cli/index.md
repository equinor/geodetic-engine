# Command-line tools

Installing the package puts two commands on your `PATH`. Both build the same
artefact -- an enriched copy of PROJ's `proj.db` -- from different sources, and
share the same writer, schema and validator.

| Command | Source of definitions | Needs |
|---|---|---|
| [`geodetic-projdb`](geodetic-projdb.md) | A Georepository instance, over its API | `geodetic-projdb.toml` and OAuth2 credentials in `.env` |
| [`geodetic-osdudb`](geodetic-osdudb.md) | An OSDU `CRS_CT.json` catalogue file | The file; `geodetic-osdudb.toml` is optional |

Both commands take the same four subcommands:

`build`
: Build, validate and atomically publish the database.

`validate`
: Check an existing database: SQLite integrity, and that PROJ can construct
  every object of the named authorities.

`inspect`
: Summarise what a database holds and which builds wrote it.

`config`
: Print the resolved settings and where each came from. Credentials are
  reported as present or missing, never printed.

Settings are read, in increasing precedence, from the TOML file, then from
environment variables prefixed `GEODETIC_ENGINE_`, then from command-line
options. The guides explain what each setting does:
{doc}`/user-guide/projdb` and {doc}`/user-guide/osdudb`.

```{toctree}
:hidden:

geodetic-projdb
geodetic-osdudb
```
