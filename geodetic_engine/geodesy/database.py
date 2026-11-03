"""Reading proj.db directly: kept at this path, defined in the lower layer.

The module moved to ``geodetic_engine._referencing.database`` so that
:mod:`geodetic_engine.persistablereference` can read the database without
importing ``geodesy``. Its names are the same objects here.
"""

from geodetic_engine._referencing.database import (
    CRS_OBJECT_TABLES,
    OPERATION_OBJECT_TABLES,
    DatabaseFile,
    DatabaseIdentity,
    GridDataset,
    IdentifiedObject,
    StoredReferenceRow,
    bound_definition,
    bound_definition_by_name,
    database_file,
    database_fingerprints,
    database_identity,
    grid_dataset,
    identified_object,
    identifier_scheme_present,
    object_identifiers,
    persistable_references_present,
    recorded_identifiers,
    skip_reason,
    stored_reference_rows,
    unrecorded_reference_reasons,
)

__all__ = [
    "CRS_OBJECT_TABLES",
    "OPERATION_OBJECT_TABLES",
    "DatabaseFile",
    "DatabaseIdentity",
    "GridDataset",
    "IdentifiedObject",
    "StoredReferenceRow",
    "bound_definition",
    "bound_definition_by_name",
    "database_file",
    "database_fingerprints",
    "database_identity",
    "grid_dataset",
    "identified_object",
    "identifier_scheme_present",
    "object_identifiers",
    "persistable_references_present",
    "recorded_identifiers",
    "skip_reason",
    "stored_reference_rows",
    "unrecorded_reference_reasons",
]
