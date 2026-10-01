"""The multi-source shell workflow must not destroy a published database."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.osdudb.conftest import write_catalog
from tests.osdudb.test_build import geographic
from tests.support import installed_proj_db


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
