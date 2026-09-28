"""Loading the legacy service's data, which is kept for comparison, not trust."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

DATA = Path(__file__).parent / "data"
REQUESTS = sorted((DATA / "requests").glob("*.json"))


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
