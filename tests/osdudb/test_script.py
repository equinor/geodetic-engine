"""The multi-source shell workflow must not destroy a published database."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.osdudb.conftest import write_catalog
from tests.osdudb.test_build import geographic


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX permission bits")
@pytest.mark.parametrize("dry_run", [False, True])
def test_shell_publication_preserves_permissions(tmp_path: Path, dry_run: bool) -> None:
    repository = Path(__file__).resolve().parents[2]
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
    result = subprocess.run(
        [
            "bash",
            str(repository / "scripts/build-projdb.sh"),
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
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    if dry_run:
        assert output.read_bytes() == b"published artifact must survive"
    else:
        assert output.read_bytes().startswith(b"SQLite format 3")
