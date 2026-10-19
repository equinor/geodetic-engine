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
import os
import tempfile
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
        register_versions: Every dataset version the source states, by data
            source name. A register versions the authority's own objects and the
            EPSG dataset it carries separately, and a build is only reproducible
            against the pair.
        cache: What the local response cache contributed, and the versions it
            had been filled against. Empty when the cache was not used, so a
            build served from cache is never indistinguishable from a fresh one.
        appended: Whether this build added to a database another build had
            already written, rather than to a fresh copy of the base proj.db.
            Recorded because it decides what the row counts below are counts
            of, and because the output then has more than one report describing
            it.
        overwrite_rows: Whether a colliding row of this build's own
            authorities was replaced rather than reported as a collision.
        identifiers: The identifiers another scheme gives objects, an OSDU
            record id for one, as this build recorded them: the scheme, how
            many were recorded and how many of those this build wrote, how many
            were also written as aliases, every identifier that names nothing
            in the database and why, and where the source and proj.db disagree
            about an identified object. Empty for a source that has none.
        persistable_references: The OSDU ``PersistableReference`` strings this
            build recorded against record ids: whether recording was enabled,
            how many are recorded, written and unchanged, how many an earlier
            build recorded were kept or removed because this catalogue no
            longer states a usable one, how many came from records whose id
            names nothing here, every record that states none (``missing``),
            every one not recorded because it could not be used and why
            (``invalid``), and every one recorded or kept despite a
            disagreement (``warnings``). Empty for a source that has none.
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
    overwrite_rows: bool = False
    dry_run: bool = False
    register_versions: dict[str, str] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)
    rows_by_table: dict[str, int] = field(default_factory=dict)
    imported: list[dict[str, str]] = field(default_factory=list)
    deprecated_imported: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str | bool | None]] = field(default_factory=list)
    supersessions_written: int = 0
    supersessions_dropped: list[dict[str, str]] = field(default_factory=list)
    authority_preferences: list[dict[str, str]] = field(default_factory=list)
    identifiers: dict[str, Any] = field(default_factory=dict)
    persistable_references: dict[str, Any] = field(default_factory=dict)
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
                "identifiers_recorded": int(self.identifiers.get("mapped", 0)),
                "identifiers_unmapped": len(self.identifiers.get("unmapped", [])),
                "persistable_references_recorded": int(
                    self.persistable_references.get("recorded", 0)
                ),
                "persistable_references_missing": len(
                    self.persistable_references.get("missing", [])
                ),
                "persistable_references_invalid": len(
                    self.persistable_references.get("invalid", [])
                ),
            },
            "skipped": data.pop("skipped"),
            "supersessions_dropped": data.pop("supersessions_dropped"),
            "validation": data.pop("validation"),
        }
        return {**problems, **data}

    def to_json(self, indent: int = 2) -> str:
        """Serialise the report as JSON, problems first.

        Args:
            indent: Spaces per indentation level.

        Returns:
            The report from :meth:`as_dict`, as JSON text.
        """
        return json.dumps(self.as_dict(), indent=indent, sort_keys=False)

    def write(self, path: Path) -> None:
        """Atomically export the report next to the database it describes.

        Args:
            path: File to write. It is replaced only once the whole report has
                been written and synced, so a reader never sees half of one.
        """
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        staging = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(self.to_json())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staging, path)
        finally:
            staging.unlink(missing_ok=True)


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

    if identifiers := report.identifiers:
        scheme = identifiers.get("scheme", "")
        if unmapped := identifiers.get("unmapped"):
            logger.warning(
                "%d %s id(s) name nothing in this database:", len(unmapped), scheme
            )
            for item in unmapped:
                logger.warning("  %s", item["identifier"] or item["name"])
                logger.warning("      %s", item["reason"])
        for item in identifiers.get("warnings") or ():
            logger.info("  %s: %s", item["identifier"], item["warning"])
        logger.info(
            "%d %s id(s) recorded, %d written by this build, %d as aliases",
            identifiers.get("mapped", 0),
            scheme,
            identifiers.get("written", 0),
            identifiers.get("aliases_written", 0),
        )

    if references := report.persistable_references:
        _log_persistable_references(references)

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


def _log_persistable_references(references: dict[str, Any]) -> None:
    """Log what was left out of, and noticed about, the persistable references.

    A record that states no reference is expected -- ESRI has no geocentric
    CRS, so OSDU publishes none for one -- and is listed at INFO under a
    single WARNING. One that could not be read, or was kept despite a
    disagreement, is a WARNING each.
    """
    if not references.get("enabled", True):
        logger.info("persistable references not recorded: disabled")
        return
    if missing := references.get("missing"):
        logger.warning(
            "%d record(s) state no persistableReference, so none is recorded for them:",
            len(missing),
        )
        for item in missing:
            logger.info("  %s (%s)", item["identifier"], item["reason"])
    if invalid := references.get("invalid"):
        logger.warning(
            "%d persistableReference(s) NOT recorded because they cannot be used:",
            len(invalid),
        )
        for item in invalid:
            logger.warning("  %s", item["identifier"])
            logger.warning("      %s", item["reason"])
    for item in references.get("warnings") or ():
        logger.warning("  %s: %s", item["identifier"], item["warning"])
    logger.info(
        "%d persistableReference(s) recorded, %d written by this build, "
        "%d unchanged, %d kept from an earlier build, %d removed, %d published "
        "by records whose id names nothing here",
        references.get("recorded", 0),
        references.get("written", 0),
        references.get("unchanged", 0),
        references.get("kept", 0),
        references.get("removed", 0),
        references.get("unlinked", 0),
    )
