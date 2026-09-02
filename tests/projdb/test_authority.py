"""Authority preference rows, which decide whose operations PROJ considers."""

from __future__ import annotations

from pathlib import Path

from geodetic_engine.projdb.authority import preference_rows
from geodetic_engine.projdb.config import AuthorityPreference
from tests.projdb.conftest import AUTHORITY, make_config

# A representative subset of what PROJ ships.
STOCK = {
    ("any", "EPSG"): "PROJ,EPSG,any",
    ("EPSG", "EPSG"): "PROJ,EPSG,NKG",
    ("NKG", "EPSG"): "NKG,PROJ,EPSG",
}


def _rows(base: Path, out: Path, **overrides: object) -> dict[tuple[str, str], str]:
    config = make_config(base, out, **overrides)
    return {
        (row["source_auth_name"], row["target_auth_name"]): row["allowed_authorities"]
        for row in preference_rows(config, STOCK)
    }


def test_custom_authority_is_preferred_for_its_own_pairs(
    base_proj_db: Path, output_db: Path
) -> None:
    rows = _rows(base_proj_db, output_db)
    assert rows[(AUTHORITY, "any")] == f"{AUTHORITY},PROJ,EPSG"
    assert rows[("any", AUTHORITY)] == f"{AUTHORITY},PROJ,EPSG"
    assert rows[(AUTHORITY, "EPSG")] == f"{AUTHORITY},PROJ,EPSG"


def test_custom_first_extends_rather_than_replaces_stock_rules(
    base_proj_db: Path, output_db: Path
) -> None:
    """Custom operations become candidates without displacing EPSG ordering."""
    rows = _rows(base_proj_db, output_db)
    assert rows[("EPSG", "EPSG")] == f"PROJ,EPSG,NKG,{AUTHORITY}"
    assert rows[("NKG", "EPSG")] == f"NKG,PROJ,EPSG,{AUTHORITY}"


def test_custom_only_leaves_other_authorities_untouched(
    base_proj_db: Path, output_db: Path
) -> None:
    rows = _rows(
        base_proj_db,
        output_db,
        authority_preference=AuthorityPreference.CUSTOM_ONLY,
    )
    assert (AUTHORITY, "any") in rows
    assert ("EPSG", "EPSG") not in rows


def test_none_writes_nothing(base_proj_db: Path, output_db: Path) -> None:
    """The operator can opt out of influencing operation selection entirely."""
    assert (
        _rows(base_proj_db, output_db, authority_preference=AuthorityPreference.NONE)
        == {}
    )


def test_multiple_custom_authorities_are_all_listed(
    base_proj_db: Path, output_db: Path
) -> None:
    rows = _rows(base_proj_db, output_db, authorities=frozenset({"Alpha", "Beta"}))
    assert rows[("Alpha", "any")] == "Alpha,Beta,PROJ,EPSG"
    assert rows[("EPSG", "EPSG")] == "PROJ,EPSG,NKG,Alpha,Beta"


def test_authority_names_are_never_duplicated(
    base_proj_db: Path, output_db: Path
) -> None:
    """A stock rule that already names the authority is left alone."""
    rows = _rows(base_proj_db, output_db, authorities=frozenset({"NKG"}))
    assert ("EPSG", "EPSG") not in rows
    assert rows[("NKG", "any")] == "NKG,PROJ,EPSG"
    assert rows[("any", "EPSG")] == "PROJ,EPSG,any,NKG"
