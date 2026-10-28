# Runbook: upgrading PROJ, proj-data and pyproj

For maintainers of this repository. It is deliberately not part of the
documentation site.

Every answer this package gives depends on the EPSG dataset inside PROJ's
`proj.db`, so PROJ, its grid package proj-data, and pyproj are pinned to exact
releases. Moving a pin is a deliberate change, made in one place, checked by
CI at every step, and released only after the matching pyproj wheels are
published. Follow the steps in order; the checklist at the end repeats them.

## How the pins are wired

The releases are stated once, in [.devcontainer/pins.env](../.devcontainer/pins.env):

| Key | What it is |
|---|---|
| `PROJ_VERSION` | The PROJ release, for example `9.9.0` |
| `PROJ_SHA256` | SHA-256 of `proj-<PROJ_VERSION>.tar.gz` |
| `PROJ_DATA_VERSION` | The proj-data release; must equal the `PROJ_DATA.VERSION` the new PROJ's `proj.db` records |
| `PROJ_DATA_SHA256` | SHA-256 of `proj-data-<PROJ_DATA_VERSION>.tar.gz` |
| `PYPROJ_VERSION` | The pyproj release built against that PROJ |
| `PYPROJ_WHEEL_INDEX` | Where those pyproj wheels are served |

The file holds `KEY=VALUE` lines only, with no quotes, spaces or shell syntax.
It is parsed strictly, never sourced, so an unknown, repeated, missing or
malformed entry is an error everywhere it is read.

What reads it:

| Reader | How |
|---|---|
| `.devcontainer/install-proj.sh` and the Dockerfile | Read the PROJ and proj-data versions and checksums. An environment variable still overrides each, and `--print-pins` shows what would be built. |
| `geodetic_engine` at import | Reads `geodetic_engine/_pins.env`, the copy the wheel ships, or `.devcontainer/pins.env` in a checkout. `geodetic_engine/_pins.py` parses it. |
| `.github/workflows/pyproj-wheels.yml` | `python3 scripts/sync_pins.py --get KEY` |
| The Windows job in `.github/workflows/tests.yml` | Same, for the pyproj requirement and index. |
| `scripts/build_bundled_osdudb.py`, `scripts/check_package_dist.py`, `scripts/check_installed_wheel.py` | The checkout's pins, or the pins inside the built wheel. |
| The tests | Through `geodetic_engine._environment`, or by comparing with the base `proj.db`. |

What cannot read it is rewritten by `python scripts/sync_pins.py`, which knows
exactly where each copy is (`COPIES` in the script):

- the `pyproj==` dependency in `pyproject.toml`, followed by `uv lock`;
- the install commands in `README.md`, `docs/getting-started/installation.md`,
  `docs/user-guide/welltrajectory/plotting.md`,
  `geodetic_engine/welltrajectory/README.md` and
  `examples/welltrajectory_examples.ipynb`;
- the PROJ and pyproj releases named in `README.md`, and the source-build
  command in `docs/getting-started/installation.md`.

GitHub, PyPI, pip and uv read these files as written, which is why they hold
copies. History, such as earlier `CHANGELOG.md` entries, is never touched.

What stops a mistake:

| Guard | Fails when |
|---|---|
| `python scripts/sync_pins.py --check`, in `lint.yml` and in `tests/test_pins.py` | A copy is stale, or a spot in `COPIES` can no longer be found |
| `tests/test_pins.py` | Any build input states a version of its own; `install-proj.sh` does not read the pins |
| `tests/test_environment.py` | pyproj is not the pinned release, links or was compiled against another PROJ, or proj-data does not match the `proj.db` |
| `import geodetic_engine` | pyproj links another PROJ release than the pinned one |
| `install-proj.sh` and `pyproj-wheels.yml` | A downloaded archive does not match its pinned SHA-256 |
| `uv sync --locked` in CI | `uv.lock` was not updated after a pyproj change |
| `scripts/check_installed_wheel.py`, in the release | The shipped OSDU database was not built from the pinned pyproj wheel's own `proj.db`, or OSDU lookups fail in a clean install |

## 0. Decide what moves

- **A PROJ patch release** (9.9.0 to 9.9.1) usually keeps the EPSG dataset,
  the database layout and proj-data. It is mostly mechanical.
- **A PROJ minor or major release** (9.9 to 9.10 or 10.0) brings a new EPSG
  dataset, often a new proj-data, sometimes a new database layout. Expect
  changed test expectations and workarounds to re-check. Plan time for it.
- **A pyproj release** can break the patches applied to pyproj's wheel build
  (step 3) and its private API this package reads.

Move one at a time where possible: PROJ first, pyproj in a separate change,
so a failure points at one cause.

Check compatibility first. pyproj states the oldest PROJ it builds against in
its `setup.py` (`PROJ_MIN_VERSION`; 9.4.0 for pyproj 3.8.0), and its release
notes say which PROJ it was tested with. Read PROJ's `NEWS.md` for the release,
and EPSG's release notes for the dataset it brings.

## 1. Collect and verify the new values

Never take a checksum from a single source. Download the PROJ archive from both
mirrors `install-proj.sh` uses; the two SHA-256 values must be identical, and
match the MD5 OSGeo publishes beside the archive:

```bash
v=9.9.1   # the new PROJ release
cd "$(mktemp -d)"
curl -fsSLO "https://download.osgeo.org/proj/proj-$v.tar.gz"
curl -fsSL -o github.tar.gz "https://github.com/OSGeo/PROJ/releases/download/$v/proj-$v.tar.gz"
sha256sum "proj-$v.tar.gz" github.tar.gz
curl -fsSL "https://download.osgeo.org/proj/proj-$v.tar.gz.md5"; md5sum "proj-$v.tar.gz"
```

The archive states which EPSG dataset, database layout and proj-data release
the new `proj.db` will record:

```bash
tar -xOzf "proj-$v.tar.gz" "proj-$v/data/sql/metadata.sql" | grep VERSION
```

If `PROJ_DATA.VERSION` differs from the pinned `PROJ_DATA_VERSION`, download
that proj-data release and take its checksum. GitHub's proj-data releases have
no archives attached, so OSGeo is the only source; check the MD5 there too:

```bash
d=1.26   # PROJ_DATA.VERSION from metadata.sql
curl -fsSLO "https://download.osgeo.org/proj/proj-data-$d.tar.gz"
sha256sum "proj-data-$d.tar.gz"
curl -fsSL "https://download.osgeo.org/proj/proj-data-$d.tar.gz.md5"; md5sum "proj-data-$d.tar.gz"
```

For a new pyproj, check that the tag exists at
<https://github.com/pyproj4/pyproj/tags>: `pyproj-wheels.yml` checks pyproj
out at the tag named exactly `PYPROJ_VERSION`.

Note what changes in the database layout and EPSG version; they decide how
much of step 5 to expect.

## 2. Edit the pins, then sync the copies

1. Create a branch.
2. Edit `.devcontainer/pins.env`: the version and its checksum always change
   together.
3. Run:

   ```bash
   python scripts/sync_pins.py           # rewrites the copies; runs uv lock if pyproject.toml changed
   python scripts/sync_pins.py --check   # must print: every copy matches .devcontainer/pins.env
   bash .devcontainer/install-proj.sh --print-pins
   git diff
   ```

4. Review the diff: only the pins file, the copies listed above and, for a
   pyproj change, `uv.lock` should have changed.

Do not edit the copies by hand. If the sync reports that it found something
"0 times, not once", someone reworded one of those files: update `COPIES` in
`scripts/sync_pins.py` to where the value is stated now, then run it again.
If uv is not on your path, run `uv lock` yourself after a pyproj change.

## 3. pyproj only: the wheel build patches

`scripts/patch_pyproj_build.py` patches pyproj's own release tooling before
the wheels are built: the `+proj<PROJ_VERSION>` version label, a PROJ build
without embedded resources, an OpenSSL checksum, the macOS TLS backend, and
`pyproj/datadir.py`, so that a `PROJ_DATA` holding a `proj.db` beats the
bundled database while `set_data_dir()` still beats both. Every patch replaces
an exact text and refuses, naming the file, when that text is not found once.
Check them against the new release before CI does:

```bash
git clone --depth 1 --branch "<PYPROJ_VERSION>" https://github.com/pyproj4/pyproj /tmp/pyproj-src
python scripts/patch_pyproj_build.py /tmp/pyproj-src "<PYPROJ_VERSION>" "<PROJ_VERSION>"
```

- If a patch no longer applies, port it to the new text and keep its intent.
  `tests/test_pyproj_wheel_build.py` states the data directory precedence the
  patch must keep.
- If upstream fixed what a patch works around (the OpenSSL checksum, the
  `--with-darwinssl` flag, the `perl-core` package), remove that patch and its
  test rather than carrying it.
- `geodetic_engine/_bundled_database.py` reads pyproj's private
  `pyproj.datadir._USER_PROJ_DATA` to tell whether the caller chose a data
  directory before the import.
  `tests/test_bundled_database.py::test_pyproj_keeps_the_explicit_data_dir_where_this_package_reads_it`
  fails if pyproj renamed it; adapt `_user_data_dir()` if so.
- Read pyproj's changelog for API changes this package uses, and for changes
  in which Python versions it supports (`CIBW_BUILD` in `pyproj-wheels.yml`).

## 4. Rebuild your environment

The devcontainer builds PROJ and proj-data from the pins when the image is
built. In VS Code run **Dev Containers: Rebuild Container**.

The `.venv` survives the rebuild, and uv reuses a cached pyproj built against
the old PROJ when the pyproj version did not change. Rebuild it explicitly:

```bash
uv cache clean pyproj
uv sync --reinstall-package pyproj --extra dev --extra docs
uv run python -c "import pyproj; print(pyproj.__version__, pyproj.proj_version_str, pyproj.__proj_compiled_version__)"
uv run pytest tests/test_environment.py
```

All three printed versions must be the pinned ones. `test_environment.py` also
checks that the installed proj-data is the release the new `proj.db` names.

On container creation `scripts/install-osdu-projdb.sh` finds that
`build/proj.db` was built for another PROJ release, moves it to
`build/proj.db.stale`, and builds the official OSDU catalogue from scratch.
Anything else imported into it, such as the Georepository register, has to be
built again with `scripts/build-projdb.sh`.

Outside the devcontainer, follow the by-hand steps in
`docs/getting-started/installation.md`; `install-proj.sh` reads the same pins.

## 5. Run everything, and explain every change

```bash
uv run pytest -n logical
scripts/run_all_tests.sh   # including the dataset sweep
uv run pytest tests/bingrid/test_properties.py --hypothesis-profile=geodetic-engine-random
uv run ruff check geodetic_engine tests && uv run ruff format --check geodetic_engine tests
uv run mypy geodetic_engine
python scripts/sync_pins.py --check
uv run --extra docs sphinx-build -W --keep-going -b html docs docs/_build/html
```

A new EPSG dataset changes some answers: operations are deprecated or
restated, parameters and accuracies corrected. For each failing expectation:

- find the cause in EPSG's or PROJ's release notes before changing anything;
- update the expected value only when the cause explains it, and say so in
  the CHANGELOG entry, as the 9.8.1 to 9.9.0 upgrade did for `EPSG:28991`;
- never widen a tolerance or mark a test as expected to fail to get a green
  run.

The build reports beside `build/proj.db` (`*.report.json`) and the output of
`scripts/install-osdu-projdb.sh` show whether the official OSDU catalogue
still builds without new skips.

## 6. Re-check the workarounds and measured prose

- Every entry in `docs/workarounds.md` says when it can be removed. Check each
  against the PROJ release notes; remove a workaround PROJ now makes
  unnecessary, together with its code and tests.
- `scripts/patch-grid-alternatives.sh` adds `grid_alternatives` rows missing
  upstream. For each entry, query the new stock database:

  ```bash
  sqlite3 /usr/local/share/proj/proj.db \
      "SELECT * FROM grid_alternatives WHERE original_grid_name = '<name>';"
  ```

  Remove entries PROJ now ships, and update the "Verified missing against"
  note in the script to the new PROJ and EPSG versions.
- Prose that reports a measurement on a given release, such as the statistics
  in `docs/background/bound-crs.md`, the benchmark in
  `docs/user-guide/geodesy/input-and-output.md` and the `tolerance_rationale`
  in `tests/bingrid/data/osdu_expected_outcomes.json`: measure it again, or
  leave it as stated, since it names the release it was measured on.

## 7. CHANGELOG and package version

Add an entry under **Changed**, in the style of the existing one: the new PROJ,
EPSG and proj-data versions and the old ones, whether the database layout
changed (a database built for another layout must be rebuilt), every changed
expectation with its cause, and every workaround added or removed. Bump the
package version in `pyproject.toml` if a release follows, then run `uv lock`.

## 8. Merge, then publish the pyproj wheels

pyproj wheels are published only from the default branch, so the order is
fixed:

1. Open the pull request. These must pass on it: **Lint and type check**,
   **Tests**, **Python 3.15 tests**, and **pyproj wheels**, which runs on the
   pull request because the pins changed. It builds and smoke-tests every
   wheel against the new PROJ without publishing.
2. **Windows tests** and **Installed wheel (Linux)** fail on the pull request
   at installing `pyproj==<PYPROJ_VERSION>+proj<PROJ_VERSION>`, because no such
   wheel is on the index yet. That is expected; nothing else may be red.
3. Merge.
4. Run **Actions > pyproj wheels > Run workflow** on the default branch with
   **publish** checked. It creates the GitHub release
   `pyproj-<PYPROJ_VERSION>-proj<PROJ_VERSION>` with a `SHA256SUMS` file, then
   starts the documentation workflow, which adds the wheels to the index.
5. When that has finished, check that the index serves the wheel:

   ```bash
   pip download --no-deps --dest "$(mktemp -d)" \
       "$(python scripts/sync_pins.py --get PYPROJ_REQUIREMENT)" \
       --extra-index-url "$(python scripts/sync_pins.py --get PYPROJ_WHEEL_INDEX)"
   ```

6. Re-run **Windows tests** and **Installed wheel (Linux)** on the default
   branch. They must pass before any release.

A published pyproj release is never replaced: pip caches and lock files hold
its hashes, and the workflow refuses to overwrite one. Never delete a pyproj
release that any published geodetic-engine release pins.

## 9. Release

Run **pypi_release** to TestPyPI first. It builds the shipped OSDU database
from the new pyproj wheel's own `proj.db`, then installs the built wheel into a
clean environment and checks that the database carries exactly that `proj.db`'s
metadata (PROJ release, layout, EPSG version) and that OSDU lookups work. To
try the same locally before releasing:

```bash
python scripts/build_bundled_osdudb.py
uv build
python scripts/check_installed_wheel.py dist/*.whl dist/*.tar.gz
```

Install the TestPyPI release with the command from `README.md` in a clean
environment on Linux, macOS and Windows if you can, then release to PyPI.

## Rolling back

Revert the pin commit on the default branch and run
`python scripts/sync_pins.py --check`. Nothing has to be unpublished: the new
pyproj wheels stay on the index unused, and every geodetic-engine release keeps
pinning the wheels it was released with.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ImportError: geodetic-engine requires PROJ X, but pyproj ... uses PROJ Y` | pyproj links another PROJ | Step 4; or install the pinned wheel |
| `test_proj_version_is_pinned` fails on `__proj_compiled_version__` | pyproj was compiled against the old PROJ, from uv's cache | `uv cache clean pyproj && uv sync --reinstall-package pyproj --extra dev --extra docs` |
| `test_proj_data_matches_the_pinned_proj` fails | `PROJ_DATA_VERSION` is not what the new `proj.db` records | Step 1; update version and checksum, rebuild the container |
| `sha256sum: WARNING: 1 computed checksum did NOT match` from `install-proj.sh`, or `has SHA-256 ..., expected ...` from `pyproj-wheels.yml` | A wrong checksum in the pins, or a corrupted download | Recompute from both mirrors (step 1) |
| `error: .../pins.env:N: ...` | The pins file is malformed | Fix the line it names; `KEY=VALUE`, no quotes or spaces |
| `stale copies of .devcontainer/pins.env: ...` | `sync_pins.py` was not run | `python scripts/sync_pins.py` |
| `found ... 0 times, not once; update COPIES` | A copy was reworded or moved | Update `COPIES` in `scripts/sync_pins.py` |
| `uv sync --locked` fails in CI | `uv.lock` predates the pyproj change | `uv lock` |
| `No matching distribution found for pyproj==A+projB` | The wheels are not published, or the index is not refreshed yet | Step 8; check the documentation workflow run |
| `Patch pyproj's build` fails with `expected ... exactly once` | pyproj changed the text a patch replaces | Step 3 |
| `SchemaDriftError` from an OSDU or Georepository build | PROJ changed the `proj.db` schema | Update `geodetic_engine/projdb/schema.py` and the writers |
| `the bundled proj.db's metadata differs from the pinned PROJ's own proj.db` | The shipped database was built on another `proj.db` | `python scripts/build_bundled_osdudb.py` |
| `the pyproj installed here links PROJ X, not the pinned Y` | `check_package_dist.py` was run in the wrong environment | Use `check_installed_wheel.py`, or pass `--reference-proj-db` |
| `test_pyproj_keeps_the_explicit_data_dir_where_this_package_reads_it` fails | pyproj renamed its private data directory global | Adapt `_user_data_dir()` in `geodetic_engine/_bundled_database.py` (step 3) |

## Checklist

- [ ] Decided what moves, and checked pyproj supports the PROJ release
- [ ] New checksums taken from both mirrors and checked against OSGeo's MD5
- [ ] `PROJ_DATA_VERSION` matches `PROJ_DATA.VERSION` in the new `metadata.sql`
- [ ] `.devcontainer/pins.env` edited
- [ ] `python scripts/sync_pins.py` run, `--check` clean, diff reviewed, `uv.lock` updated if pyproj moved
- [ ] pyproj only: the wheel build patches apply to the new release
- [ ] Container rebuilt, pyproj rebuilt from a clean cache, `tests/test_environment.py` green
- [ ] Full suite, dataset sweep, fresh property examples, lint, mypy and docs build green
- [ ] Every changed expectation explained by a release note
- [ ] Workarounds and grid patches re-checked
- [ ] CHANGELOG entry written, package version bumped if releasing
- [ ] Pull request merged with only the two index-dependent jobs red
- [ ] pyproj wheels published, index serving them, Windows and installed-wheel jobs green on the default branch
- [ ] TestPyPI release checked in a clean environment, then PyPI
