"""Reading back the persistable references a build from an OSDU catalogue records.

The database here is a copy of the installed proj.db with identifiers and
references added the way a build adds them, written by hand so each test
states exactly what the database records.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import pyproj
import pytest
from pyproj import CRS

from geodetic_engine.geodesy import CoordinateReferenceSystem
from geodetic_engine.geodesy.osdu_id import CRS_ENTITY, SCHEME, TRANSFORMATION_ENTITY
from geodetic_engine.persistablereference import (
    AmbiguousCatalogReferenceError,
    AuthorityCode,
    CrsReference,
    Kind,
    MalformedReferenceError,
    export_persistable_references_from_catalog,
    missing_reference_reason,
    persistable_reference_from_catalog,
    persistable_references_from_catalog,
    pretty_persistable_reference,
)
from geodetic_engine.projdb import schema
from tests.support import installed_proj_db

PROJECTED = f"{CRS_ENTITY}:Projected:EPSG::23031"
GEOGRAPHIC = f"{CRS_ENTITY}:Geographic2D:EPSG::4326"
GEOGRAPHIC_AGAIN = f"{CRS_ENTITY}:Geographic2D:EPSG::4326_again"
GEOCENTRIC = f"{CRS_ENTITY}:Geocentric:EPSG::4978"
TRANSFORMATION = f"{TRANSFORMATION_ENTITY}:EPSG::1612"

GEOGCS_ED50 = (
    'GEOGCS["GCS_European_1950",DATUM["D_European_1950",'
    'SPHEROID["International_1924",6378388.0,297.0]],PRIMEM["Greenwich",0.0],'
    'UNIT["Degree",0.0174532925199433]]'
)
GEOGCS_WGS84 = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,'
    '298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)
PROJCS = (
    f'PROJCS["ED_1950_UTM_Zone_31N",{GEOGCS_ED50},'
    'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],'
    'PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",3.0],'
    'PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",0.0],'
    'UNIT["Meter",1.0],AUTHORITY["EPSG",23031]]'
)
GEOGTRAN = (
    f'GEOGTRAN["ED_1950_To_WGS_1984_23",{GEOGCS_ED50},{GEOGCS_WGS84},'
    'METHOD["Position_Vector"],PARAMETER["X_Axis_Translation",-116.641],'
    'PARAMETER["Y_Axis_Translation",-56.931],'
    'PARAMETER["Z_Axis_Translation",-110.559],PARAMETER["X_Axis_Rotation",0.893],'
    'PARAMETER["Y_Axis_Rotation",0.921],PARAMETER["Z_Axis_Rotation",-0.917],'
    'PARAMETER["Scale_Difference",-3.52],OPERATIONACCURACY[1.0],'
    'AUTHORITY["EPSG",1612]]'
)


def _payload(**members: object) -> str:
    return json.dumps(members, separators=(",", ":"))


PROJECTED_PAYLOAD = _payload(
    authCode={"auth": "EPSG", "code": "23031"},
    name="ED_1950_UTM_Zone_31N",
    type="LBC",
    ver="PE_10_9_1",
    wkt=PROJCS,
)
TRANSFORMATION_PAYLOAD = _payload(
    authCode={"auth": "EPSG", "code": "1612"},
    name="ED_1950_To_WGS_1984_23",
    type="ST",
    ver="PE_10_9_1",
    wkt=GEOGTRAN,
)
GEOGRAPHIC_PAYLOAD = _payload(
    authCode={"auth": "EPSG", "code": "4326"},
    name="GCS_WGS_1984",
    type="LBC",
    ver="PE_10_9_1",
    wkt=GEOGCS_WGS84,
)
# The user's example of an early bound CRS, as OSDU publishes it.
BOUND_PAYLOAD = _payload(
    authCode={"auth": "OSDU", "code": "23031023"},
    lateBoundCRS=json.loads(PROJECTED_PAYLOAD),
    name="ED50 * EPSG-Nor N62 2001 / UTM zone 31N [23031,1612]",
    singleCT=json.loads(TRANSFORMATION_PAYLOAD),
    type="EBC",
    ver="PE_10_9_1",
)


def _write(
    directory: Path,
    *,
    geographic_again: str | None = None,
    geographic_again_deprecated: int = 0,
) -> Path:
    """A proj.db recording ids and references as an OSDU build would."""
    directory.mkdir()
    database = directory / "proj.db"
    shutil.copyfile(installed_proj_db(), database)
    identifiers = [
        (PROJECTED, "projected_crs", "EPSG", "23031", 0),
        (GEOGRAPHIC, "geodetic_crs", "EPSG", "4326", 0),
        (GEOCENTRIC, "geodetic_crs", "EPSG", "4978", 0),
        (TRANSFORMATION, "helmert_transformation", "EPSG", "1612", 0),
    ]
    references = [
        (PROJECTED, "LBC", "EPSG", "23031", PROJECTED_PAYLOAD),
        (GEOGRAPHIC, "LBC", "EPSG", "4326", GEOGRAPHIC_PAYLOAD),
        (TRANSFORMATION, "ST", "EPSG", "1612", TRANSFORMATION_PAYLOAD),
    ]
    if geographic_again is not None:
        identifiers.append(
            (
                GEOGRAPHIC_AGAIN,
                "geodetic_crs",
                "EPSG",
                "4326",
                geographic_again_deprecated,
            )
        )
        references.append((GEOGRAPHIC_AGAIN, "LBC", "EPSG", "4326", geographic_again))
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for statement in (*schema.IDENTIFIER_DDL, *schema.PERSISTABLE_REFERENCE_DDL):
            connection.execute(statement)
        connection.executemany(
            f"INSERT INTO {schema.IDENTIFIER_TABLE} (scheme, identifier, "
            "table_name, auth_name, code, deprecated, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'CRS_CT.json')",
            [(SCHEME, *row) for row in identifiers],
        )
        connection.executemany(
            f"INSERT INTO {schema.PERSISTABLE_REFERENCE_TABLE} "
            f"({', '.join(schema.PERSISTABLE_REFERENCE_COLUMNS)}) "
            "VALUES (?, ?, ?, NULL, ?, ?, 'PE_10_9_1', ?, ?, 'CRS_CT.json')",
            [
                (
                    SCHEME,
                    identifier,
                    kind,
                    auth,
                    code,
                    hashlib.sha256(payload.encode()).hexdigest(),
                    payload,
                )
                for identifier, kind, auth, code, payload in references
            ],
        )
        connection.execute(
            f"CREATE TABLE {schema.BUILD_HISTORY_TABLE} "
            "(sequence INTEGER PRIMARY KEY, report TEXT NOT NULL)"
        )
        connection.execute(
            f"INSERT INTO {schema.BUILD_HISTORY_TABLE} (report) VALUES (?)",
            (
                json.dumps(
                    {
                        "persistable_references": {
                            "missing": [
                                {
                                    "identifier": GEOCENTRIC,
                                    "reason": "the record states no "
                                    "PersistableReference",
                                }
                            ]
                        }
                    }
                ),
            ),
        )
    return database


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return _write(tmp_path / "osdu")


@pytest.fixture
def active(database: Path) -> Iterator[Path]:
    """PROJ reading the database, as the default for every lookup."""
    previous = pyproj.datadir.get_data_dir()
    pyproj.datadir.set_data_dir(f"{database.parent}{os.pathsep}{previous}")
    try:
        yield database
    finally:
        pyproj.datadir.set_data_dir(previous)


class TestPrettyPrinting:
    def test_the_default_is_indented_json_in_the_payloads_own_order(self) -> None:
        laid_out = pretty_persistable_reference(BOUND_PAYLOAD)
        assert json.loads(laid_out) == json.loads(BOUND_PAYLOAD)
        assert laid_out == json.dumps(json.loads(BOUND_PAYLOAD), indent=2)
        assert laid_out.splitlines()[:5] == [
            "{",
            '  "authCode": {',
            '    "auth": "OSDU",',
            '    "code": "23031023"',
            "  },",
        ]
        assert laid_out.splitlines()[-3:] == [
            '  "type": "EBC",',
            '  "ver": "PE_10_9_1"',
            "}",
        ]

    def test_the_wkt_can_be_laid_out_too_for_reading(self) -> None:
        laid_out = pretty_persistable_reference(BOUND_PAYLOAD, wkt=True)
        lines = laid_out.splitlines()
        start = lines.index('    "wkt":')
        assert lines[start + 1 : start + 4] == [
            '      PROJCS["ED_1950_UTM_Zone_31N",',
            '        GEOGCS["GCS_European_1950",',
            '          DATUM["D_European_1950",',
        ]
        assert '        AUTHORITY["EPSG",23031]]' in lines
        assert '        AUTHORITY["EPSG",1612]]' in lines
        assert lines[-3:] == ['  "type": "EBC",', '  "ver": "PE_10_9_1"', "}"]
        with pytest.raises(json.JSONDecodeError):
            json.loads(laid_out)

    def test_a_wkt_that_does_not_parse_is_left_as_it_is(self) -> None:
        laid_out = pretty_persistable_reference(
            _payload(type="LBC", wkt='GEOGCS["a",'), wkt=True
        )
        assert json.loads(laid_out)["wkt"] == 'GEOGCS["a",'

    def test_a_url_encoded_payload_is_decoded_first(self) -> None:
        assert pretty_persistable_reference(quote(PROJECTED_PAYLOAD)) == (
            pretty_persistable_reference(PROJECTED_PAYLOAD)
        )

    def test_something_that_is_not_a_json_object_is_refused(self) -> None:
        with pytest.raises(MalformedReferenceError):
            pretty_persistable_reference("[1, 2]")


class TestLookup:
    def test_by_osdu_id_with_partition_and_version(self, database: Path) -> None:
        for value in (
            PROJECTED,
            f"opendes:{PROJECTED}",
            f"{{{{NAMESPACE}}}}:{PROJECTED}:",
            f"opendes:{PROJECTED}:17",
        ):
            found = persistable_reference_from_catalog(value, database=database)
            assert found is not None, value
            assert found.payload == PROJECTED_PAYLOAD
        found = persistable_reference_from_catalog(PROJECTED, database=database)
        assert found is not None
        assert found.identifier == PROJECTED
        assert found.authority_code == "EPSG:23031"
        assert found.kind is Kind.LATE_BOUND_CRS
        assert found.stated_authority == AuthorityCode("EPSG", "23031")
        assert found.version == "PE_10_9_1"
        assert found.is_crs and not found.is_operation
        assert isinstance(found.parse(), CrsReference)

    def test_a_bare_data_id_needs_the_entity(self, database: Path) -> None:
        with pytest.raises(ValueError, match="neither"):
            persistable_reference_from_catalog(
                "Projected:EPSG::23031", database=database
            )
        found = persistable_reference_from_catalog(
            "Projected:EPSG::23031", entity="crs", database=database
        )
        assert found is not None and found.identifier == PROJECTED

    def test_by_authority_code_for_an_object_proj_ships(self, database: Path) -> None:
        found = persistable_reference_from_catalog("EPSG:1612", database=database)
        assert found is not None
        assert found.identifier == TRANSFORMATION
        assert found.is_operation
        assert (
            persistable_reference_from_catalog("epsg:1612", database=database) == found
        )

    def test_the_entity_limits_where_a_code_is_looked_for(self, database: Path) -> None:
        assert (
            persistable_reference_from_catalog(
                "EPSG:1612", entity="crs", database=database
            )
            is None
        )
        assert (
            persistable_reference_from_catalog(
                TRANSFORMATION, entity="crs", database=database
            )
            is None
        )
        found = persistable_reference_from_catalog(
            "EPSG:1612", entity="operation", database=database
        )
        assert found is not None

    def test_by_crs(self, database: Path) -> None:
        found = persistable_reference_from_catalog(
            CRS.from_epsg(23031), database=database
        )
        assert found is not None and found.identifier == PROJECTED
        unidentified = CRS.from_proj4("+proj=utm +zone=31 +ellps=intl")
        assert (
            persistable_reference_from_catalog(unidentified, database=database) is None
        )
        assert "names no catalogue record" in missing_reference_reason(
            unidentified, database=database
        )

    def test_nothing_recorded_is_none_not_an_error(self, database: Path) -> None:
        assert (
            persistable_reference_from_catalog("EPSG:23032", database=database) is None
        )
        assert persistable_reference_from_catalog(GEOCENTRIC, database=database) is None
        assert (
            persistable_reference_from_catalog(f"{PROJECTED}x", database=database)
            is None
        )

    def test_something_that_names_nothing_is_refused(self, database: Path) -> None:
        with pytest.raises(ValueError, match="neither"):
            persistable_reference_from_catalog("not an id at all", database=database)
        with pytest.raises(ValueError, match="entity"):
            persistable_reference_from_catalog(
                "EPSG:1612", entity="crss", database=database
            )  # type: ignore[arg-type]

    def test_a_database_without_references_answers_with_nothing(self) -> None:
        stock = installed_proj_db()
        assert persistable_reference_from_catalog("EPSG:23031", database=stock) is None
        assert persistable_references_from_catalog(database=stock) == ()
        assert "records no persistable references" in missing_reference_reason(
            "EPSG:23031", database=stock
        )

    def test_a_database_that_does_not_exist_answers_with_nothing(
        self, tmp_path: Path
    ) -> None:
        absent = tmp_path / "absent.db"
        assert persistable_reference_from_catalog("EPSG:23031", database=absent) is None
        assert missing_reference_reason("EPSG:23031", database=absent) == (
            f"{absent} does not exist"
        )


class TestSeveralRecordsForOneObject:
    def test_the_same_reference_twice_is_one_answer(self, tmp_path: Path) -> None:
        database = _write(tmp_path / "osdu", geographic_again=GEOGRAPHIC_PAYLOAD)
        found = persistable_reference_from_catalog("EPSG:4326", database=database)
        assert found is not None and found.identifier == GEOGRAPHIC

    def test_different_references_are_ambiguous(self, tmp_path: Path) -> None:
        other = GEOGRAPHIC_PAYLOAD.replace("PE_10_9_1", "PE_10_10_0")
        database = _write(tmp_path / "osdu", geographic_again=other)
        with pytest.raises(AmbiguousCatalogReferenceError, match="OSDU id"):
            persistable_reference_from_catalog("EPSG:4326", database=database)
        found = persistable_reference_from_catalog(GEOGRAPHIC_AGAIN, database=database)
        assert found is not None and found.payload == other

    def test_an_active_record_wins_over_an_inactive_one(self, tmp_path: Path) -> None:
        other = GEOGRAPHIC_PAYLOAD.replace("PE_10_9_1", "PE_10_10_0")
        database = _write(
            tmp_path / "osdu", geographic_again=other, geographic_again_deprecated=1
        )
        found = persistable_reference_from_catalog("EPSG:4326", database=database)
        assert found is not None and found.identifier == GEOGRAPHIC


class TestListing:
    def test_every_reference_by_identifier(self, database: Path) -> None:
        assert [
            reference.identifier
            for reference in persistable_references_from_catalog(database=database)
        ] == sorted([PROJECTED, GEOGRAPHIC, TRANSFORMATION])

    def test_filters(self, database: Path) -> None:
        def listed(**filters: object) -> set[str]:
            return {
                reference.identifier
                for reference in persistable_references_from_catalog(
                    database=database, **filters
                )  # type: ignore[arg-type]
            }

        assert listed(entity="operation") == {TRANSFORMATION}
        assert listed(entity="crs") == {PROJECTED, GEOGRAPHIC}
        assert listed(kinds=[Kind.TRANSFORMATION]) == {TRANSFORMATION}
        assert listed(kinds=["lbc"]) == {PROJECTED, GEOGRAPHIC}
        assert listed(authorities=["osdu"]) == set()
        assert listed(authorities=["EPSG"]) == {PROJECTED, GEOGRAPHIC, TRANSFORMATION}

    def test_inactive_records_can_be_left_out(self, tmp_path: Path) -> None:
        database = _write(
            tmp_path / "osdu",
            geographic_again=GEOGRAPHIC_PAYLOAD,
            geographic_again_deprecated=1,
        )
        everything = persistable_references_from_catalog(database=database)
        active = persistable_references_from_catalog(
            database=database, include_deprecated=False
        )
        assert len(everything) == 4
        assert GEOGRAPHIC_AGAIN not in {reference.identifier for reference in active}


class TestMissingReasons:
    def test_a_record_that_states_none(self, database: Path) -> None:
        for value in ("EPSG:4978", GEOCENTRIC):
            assert missing_reference_reason(value, database=database) == (
                f"the catalogue record {GEOCENTRIC!r} states no persistableReference"
            )

    def test_an_object_no_record_names(self, database: Path) -> None:
        assert "no record of the catalogue" in missing_reference_reason(
            "EPSG:23032", database=database
        )

    def test_an_id_not_recorded(self, database: Path) -> None:
        assert "records no OSDU id" in missing_reference_reason(
            f"{PROJECTED}x", database=database
        )

    def test_an_id_of_the_wrong_kind_of_record(self, database: Path) -> None:
        assert "OSDU id of a coordinate transformation, not of a CRS" in (
            missing_reference_reason(TRANSFORMATION, entity="crs", database=database)
        )

    def test_the_active_database_by_default(self, active: Path) -> None:
        assert "records no OSDU id" in missing_reference_reason(f"{PROJECTED}x")
        assert "states no persistableReference" in missing_reference_reason("EPSG:4978")


class TestConvenience:
    def test_a_crs_finds_its_reference_in_the_active_database(
        self, active: Path
    ) -> None:
        for value in (f"opendes:{PROJECTED}", "EPSG:23031", 23031):
            crs = CoordinateReferenceSystem.from_user_input(value)
            found = crs.persistable_reference_from_catalog()
            assert found is not None, value
            assert found.identifier == PROJECTED
        geocentric = CoordinateReferenceSystem.from_user_input("EPSG:4978")
        assert geocentric.persistable_reference_from_catalog() is None

    def test_a_crs_can_be_looked_up_in_another_database(self, database: Path) -> None:
        crs = CoordinateReferenceSystem.from_user_input("EPSG:23031")
        found = crs.persistable_reference_from_catalog(database=database)
        assert found is not None and found.identifier == PROJECTED

    def test_an_applied_operation_finds_its_reference(self, active: Path) -> None:
        from geodetic_engine.geodesy import transform

        for operation in ("EPSG:1612", f"opendes:{TRANSFORMATION}"):
            result = transform(
                "EPSG:23031", "EPSG:4326", [(500000.0, 6600000.0)], operation=operation
            )
            found = result.operation.persistable_reference_from_catalog()
            assert found is not None, operation
            assert found.identifier == TRANSFORMATION


class TestExport:
    def test_json_holds_each_reference_verbatim(self, database: Path) -> None:
        stream = io.StringIO()
        assert (
            export_persistable_references_from_catalog(stream, database=database) == 3
        )
        entries = json.loads(stream.getvalue())
        assert [entry["identifier"] for entry in entries] == sorted(
            [PROJECTED, GEOGRAPHIC, TRANSFORMATION]
        )
        by_id = {entry["identifier"]: entry for entry in entries}
        assert by_id[PROJECTED] == {
            "identifier": PROJECTED,
            "table_name": "projected_crs",
            "auth_name": "EPSG",
            "code": "23031",
            "deprecated": False,
            "kind": "LBC",
            "name": None,
            "stated_authority": "EPSG:23031",
            "version": "PE_10_9_1",
            "sha256": hashlib.sha256(PROJECTED_PAYLOAD.encode()).hexdigest(),
            "source": "CRS_CT.json",
            "persistable_reference": PROJECTED_PAYLOAD,
        }

    def test_pretty_json_embeds_each_reference_as_an_object(
        self, database: Path
    ) -> None:
        stream = io.StringIO()
        export_persistable_references_from_catalog(
            stream, pretty=True, database=database
        )
        entries = json.loads(stream.getvalue())
        assert all(
            isinstance(entry["persistable_reference"], dict) for entry in entries
        )

    def test_pretty_wkt_is_for_reading(self, database: Path) -> None:
        stream = io.StringIO()
        export_persistable_references_from_catalog(
            stream, wkt=True, values=["EPSG:1612"], database=database
        )
        text = stream.getvalue()
        assert '      "wkt":\n        GEOGTRAN["ED_1950_To_WGS_1984_23",' in text
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)

    def test_jsonl_is_one_entry_a_line(self, database: Path) -> None:
        stream = io.StringIO()
        export_persistable_references_from_catalog(
            stream, format="jsonl", database=database
        )
        lines = stream.getvalue().splitlines()
        assert len(lines) == 3
        assert {json.loads(line)["persistable_reference"] for line in lines} == {
            PROJECTED_PAYLOAD,
            GEOGRAPHIC_PAYLOAD,
            TRANSFORMATION_PAYLOAD,
        }

    def test_csv_round_trips_the_payload(self, database: Path, tmp_path: Path) -> None:
        target = tmp_path / "references.csv"
        assert (
            export_persistable_references_from_catalog(
                target, format="csv", entity="operation", database=database
            )
            == 1
        )
        with target.open(encoding="utf-8", newline="") as stream:
            (row,) = list(csv.DictReader(stream))
        assert row["identifier"] == TRANSFORMATION
        assert row["persistable_reference"] == TRANSFORMATION_PAYLOAD
        assert row["deprecated"] == "False"

    def test_named_values_without_a_reference_say_why(self, database: Path) -> None:
        stream = io.StringIO()
        written = export_persistable_references_from_catalog(
            stream,
            values=["EPSG:23031", "EPSG:4978", "EPSG:23032"],
            database=database,
        )
        assert written == 1
        entries = json.loads(stream.getvalue())
        assert [entry["query"] for entry in entries] == [
            "EPSG:23031",
            "EPSG:4978",
            "EPSG:23032",
        ]
        assert entries[0]["identifier"] == PROJECTED
        assert entries[1]["persistable_reference"] is None
        assert "states no persistableReference" in entries[1]["reason"]
        assert "no record of the catalogue" in entries[2]["reason"]

    def test_named_values_in_csv_carry_a_reason_column(self, database: Path) -> None:
        stream = io.StringIO()
        export_persistable_references_from_catalog(
            stream, format="csv", values=["EPSG:4978"], database=database
        )
        (row,) = list(csv.DictReader(io.StringIO(stream.getvalue())))
        assert row["query"] == "EPSG:4978"
        assert row["persistable_reference"] == ""
        assert "states no persistableReference" in row["reason"]

    @pytest.mark.parametrize(
        "options",
        [
            {"format": "csv", "pretty": True},
            {"format": "jsonl", "wkt": True},
            {"values": ["EPSG:23031"], "kinds": ["LBC"]},
            {"values": ["EPSG:23031"], "include_deprecated": False},
            {"format": "xml"},
        ],
    )
    def test_contradictory_options_are_refused(
        self, database: Path, options: dict[str, object]
    ) -> None:
        with pytest.raises(ValueError):
            export_persistable_references_from_catalog(
                io.StringIO(), database=database, **options
            )  # type: ignore[arg-type]


def test_a_proj_code_with_brackets_and_slashes_is_an_authority_code(
    database: Path,
) -> None:
    code = "PROJ:ETRS89_TO_ETRS89-DEU[ETRS89/DREF91/2016]"
    assert persistable_reference_from_catalog(code, database=database) is None
    assert "no record of the catalogue" in missing_reference_reason(
        code, database=database
    )


def test_an_operation_with_such_a_code_has_no_reference_rather_than_failing(
    active: Path,
) -> None:
    from geodetic_engine.geodesy.operation import AppliedOperation, OperationRoute

    applied = AppliedOperation(
        requested=None,
        auth_name="PROJ",
        code="ETRS89_TO_ETRS89-DEU[ETRS89/DREF91/2016]",
        name="ETRS89 to ETRS89-DEU",
        method_name=None,
        accuracy=None,
        route=OperationRoute.CHAINED,
    )
    assert applied.persistable_reference_from_catalog() is None
    odd = AppliedOperation(
        requested=None,
        auth_name="X",
        code="a:b",
        name="odd",
        method_name=None,
        accuracy=None,
        route=OperationRoute.CHAINED,
    )
    assert odd.persistable_reference_from_catalog() is None
