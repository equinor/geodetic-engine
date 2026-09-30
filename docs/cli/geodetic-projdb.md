# `geodetic-projdb`

Builds an enriched `proj.db` from a Georepository instance. See
{doc}`/user-guide/projdb` for the workflow and what each option is for.

```{eval-rst}
.. argparse::
   :module: geodetic_engine.projdb.__main__
   :func: _parser
   :prog: geodetic-projdb
```

## Example configuration

`geodetic-projdb.example.toml`, from the repository root. Copy it to
`geodetic-projdb.toml` and edit; the file is picked up from the working
directory without a flag. Credentials never go in this file -- the loader
rejects them -- see {ref}`projdb-secrets`.

```{literalinclude} ../../geodetic-projdb.example.toml
:language: toml
```
