"""Loading the test data: the legacy service's, kept for comparison, not trust,
and the survey report of a real well."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd

DATA = Path(__file__).parent / "data"
REQUESTS = sorted((DATA / "requests").glob("*.json"))
# Volve data set, Equinor and the Volve licence partners, Equinor Open Data Licence.
VOLVE_F1 = DATA / "volve_f1_survey.txt"


@cache
def volve_f1() -> pd.DataFrame:
    """Volve F-1's survey report: each station and what the report computed there."""
    return pd.read_csv(
        VOLVE_F1,
        sep=r"\s+",
        skiprows=45,  # the report's header, then the column names and units
        names=[
            "md",
            "inclination",
            "azimuth",
            "tvd",
            "x_offset",
            "y_offset",
            "easting",
            "northing",
            "dls",
        ],
        encoding="latin-1",
    )


@cache
def legacy(name: str) -> dict[str, Any]:
    """One table from ``legacy_fixtures.json``, with its column names."""
    tables: dict[str, Any] = json.loads(
        (DATA / "legacy_fixtures.json").read_text(encoding="utf-8")
    )
    return dict(tables[name])


def request(path: Path) -> dict[str, Any]:
    """The request body of one of the legacy service's examples."""
    body: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))["value"]
    return body
