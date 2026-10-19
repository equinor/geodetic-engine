"""The devcontainer's OSDU install must never fail container creation.

Each test runs scripts/install-osdu-projdb.sh in an isolated copy of the
repository, against a copy of the stock database, so nothing in the working
tree or PROJ's data directory is touched and nothing is downloaded: --update is
answered by a stand-in for the download command.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from geodetic_engine.geodesy.osdu_id import CRS_ENTITY
from geodetic_engine.osdudb.download import (
    DEFAULT_CATALOG_DESTINATION,
    provenance_path,
)
from tests.osdudb.conftest import (
    AUTHORITY,
    CUSTOM_GEOGRAPHIC_WKT,
    authority_code,
    write_catalog,
)
from tests.osdudb.test_build import geographic, projected, with_id
from tests.support import installed_proj_db

REPOSITORY = Path(__file__).resolve().parents[2]
WARNING = "WARNING: the OSDU catalogue is NOT installed"
GEOGRAPHIC_ID = f"{CRS_ENTITY}:Geographic2D:{AUTHORITY}::4100"
RUNNER = f"uv run --project {REPOSITORY} --no-sync"
FIRST = "1" * 40
SECOND = "2" * 40

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None, reason="sqlite3 CLI is required"
)


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    isolated = tmp_path / "repository"
    for relative in (
        "scripts/install-osdu-projdb.sh",
        "scripts/build-projdb.sh",
        "scripts/patch-grid-alternatives.sh",
        ".devcontainer/link-local-grids.sh",
    ):
        destination = isolated / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPOSITORY / relative, destination)
    return isolated


@pytest.fixture
def stock(tmp_path: Path) -> Path:
    directory = tmp_path / "proj"
    directory.mkdir()
    shutil.copy2(installed_proj_db(), directory / "proj.db")
    return directory / "proj.db"


def _publish(catalog: Path, commit: str, *records: dict[str, Any]) -> Path:
    """A catalogue and the record of where it came from, as download writes them."""
    write_catalog(catalog, *records)
    digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
    provenance_path(catalog).write_text(
        json.dumps({"commit": commit, "sha256": digest}, indent=2)
    )
    return catalog


def _commit(checkout: Path, commit: str, *records: dict[str, Any]) -> Path:
    """The official catalogue, as committed in the repository."""
    return _publish(checkout / DEFAULT_CATALOG_DESTINATION, commit, *records)


def _offline_runner(tmp_path: Path) -> str:
    """A runner whose every command fails, so nothing may need one."""
    runner = tmp_path / "offline-runner"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        'echo "error: could not download the OSDU catalogue: connection refused" >&2\n'
        "exit 1\n"
    )
    runner.chmod(0o755)
    return str(runner)


def _runner(tmp_path: Path, newest: Path | None = None) -> str:
    """The real runner, but a download is answered by ``newest`` or fails."""
    if newest is None:
        download = (
            'echo "error: could not download the OSDU catalogue: '
            'connection refused" >&2\nexit 1\n'
        )
    else:
        source = provenance_path(newest)
        download = (
            f'cp "{newest}" "$4"\ncp "{source}" "${{4%.json}}.source.json"\nexit 0\n'
        )
    runner = tmp_path / "runner"
    runner.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$1" == geodetic-osdudb && "$2" == download ]]; then\n'
        f"{download}"
        "fi\n"
        f'exec {RUNNER} "$@"\n'
    )
    runner.chmod(0o755)
    return str(runner)


def _run(
    checkout: Path, stock: Path, runner: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GEODETIC_ENGINE_")
    }
    environment["GEODETIC_RUNNER"] = runner
    environment["PROJ_DATA"] = f"{checkout / 'local/proj-data'}:{stock.parent}"
    return subprocess.run(
        ["bash", str(checkout / "scripts/install-osdu-projdb.sh"), *arguments],
        env=environment,
        capture_output=True,
        text=True,
    )


def test_a_missing_catalogue_warns_and_leaves_proj_reading_the_stock_database(
    checkout: Path, stock: Path, tmp_path: Path
) -> None:
    result = _run(checkout, stock, _offline_runner(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert WARNING in result.stderr
    assert "does not exist" in result.stderr
    active = checkout / "local/proj-data/proj.db"
    assert active.is_symlink()
    assert active.resolve() == stock.resolve()
    assert not (checkout / "build/proj.db").exists()


def test_strict_makes_a_failure_fail(
    checkout: Path, stock: Path, tmp_path: Path
) -> None:
    result = _run(checkout, stock, _offline_runner(tmp_path), "--strict")

    assert result.returncode == 1
    assert WARNING in result.stderr


def test_the_committed_catalogue_is_built_offline_and_rebuilt_when_it_changes(
    checkout: Path, stock: Path, tmp_path: Path
) -> None:
    _commit(checkout, FIRST, with_id(geographic(), GEOGRAPHIC_ID))
    before = stock.read_bytes()
    built = checkout / "build/proj.db"

    first = _run(checkout, stock, _runner(tmp_path))

    assert first.returncode == 0, first.stdout + first.stderr
    assert WARNING not in first.stderr
    assert f"osdu/data/data-definitions@{FIRST}" in first.stdout
    assert "OSDU catalogue installed" in first.stdout
    active = checkout / "local/proj-data/proj.db"
    assert os.readlink(active) == "../../build/proj.db"
    assert stock.read_bytes() == before

    # A rebuilt container finds the catalogue already built and builds nothing.
    rerun = _run(checkout, stock, _offline_runner(tmp_path))
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    assert "already holds the official OSDU catalogue" in rerun.stdout
    assert WARNING not in rerun.stderr

    # A missing link is put back on start, to the database already built.
    active.unlink()
    checked = _run(checkout, stock, _offline_runner(tmp_path), "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert os.readlink(active) == "../../build/proj.db"
    assert "OSDU catalogue installed" in checked.stdout

    # A pull brings a newer committed catalogue: the start says so, and the next
    # run imports it.
    _commit(
        checkout,
        SECOND,
        with_id(geographic(Name="Example 2020 revised"), GEOGRAPHIC_ID),
    )
    stale = _run(checkout, stock, _offline_runner(tmp_path), "--check")
    assert "does not hold the committed OSDU catalogue" in stale.stderr
    pulled = _run(checkout, stock, _runner(tmp_path))
    assert pulled.returncode == 0, pulled.stdout + pulled.stderr
    assert f"osdu/data/data-definitions@{SECOND}" in pulled.stdout
    assert _name(built, "geodetic_crs", "4100") == "Example 2020 revised"


def test_update_replaces_the_committed_catalogue_with_the_newest_and_builds_it(
    checkout: Path, stock: Path, tmp_path: Path
) -> None:
    committed = _commit(checkout, FIRST, with_id(geographic(), GEOGRAPHIC_ID))
    newest = _publish(
        tmp_path / "newest/CRS_CT.json",
        SECOND,
        with_id(geographic(Name="Example 2020 revised"), GEOGRAPHIC_ID),
    )

    result = _run(checkout, stock, _runner(tmp_path, newest), "--update")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        f"UPDATED the committed OSDU catalogue: osdu/data/data-definitions@{FIRST} "
        f"-> osdu/data/data-definitions@{SECOND}"
    ) in result.stdout
    assert committed.read_bytes() == newest.read_bytes()
    assert _name(checkout / "build/proj.db", "geodetic_crs", "4100") == (
        "Example 2020 revised"
    )


@pytest.mark.parametrize(("strict", "status"), [(False, 0), (True, 1)])
def test_a_failed_update_warns_and_builds_the_committed_catalogue(
    checkout: Path, stock: Path, tmp_path: Path, strict: bool, status: int
) -> None:
    committed = _commit(checkout, FIRST, with_id(geographic(), GEOGRAPHIC_ID))
    before = committed.read_bytes()

    result = _run(
        checkout,
        stock,
        _runner(tmp_path),
        "--update",
        *(["--strict"] if strict else []),
    )

    assert result.returncode == status, result.stdout + result.stderr
    assert "connection refused" in result.stderr
    assert "Could not update the OSDU catalogue" in result.stderr
    assert "OSDU catalogue installed" in result.stdout
    assert committed.read_bytes() == before


def test_update_and_another_catalogue_are_contradictory(
    checkout: Path, stock: Path, tmp_path: Path
) -> None:
    result = _run(
        checkout,
        stock,
        _offline_runner(tmp_path),
        "--update",
        "--catalog",
        str(tmp_path / "CRS_CT.json"),
    )

    assert result.returncode == 2
    assert "cannot be combined" in result.stderr


def test_a_patched_copy_written_by_hand_is_left_in_use(
    checkout: Path, stock: Path, tmp_path: Path
) -> None:
    active = checkout / "local/proj-data/proj.db"
    active.parent.mkdir(parents=True)
    shutil.copy2(stock, active)

    result = _run(checkout, stock, _offline_runner(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert not active.is_symlink()
    assert "leaving it in use" in result.stderr


def _name(database: Path, table: str, code: str, auth: str = AUTHORITY) -> str | None:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as db:
        row = db.execute(
            f"SELECT name FROM {table} WHERE auth_name = ? AND code = ?",
            (auth, code),
        ).fetchone()
    return None if row is None else str(row[0])


def _own_code_space() -> dict[str, object]:
    """A CRS under the organisation's own code space, not OSDU's."""
    return geographic(
        CodeSpace="Example",
        Code="4200",
        Name="Example local",
        OGCWellKnownText2=CUSTOM_GEOGRAPHIC_WKT.replace('"OSDU"', '"Example"')
        .replace("4100", "4200")
        .replace('"Example 2020"', '"Example local"'),
        Datum=authority_code("Example", 6100, Name="Example Datum 2020"),
    )


def test_a_custom_catalogue_goes_on_top_and_what_it_replaces_is_named(
    checkout: Path, stock: Path, tmp_path: Path
) -> None:
    official = write_catalog(
        tmp_path / "CRS_CT.json", with_id(geographic(), GEOGRAPHIC_ID)
    )
    write_catalog(
        checkout / "local/catalogs/custom/ours.json",
        with_id(geographic(Name="Example 2020 (ours)"), GEOGRAPHIC_ID),
        projected(),
        _own_code_space(),
    )
    built = checkout / "build/proj.db"

    first = _run(checkout, stock, RUNNER, "--catalog", str(official))

    assert first.returncode == 0, first.stdout + first.stderr
    assert "(code spaces: Example, OSDU)" in first.stdout
    assert "REPLACED by this source's" in first.stderr
    assert "geodetic_crs OSDU:4100 Example 2020 -> Example 2020 (ours)" in (
        first.stderr
    )
    assert "1 applied" in first.stdout
    assert _name(built, "geodetic_crs", "4100") == "Example 2020 (ours)"
    assert _name(built, "projected_crs", "32100") == "Example 2020 / UTM zone 31N"
    assert _name(built, "geodetic_crs", "4200", "Example") == "Example local"

    # Importing the official catalogue again keeps the custom one on top.
    forced = _run(checkout, stock, RUNNER, "--catalog", str(official), "--force")

    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert _name(built, "geodetic_crs", "4100") == "Example 2020 (ours)"

    # Applying it again, with nothing to change, replaces nothing, and the
    # official catalogue already built is not built again.
    again = _run(checkout, stock, RUNNER, "--catalog", str(official))
    assert again.returncode == 0, again.stdout + again.stderr
    assert "already holds the official OSDU catalogue" in again.stdout
    assert "REPLACED" not in again.stderr
    assert "1 applied" in again.stdout


@pytest.mark.parametrize(("strict", "status"), [(False, 0), (True, 1)])
def test_a_custom_catalogue_that_cannot_be_applied_is_a_warning(
    checkout: Path, stock: Path, tmp_path: Path, strict: bool, status: int
) -> None:
    official = write_catalog(
        tmp_path / "CRS_CT.json", with_id(geographic(), GEOGRAPHIC_ID)
    )

    result = _run(
        checkout,
        stock,
        RUNNER,
        "--catalog",
        str(official),
        "--custom",
        str(tmp_path / "absent.json"),
        *(["--strict"] if strict else []),
    )

    assert result.returncode == status, result.stdout + result.stderr
    assert "absent.json was NOT applied" in result.stderr
    assert "OSDU catalogue installed" in result.stdout
