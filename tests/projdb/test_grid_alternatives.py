"""Standalone grid patches must restore the database on every patch failure."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "patch-grid-alternatives.sh"
GRID_NAMES = (
    "Und_min1x1_egm2008_isw=82_WGS84_TideFree",
    "Und_min1x1_egm2008_isw=82_WGS84_TideFree.gz",
    "NNTrans2018B.gtx",
)

pytestmark = pytest.mark.skipif(
    shutil.which("sqlite3") is None, reason="sqlite3 CLI is required"
)


@pytest.fixture
def grid_db(tmp_path: Path) -> Path:
    path = tmp_path / "proj.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE grid_alternatives (
                original_grid_name TEXT PRIMARY KEY,
                proj_grid_name TEXT,
                proj_grid_format TEXT,
                proj_method TEXT,
                inverse_direction INTEGER,
                url TEXT,
                direct_download INTEGER,
                open_license INTEGER
            );
            CREATE TABLE grid_transformation (grid_name TEXT);
            CREATE TABLE other_transformation (grid_name TEXT);
            """
        )
        connection.executemany(
            "INSERT INTO grid_transformation VALUES (?)",
            [(name,) for name in GRID_NAMES],
        )
        connection.commit()
    return path


def test_grid_patches_are_successful_and_idempotent(grid_db: Path) -> None:
    for expected in ("3 patched, 0 already present", "0 patched, 3 already present"):
        result = subprocess.run(
            ["bash", str(SCRIPT), "--db", str(grid_db)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert expected in result.stdout
        assert not Path(f"{grid_db}.grid-alternatives.bak").exists()
    with closing(sqlite3.connect(grid_db)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM grid_alternatives"
        ).fetchone() == (3,)


@pytest.mark.parametrize("failure", ["unreferenced", "sql_error"])
def test_grid_patch_failure_restores_earlier_committed_inserts(
    grid_db: Path, failure: str
) -> None:
    with closing(sqlite3.connect(grid_db)) as connection:
        if failure == "unreferenced":
            connection.execute(
                "DELETE FROM grid_transformation WHERE grid_name = ?",
                (GRID_NAMES[1],),
            )
        else:
            connection.executescript(
                """
                CREATE TRIGGER fail_second_patch BEFORE INSERT ON grid_alternatives
                WHEN NEW.original_grid_name LIKE '%.gz'
                BEGIN
                    SELECT RAISE(ABORT, 'injected SQL failure');
                END;
                """
            )
        connection.commit()
    original = grid_db.read_bytes()

    result = subprocess.run(
        ["bash", str(SCRIPT), "--db", str(grid_db)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert f"added: {GRID_NAMES[0]} ->" in result.stdout
    assert "restoring backup" in result.stderr
    if failure == "sql_error":
        assert "injected SQL failure" in result.stderr
    assert grid_db.read_bytes() == original
    assert not Path(f"{grid_db}.grid-alternatives.bak").exists()
