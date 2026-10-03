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
        "now", "9.9.0", "test", "1.25", "1.7", "test", None, [], True, "base", "output"
    )

    def reject(*args: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("geodetic_engine.projdb.report.os.replace", reject)
    with pytest.raises(OSError, match="replace failed"):
        report.write(path)
    assert path.read_text() == "previous report"
    assert [entry for entry in tmp_path.iterdir() if entry.is_file()] == [path]
