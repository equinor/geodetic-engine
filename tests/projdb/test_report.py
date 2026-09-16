"""Sidecar publication cannot destroy the previous report on failure."""

from pathlib import Path

import pytest

from geodetic_engine.projdb.report import BuildReport


def test_failed_report_replace_keeps_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "report.json"
    path.write_text("previous report")
    report = BuildReport(
        "now", "9.8.1", "test", "1.24", "1.6", "test", None, [], True, "base", "output"
    )

    def reject(*args: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("geodetic_engine.projdb.report.os.replace", reject)
    with pytest.raises(OSError, match="replace failed"):
        report.write(path)
    assert path.read_text() == "previous report"
    assert [entry for entry in tmp_path.iterdir() if entry.is_file()] == [path]
