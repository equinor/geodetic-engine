# `geodetic-osdudb`

Builds an enriched `proj.db` from an OSDU coordinate reference catalogue. No
credentials and no network are involved. See {doc}`/user-guide/osdudb` for the
workflow.

```{eval-rst}
.. argparse::
   :module: geodetic_engine.osdudb.__main__
   :func: _parser
   :prog: geodetic-osdudb
```

## Example configuration

`geodetic-osdudb.example.toml`, from the repository root. The file is optional:
the catalogue path alone is enough to build.

```{literalinclude} ../../geodetic-osdudb.example.toml
:language: toml
```
