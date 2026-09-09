"""The multi-source shell workflow must not destroy a published database."""

import os
import subprocess
from pathlib import Path

from tests.osdudb.conftest import write_catalog
from tests.osdudb.test_build import geographic


def test_shell_dry_run_preserves_published_database(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    catalog = write_catalog(tmp_path / "catalog.json", geographic())
    output = tmp_path / "published.db"
    output.write_bytes(b"published artifact must survive")
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
            "--dry-run",
            "--skip-grid-patch",
        ],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_bytes() == b"published artifact must survive"
