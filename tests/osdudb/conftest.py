"""Shared fixtures for the osdudb tests.

Catalogues are written by hand rather than carved out of a real OSDU manifest,
so no organisation's CRS definitions end up in the repository and each test
states exactly the records it depends on.

The WKT in these fixtures is real WKT2: it is what the builder reads a CRS's
ellipsoid, axes and parameters out of, so a fake would test nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyproj
import pytest

from geodetic_engine.osdudb.catalog import OsduCatalog
from geodetic_engine.osdudb.config import OsduBuildConfig

AUTHORITY = "OSDU"

# Real EPSG codes present in the proj.db shipped with PROJ, so catalogue records
# can reference them the way OSDU's own records do.
EPSG_ELLIPSOIDAL_2D_CS = "6422"
EPSG_CARTESIAN_2D_CS = "4400"
EPSG_WORLD_EXTENT = "1262"
EPSG_SCOPE = "1026"

CRS_KIND = "osdu:wks:reference-data--CoordinateReferenceSystem:1.2.0"
TRANSFORMATION_KIND = "osdu:wks:reference-data--CoordinateTransformation:1.2.0"


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run each test in an empty directory.

    Config file discovery looks in the working directory, so a stray
    geodetic-osdudb.toml in the repository would otherwise leak into tests.
    """
    working = tmp_path / "cwd"
    working.mkdir()
    monkeypatch.chdir(working)


@pytest.fixture
def base_proj_db() -> Path:
    """Path to the official proj.db of the linked PROJ."""
    return Path(pyproj.datadir.get_data_dir()) / "proj.db"


@pytest.fixture
def output_db(tmp_path: Path) -> Path:
    return tmp_path / "custom" / "proj.db"


def authority_code(authority: str, code: str | int, **extra: Any) -> dict[str, Any]:
    """An OSDU cross reference."""
    return {"AuthorityCode": {"Authority": authority, "Code": code}, **extra}


def usage(
    *,
    scope_code: str | int | None = EPSG_SCOPE,
    extent_code: str | int | None = EPSG_WORLD_EXTENT,
    bounds: tuple[float, float, float, float] = (-90.0, 90.0, -180.0, 180.0),
) -> dict[str, Any]:
    """One usage entry, optionally with an extent that carries no code."""
    south, north, west, east = bounds
    extent: dict[str, Any] = {
        "Name": "World",
        "Description": "World.",
        "BoundingBoxSouthBoundLatitude": south,
        "BoundingBoxNorthBoundLatitude": north,
        "BoundingBoxWestBoundLongitude": west,
        "BoundingBoxEastBoundLongitude": east,
    }
    if extent_code is not None:
        extent |= {"AuthorityCode": {"Authority": "EPSG", "Code": extent_code}}
    scope: dict[str, Any] = {"Name": "Geodesy."}
    if scope_code is not None:
        scope |= {"AuthorityCode": {"Authority": "EPSG", "Code": scope_code}}
    return {"Extent": extent, "Scope": scope}


def crs_record(**data: Any) -> dict[str, Any]:
    """One ``reference-data--CoordinateReferenceSystem`` entry.

    Fields are given under their real OSDU names, so a test overriding one reads
    the same way the manifest does.
    """
    fields: dict[str, Any] = {
        "CodeSpace": AUTHORITY,
        "InactiveIndicator": False,
        "Usages": [usage()],
    } | data
    fields.setdefault("CodeAsNumber", int(fields["Code"]))
    return {"kind": CRS_KIND, "data": fields}


def transformation_record(**data: Any) -> dict[str, Any]:
    """One ``reference-data--CoordinateTransformation`` entry."""
    fields: dict[str, Any] = {
        "CodeSpace": AUTHORITY,
        "CoordinateTransformationType": "Transformation",
        "Kind": "Transformation",
        "InactiveIndicator": False,
        "Usages": [usage()],
    } | data
    fields.setdefault("CodeAsNumber", int(fields["Code"]))
    return {"kind": TRANSFORMATION_KIND, "data": fields}


def make_catalog(*entries: dict[str, Any], path: Path | None = None) -> OsduCatalog:
    """Index a hand written manifest."""
    return OsduCatalog.from_document(
        {"kind": "osdu:wks:Manifest:1.0.0", "ReferenceData": list(entries)}, path=path
    )


def write_catalog(path: Path, *entries: dict[str, Any]) -> Path:
    """Write a hand written manifest to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"kind": "osdu:wks:Manifest:1.0.0", "ReferenceData": list(entries)}),
        encoding="utf-8",
    )
    return path


def make_config(
    base_proj_db: Path, output_db: Path, catalog: Path, **overrides: Any
) -> OsduBuildConfig:
    """A configuration pointing at a hand written catalogue."""
    defaults: dict[str, Any] = {
        "catalog": catalog,
        "authorities": frozenset({AUTHORITY}),
        "base_proj_db": base_proj_db,
        "output_db": output_db,
    }
    return OsduBuildConfig(**(defaults | overrides))


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    """Where a test's catalogue is written."""
    return tmp_path / "CRS_CT.json"


# A geographic 2D CRS on a datum, ellipsoid and prime meridian that no EPSG
# dataset defines, so importing it exercises the whole WKT extraction.
CUSTOM_GEOGRAPHIC_WKT = """
GEOGCRS["Example 2020",
    DATUM["Example Datum 2020",
        ELLIPSOID["Example 1980",6378137,298.257222101,
            LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["OSDU",7100]],
        ID["OSDU",6100]],
    PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],
        ID["EPSG",8901]],
    CS[ellipsoidal,2,ID["EPSG",6422]],
    AXIS["Geodetic latitude (Lat)",north],
    AXIS["Geodetic longitude (Lon)",east],
    ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],
    ID["OSDU",4100]]
"""

# A projected CRS on that geographic CRS, so the conversion has to be read out
# of the WKT as well.
CUSTOM_PROJECTED_WKT = """
PROJCRS["Example 2020 / UTM zone 31N",
    BASEGEOGCRS["Example 2020",
        DATUM["Example Datum 2020",
            ELLIPSOID["Example 1980",6378137,298.257222101,
                LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["OSDU",7100]],
            ID["OSDU",6100]],
        ID["OSDU",4100]],
    CONVERSION["Example UTM zone 31N",
        METHOD["Transverse Mercator",ID["EPSG",9807]],
        PARAMETER["Latitude of natural origin",0,
            ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],ID["EPSG",8801]],
        PARAMETER["Longitude of natural origin",3,
            ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],ID["EPSG",8802]],
        PARAMETER["Scale factor at natural origin",0.9996,
            SCALEUNIT["unity",1,ID["EPSG",9201]],ID["EPSG",8805]],
        PARAMETER["False easting",500000,
            LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",8806]],
        PARAMETER["False northing",0,
            LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",8807]],
        ID["OSDU",17100]],
    CS[Cartesian,2,ID["EPSG",4400]],
    AXIS["Easting (E)",east],
    AXIS["Northing (N)",north],
    LENGTHUNIT["metre",1,ID["EPSG",9001]],
    ID["OSDU",32100]]
"""

# A seven parameter transformation from that CRS to WGS 84, whose rotation and
# scale units PROJ exports without codes.
CUSTOM_TRANSFORMATION_WKT = """
COORDINATEOPERATION["Example 2020 to WGS 84 (1)",
    VERSION["EXAMPLE-Test"],
    SOURCECRS[GEOGCRS["Example 2020",
        DATUM["Example Datum 2020",
            ELLIPSOID["Example 1980",6378137,298.257222101,
                LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["OSDU",7100]],
            ID["OSDU",6100]],
        CS[ellipsoidal,2,ID["EPSG",6422]],
        AXIS["Geodetic latitude (Lat)",north],
        AXIS["Geodetic longitude (Lon)",east],
        ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],
        ID["OSDU",4100]]],
    TARGETCRS[GEOGCRS["WGS 84",
        DATUM["World Geodetic System 1984",
            ELLIPSOID["WGS 84",6378137,298.257223563,
                LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",7030]],
            ID["EPSG",6326]],
        CS[ellipsoidal,2,ID["EPSG",6422]],
        AXIS["Geodetic latitude (Lat)",north],
        AXIS["Geodetic longitude (Lon)",east],
        ANGLEUNIT["degree",0.0174532925199433,ID["EPSG",9102]],
        ID["EPSG",4326]]],
    METHOD["Position Vector transformation (geog2D domain)",ID["EPSG",9606]],
    PARAMETER["X-axis translation",1.5,
        LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",8605]],
    PARAMETER["Y-axis translation",-2.5,
        LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",8606]],
    PARAMETER["Z-axis translation",3.5,
        LENGTHUNIT["metre",1,ID["EPSG",9001]],ID["EPSG",8607]],
    PARAMETER["X-axis rotation",0.1,
        ANGLEUNIT["arc-second",4.84813681109536E-06,ID["EPSG",9104]],ID["EPSG",8608]],
    PARAMETER["Y-axis rotation",0.2,
        ANGLEUNIT["arc-second",4.84813681109536E-06,ID["EPSG",9104]],ID["EPSG",8609]],
    PARAMETER["Z-axis rotation",0.3,
        ANGLEUNIT["arc-second",4.84813681109536E-06,ID["EPSG",9104]],ID["EPSG",8610]],
    PARAMETER["Scale difference",4.5,
        SCALEUNIT["parts per million",1E-06,ID["EPSG",9202]],ID["EPSG",8611]],
    OPERATIONACCURACY[1.0],
    ID["OSDU",9100]]
"""
