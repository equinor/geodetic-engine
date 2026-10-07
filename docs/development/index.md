# Development

## Environment

Use the devcontainer ({doc}`/getting-started/installation`). CI builds and
tests in the same image, so a local run gives the same answers as CI.

```bash
uv sync --extra dev --extra docs
uv run pytest tests/test_environment.py   # check the environment first
```

## Tests and checks

```bash
# The usual run: everything except the exhaustive dataset sweep (about 1,900 tests).
uv run pytest

# Everything, including the sweep of tests/testdataset (about 15,000 tests),
# on all logical cores. Arguments are passed through to pytest.
scripts/run_all_tests.sh

# What CI runs besides the tests.
uv run ruff check geodetic_engine tests
uv run ruff format --check geodetic_engine tests
uv run mypy geodetic_engine
```

`pyproject.toml` deselects the `dataset` marker by default. The sweep is worth
running before a release, or after changing operation selection. It takes
minutes, not seconds, which is why it is opt-in. Tests marked `network` need a
reachable Georepository instance and credentials.

The bin grid property tests (`tests/bingrid/test_properties.py`, Hypothesis)
run a fixed set of examples by default, so that every machine gets the same
run. To draw fresh examples, as CI does in a separate step:

```bash
uv run pytest tests/bingrid/test_properties.py --hypothesis-profile=geodetic-engine-random
```

CI runs these as two separate GitHub workflows, so a red check says which kind
of problem it is:

- **Lint and type check** (`.github/workflows/lint.yml`): `ruff` and `mypy`.
- **Tests** (`.github/workflows/tests.yml`): the unit and integration tests,
  the bin grid property tests with fresh Hypothesis examples, then the dataset
  sweep, with a coverage floor of 80 % across the unit tests and the sweep.

## Building the documentation

The documentation is built with [Sphinx](https://www.sphinx-doc.org/). Pages
are written in [MyST Markdown](https://myst-parser.readthedocs.io/), a Markdown
dialect with Sphinx's cross-referencing, so there is no reStructuredText to
learn.

```bash
uv run --extra docs sphinx-build -W --keep-going -b html docs docs/_build/html
```

Or build and serve it on <http://127.0.0.1:8000> in one step
(`--help` lists the options, including `--live` for rebuild-on-save):

```bash
scripts/build-docs.sh
```

Open `docs/_build/html/index.html` in a browser. For a live preview that
rebuilds on save:

```bash
uv run --extra docs sphinx-autobuild docs docs/_build/html
```

`-W` turns every warning into an error, and cross-references are checked
strictly (`nitpicky = True`). A broken link to a class or a page fails the
build, just as a failing test would. Run the docstring examples with:

```bash
uv run pytest --doctest-modules geodetic_engine -o addopts=""
```

If `sphinx-build` fails at startup with `unsupported locale setting`, set
`LC_ALL=C.UTF-8`. The devcontainer sets it for you.

### How the site is put together

| Path | What it is |
|---|---|
| `docs/conf.py` | Sphinx configuration: the [Furo](https://pradyunsg.me/furo/) theme, extensions, cross-reference checking |
| `docs/_static/custom.css`, `logo.svg`, `favicon.svg` | Site styling on top of Furo, and the logo |
| `docs/_ext/geodetic_docs.py` | Local extension: generates the API pages from `__all__`, copies the notebooks, resolves references to private module paths |
| `docs/_templates/autosummary/` | Layout of each generated API page |
| `docs/getting-started/`, `docs/user-guide/`, `docs/background/` | Hand-written pages |
| `docs/api/` | **Generated** on every build from each subpackage's `__all__`; gitignored |
| `docs/examples/notebooks/` | **Copied** from `examples/` on every build; gitignored |
| `docs/_static/switcher.json` | Entries of the version switcher, reserved for when a `stable` build exists |

Pages with a `file_format: mystnb` header are notebooks written as Markdown.
Their `{code-cell}` blocks run during the build, and the output is included in
the page. A cell expected to raise is tagged `raises-exception`. Any other
error fails the build. Results are cached in `docs/_build/.jupyter_cache`, so
only changed pages are re-run.

### Adding to the API reference

Nothing to do. Export the name from its subpackage's `__all__` and it gets a
page. A type that public functions return or accept but that is not exported
can be listed in `SUPPORTING` in `docs/_ext/geodetic_docs.py`.

### Adding a page

Create a `.md` file in the relevant section and add it to that section's
`toctree`. Use an executed page (`file_format: mystnb`) for anything that shows
coordinates, so the numbers cannot go stale.

## Docstring style

Docstrings use [Google style](https://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html),
rendered by `sphinx.ext.napoleon`:

```python
def transform(source_crs, target_crs, x, y=None, z=None, *, operation=None):
    """Transform points between two CRSs in one call.

    Longer description: when to use it, what it checks, what it refuses.

    Args:
        source_crs: CRS the input coordinates are in.
        operation: EPSG coordinate operation to apply, for example
            ``"EPSG:15670"``.

    Returns:
        The transformed coordinates and their provenance.

    Raises:
        AmbiguousOperationError: If no operation was given and the
            transformation involves an unbound datum change.

    Example:
        >>> result = transform("EPSG:4258", "EPSG:25832", (10.75, 59.91),
        ...                    operation="EPSG:16032")
        >>> result.operation.authority_code
        'EPSG:16032'
    """
```

- Every public name gets a one-line summary, then a description.
- Functions and methods list `Args`, `Returns`, and `Raises` with the
  package's own exceptions.
- Dataclasses list their fields under `Attributes`.
- Cross-reference with Sphinx roles: ``:class:`Transformation` ``,
  ``:func:`~geodetic_engine.geodesy.transform` ``. Refer to private helpers in
  double backticks, not with a role, because they have no page to link to.
- An `Example` is a doctest. It must run offline against the pinned PROJ. Mark
  a line `# doctest: +SKIP` only if it needs a network, credentials or a custom
  database. Use `...` for float tails; `ELLIPSIS` is enabled.

## Publishing the documentation

`.github/workflows/docs.yml` builds the site in the devcontainer image, like
the lint and test workflows:

- **Pull requests**: build with warnings as errors, run the doctests, run the
  link check (reported, not blocking), and upload the HTML as an artifact to
  download and review.
- **Push to `main`**: the same, then deploy to GitHub Pages under `/dev/`.

The site root redirects to `/dev/`. When the first release is tagged, add a
`stable/` build and an entry per release to `docs/_static/switcher.json`. The
URLs are already versioned, so no page moves.

One-time repository setting: **Settings → Pages → Build and deployment →
Source: GitHub Actions**.
