"""Recognising OSDU record ids, and nothing else, as ids."""

from __future__ import annotations

import pytest

from geodetic_engine.geodesy.osdu_id import (
    CRS_ENTITY,
    TRANSFORMATION_ENTITY,
    parse_osdu_id,
)

PROJECTED = f"{CRS_ENTITY}:Projected:EPSG::23031"
BOUND = f"{CRS_ENTITY}:BoundProjected:EPSG::23031_EPSG::1133"
TRANSFORMATION = f"{TRANSFORMATION_ENTITY}:EPSG::1612"


@pytest.mark.parametrize(
    ("value", "namespace", "identifier"),
    [
        (f"opendes:{PROJECTED}", "opendes", PROJECTED),
        (f"{{{{NAMESPACE}}}}:{BOUND}", "{{NAMESPACE}}", BOUND),
        (
            f"osdu-test.partition:{TRANSFORMATION}",
            "osdu-test.partition",
            TRANSFORMATION,
        ),
        (PROJECTED, None, PROJECTED),
        (f"  opendes:{PROJECTED}\n", "opendes", PROJECTED),
        (
            f"ns:{CRS_ENTITY}:8cfcd4e5-9fda-5a85-93cd-4562b92e6172",
            "ns",
            f"{CRS_ENTITY}:8cfcd4e5-9fda-5a85-93cd-4562b92e6172",
        ),
        (
            f"ns:{CRS_ENTITY}:Projected:ACME::Grid%20A",
            "ns",
            f"{CRS_ENTITY}:Projected:ACME::Grid%20A",
        ),
    ],
)
def test_an_id_is_read_with_or_without_its_partition(
    value: str, namespace: str | None, identifier: str
) -> None:
    osdu = parse_osdu_id(value)
    assert osdu is not None
    assert osdu.namespace == namespace
    assert osdu.identifier == identifier


def test_the_entity_type_is_told_apart() -> None:
    crs = parse_osdu_id(f"ns:{PROJECTED}")
    operation = parse_osdu_id(f"ns:{TRANSFORMATION}")
    assert crs is not None and crs.is_crs and not crs.is_transformation
    assert (
        operation is not None and operation.is_transformation and not operation.is_crs
    )


@pytest.mark.parametrize(
    "value",
    [
        "EPSG:23031",
        "EPSG::1612",
        "urn:ogc:def:crs:EPSG::23031",
        "Projected:EPSG::23031",
        "ED50 / UTM zone 31N",
        '{"type":"LBC","wkt":"PROJCS[...]"}',
        f'PROJCRS["{PROJECTED}"]',
        f":{PROJECTED}",
        f"two words:{PROJECTED}",
        f"ns:extra:{PROJECTED}",
        f"x{PROJECTED}",
        f"ns:{CRS_ENTITY}:",
        f"ns:{CRS_ENTITY}:Projected EPSG 23031",
        "ns:reference-data--UnitOfMeasure:m",
        # Payloads, plain and percent-encoded, even ones quoting an id.
        f'{{"type":"LBC","name":"{PROJECTED}"}}',
        f"%7B%22name%22%3A%22x%22%7D:{PROJECTED}",
        f"{{{{NAMESPACE}}}}x:{PROJECTED}",
    ],
)
def test_anything_else_is_not_an_id(value: str) -> None:
    assert parse_osdu_id(value) is None


def test_a_bare_data_id_is_an_id_only_where_the_entity_is_implied() -> None:
    assert parse_osdu_id("Projected:EPSG::23031") is None
    osdu = parse_osdu_id("Projected:EPSG::23031", entity=CRS_ENTITY)
    assert osdu is not None
    assert osdu.identifier == PROJECTED


def test_a_full_id_of_the_other_entity_keeps_its_own_entity() -> None:
    osdu = parse_osdu_id(f"ns:{TRANSFORMATION}", entity=CRS_ENTITY)
    assert osdu is not None
    assert osdu.is_transformation


def test_an_unknown_entity_is_refused() -> None:
    with pytest.raises(ValueError, match="not an OSDU"):
        parse_osdu_id("EPSG::1612", entity="reference-data--UnitOfMeasure")


@pytest.mark.parametrize(
    ("value", "candidates"),
    [
        # A reference to the latest version ends in a bare separator.
        (f"ns:{PROJECTED}:", (f"{PROJECTED}:", PROJECTED)),
        # A reference to one version ends in its number.
        (
            f"ns:{TRANSFORMATION}:1562066009929332",
            (f"{TRANSFORMATION}:1562066009929332", TRANSFORMATION),
        ),
        (f"ns:{BOUND}:", (f"{BOUND}:", BOUND)),
        # An id proper ends in a code after a double colon, not in a version.
        (f"ns:{TRANSFORMATION}", (TRANSFORMATION,)),
        (f"ns:{BOUND}", (BOUND,)),
        (f"ns:{CRS_ENTITY}:8cfcd4e5-9fda", (f"{CRS_ENTITY}:8cfcd4e5-9fda",)),
        # Ambiguous: the id as given is always tried first.
        (
            f"ns:{CRS_ENTITY}:Projected:ACME:123",
            (f"{CRS_ENTITY}:Projected:ACME:123", f"{CRS_ENTITY}:Projected:ACME"),
        ),
    ],
)
def test_a_version_separator_is_dropped_after_trying_the_id_as_given(
    value: str, candidates: tuple[str, ...]
) -> None:
    osdu = parse_osdu_id(value)
    assert osdu is not None
    assert osdu.candidates() == candidates
