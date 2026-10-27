"""Regression checks for the pyproj wheel smoke test itself."""

import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from scripts import check_pyproj_wheel


def test_custom_database_connection_is_closed_before_wheel_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pyproj"
    database = root / "proj_dir/share/proj/proj.db"
    database.parent.mkdir(parents=True)
    original_connect = sqlite3.connect
    with closing(original_connect(database)) as connection:
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO metadata VALUES ('EPSG.VERSION', 'original')")
        connection.commit()

    class TrackedConnection(sqlite3.Connection):
        was_closed = False

        def close(self) -> None:
            super().close()
            self.was_closed = True

    connections: list[TrackedConnection] = []

    def connect(*args: Any, **kwargs: Any) -> TrackedConnection:
        connection = original_connect(*args, factory=TrackedConnection, **kwargs)
        connections.append(connection)
        return connection

    calls = 0

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        assert len(connections) == 1 and connections[0].was_closed
        if calls == 0:
            custom = Path(kwargs["env"]["PROJ_DATA"]) / "proj.db"
            with closing(original_connect(custom)) as connection:
                marker = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'EPSG.VERSION'"
                ).fetchone()
            assert marker == (check_pyproj_wheel._MARKER,)
        calls += 1
        if kwargs.get("check") is False:
            return subprocess.CompletedProcess(
                args, 1, stderr="first PROJ_DATA/PROJ_LIB"
            )
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(
        check_pyproj_wheel.pyproj, "__file__", str(root / "__init__.py")
    )
    monkeypatch.setattr(check_pyproj_wheel.sqlite3, "connect", connect)
    monkeypatch.setattr(check_pyproj_wheel.subprocess, "run", run)
    check_pyproj_wheel._check_data_paths()
    assert calls == 3
