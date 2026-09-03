"""Provenance for one custom proj.db build.

A built database is only as trustworthy as the account of how it was built, so
every build produces a report naming the PROJ and EPSG versions it was built
against, where the definitions came from, what was imported, and above all what
was not. The shape is the same whatever the source, so a consumer reads one
format.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BuildReport:
    """Provenance for one build, so a result can be traced after the fact.

    Attributes:
        source: Where the definitions came from: a Georepository base URL, or
            the path of an OSDU catalogue file.
        source_version: The source's own version, when it states one.
        appended: Whether this build added to a database another build had
            already written, rather than to a fresh copy of the base proj.db.
            Recorded because it decides what the row counts below are counts
            of, and because the output then has more than one report describing
            it.
        overwrite_existing: Whether a colliding row of this build's own
            authorities was replaced rather than reported as a collision.
    """

    built_at: str
    proj_version: str
    epsg_version: str
    proj_data_version: str
    database_layout_version: str
    source: str
    source_version: str | None
    authorities: list[str]
    include_deprecated: bool
    base_proj_db: str
    output_db: str
    appended: bool = False
    overwrite_existing: bool = False
    dry_run: bool = False
    rows_by_table: dict[str, int] = field(default_factory=dict)
    imported: list[dict[str, str]] = field(default_factory=list)
    deprecated_imported: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str | bool | None]] = field(default_factory=list)
    supersessions_written: int = 0
    supersessions_dropped: list[dict[str, str]] = field(default_factory=list)
    authority_preferences: list[dict[str, str]] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        """``failed``, ``passed`` or ``not validated``."""
        if self.dry_run:
            return "dry run"
        return str(self.validation.get("status", "not validated"))

    def imported_objects(self) -> list[tuple[str, str, str]]:
        """The objects this build added, as ``(table, auth_name, code)`` triples."""
        return [
            (item["table"], item["auth_name"], item["code"]) for item in self.imported
        ]

    def as_dict(self) -> dict[str, Any]:
        """Return the report with everything that went wrong first.

        A report is read when something is missing from the database, so the
        objects that were not imported, the supersessions that were dropped and
        the validation outcome come before the inventory of what succeeded.
        """
        data = asdict(self)
        problems = {
            "status": self.status,
            "counts": {
                "rows": sum(self.rows_by_table.values()),
                "imported": len(self.imported),
                "deprecated": len(self.deprecated_imported),
                "skipped": len(self.skipped),
                "skipped_active": sum(
                    1 for item in self.skipped if not item["deprecated"]
                ),
                "supersessions_written": self.supersessions_written,
                "supersessions_dropped": len(self.supersessions_dropped),
                "missing_grids": len(self.validation.get("missing_grids", [])),
            },
            "skipped": data.pop("skipped"),
            "supersessions_dropped": data.pop("supersessions_dropped"),
            "validation": data.pop("validation"),
        }
        return {**problems, **data}

    def to_json(self, indent: int = 2) -> str:
        """Serialise the report as JSON, problems first."""
        return json.dumps(self.as_dict(), indent=indent, sort_keys=False)

    def write(self, path: Path) -> None:
        """Write the report next to the database it describes."""
        path.write_text(self.to_json(), encoding="utf-8")


def log_summary(report: BuildReport) -> None:
    """Log what the build imported and, in full, what it left out.

    Emitted at the very end of a run, after validation, so the last thing in the
    log is the whole picture. The per-object lines also appear earlier in the
    log where they happened; this gathers them in one place.
    """
    rule = "=" * 72
    logger.info(rule)
    logger.info("BUILD SUMMARY - %s", report.status.upper())

    if report.skipped:
        active = sum(1 for item in report.skipped if not item["deprecated"])
        logger.warning(
            "%d object(s) NOT imported (%d active, %d deprecated):",
            len(report.skipped),
            active,
            len(report.skipped) - active,
        )
        for item in report.skipped:
            logger.warning(
                "  [%s] %s %s:%s %s",
                "DEPRECATED" if item["deprecated"] else "  ACTIVE  ",
                item["table"],
                item["auth_name"],
                item["code"],
                item["name"] or "",
            )
            logger.warning("      %s", item["reason"])

    if report.supersessions_dropped:
        logger.warning(
            "%d supersession(s) dropped, replacement not found:",
            len(report.supersessions_dropped),
        )
        for dropped in report.supersessions_dropped:
            logger.warning("  %s -> %s", dropped["superseded"], dropped["replacement"])

    if missing := report.validation.get("missing_grids"):
        logger.warning(
            "%d grid file(s) referenced but not installed here: %s",
            len(missing),
            missing,
        )

    if error := report.validation.get("error"):
        logger.error("VALIDATION FAILED: %s", error)

    # The tally goes last so the final lines of the log are the totals.
    logger.info(
        "%d rows, %d objects imported (%d deprecated), %d skipped",
        sum(report.rows_by_table.values()),
        len(report.imported),
        len(report.deprecated_imported),
        len(report.skipped),
    )
    for table, count in report.rows_by_table.items():
        logger.info("  %6d  %s", count, table)
    logger.info(rule)
