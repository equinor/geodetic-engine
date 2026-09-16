"""The multi-source shell workflow must not destroy a published database."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from pyproj import datadir

from tests.osdudb.conftest import write_catalog
from tests.osdudb.test_build import geographic


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
    base = Path(datadir.get_data_dir().split(os.pathsep)[0]) / "proj.db"
    installed_db = proj_dir / "proj.db"
    shutil.copy2(base, installed_db)
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
    assert installed_db.read_bytes() == before_database
    linked = proj_dir / grid.name
    if dry_run:
        assert not linked.exists()
    else:
        assert linked.is_symlink()
        assert linked.resolve() == grid.resolve()
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    if dry_run:
        assert output.read_bytes() == b"published artifact must survive"
    else:
        assert output.read_bytes().startswith(b"SQLite format 3")
        repeated = subprocess.run(
            result.args, env=environment, capture_output=True, text=True
        )
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
        assert linked.is_symlink()
        assert linked.resolve() == grid.resolve()
        assert installed_db.read_bytes() == before_database
