"""The CSV survey file: read for, and written by, :class:`TrajectoryInput`.

A survey file states the settings of a trajectory input in a header of
``# key: value`` lines, then its stations in a table.
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
from geodetic_engine.welltrajectory.survey import angle_factor, length_factor

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
_NORTH_NAMES = {"grid": "GN", "gridnorth": "GN", "true": "TN", "truenorth": "TN"}
_DELIMITERS = (",", ";", "\t")
# "# key: value", the key words; any other line starting with # is a comment.
_HEADER_LINE = re.compile(r"#\s*([A-Za-z][\w \-]*?)\s*:(.*)")
# A column name with its unit in brackets: "MD (ft)", "Inc [deg]".
_UNIT_IN_NAME = re.compile(r"(.*?)\s*[(\[]\s*([^)\]]*?)\s*[)\]]")


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
    where, text = _read(source)
    header, names, names_at, rows = _parse(text, where, delimiter)
    table, units = _survey_columns(
        names,
        names_at,
        rows,
        {
            "md": md_column,
            "inclination": inclination_column,
            "azimuth": azimuth_column,
        },
    )
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
        md=table["md"],
        inclination=table["inclination"],
        azimuth=table.get("azimuth"),
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
        return source.name, source.read_text(encoding="utf-8-sig")
    return str(getattr(source, "name", "the survey file")), source.read()


def _parse(
    text: str, where: str, delimiter: str | None
) -> tuple[dict[str, str], list[str], str, _Rows]:
    """A survey file's header settings, as text, then its table.

    The table is the column names, the line naming them, and each station's
    line and values, as text.
    """
    header: dict[str, str] = {}
    names: list[str] | None = None
    names_at = where
    separator = delimiter or ","
    rows: _Rows = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        at = f"{where}, line {number}"
        if stripped.startswith("#"):
            if names is not None:
                raise InvalidInputError(
                    f"{at}: header and comment lines go above the table"
                )
            match = _HEADER_LINE.fullmatch(stripped)
            if match and (key := _header_key(match[1], at)):
                if key in header:
                    raise InvalidInputError(f"{at}: {key} is stated twice")
                header[key] = match[2].strip()
            continue
        if names is None:
            if delimiter is None:
                separator = max(_DELIMITERS, key=stripped.count)
            names = _cells(stripped, separator)
            while names and not names[-1]:
                names.pop()
            names_at = at
        else:
            rows.append((at, _cells(stripped, separator)))
    if names is None:
        raise InvalidInputError(
            f"{where} has no table: below the header, a line naming the columns "
            "md, inclination and azimuth, then one line per station"
        )
    if not rows:
        raise InvalidInputError(f"{where} has no stations below its column names")
    return header, names, names_at, rows


def _header_key(word: str, at: str) -> str | None:
    """The key a header line states, or None for a comment."""
    squeezed = _SQUEEZE.sub("", word.casefold())
    forms = dict.fromkeys((squeezed, squeezed.removesuffix("s")))
    for name in forms:
        if name in _HEADER_NAMES:
            return _HEADER_NAMES[name][0]
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


def _cells(line: str, delimiter: str) -> list[str]:
    reader = csv.reader([line], delimiter=delimiter, skipinitialspace=True)
    return [cell.strip() for cell in next(reader)]


def _survey_columns(
    names: list[str], at: str, rows: _Rows, named: Mapping[str, str | None]
) -> tuple[dict[str, list[float]], dict[str, tuple[str, str]]]:
    """The md, inclination and azimuth columns, and the units their names give.

    The units are keyed by the setting they give, with the line naming them.
    """
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
    if md_unit := _unit_in_name(names[found["md"]], length_factor):
        units["md_unit"] = (md_unit, at)
    angle_units = [
        unit
        for quantity in _COLUMNS[1:]
        if quantity in found
        and (unit := _unit_in_name(names[found[quantity]], angle_factor))
    ]
    if len({angle_factor(unit) for unit in angle_units}) > 1:
        raise InvalidInputError(
            f"{at}: the inclinations are in {angle_units[0]!r} and the azimuths "
            f"in {angle_units[1]!r}; a survey has one angle unit"
        )
    if angle_units:
        units["angle_unit"] = (angle_units[0], at)
    return columns, units


def _column(names: list[str], quantity: str, named: str | None, at: str) -> int | None:
    """Where the ``quantity`` column is: the one named, or else by its usual names."""
    bare = [_bare(name) for name in names]
    if named is not None:
        wanted = named.strip().casefold()
        found = [
            index
            for index, name in enumerate(names)
            if wanted in (name.casefold(), bare[index].casefold())
        ]
        if not found:
            raise InvalidInputError(
                f"{at}: the table has no column {named!r}; its columns are "
                f"{', '.join(map(repr, names))}"
            )
    else:
        found = [
            index
            for index, name in enumerate(bare)
            if _is_usual(quantity, _SQUEEZE.sub("", name.casefold()))
        ]
        if not found:
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
    return match[1] if (match := _UNIT_IN_NAME.fullmatch(name)) else name


def _unit_in_name(name: str, factor: Callable[[str], float]) -> str | None:
    """The unit in brackets after a column's name, if ``factor`` knows it."""
    if not (match := _UNIT_IN_NAME.fullmatch(name)):
        return None
    try:
        factor(match[2])
    except UnitError:
        return None
    return match[2]


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
        elif key == "north_reference":
            values[key] = _NORTH_NAMES.get(_SQUEEZE.sub("", text.casefold()), text)
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
