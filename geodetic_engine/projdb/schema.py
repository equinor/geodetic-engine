"""Table and column contracts for the PROJ database.

Column lists are frozen here rather than discovered at runtime so that a PROJ
upgrade which renames or reorders a column fails loudly at
:func:`verify_schema` instead of silently writing rows into the wrong shape.
Table and column identifiers are only ever taken from this module, never from
remote data, so no identifier can be injected into SQL.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Final

from geodetic_engine.projdb.errors import SchemaDriftError

# Columns written by the builder, in the order they are bound. A subset of each
# table's real columns is allowed; verify_schema only requires that every column
# named here exists.
TABLE_COLUMNS: Final[Mapping[str, tuple[str, ...]]] = {
    "unit_of_measure": (
        "auth_name",
        "code",
        "name",
        "type",
        "conv_factor",
        "proj_short_name",
        "deprecated",
    ),
    "ellipsoid": (
        "auth_name",
        "code",
        "name",
        "description",
        "celestial_body_auth_name",
        "celestial_body_code",
        "semi_major_axis",
        "uom_auth_name",
        "uom_code",
        "inv_flattening",
        "semi_minor_axis",
        "deprecated",
    ),
    "prime_meridian": (
        "auth_name",
        "code",
        "name",
        "longitude",
        "uom_auth_name",
        "uom_code",
        "deprecated",
    ),
    "coordinate_system": ("auth_name", "code", "type", "dimension"),
    "axis": (
        "auth_name",
        "code",
        "name",
        "abbrev",
        "orientation",
        "coordinate_system_auth_name",
        "coordinate_system_code",
        "coordinate_system_order",
        "uom_auth_name",
        "uom_code",
    ),
    "geodetic_datum": (
        "auth_name",
        "code",
        "name",
        "description",
        "ellipsoid_auth_name",
        "ellipsoid_code",
        "prime_meridian_auth_name",
        "prime_meridian_code",
        "publication_date",
        "frame_reference_epoch",
        "ensemble_accuracy",
        "anchor",
        "anchor_epoch",
        "deprecated",
    ),
    "vertical_datum": (
        "auth_name",
        "code",
        "name",
        "description",
        "publication_date",
        "frame_reference_epoch",
        "ensemble_accuracy",
        "anchor",
        "anchor_epoch",
        "deprecated",
    ),
    # engineering_datum has no description column; do not add one.
    "engineering_datum": (
        "auth_name",
        "code",
        "name",
        "publication_date",
        "anchor",
        "anchor_epoch",
        "deprecated",
    ),
    "geodetic_crs": (
        "auth_name",
        "code",
        "name",
        "description",
        "type",
        "coordinate_system_auth_name",
        "coordinate_system_code",
        "datum_auth_name",
        "datum_code",
        "text_definition",
        "deprecated",
    ),
    "projected_crs": (
        "auth_name",
        "code",
        "name",
        "description",
        "coordinate_system_auth_name",
        "coordinate_system_code",
        "geodetic_crs_auth_name",
        "geodetic_crs_code",
        "conversion_auth_name",
        "conversion_code",
        "text_definition",
        "deprecated",
    ),
    "vertical_crs": (
        "auth_name",
        "code",
        "name",
        "description",
        "coordinate_system_auth_name",
        "coordinate_system_code",
        "datum_auth_name",
        "datum_code",
        "deprecated",
    ),
    "engineering_crs": (
        "auth_name",
        "code",
        "name",
        "description",
        "coordinate_system_auth_name",
        "coordinate_system_code",
        "datum_auth_name",
        "datum_code",
        "deprecated",
    ),
    "compound_crs": (
        "auth_name",
        "code",
        "name",
        "description",
        "horiz_crs_auth_name",
        "horiz_crs_code",
        "vertical_crs_auth_name",
        "vertical_crs_code",
        "deprecated",
    ),
    "conversion_param": (
        "auth_name",
        "code",
        "name",
    ),
    "conversion_table": (
        "auth_name",
        "code",
        "name",
        "description",
        "method_auth_name",
        "method_code",
        *(
            f"param{i}_{suffix}"
            for i in range(1, 8)
            for suffix in ("auth_name", "code", "value", "uom_auth_name", "uom_code")
        ),
        "deprecated",
    ),
    "helmert_transformation_table": (
        "auth_name",
        "code",
        "name",
        "description",
        "method_auth_name",
        "method_code",
        "source_crs_auth_name",
        "source_crs_code",
        "target_crs_auth_name",
        "target_crs_code",
        "accuracy",
        "tx",
        "ty",
        "tz",
        "translation_uom_auth_name",
        "translation_uom_code",
        "rx",
        "ry",
        "rz",
        "rotation_uom_auth_name",
        "rotation_uom_code",
        "scale_difference",
        "scale_difference_uom_auth_name",
        "scale_difference_uom_code",
        "rate_tx",
        "rate_ty",
        "rate_tz",
        "rate_translation_uom_auth_name",
        "rate_translation_uom_code",
        "rate_rx",
        "rate_ry",
        "rate_rz",
        "rate_rotation_uom_auth_name",
        "rate_rotation_uom_code",
        "rate_scale_difference",
        "rate_scale_difference_uom_auth_name",
        "rate_scale_difference_uom_code",
        "epoch",
        "epoch_uom_auth_name",
        "epoch_uom_code",
        "px",
        "py",
        "pz",
        "pivot_uom_auth_name",
        "pivot_uom_code",
        "operation_version",
        "deprecated",
    ),
    "grid_transformation": (
        "auth_name",
        "code",
        "name",
        "description",
        "method_auth_name",
        "method_code",
        "method_name",
        "source_crs_auth_name",
        "source_crs_code",
        "target_crs_auth_name",
        "target_crs_code",
        "accuracy",
        "grid_param_auth_name",
        "grid_param_code",
        "grid_param_name",
        "grid_name",
        "grid2_param_auth_name",
        "grid2_param_code",
        "grid2_param_name",
        "grid2_name",
        *(
            f"param{i}_{suffix}"
            for i in range(1, 3)
            for suffix in (
                "auth_name",
                "code",
                "name",
                "value",
                "uom_auth_name",
                "uom_code",
            )
        ),
        "interpolation_crs_auth_name",
        "interpolation_crs_code",
        "operation_version",
        "deprecated",
    ),
    "other_transformation": (
        "auth_name",
        "code",
        "name",
        "description",
        "method_auth_name",
        "method_code",
        "method_name",
        "source_crs_auth_name",
        "source_crs_code",
        "target_crs_auth_name",
        "target_crs_code",
        "accuracy",
        *(
            f"param{i}_{suffix}"
            for i in range(1, 10)
            for suffix in (
                "auth_name",
                "code",
                "name",
                "value",
                "uom_auth_name",
                "uom_code",
            )
        ),
        "grid_param_auth_name",
        "grid_param_code",
        "grid_param_name",
        "grid_name",
        "interpolation_crs_auth_name",
        "interpolation_crs_code",
        "operation_version",
        "deprecated",
    ),
    "concatenated_operation": (
        "auth_name",
        "code",
        "name",
        "description",
        "source_crs_auth_name",
        "source_crs_code",
        "target_crs_auth_name",
        "target_crs_code",
        "accuracy",
        "operation_version",
        "deprecated",
    ),
    "concatenated_operation_step": (
        "operation_auth_name",
        "operation_code",
        "step_number",
        "step_auth_name",
        "step_code",
        "step_direction",
    ),
    "scope": ("auth_name", "code", "scope", "deprecated"),
    "extent": (
        "auth_name",
        "code",
        "name",
        "description",
        "south_lat",
        "north_lat",
        "west_lon",
        "east_lon",
        "deprecated",
    ),
    "usage": (
        "auth_name",
        "code",
        "object_table_name",
        "object_auth_name",
        "object_code",
        "extent_auth_name",
        "extent_code",
        "scope_auth_name",
        "scope_code",
    ),
    "alias_name": ("table_name", "auth_name", "code", "alt_name", "source"),
    "supersession": (
        "superseded_table_name",
        "superseded_auth_name",
        "superseded_code",
        "replacement_table_name",
        "replacement_auth_name",
        "replacement_code",
        "source",
        "same_source_target_crs",
    ),
    "authority_to_authority_preference": (
        "source_auth_name",
        "target_auth_name",
        "allowed_authorities",
    ),
}

# Tables keyed by (auth_name, code) that the builder may populate with custom
# authority objects, in an order that satisfies foreign keys.
OBJECT_TABLES: Final[tuple[str, ...]] = (
    "unit_of_measure",
    "ellipsoid",
    "prime_meridian",
    "coordinate_system",
    "axis",
    "geodetic_datum",
    "vertical_datum",
    "engineering_datum",
    "geodetic_crs",
    "projected_crs",
    "vertical_crs",
    "engineering_crs",
    "compound_crs",
    "conversion_table",
    "helmert_transformation_table",
    "grid_transformation",
    "other_transformation",
    "concatenated_operation",
)

# Tables whose rows legitimately reference authorities other than the custom
# ones, so the per-row authority guard does not apply to them. conversion_param
# is EPSG's shared vocabulary of parameter names, not an object of its own.
FOREIGN_AUTHORITY_ALLOWED: Final[frozenset[str]] = frozenset(
    {
        "authority_to_authority_preference",
        "supersession",
        "alias_name",
        "conversion_param",
    }
)

# The column naming the authority that owns a row. Most tables use auth_name;
# the step table is keyed by the operation it belongs to.
AUTHORITY_COLUMN: Final[Mapping[str, str]] = {
    "concatenated_operation_step": "operation_auth_name",
}

# The object table vocabulary accepted by the CHECK constraints on
# usage.object_table_name, alias_name.table_name and supersession.*_table_name.
# PROJ spells the operation tables without their _table suffix; using the real
# table name there violates the constraint.
OBJECT_TABLE_NAME: Final[Mapping[str, str]] = {
    "unit_of_measure": "unit_of_measure",
    "celestial_body": "celestial_body",
    "ellipsoid": "ellipsoid",
    "extent": "extent",
    "prime_meridian": "prime_meridian",
    "geodetic_crs": "geodetic_crs",
    "projected_crs": "projected_crs",
    "vertical_crs": "vertical_crs",
    "engineering_crs": "engineering_crs",
    "compound_crs": "compound_crs",
    "geodetic_datum": "geodetic_datum",
    "vertical_datum": "vertical_datum",
    "engineering_datum": "engineering_datum",
    "conversion_table": "conversion",
    "helmert_transformation_table": "helmert_transformation",
    "grid_transformation": "grid_transformation",
    "other_transformation": "other_transformation",
    "concatenated_operation": "concatenated_operation",
}


def verify_schema(connection: sqlite3.Connection) -> None:
    """Check that every table and column this builder writes exists.

    Args:
        connection: Open connection to the database about to be written.

    Raises:
        SchemaDriftError: If a table is absent or is missing an expected column.
    """
    drift: list[str] = []
    for table, columns in TABLE_COLUMNS.items():
        present = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM pragma_table_info(?)", (table,)
            )
        }
        if not present:
            drift.append(f"table {table!r} is absent")
            continue
        missing = [column for column in columns if column not in present]
        if missing:
            drift.append(f"table {table!r} is missing columns {missing}")
    if drift:
        raise SchemaDriftError(
            "base proj.db schema is not the one this builder was written "
            "against: " + "; ".join(drift)
        )


def database_layout_version(connection: sqlite3.Connection) -> str:
    """Return the proj.db layout version, for example ``1.6``.

    Args:
        connection: Open connection to a PROJ database.

    Returns:
        ``MAJOR.MINOR`` layout version recorded in the ``metadata`` table.
    """
    rows = dict(
        connection.execute(
            "SELECT key, value FROM metadata WHERE key IN (?, ?)",
            (
                "DATABASE.LAYOUT.VERSION.MAJOR",
                "DATABASE.LAYOUT.VERSION.MINOR",
            ),
        )
    )
    return (
        f"{rows.get('DATABASE.LAYOUT.VERSION.MAJOR', '?')}."
        f"{rows.get('DATABASE.LAYOUT.VERSION.MINOR', '?')}"
    )


def metadata(connection: sqlite3.Connection) -> dict[str, str]:
    """Return the full ``metadata`` table of a PROJ database as a dict."""
    return dict(connection.execute("SELECT key, value FROM metadata"))
