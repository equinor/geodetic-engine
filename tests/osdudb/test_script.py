"""The multi-source shell workflow must not destroy a published database."""

import os
import shutil
import sqlite3
import stat
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

from tests.osdudb.conftest import write_catalog
from tests.osdudb.test_build import geographic
from tests.support import installed_proj_db


@pytest.mark.parametrize("grids", [False, True], ids=["no-local-grids", "local-grids"])
def test_the_start_up_grid_check_never_deletes_a_patched_copy(
    tmp_path: Path, grids: bool
) -> None:
    """Only ``--patched-copy`` may touch local/proj-data; the default run reports.

    A developer who put the patched copy first on PROJ_DATA is reading it;
    removing it at container start because local/grids/ happens to be empty
    would silently switch them to another database.
    """
    repository = Path(__file__).resolve().parents[2]
    script = tmp_path / "repository/.devcontainer/link-local-grids.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(repository / ".devcontainer/link-local-grids.sh", script)
    copy = tmp_path / "repository/local/proj-data/proj.db"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"a developer's patched copy")
    if grids:
        grid = tmp_path / "repository/local/grids/new-grid.tif"
        grid.parent.mkdir(parents=True)
        grid.write_bytes(b"grid")
    environment = {**os.environ, "PROJ_DATA": str(tmp_path / "proj")}

    result = subprocess.run(
        ["bash", str(script)], env=environment, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert copy.read_bytes() == b"a developer's patched copy"


def _grid_script_repository(tmp_path: Path) -> tuple[Path, Path]:
    """An isolated checkout holding the grid script, and a stock data directory."""
    repository = Path(__file__).resolve().parents[2]
    isolated = tmp_path / "repository"
    for relative in (
        ".devcontainer/link-local-grids.sh",
        "scripts/patch-grid-alternatives.sh",
    ):
        destination = isolated / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository / relative, destination)
    stock = tmp_path / "proj"
    stock.mkdir()
    shutil.copy2(installed_proj_db(), stock / "proj.db")
    return isolated, stock


def _patched_rows(database: Path) -> int:
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        (count,) = connection.execute(
            "SELECT count(*) FROM grid_alternatives "
            "WHERE original_grid_name = 'NNTrans2018B.gtx'"
        ).fetchone()
    return int(count)


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI is required")
def test_a_patched_copy_is_refreshed_when_proj_data_names_it_relatively(
    tmp_path: Path,
) -> None:
    """The copy on PROJ_DATA is recognised as itself, however its path is spelled.

    The documented rerun puts ``local/proj-data`` first on PROJ_DATA. Spelled
    relatively, a string comparison takes the copy for the installed database
    and ``cp`` fails copying it onto itself; the copy must instead be
    refreshed from the stock database behind it.
    """
    isolated, stock = _grid_script_repository(tmp_path)
    copy = isolated / "local/proj-data/proj.db"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"stale copy")
    environment = {**os.environ, "PROJ_DATA": f"local/proj-data:{stock}"}

    result = subprocess.run(
        ["bash", ".devcontainer/link-local-grids.sh", "--patched-copy"],
        cwd=isolated,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert _patched_rows(copy) == 1
    assert _patched_rows(stock / "proj.db") == 0


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI is required")
def test_a_patched_copy_is_never_written_through_a_link(tmp_path: Path) -> None:
    """A symlinked or hard-linked destination is replaced, not written through."""
    isolated, stock = _grid_script_repository(tmp_path)
    victim = tmp_path / "victim.db"
    shutil.copy2(stock / "proj.db", victim)
    before = victim.read_bytes()
    copy = isolated / "local/proj-data/proj.db"
    copy.parent.mkdir(parents=True)
    environment = {**os.environ, "PROJ_DATA": str(stock)}

    for link in (copy.symlink_to, copy.hardlink_to):
        link(victim)
        result = subprocess.run(
            [
                "bash",
                str(isolated / ".devcontainer/link-local-grids.sh"),
                "--patched-copy",
            ],
            env=environment,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert victim.read_bytes() == before
        assert not copy.is_symlink()
        assert _patched_rows(copy) == 1
        copy.unlink()


def test_a_failed_refresh_keeps_the_patched_copy_in_use(tmp_path: Path) -> None:
    """A failed refresh keeps the copy in use and leaves no staged file behind."""
    isolated, stock = _grid_script_repository(tmp_path)
    (isolated / "scripts/patch-grid-alternatives.sh").write_text(
        "#!/usr/bin/env bash\nexit 1\n"
    )
    copy = isolated / "local/proj-data/proj.db"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"the patched copy in use")

    result = subprocess.run(
        ["bash", str(isolated / ".devcontainer/link-local-grids.sh"), "--patched-copy"],
        env={**os.environ, "PROJ_DATA": str(stock)},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert copy.read_bytes() == b"the patched copy in use"
    assert list(copy.parent.iterdir()) == [copy]


def test_a_symlinked_copy_directory_is_refused(tmp_path: Path) -> None:
    """Unlinking proj.db through a linked directory could delete the stock one.

    A second database is on PROJ_DATA so that a source is found and the
    directory check itself is what refuses.
    """
    isolated, stock = _grid_script_repository(tmp_path)
    (isolated / "local").mkdir()
    (isolated / "local/proj-data").symlink_to(stock, target_is_directory=True)
    second = tmp_path / "second"
    second.mkdir()
    shutil.copy2(stock / "proj.db", second / "proj.db")
    before = (stock / "proj.db").read_bytes()

    result = subprocess.run(
        ["bash", str(isolated / ".devcontainer/link-local-grids.sh"), "--patched-copy"],
        env={**os.environ, "PROJ_DATA": f"{stock}:{second}"},
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert (stock / "proj.db").read_bytes() == before


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX permission bits")
@pytest.mark.parametrize("dry_run", [False, True])
def test_shell_publication_preserves_permissions(tmp_path: Path, dry_run: bool) -> None:
    repository = Path(__file__).resolve().parents[2]
    isolated = tmp_path / "repository"
    for relative in (
        "scripts/build-projdb.sh",
        "scripts/patch-grid-alternatives.sh",
        ".devcontainer/link-local-grids.sh",
    ):
        destination = isolated / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repository / relative, destination)
    grid = isolated / "local/grids/new-grid.tif"
    grid.parent.mkdir(parents=True)
    grid.write_bytes(b"grid added after container startup")
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    installed_db = proj_dir / "proj.db"
    shutil.copy2(installed_proj_db(), installed_db)
    before_database = installed_db.read_bytes()
    catalog = write_catalog(tmp_path / "catalog.json", geographic())
    output = tmp_path / "published.db"
    output.write_bytes(b"published artifact must survive")
    output.chmod(0o640)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GEODETIC_ENGINE_")
    }
    environment["GEODETIC_RUNNER"] = f"uv run --project {repository} --no-sync"
    environment["PROJ_DATA"] = str(proj_dir)
    result = subprocess.run(
        [
            "bash",
            str(isolated / "scripts/build-projdb.sh"),
            "--source",
            "osdu",
            "--catalog",
            str(catalog),
            "--output",
            str(output),
            *(["--dry-run"] if dry_run else []),
            "--skip-grid-patch",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # Nothing in PROJ's data directory is modified or added: the database is
    # byte-identical and the local grid is not linked in. The build only warns
    # that local/grids/ is not on this PROJ_DATA.
    assert installed_db.read_bytes() == before_database
    assert [entry.name for entry in proj_dir.iterdir()] == ["proj.db"]
    if dry_run:
        assert "not on PROJ_DATA" not in result.stderr
    else:
        assert "local/grids/ holds 1 file(s) but is not on PROJ_DATA" in result.stderr
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    if dry_run:
        assert output.read_bytes() == b"published artifact must survive"
    else:
        assert output.read_bytes().startswith(b"SQLite format 3")
        repeated = subprocess.run(
            result.args, env=environment, capture_output=True, text=True
        )
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        assert [entry.name for entry in proj_dir.iterdir()] == ["proj.db"]
        assert installed_db.read_bytes() == before_database
