"""The CSV survey file: read for, and written by, :class:`TrajectoryInput`.

A survey file states the settings of a trajectory input in a header of
``# key: value`` lines, then its stations in a table. A survey report, with
free text above a table whose columns are lined up with spaces, reads too.
:meth:`TrajectoryInput.from_csv` documents the format, and reads it with
:func:`read_survey_file`; :meth:`TrajectoryInput.to_csv` writes it with
:func:`format_survey_file`.
"""

from __future__ import annotations

import csv
import difflib
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, TextIO

import pandas as pd

from geodetic_engine.geodesy import CoordinateReferenceSystem
from geodetic_engine.welltrajectory.errors import InvalidInputError, UnitError
from geodetic_engine.welltrajectory.survey import (
    NorthReference,
    angle_factor,
    length_factor,
)

# A survey file's stations: each one's line, for messages, and its values.
type _Rows = list[tuple[str, list[str]]]

# Header keys, in the order format_survey_file writes them.
HEADER_KEYS = (
    "name",
    "crs",
    "wellhead_x",
    "wellhead_y",
    "wellhead_z",
    "north_reference",
    "md_unit",
    "angle_unit",
    "z_unit",
    "method",
    "md_step",
    "md_points",
)
_NUMERIC_KEYS = frozenset(("wellhead_x", "wellhead_y", "wellhead_z", "md_step"))
_WELLHEAD_KEYS = ("wellhead_x", "wellhead_y", "wellhead_z")
# The keys that are not optional settings of TrajectoryOptions.
_REQUIRED_KEYS = frozenset(("crs", "north_reference", "wellhead", *_WELLHEAD_KEYS))
# Other names a header may give each key, and "wellhead", the wellhead on one
# line.
_HEADER_SYNONYMS = {
    "name": ("well", "well_name", "wellbore", "wellbore_name"),
    "crs": ("coordinate_system", "coordinate_reference_system", "trajectory_crs"),
    "wellhead": ("origin", "surface_location", "reference_point"),
    "wellhead_x": ("x", "easting", "origin_x", "surface_x", "wellhead_easting"),
    "wellhead_y": ("y", "northing", "origin_y", "surface_y", "wellhead_northing"),
    "wellhead_z": (
        "z",
        "elevation",
        "origin_z",
        "surface_z",
        "wellhead_elevation",
        "datum_elevation",
        "kb",
        "rkb",
        "kb_elevation",
        "rkb_elevation",
    ),
    "north_reference": ("north", "north_ref", "azimuth_reference"),
    "md_unit": ("depth_unit",),
    "angle_unit": ("angular_unit",),
    "z_unit": ("elevation_unit", "vertical_unit"),
    "md_step": ("md_interval",),
}
# Header and column names are compared squeezed: casefolded, without spaces,
# underscores or punctuation, so "Wellhead X" is wellhead_x.
_SQUEEZE = re.compile(r"[\W_]+")
# Every name a header key goes by, squeezed, to the key and the name.
_HEADER_NAMES = {
    _SQUEEZE.sub("", name.casefold()): (key, name)
    for key in (*HEADER_KEYS, "wellhead")
    for name in (key, *_HEADER_SYNONYMS.get(key, ()))
}
_COLUMNS = ("md", "inclination", "azimuth")
# The names of the survey columns, squeezed. Any name starting "az" is also an
# azimuth, as AZIM_GN is.
_COLUMN_NAMES = {
    "md": frozenset(("md", "measureddepth", "depth", "dept", "mdepth")),
    "inclination": frozenset(
        ("inclination", "inc", "incl", "inclin", "devi", "deviation")
    ),
    "azimuth": frozenset(("azimuth", "azi", "azim", "az", "hazi", "direction")),
}
_DELIMITERS = (",", ";", "\t")
# The delimiter of a table whose columns are lined up with spaces.
_SPACES = " "
# Lined up with spaces, a column's name may hold single spaces; more end it.
_GAP = re.compile(r"\s{2,}")
# No two quantifiers below compete for the same characters: long lines stay linear.
# "# key: value", the key words; any other line starting with # is a comment.
_HEADER_LINE = re.compile(r"#\s*([A-Za-z][\w\s-]*):(.*)")
# "key: value" in the free text above a table, as in a survey report.
_TEXT_LINE = re.compile(r"([A-Za-z][\w\s-]*):(.*)")
# A column name with its unit in brackets: "MD (ft)", "Inc [deg]".
_UNIT_IN_NAME = re.compile(r"(.*)[(\[]([^()\[\]]*)[)\]]")
# The settings free text is read for: a report's text also gives positions and
# elevations of other things than the wellhead.
_TEXT_KEYS = frozenset(("name", "north_reference"))


@dataclass(frozen=True, slots=True)
class SurveyFile:
    """What a survey file states, with the arguments read beside it applied.

    Attributes:
        md: The measured depths.
        inclination: The inclinations.
        azimuth: The azimuths; None for an inclination-only survey.
        wellhead: The wellhead, as stated or as given.
        crs: The trajectory CRS.
        north_reference: What the azimuths are measured from, as text.
        options: The optional settings, those given over those stated.
    """

    md: list[float]
    inclination: list[float]
    azimuth: list[float] | None
    wellhead: object
    crs: Any
    north_reference: object
    options: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _Table:
    """A survey file's table, each part with its line for messages.

    Attributes:
        names: The column names.
        names_at: The line naming them.
        units: The line of units below the names, a cell per column; empty if
            there is none.
        units_at: The line of units; :attr:`names_at` if there is none.
        rows: Each station's line and values, as text.
    """

    names: list[str]
    names_at: str
    units: list[str]
    units_at: str
    rows: _Rows


def read_survey_file(
    source: str | os.PathLike[str] | Traversable | TextIO,
    *,
    wellhead: object = None,
    crs: Any = None,
    north_reference: object = None,
    md_column: str | None = None,
    inclination_column: str | None = None,
    azimuth_column: str | None = None,
    delimiter: str | None = None,
    options: Mapping[str, Any],
) -> SurveyFile:
    """Read a survey file, the arguments filling in and overriding its header.

    The arguments are those of :meth:`TrajectoryInput.from_csv`.

    Raises:
        InvalidInputError: If the file does not follow the format, a column
            cannot be found or is found twice, or a required setting is in
            neither the header nor the arguments.
        OSError: If the file cannot be read.
    """
    if delimiter is not None and len(delimiter) != 1:
        raise InvalidInputError(f"delimiter must be one character, not {delimiter!r}")
    named = {
        "md": md_column,
        "inclination": inclination_column,
        "azimuth": azimuth_column,
    }
    where, text = _read(source)
    header, table = _parse(text, where, delimiter, named)
    columns, units = _survey_columns(table, named)
    stated = _header_values(header, where)
    for key, (unit, at) in units.items():
        if key in options:
            continue
        if key not in stated:
            stated[key] = unit
        elif not _same_unit(key, stated[key], unit):
            raise InvalidInputError(
                f"{at}: the header states {key} {stated[key]!r}, but the "
                f"column names {unit!r}"
            )
    if wellhead is None:
        wellhead = _stated_wellhead(stated, where)
    crs = stated.get("crs") if crs is None else crs
    if north_reference is None:
        north_reference = stated.get("north_reference")
    required = {"crs": crs, "wellhead": wellhead, "north_reference": north_reference}
    if missing := [key for key, value in required.items() if value is None]:
        raise InvalidInputError(
            f"{where} states no {', '.join(missing)}: add each to the "
            "header, as in '# crs: EPSG:23031' or '# wellhead: 455000, "
            "6785000, 32', or pass it as an argument"
        )
    settings = {key: stated[key] for key in stated.keys() - _REQUIRED_KEYS}
    return SurveyFile(
        md=columns["md"],
        inclination=columns["inclination"],
        azimuth=columns.get("azimuth"),
        wellhead=wellhead,
        crs=crs,
        north_reference=north_reference,
        options=settings | dict(options),
    )


def format_survey_file(settings: Mapping[str, Any], stations: pd.DataFrame) -> str:
    """The text of a survey file: ``settings`` in a header, then ``stations``.

    The settings are keyed as :data:`HEADER_KEYS`, and those that are None or
    empty are left out. Numbers are written in full, so reading the file back
    gives the same values, and a CRS that is not one line of text is written
    as WKT.

    Raises:
        InvalidInputError: If a name or a unit runs over more than one line.
    """
    lines = []
    for key in HEADER_KEYS:
        value = settings.get(key)
        if key == "md_points":
            value = ", ".join(map(_number, value if value is not None else ()))
        if value is None or value == "":
            continue
        if key in _NUMERIC_KEYS:
            value = _number(value)
        elif key == "crs":
            value = _one_line_crs(value)
        lines.append(f"# {key}: {_one_line(str(value), key)}")
    lines.append(",".join(stations.columns))
    lines += [",".join(map(_number, row)) for row in stations.to_numpy()]
    return "\n".join(lines) + "\n"


def _read(
    source: str | os.PathLike[str] | Traversable | TextIO,
) -> tuple[str, str]:
    """A name for messages, and the text, of a survey file."""
    if isinstance(source, str | os.PathLike):
        source = Path(source)
    if isinstance(source, Traversable):
        data = source.read_bytes()
        try:
            return source.name, data.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Older software writes a Windows code page; Latin-1 reads any byte.
            return source.name, data.decode("latin-1")
    return str(getattr(source, "name", "the survey file")), source.read()


def _parse(
    text: str, where: str, delimiter: str | None, named: Mapping[str, str | None]
) -> tuple[dict[str, str], _Table]:
    """A survey file's header settings, as text, then its table.

    Above the table, ``#`` lines are the header and other lines free text, of
    which a ``key: value`` line giving the name or the north reference is read
    as well, the first of each, unless the header states it.
    """
    lines = [
        (f"{where}, line {number}", stripped)
        for number, line in enumerate(text.splitlines(), start=1)
        if (stripped := line.strip())
    ]
    start = _names_line(lines, where, delimiter, named)
    header: dict[str, str] = {}
    in_text: dict[str, str] = {}
    for at, line in lines[:start]:
        if line.startswith("#"):
            match = _HEADER_LINE.fullmatch(line)
            if match and (key := _header_key(match[1].rstrip(), at)):
                if key in header:
                    raise InvalidInputError(f"{at}: {key} is stated twice")
                header[key] = match[2].strip()
        elif setting := _text_setting(line, at):
            in_text.setdefault(*setting)

    names_at, line = lines[start]
    separator = delimiter or _separator(line)
    names = _labels(line, separator)
    while names and not names[-1]:
        names.pop()
    body = lines[start + 1 :]
    units: list[str] = []
    units_at = names_at
    if (
        len(body) > 1
        and body[0][1][0] != "#"
        and not any(map(_is_number, labels := _labels(body[0][1], separator)))
    ):
        (units_at, _), units = body.pop(0), labels
    rows: _Rows = []
    for at, line in body:
        if line.startswith("#"):
            raise InvalidInputError(
                f"{at}: header and comment lines go above the table"
            )
        rows.append((at, _cells(line, separator)))
    if not rows:
        raise InvalidInputError(f"{where} has no stations below its column names")
    return in_text | header, _Table(names, names_at, units, units_at, rows)


def _names_line(
    lines: list[tuple[str, str]],
    where: str,
    delimiter: str | None,
    named: Mapping[str, str | None],
) -> int:
    """Where the table starts: the first line naming the md and inclination
    columns, or else the first that is not a header or comment line."""
    candidates = [index for index, (_, line) in enumerate(lines) if line[0] != "#"]
    if not candidates:
        raise InvalidInputError(
            f"{where} has no table: below the header, a line naming the columns "
            "md, inclination and azimuth, then one line per station"
        )
    for index in candidates:
        line = lines[index][1]
        names = _labels(line, delimiter or _separator(line))
        if all(_matches(names, quantity, named[quantity]) for quantity in _COLUMNS[:2]):
            return index
    return candidates[0]


def _header_key(word: str, at: str, *, guess: bool = True) -> str | None:
    """The key a header line states, or None for a comment.

    With ``guess``, a word so close to a known key that it is likely misspelt
    is refused.
    """
    squeezed = _SQUEEZE.sub("", word.casefold())
    forms = dict.fromkeys((squeezed, squeezed.removesuffix("s")))
    for name in forms:
        if name in _HEADER_NAMES:
            return _HEADER_NAMES[name][0]
    if not guess:
        return None
    # A misspelling, not a comment: close, and about as long, as a known name.
    close = {
        _HEADER_NAMES[name][1]: None
        for form in forms
        for name in difflib.get_close_matches(form, _HEADER_NAMES, n=3, cutoff=0.8)
        if abs(len(name) - len(form)) <= 2
    }
    if close:
        raise InvalidInputError(
            f"{at}: {word!r} is not a header key; did you mean {' or '.join(close)}?"
        )
    return None


def _text_setting(line: str, at: str) -> tuple[str, str] | None:
    """The setting a line of free text states, if it is one read there.

    A value that does not read as its setting is free text like the rest, so
    ``North: 6478566.7`` states nothing.
    """
    if not (match := _TEXT_LINE.fullmatch(line)):
        return None
    key, value = _header_key(match[1].rstrip(), at, guess=False), match[2].strip()
    if key is None or key not in _TEXT_KEYS or not value:
        return None
    if key == "north_reference":
        try:
            NorthReference(value)
        except ValueError:
            return None
    return key, value


def _separator(line: str) -> str:
    """The delimiter a line uses: the commonest of comma, semicolon and tab, or
    else spaces."""
    common = max(_DELIMITERS, key=line.count)
    return common if common in line else _SPACES


def _labels(line: str, delimiter: str) -> list[str]:
    """The column names, or units, a line gives."""
    if delimiter == _SPACES and len(labels := _GAP.split(line)) > 1:
        return labels
    return _cells(line, delimiter)


def _cells(line: str, delimiter: str) -> list[str]:
    if delimiter == _SPACES:
        return line.split()
    reader = csv.reader([line], delimiter=delimiter, skipinitialspace=True)
    return [cell.strip() for cell in next(reader)]


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _survey_columns(
    table: _Table, named: Mapping[str, str | None]
) -> tuple[dict[str, list[float]], dict[str, tuple[str, str]]]:
    """The md, inclination and azimuth columns, and the units the table gives.

    The units are keyed by the setting they give, with the line giving them.
    """
    names, at, rows = table.names, table.names_at, table.rows
    found = {
        quantity: index
        for quantity in _COLUMNS
        if (index := _column(names, quantity, named[quantity], at)) is not None
    }
    for quantity in _COLUMNS[:2]:
        if quantity not in found:
            hint = (
                ", or the character separating them with delimiter"
                if len(names) == 1
                else ""
            )
            raise InvalidInputError(
                f"{at}: the table has no {quantity} column among "
                f"{', '.join(map(repr, names))}; name it with {quantity}_column"
                f"{hint}"
            )
    if len(set(found.values())) < len(found):
        raise InvalidInputError(f"{at}: one column is named for two quantities")
    columns: dict[str, list[float]] = {quantity: [] for quantity in found}
    for row_at, cells in rows:
        if len(cells) < len(names) or any(cells[len(names) :]):
            raise InvalidInputError(
                f"{row_at}: {len(cells)} values for {len(names)} columns"
            )
        for quantity, index in found.items():
            columns[quantity].append(_float(cells[index], quantity, row_at))

    units: dict[str, tuple[str, str]] = {}
    if md_unit := _column_unit(table, found["md"], length_factor):
        units["md_unit"] = md_unit
    angle_units = [
        unit
        for quantity in _COLUMNS[1:]
        if quantity in found
        and (unit := _column_unit(table, found[quantity], angle_factor))
    ]
    if len({angle_factor(unit) for unit, _ in angle_units}) > 1:
        raise InvalidInputError(
            f"{angle_units[1][1]}: the inclinations are in {angle_units[0][0]!r} "
            f"and the azimuths in {angle_units[1][0]!r}; a survey has one angle unit"
        )
    if angle_units:
        units["angle_unit"] = angle_units[0]
    return columns, units


def _column_unit(
    table: _Table, index: int, factor: Callable[[str], float]
) -> tuple[str, str] | None:
    """A column's unit, with the line giving it: in brackets after its name, or
    in the line of units below the names."""
    name = table.names[index]
    try:
        in_name = _unit_in_name(name, factor)
    except UnitError as error:
        raise UnitError(f"{table.names_at}: {error}") from error
    cell = table.units[index].strip("()[] ") if index < len(table.units) else ""
    if not cell:
        return (in_name, table.names_at) if in_name else None
    # The unit may be followed by its datum, as in "m RKB".
    in_line = next(
        (unit for unit in (cell, cell.split()[0]) if _is_unit(unit, factor)), None
    )
    if in_line is None:
        raise UnitError(f"{table.units_at}: {cell!r} is not a unit of {name!r}")
    if in_name and not math.isclose(factor(in_name), factor(in_line)):
        raise InvalidInputError(
            f"{table.units_at}: {name!r} is in {in_name!r}, not {in_line!r}"
        )
    return (in_name, table.names_at) if in_name else (in_line, table.units_at)


def _is_unit(unit: str, factor: Callable[[str], float]) -> bool:
    try:
        factor(unit)
    except UnitError:
        return False
    return True


def _matches(names: list[str], quantity: str, named: str | None) -> list[int]:
    """Each column that could be ``quantity``: the one named, or else by its
    usual names."""
    bare = [_bare(name) for name in names]
    if named is not None:
        wanted = named.strip().casefold()
        return [
            index
            for index, name in enumerate(names)
            if wanted in (name.casefold(), bare[index].casefold())
        ]
    return [
        index
        for index, name in enumerate(bare)
        if _is_usual(quantity, _SQUEEZE.sub("", name.casefold()))
    ]


def _column(names: list[str], quantity: str, named: str | None, at: str) -> int | None:
    """Where the ``quantity`` column is: the one named, or else by its usual names."""
    found = _matches(names, quantity, named)
    if not found:
        if named is not None:
            raise InvalidInputError(
                f"{at}: the table has no column {named!r}; its columns are "
                f"{', '.join(map(repr, names))}"
            )
        return None
    if len(found) > 1:
        candidates = [names[index] for index in found]
        if len({name.casefold() for name in candidates}) == 1:
            raise InvalidInputError(f"{at}: {candidates[0]!r} is named twice")
        raise InvalidInputError(
            f"{at}: {', '.join(map(repr, candidates))} could each be the "
            f"{quantity} column; name the one to read with {quantity}_column"
        )
    return found[0]


def _is_usual(quantity: str, squeezed: str) -> bool:
    return squeezed in _COLUMN_NAMES[quantity] or (
        quantity == "azimuth" and squeezed.startswith("az")
    )


def _bare(name: str) -> str:
    """A column's name without the unit in brackets after it."""
    return match[1].strip() if (match := _UNIT_IN_NAME.fullmatch(name)) else name


def _unit_in_name(name: str, factor: Callable[[str], float]) -> str | None:
    """The annotated unit, refusing an unknown unit or the wrong quantity."""
    if not (match := _UNIT_IN_NAME.fullmatch(name)):
        return None
    unit = match[2].strip()
    factor(unit)
    return unit


def _same_unit(key: str, stated: str, named: str) -> bool:
    factor = length_factor if key == "md_unit" else angle_factor
    return math.isclose(factor(stated), factor(named))


def _header_values(header: Mapping[str, str], where: str) -> dict[str, Any]:
    """The header's settings, with numbers read as numbers."""
    values: dict[str, Any] = {}
    for key, text in header.items():
        at = f"{where}, header {key}"
        if key in _NUMERIC_KEYS:
            values[key] = _float(text, key, at)
        elif key in ("md_points", "wellhead"):
            values[key] = [
                _float(value, key, at) for value in re.split(r"[,;\s]+", text) if value
            ]
        else:
            values[key] = text
    return values


def _stated_wellhead(stated: Mapping[str, Any], where: str) -> tuple[float, ...] | None:
    """The wellhead a survey file's header states, if it does."""
    parts = [key for key in _WELLHEAD_KEYS if key in stated]
    if "wellhead" in stated:
        if parts:
            raise InvalidInputError(
                f"{where} states the wellhead twice: on one line, and as "
                f"{', '.join(parts)}"
            )
        if len(stated["wellhead"]) not in (2, 3):
            raise InvalidInputError(
                f"{where}, header wellhead: x, y and optionally z, not "
                f"{len(stated['wellhead'])} values"
            )
        return tuple(stated["wellhead"])
    if "wellhead_x" in stated and "wellhead_y" in stated:
        return (
            stated["wellhead_x"],
            stated["wellhead_y"],
            stated.get("wellhead_z", 0.0),
        )
    if "wellhead_x" in stated or "wellhead_y" in stated:
        raise InvalidInputError(f"{where} states only one of wellhead_x and wellhead_y")
    return None


def _float(text: str, column: str, at: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise InvalidInputError(f"{at}: {text!r} in {column} is not a finite number")
    return value


def _number(value: float) -> str:
    """A float written so that reading it back gives the same float."""
    return repr(float(value))


def _one_line(value: str, key: str) -> str:
    if len(value.splitlines()) > 1:
        raise InvalidInputError(f"the {key} {value!r} runs over more than one line")
    return value.strip()


def _one_line_crs(crs: Any) -> str:
    """The CRS as given, if it is one line of text, or else as WKT."""
    if isinstance(crs, str) and len(crs.strip().splitlines()) == 1:
        return crs.strip()
    return CoordinateReferenceSystem.from_user_input(crs).crs.to_wkt()
