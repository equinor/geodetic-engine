"""The ESRI WKT reader and writer, which PROJ provides for CRSs but not for CTs."""

from __future__ import annotations

import pytest

from geodetic_engine.persistablereference import MalformedReferenceError
from geodetic_engine.persistablereference.esriwkt import Node, Word, read, write

GEOGTRAN = (
    'GEOGTRAN["ED_1950_To_WGS_1984_23",'
    'GEOGCS["GCS_European_1950",DATUM["D_European_1950",'
    'SPHEROID["International_1924",6378388.0,297.0]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
    'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'METHOD["Position_Vector"],'
    'PARAMETER["X_Axis_Translation",-116.641],'
    'PARAMETER["Scale_Difference",-3.52],'
    'OPERATIONACCURACY[1.0],AUTHORITY["EPSG",1612]]'
)


def test_reads_the_tree_a_transformation_describes() -> None:
    """A GEOGTRAN is read as its keyword, name and nested elements."""
    node = read(GEOGTRAN)
    assert node.keyword == "GEOGTRAN"
    assert node.name == "ED_1950_To_WGS_1984_23"
    assert len(node.nodes("GEOGCS")) == 2
    assert node.node("METHOD") is not None
    assert node.node("METHOD").name == "Position_Vector"


def test_nested_elements_are_found_by_position_not_by_pattern() -> None:
    """Which spheroid is which follows from the tree, not from counting matches."""
    source, target = read(GEOGTRAN).nodes("GEOGCS")
    assert source.node("DATUM").node("SPHEROID").values == (6378388.0, 297.0)
    assert target.node("DATUM").node("SPHEROID").name == "WGS_1984"


def test_writing_reproduces_the_input_exactly() -> None:
    """A payload read here is written back byte for byte."""
    assert write(read(GEOGTRAN)) == GEOGTRAN


def test_integers_stay_integers() -> None:
    """ESRI writes an authority code without a decimal point and a value with one."""
    assert write(Node("AUTHORITY", ("EPSG", 23032))) == 'AUTHORITY["EPSG",23032]'
    assert write(Node("PARAMETER", ("Scale", 1.0))) == 'PARAMETER["Scale",1.0]'


def test_axis_directions_are_unquoted() -> None:
    """An axis direction is a bare word, and putting quotes round it is invalid WKT."""
    node = read('AXIS["Lat",north]')
    assert node.children == ("Lat", Word("north"))
    assert node.name == "Lat"
    assert write(node) == 'AXIS["Lat",north]'


def test_a_direction_beginning_with_e_is_not_a_number() -> None:
    """``east`` opens with an exponent marker and is still a direction."""
    assert read('AXIS["Lon",east]').children[1] == Word("east")


def test_quotes_and_brackets_inside_a_name_survive() -> None:
    """A quoted name is read as text, however much like WKT its contents look."""
    node = read('GEOGCS["a \\"b\\" [c]",SPHEROID["s",1.0,2.0]]')
    assert node.name == 'a "b" [c]'
    assert write(node) == 'GEOGCS["a \\"b\\" [c]",SPHEROID["s",1.0,2.0]]'


def test_descendants_reach_every_depth() -> None:
    """Every spheroid is found wherever it sits."""
    assert len(read(GEOGTRAN).descendants("SPHEROID")) == 2


@pytest.mark.parametrize(
    "broken",
    [
        "",
        "GEOGCS",
        "GEOGCS[",
        'GEOGCS["unterminated',
        'GEOGCS["a"] trailing',
        "[1,2]",
    ],
)
def test_unreadable_wkt_is_refused(broken: str) -> None:
    """Text that is not one well-formed element is an error, not a partial tree."""
    with pytest.raises(MalformedReferenceError):
        read(broken)


def test_deeply_nested_wkt_is_refused_before_recursion_exhaustion() -> None:
    with pytest.raises(MalformedReferenceError, match="nesting"):
        read("GEOGTRAN[" + "NODE[" * 600 + "0" + "]" * 601)


def test_oversized_bare_wkt_is_refused() -> None:
    with pytest.raises(MalformedReferenceError, match="exceeds"):
        read('GEOGTRAN["' + "x" * (1 << 20) + '"]')


@pytest.mark.parametrize("number", ["1e309", "-1e309", "9" * 400])
def test_nonfinite_numeric_values_are_refused(number: str) -> None:
    with pytest.raises(MalformedReferenceError, match="finite"):
        read(f'PARAMETER["X_Axis_Translation",{number}]')
