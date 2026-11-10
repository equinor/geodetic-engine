"""Fixtures shared by the catalog tests: the schemas a listing's JSON obeys."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import pyproj
import pytest
from referencing import Registry, Resource

from geodetic_engine import catalog


def _projjson_schema() -> dict[str, Any]:
    """PROJJSON's schema, as the PROJ this package runs installs it."""
    for directory in pyproj.datadir.get_data_dir().split(os.pathsep):
        candidate = Path(directory) / "projjson.schema.json"
        if candidate.is_file():
            schema: dict[str, Any] = json.loads(candidate.read_text(encoding="utf-8"))
            return schema
    pytest.skip("PROJ installed no projjson.schema.json")


@pytest.fixture(scope="session")
def projjson_schema() -> dict[str, Any]:
    return _projjson_schema()


@pytest.fixture(scope="session")
def listing_validator(projjson_schema: dict[str, Any]) -> jsonschema.Draft7Validator:
    """Validates a listing's JSON, resolving PROJJSON's schema offline."""
    registry: Registry[Any] = Registry().with_resource(
        projjson_schema["$id"], Resource.from_contents(projjson_schema)
    )
    return jsonschema.Draft7Validator(catalog.listing_schema(), registry=registry)


@pytest.fixture(scope="session")
def projjson_validator(projjson_schema: dict[str, Any]) -> jsonschema.Draft7Validator:
    """Validates a document against PROJJSON's root schema."""
    return jsonschema.Draft7Validator(projjson_schema)


@pytest.fixture(scope="session")
def projjson_definition(
    projjson_schema: dict[str, Any],
) -> Any:
    """Validates against one of PROJJSON's definitions, by name."""
    registry: Registry[Any] = Registry().with_resource(
        projjson_schema["$id"], Resource.from_contents(projjson_schema)
    )

    def validate(document: object, definition: str) -> None:
        schema = {"$ref": f"{projjson_schema['$id']}#/definitions/{definition}"}
        jsonschema.Draft7Validator(schema, registry=registry).validate(document)

    return validate
