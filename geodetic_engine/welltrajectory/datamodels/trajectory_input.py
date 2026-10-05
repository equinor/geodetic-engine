"""The input: everything a trajectory is computed from, and ways to build it.

:class:`TrajectoryInput` holds the survey, the wellhead it hangs from, the CRS
and what the azimuths are measured from, which are required, and the optional
settings, which have defaults. There is one constructor for each form a survey
usually comes in, and every one of them ends in the same checks.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, TextIO, TypedDict, Unpack, overload

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray

from geodetic_engine.geodesy import CoordinateReferenceSystem
from geodetic_engine.welltrajectory.datamodels.well_trajectory import WellTrajectory
from geodetic_engine.welltrajectory.errors import (
    InvalidInputError,
    InvalidSurveyError,
    UnitError,
)
from geodetic_engine.welltrajectory.methods import LocalFrame, Method
from geodetic_engine.welltrajectory.minimum_curvature import _validated
from geodetic_engine.welltrajectory.survey import (
    NorthReference,
    Survey,
    Wellhead,
    length_factor,
)
from geodetic_engine.welltrajectory.trajectory import compute_trajectory

type FloatArray = NDArray[np.float64]

_OPTIONS = frozenset(
    ("md_unit", "angle_unit", "method", "z_unit", "md_step", "md_points", "name")
)
# Survey file header keys, in the order to_csv writes them.
_HEADER_KEYS = (
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
_COLUMNS = ("md", "inclination", "azimuth")
# "# key: value", the key one word; any other line starting with # is a comment.
_HEADER_LINE = re.compile(r"#\s*([A-Za-z_]\w*)\s*:(.*)")

# OSDU method names, casefolded.
_OSDU_METHODS = {
    "azimuthalequidistant": Method.AZIMUTHAL_EQUIDISTANT,
    "gnl": Method.GRID_NORTH_LOCAL,
    "gridnorthlocal": Method.GRID_NORTH_LOCAL,
    "enu": Method.ENU,
    "lmp": Method.LMP,
}
# The spacing an OSDU payload's "interpolate": true asks for, in its MD unit.
_OSDU_STEP = 100.0


class TrajectoryOptions(TypedDict, total=False):
    """The optional settings of a :class:`TrajectoryInput`, as keyword arguments.

    Every constructor of :class:`TrajectoryInput` takes them; any left out
    takes the default given here.

    Attributes:
        md_unit: Unit of the measured depths. ``"m"``.
        angle_unit: Unit of the inclinations and azimuths. ``"degree"``.
        method: How the offsets are georeferenced in the CRS; see
            :class:`Method`. ``"AzimuthalEquidistant"``.
        z_unit: Unit of the wellhead elevation and of every depth, elevation
            and offset reported. ``"m"``.
        md_step: Also a point every ``md_step`` of MD between the stations, in
            ``md_unit``. None.
        md_points: Also a point at each of these measured depths, in
            ``md_unit``. None.
        name: The well's name, carried to the trajectory and its plots. None.
    """

    md_unit: str
    angle_unit: str
    method: Method | str
    z_unit: str
    md_step: float | None
    md_points: ArrayLike | None
    name: str | None


@dataclass(frozen=True, slots=True, eq=False, kw_only=True)
class TrajectoryInput:
    """Everything a well trajectory is computed from.

    Four settings are required: the :attr:`survey`, the :attr:`wellhead`, the
    :attr:`crs` and the :attr:`north_reference`. The others have defaults.
    Build an input with the constructor for the form the survey is in:

    * :meth:`from_arrays`: one array or list per quantity.
    * :meth:`from_records`: one row per station, as tuples, lists or mappings.
    * :meth:`from_dataframe`: a pandas DataFrame, with any column names.
    * :meth:`from_csv`: a survey file, with its settings in a header.
    * :meth:`from_osdu_payload`: an OSDU ``convertTrajectory`` request body.

    or directly, from a :class:`Survey` and a :class:`Wellhead`. Then
    :meth:`compute` gives the :class:`WellTrajectory`.

    Everything that can be checked without the CRS is checked when the input
    is made: the units, the settings, and that the survey describes a
    wellbore. The CRS is resolved by :meth:`compute`. An input is frozen;
    :func:`dataclasses.replace` gives a copy with some settings changed, and
    checks it again.

    Attributes:
        survey: The stations: measured depth, inclination and azimuth, with
            the units they are stated in.
        wellhead: Where MD and TVD count from: ``x`` and ``y`` in the CRS, and
            ``z``, its elevation, in :attr:`z_unit`.
        crs: The trajectory CRS: anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts, such as ``"EPSG:23031"``, WKT, an OSDU
            ``persistableReference`` or a bound CRS.
        north_reference: What the azimuths are measured from: grid north,
            ``"GN"``, or true north, ``"TN"``.
        method: How the offsets are georeferenced in the CRS; see
            :class:`Method`.
        z_unit: Unit of the wellhead elevation and of every depth, elevation
            and offset reported.
        md_step: Also a point every ``md_step`` of MD between the stations, in
            the survey's MD unit; None for none.
        md_points: Also a point at each of these measured depths, in the
            survey's MD unit; empty for none.
        name: The well's name, carried to the trajectory and its plots.

    Example:
        >>> well = TrajectoryInput.from_arrays(
        ...     md=[0, 1000, 2000],
        ...     inclination=[0, 30, 60],
        ...     azimuth=[45, 45, 45],
        ...     wellhead=(500000.0, 6600000.0, 25.0),
        ...     crs="EPSG:32631",
        ...     north_reference="GN",
        ... )
        >>> well.compute().tvd.round(1).tolist()
        [0.0, 954.9, 1654.0]
    """

    survey: Survey
    wellhead: Wellhead
    crs: Any
    north_reference: NorthReference
    method: Method = Method.AZIMUTHAL_EQUIDISTANT
    z_unit: str = "m"
    md_step: float | None = None
    md_points: FloatArray = field(default_factory=lambda: np.empty(0))
    name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.survey, Survey):
            raise InvalidInputError(
                f"survey must be a Survey, not {type(self.survey).__name__}; "
                "TrajectoryInput.from_arrays builds one from plain values"
            )
        if self.crs is None or (isinstance(self.crs, str) and not self.crs.strip()):
            raise InvalidInputError("a trajectory input needs a CRS")
        settings = {
            "wellhead": _wellhead(self.wellhead),
            "north_reference": _member(
                NorthReference, self.north_reference, "north_reference"
            ),
            "method": _member(Method, self.method, "method"),
            "md_step": _step(self.md_step),
            "md_points": _depths(self.md_points),
            "name": None if self.name is None else str(self.name),
        }
        for setting, value in settings.items():
            object.__setattr__(self, setting, value)
        length_factor(self.z_unit)

        survey = self.survey
        _validated(
            survey.md_metres, survey.inclination_radians, np.zeros(len(survey.md))
        )
        outside = (self.md_points < survey.md[0]) | (self.md_points > survey.md[-1])
        if np.any(outside):
            raise InvalidSurveyError(
                f"md_points asks for MD {self.md_points[outside][0]:g}, outside "
                f"the surveyed interval [{survey.md[0]:g}, {survey.md[-1]:g}]"
            )

    @classmethod
    def from_arrays(
        cls,
        md: ArrayLike,
        inclination: ArrayLike,
        azimuth: ArrayLike | None = None,
        *,
        wellhead: Wellhead | Sequence[float],
        crs: Any,
        north_reference: NorthReference | str,
        **options: Unpack[TrajectoryOptions],
    ) -> TrajectoryInput:
        """An input from one array per quantity.

        Numpy arrays, lists, tuples and pandas Series all work.

        Args:
            md: Measured depth of each station.
            inclination: Inclination of each station, from vertical.
            azimuth: Azimuth of each station, clockwise from
                ``north_reference``; None for an inclination-only survey,
                which is then taken to head north throughout.
            wellhead: ``(x, y)`` or ``(x, y, z)`` in the CRS, or a
                :class:`Wellhead`.
            crs: The trajectory CRS; see :attr:`crs`.
            north_reference: ``"GN"`` or ``"TN"``.
            options: The optional settings; see :class:`TrajectoryOptions`.

        Raises:
            InvalidInputError: If a setting is missing or has a value it
                cannot take.
            InvalidSurveyError: If the stations cannot describe a wellbore.
            UnitError: If a unit is not recognised.

        Example:
            >>> well = TrajectoryInput.from_arrays(
            ...     np.array([0.0, 500.0, 1500.0]),
            ...     np.array([0.0, 20.0, 60.0]),
            ...     np.array([0.0, 30.0, 45.0]),
            ...     wellhead=(500000.0, 6600000.0),
            ...     crs="EPSG:32631",
            ...     north_reference="TN",
            ...     md_unit="ft",
            ... )
            >>> well.survey.md_metres.round(3).tolist()
            [0.0, 152.4, 457.2]
        """
        return cls._assemble(
            md, inclination, azimuth, wellhead, crs, north_reference, options
        )

    @classmethod
    def from_records(
        cls,
        records: Iterable[Sequence[float] | Mapping[str, Any]],
        *,
        wellhead: Wellhead | Sequence[float],
        crs: Any,
        north_reference: NorthReference | str,
        **options: Unpack[TrajectoryOptions],
    ) -> TrajectoryInput:
        """An input from one row per station.

        Args:
            records: The stations, each ``(md, inclination, azimuth)``, or
                ``(md, inclination)`` for an inclination-only survey, or a
                mapping with the keys ``"md"``, ``"inclination"`` and
                ``"azimuth"``. Every row has the same form.
            wellhead: ``(x, y)`` or ``(x, y, z)`` in the CRS, or a
                :class:`Wellhead`.
            crs: The trajectory CRS; see :attr:`crs`.
            north_reference: ``"GN"`` or ``"TN"``.
            options: The optional settings; see :class:`TrajectoryOptions`.

        Raises:
            InvalidInputError: If the rows differ in form, a mapping lacks a
                key, or a setting is missing or has a value it cannot take.
            InvalidSurveyError: If the stations cannot describe a wellbore.
            UnitError: If a unit is not recognised.

        Example:
            >>> well = TrajectoryInput.from_records(
            ...     [(0, 0, 0), (500, 20, 30), (1500, 60, 45)],
            ...     wellhead=(500000.0, 6600000.0),
            ...     crs="EPSG:32631",
            ...     north_reference="GN",
            ... )
            >>> well.survey.inclination.tolist()
            [0.0, 20.0, 60.0]
        """
        md, inclination, azimuth = _columns_of(list(records))
        return cls._assemble(
            md, inclination, azimuth, wellhead, crs, north_reference, options
        )

    @classmethod
    def from_dataframe(
        cls,
        frame: pd.DataFrame,
        *,
        wellhead: Wellhead | Sequence[float],
        crs: Any,
        north_reference: NorthReference | str,
        md_column: str = "md",
        inclination_column: str = "inclination",
        azimuth_column: str | None = "azimuth",
        **options: Unpack[TrajectoryOptions],
    ) -> TrajectoryInput:
        """An input from a pandas DataFrame, one row per station.

        Other columns are ignored, so a table exported with TVD, offsets or
        tool readings can be passed as it is.

        Args:
            frame: The stations.
            wellhead: ``(x, y)`` or ``(x, y, z)`` in the CRS, or a
                :class:`Wellhead`.
            crs: The trajectory CRS; see :attr:`crs`.
            north_reference: ``"GN"`` or ``"TN"``.
            md_column: The column holding measured depths.
            inclination_column: The column holding inclinations.
            azimuth_column: The column holding azimuths; None for an
                inclination-only survey.
            options: The optional settings; see :class:`TrajectoryOptions`.

        Raises:
            InvalidInputError: If a column is missing, or a setting is missing
                or has a value it cannot take.
            InvalidSurveyError: If the stations cannot describe a wellbore.
            UnitError: If a unit is not recognised.

        Example:
            >>> import pandas as pd
            >>> table = pd.DataFrame(
            ...     {"MD": [0, 500, 1500], "INC": [0, 20, 60], "AZI": [0, 30, 45]}
            ... )
            >>> well = TrajectoryInput.from_dataframe(
            ...     table,
            ...     md_column="MD",
            ...     inclination_column="INC",
            ...     azimuth_column="AZI",
            ...     wellhead=(500000.0, 6600000.0),
            ...     crs="EPSG:32631",
            ...     north_reference="GN",
            ... )
            >>> well.survey.azimuth.tolist()
            [0.0, 30.0, 45.0]
        """
        wanted = [md_column, inclination_column]
        if azimuth_column is not None:
            wanted.append(azimuth_column)
        if missing := [column for column in wanted if column not in frame.columns]:
            raise InvalidInputError(
                f"the DataFrame has no column {', '.join(map(repr, missing))}; "
                f"its columns are {list(frame.columns)}. Name the ones to read "
                "with md_column, inclination_column and azimuth_column"
            )
        return cls._assemble(
            frame[md_column].to_numpy(),
            frame[inclination_column].to_numpy(),
            None if azimuth_column is None else frame[azimuth_column].to_numpy(),
            wellhead,
            crs,
            north_reference,
            options,
        )

    @classmethod
    def from_csv(
        cls,
        source: str | os.PathLike[str] | Traversable | TextIO,
        *,
        wellhead: Wellhead | Sequence[float] | None = None,
        crs: Any = None,
        north_reference: NorthReference | str | None = None,
        **options: Unpack[TrajectoryOptions],
    ) -> TrajectoryInput:
        """An input from a survey file: a header of settings, then a table.

        The file is UTF-8 text, and reads::

            # A synthetic well, not a real one. This line is a comment.
            # name: Synthetic-1
            # crs: EPSG:23031
            # wellhead_x: 455000.0
            # wellhead_y: 6785000.0
            # wellhead_z: 32.0
            # north_reference: GN
            # md_unit: m
            md,inclination,azimuth
            0.0,0.0,0.0
            30.0,0.31,210.01
            ...

        **Header.** Above the table, lines starting with ``#``. A line
        ``# key: value`` states a setting: the key is one word from the list
        below, in any case, and the value runs to the end of the line, colons
        and all. Every other line starting with ``#`` is a comment. A key the
        format does not know is refused, so that a misspelt one cannot pass
        unnoticed; a comment therefore never starts with one word and a colon.

        * ``crs``, required: the trajectory CRS, as for :attr:`crs`, on one
          line.
        * ``wellhead_x`` and ``wellhead_y``, required: the wellhead in the
          CRS, in its own units.
        * ``wellhead_z``: the wellhead elevation in ``z_unit``; 0 if left out.
        * ``north_reference``, required: ``GN`` or ``TN``.
        * ``md_unit``, ``angle_unit``, ``z_unit``, ``method``, ``md_step`` and
          ``name``: the optional settings of :class:`TrajectoryOptions`, with
          the same defaults.
        * ``md_points``: depths separated by commas.

        **Table.** The first line that does not start with ``#`` names the
        columns, separated by commas: ``md``, ``inclination`` and ``azimuth``,
        in any order and any case. ``azimuth`` is left out for an
        inclination-only survey; no other column is allowed. Every further line
        is one station: plain numbers, with ``.`` as the decimal point and no
        thousands separator. Blank lines are skipped.

        The keyword arguments fill in what the header leaves out, and take
        precedence over what it states, so one file can be computed with
        other settings. :meth:`to_csv` writes this format.

        Args:
            source: The file: a path, or an open text stream.
            wellhead: The wellhead, replacing ``wellhead_x``, ``wellhead_y``
                and ``wellhead_z``.
            crs: The trajectory CRS, replacing ``crs``.
            north_reference: ``"GN"`` or ``"TN"``, replacing
                ``north_reference``.
            options: The optional settings, each replacing the header's; see
                :class:`TrajectoryOptions`.

        Raises:
            InvalidInputError: If the file does not follow the format, or a
                required setting is in neither the header nor the arguments.
                The message names the line at fault.
            InvalidSurveyError: If the stations cannot describe a wellbore.
            UnitError: If a unit is not recognised.
            OSError: If the file cannot be read.
        """
        where, text = _read(source)
        header, table = _parse_survey_file(text, where)
        stated = _header_values(header, where)
        if wellhead is None and ("wellhead_x" in stated or "wellhead_y" in stated):
            if "wellhead_x" not in stated or "wellhead_y" not in stated:
                raise InvalidInputError(
                    f"{where} states only one of wellhead_x and wellhead_y"
                )
            wellhead = (
                stated["wellhead_x"],
                stated["wellhead_y"],
                stated.get("wellhead_z", 0.0),
            )
        crs = stated.get("crs") if crs is None else crs
        if north_reference is None:
            north_reference = stated.get("north_reference")
        required = {
            "crs": crs,
            "wellhead_x and wellhead_y": wellhead,
            "north_reference": north_reference,
        }
        if missing := [key for key, value in required.items() if value is None]:
            raise InvalidInputError(
                f"{where} states no {', '.join(missing)}: add each to the "
                "header, as in '# crs: EPSG:23031', or pass it as an argument"
            )
        merged: dict[str, Any] = {key: stated[key] for key in _OPTIONS & stated.keys()}
        merged.update(options)
        return cls._assemble(
            table["md"],
            table["inclination"],
            table.get("azimuth"),
            wellhead,
            crs,
            north_reference,
            merged,
        )

    @classmethod
    def from_osdu_payload(cls, payload: Mapping[str, Any] | str) -> TrajectoryInput:
        """An input from an OSDU ``convertTrajectory`` request body.

        The body's fields map onto the input one for one:

        * ``inputStations`` onto the survey, with ``inputKind`` ``"MD_Incl"``
          for an inclination-only one;
        * ``referencePoint`` onto the wellhead, ``trajectoryCRS`` onto the CRS
          and ``azimuthReference`` onto the north reference;
        * ``unitMD`` onto the MD unit, ``unitZ`` if it is left out, and
          ``unitZ`` onto :attr:`z_unit`; angles are in degrees;
        * ``method`` onto :attr:`method`, where ``"GNL"`` is
          ``GridNorthLocal``;
        * ``MD_i.md_i`` onto :attr:`md_points`, and ``MD_i.md_interval`` onto
          :attr:`md_step`. ``"interpolate": true`` asks for a point every 100
          of the MD unit.

        ``unitXY`` is checked against the CRS and not stored: the wellhead is
        read in the CRS's own unit, never rescaled.

        The service returned the points ``MD_i`` asked for apart from the
        stations, as ``stations_i``. Here they are among the stations, in MD
        order, with :attr:`WellTrajectory.is_survey_station` False.

        Args:
            payload: The request body, as a mapping or as JSON text.

        Raises:
            InvalidInputError: If a required field is missing, ``method``
                names no known method, ``MD_i`` gives both ``md_i`` and
                ``md_interval``, or ``md_interval`` and ``interpolate`` ask
                for spacings that are not multiples of each other.
            InvalidSurveyError: If the stations cannot describe a wellbore.
            UnitError: If a unit is not recognised, or ``unitXY`` is not the
                CRS's own unit.
        """
        body: Mapping[str, Any] = (
            json.loads(payload) if isinstance(payload, str) else payload
        )
        missing = [
            key
            for key in (
                "trajectoryCRS",
                "azimuthReference",
                "unitZ",
                "referencePoint",
                "inputStations",
            )
            if key not in body
        ]
        if missing:
            raise InvalidInputError(f"the payload has no {', '.join(missing)}")
        method = str(body.get("method", Method.AZIMUTHAL_EQUIDISTANT))
        if method.casefold() not in _OSDU_METHODS:
            raise InvalidInputError(
                f"method {method!r} is not one of {sorted(_OSDU_METHODS)}"
            )
        rows = body["inputStations"]
        inclination_only = body.get("inputKind", "MD_Incl_Azim") == "MD_Incl"
        reference = body["referencePoint"]
        wellhead = (reference["x"], reference["y"], reference.get("z", 0.0))
        requested = body.get("MD_i") or {}
        listed, interval = requested.get("md_i") or [], requested.get("md_interval")
        if listed and interval:
            raise InvalidInputError("MD_i gives both md_i and md_interval; give one")
        if unit := body.get("unitXY"):
            _require_crs_unit(body["trajectoryCRS"], wellhead, unit)

        return cls.from_arrays(
            [row["md"] for row in rows],
            [row["inclination"] for row in rows],
            None if inclination_only else [row.get("azimuth") for row in rows],
            wellhead=wellhead,
            crs=body["trajectoryCRS"],
            north_reference=body["azimuthReference"],
            md_unit=body.get("unitMD") or body["unitZ"],
            method=_OSDU_METHODS[method.casefold()],
            z_unit=body["unitZ"],
            md_step=_osdu_step(bool(body.get("interpolate")), interval),
            md_points=listed or None,
        )

    def compute(self) -> WellTrajectory:
        """The trajectory: positions, angles and dogleg severity at every point.

        Raises:
            UnresolvableCRSError: If the CRS cannot be resolved.
            UnsupportedCRSError: If the CRS has no geographic or projected
                horizontal part, or grid azimuths are given in a geographic
                CRS.
            DegenerateSurveyError: If two consecutive stations point in
                opposite directions.
        """
        return compute_trajectory(
            self.survey,
            self.wellhead,
            self.crs,
            north=self.north_reference,
            method=self.method,
            z_unit=self.z_unit,
            md_step=self.md_step,
            md_points=self.md_points,
            name=self.name,
        )

    def to_dataframe(self) -> pd.DataFrame:
        """The stations, one row each, in the units they are stated in."""
        columns = {"md": self.survey.md, "inclination": self.survey.inclination}
        if self.survey.azimuth is not None:
            columns["azimuth"] = self.survey.azimuth
        return pd.DataFrame(columns)

    @overload
    def to_csv(self, target: None = None) -> str: ...

    @overload
    def to_csv(self, target: str | os.PathLike[str]) -> None: ...

    def to_csv(self, target: str | os.PathLike[str] | None = None) -> str | None:
        """Write the input as a survey file, in the format :meth:`from_csv` reads.

        Every setting goes in the header, defaults included, so the file states
        everything the trajectory is computed with. Numbers are written in
        full, so :meth:`from_csv` reads back exactly the same values. A CRS
        that is not already one line of text is written as WKT.

        Args:
            target: The file to write; with None the text is returned instead.

        Returns:
            The text, if no target was given.

        Raises:
            InvalidInputError: If the name or a unit runs over more than one
                line.
        """
        header = {
            "name": self.name,
            "crs": _one_line_crs(self.crs),
            "wellhead_x": _number(self.wellhead.x),
            "wellhead_y": _number(self.wellhead.y),
            "wellhead_z": _number(self.wellhead.z),
            "north_reference": self.north_reference.value,
            "md_unit": self.survey.md_unit,
            "angle_unit": self.survey.angle_unit,
            "z_unit": self.z_unit,
            "method": self.method.value,
            "md_step": None if self.md_step is None else _number(self.md_step),
            "md_points": ", ".join(map(_number, self.md_points)) or None,
        }
        lines = [
            f"# {key}: {_one_line(value, key)}"
            for key, value in header.items()
            if value is not None
        ]
        frame = self.to_dataframe()
        lines.append(",".join(frame.columns))
        lines += [",".join(map(_number, row)) for row in frame.to_numpy()]
        text = "\n".join(lines) + "\n"
        if target is None:
            return text
        Path(target).write_text(text, encoding="utf-8")
        return None

    @classmethod
    def _assemble(
        cls,
        md: ArrayLike,
        inclination: ArrayLike,
        azimuth: ArrayLike | None,
        wellhead: object,
        crs: Any,
        north_reference: object,
        options: Mapping[str, Any],
    ) -> TrajectoryInput:
        if unknown := sorted(set(options) - _OPTIONS):
            raise TypeError(
                f"unexpected keyword argument {', '.join(map(repr, unknown))}; "
                f"the optional settings are {', '.join(sorted(_OPTIONS))}"
            )
        survey = Survey(
            md,
            inclination,
            azimuth,
            md_unit=options.get("md_unit", "m"),
            angle_unit=options.get("angle_unit", "degree"),
        )
        return cls(
            survey=survey,
            wellhead=_wellhead(wellhead),
            crs=crs,
            north_reference=_member(NorthReference, north_reference, "north_reference"),
            method=_member(
                Method, options.get("method", Method.AZIMUTHAL_EQUIDISTANT), "method"
            ),
            z_unit=options.get("z_unit", "m"),
            md_step=_step(options.get("md_step")),
            md_points=_depths(options.get("md_points")),
            name=options.get("name"),
        )


def _member[E: StrEnum](kind: type[E], value: object, setting: str) -> E:
    """The member of ``kind`` whose value ``value`` is, in any case."""
    if isinstance(value, kind):
        return value
    for member in kind:
        if str(value).strip().casefold() == member.value.casefold():
            return member
    choices = ", ".join(member.value for member in kind)
    raise InvalidInputError(f"{setting} must be one of {choices}, not {value!r}")


def _wellhead(value: object) -> Wellhead:
    numbers: list[float] = []
    if isinstance(value, Wellhead):
        numbers = [value.x, value.y, value.z]
    elif isinstance(value, Iterable) and not isinstance(value, str):
        try:
            numbers = [float(number) for number in value]
        except (TypeError, ValueError):
            numbers = []
    if len(numbers) not in (2, 3) or not all(map(math.isfinite, numbers)):
        raise InvalidInputError(
            f"wellhead must be (x, y) or (x, y, z) in finite numbers, not {value!r}"
        )
    return value if isinstance(value, Wellhead) else Wellhead(*numbers)


def _step(value: object) -> float | None:
    if value is None:
        return None
    try:
        step = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        step = math.nan
    if not (math.isfinite(step) and step > 0):
        raise InvalidInputError(f"md_step must be a positive length, not {value!r}")
    return step


def _depths(value: ArrayLike | None) -> FloatArray:
    try:
        depths = np.atleast_1d(np.asarray([] if value is None else value, float))
    except (TypeError, ValueError):
        depths = np.array([math.nan])
    if depths.ndim != 1 or not np.all(np.isfinite(depths)):
        raise InvalidInputError("md_points must be a flat sequence of finite numbers")
    return depths


def _columns_of(
    rows: list[Sequence[float] | Mapping[str, Any]],
) -> tuple[list[Any], list[Any], list[Any] | None]:
    """The md, inclination and azimuth columns of one row per station."""
    if all(isinstance(row, Mapping) for row in rows):
        mappings = [row for row in rows if isinstance(row, Mapping)]
        for index, row in enumerate(mappings):
            if absent := [key for key in _COLUMNS[:2] if key not in row]:
                raise InvalidInputError(f"station {index} has no {absent[0]!r}")
        stated = {"azimuth" in row for row in mappings}
        if len(stated) > 1:
            raise InvalidInputError("some stations state an azimuth and some do not")
        return (
            [row["md"] for row in mappings],
            [row["inclination"] for row in mappings],
            [row["azimuth"] for row in mappings] if stated == {True} else None,
        )
    if any(isinstance(row, Mapping) for row in rows):
        raise InvalidInputError("give every station as a mapping, or none")
    widths = {len(row) for row in rows if not isinstance(row, Mapping)}
    if len(widths) > 1 or not widths <= {2, 3}:
        raise InvalidInputError(
            "every station must be (md, inclination, azimuth), or every one "
            f"(md, inclination); these have {sorted(widths)} values"
        )
    columns = [list(column) for column in zip(*rows, strict=True)]
    return columns[0], columns[1], columns[2] if widths == {3} else None


def _read(
    source: str | os.PathLike[str] | Traversable | TextIO,
) -> tuple[str, str]:
    """A name for messages, and the text, of a survey file."""
    if isinstance(source, str | os.PathLike):
        source = Path(source)
    if isinstance(source, Traversable):
        return source.name, source.read_text(encoding="utf-8-sig")
    return str(getattr(source, "name", "the survey file")), source.read()


def _parse_survey_file(
    text: str, where: str
) -> tuple[dict[str, str], dict[str, list[float]]]:
    """The header settings, as text, and the table's columns, of a survey file."""
    header: dict[str, str] = {}
    names: list[str] | None = None
    rows: list[list[float]] = []
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
            if match := _HEADER_LINE.fullmatch(stripped):
                key = match[1].casefold()
                if key not in _HEADER_KEYS:
                    raise InvalidInputError(
                        f"{at}: {match[1]!r} is not a header key; the keys are "
                        f"{', '.join(_HEADER_KEYS)}"
                    )
                if key in header:
                    raise InvalidInputError(f"{at}: {key} is stated twice")
                header[key] = match[2].strip()
            continue
        cells = [cell.strip() for cell in next(csv.reader([stripped]))]
        if names is None:
            names = _column_names(cells, at)
        elif len(cells) != len(names):
            raise InvalidInputError(
                f"{at}: {len(cells)} values for {len(names)} columns"
            )
        else:
            rows.append(
                [
                    _float(cell, name, at)
                    for cell, name in zip(cells, names, strict=True)
                ]
            )
    if names is None:
        raise InvalidInputError(
            f"{where} has no table: below the header, a line naming the columns "
            "md, inclination and azimuth, then one line per station"
        )
    if not rows:
        raise InvalidInputError(f"{where} has no stations below its column names")
    return header, {
        name: [row[index] for row in rows] for index, name in enumerate(names)
    }


def _column_names(cells: list[str], at: str) -> list[str]:
    names = [cell.casefold() for cell in cells]
    if unknown := [cell for cell in cells if cell.casefold() not in _COLUMNS]:
        raise InvalidInputError(
            f"{at}: the first line of the table names its columns, md, "
            f"inclination and azimuth, not {', '.join(map(repr, unknown))}"
        )
    if len(set(names)) != len(names):
        raise InvalidInputError(f"{at}: a column is named twice")
    if absent := [name for name in _COLUMNS[:2] if name not in names]:
        raise InvalidInputError(f"{at}: the table has no {absent[0]} column")
    return names


def _header_values(header: Mapping[str, str], where: str) -> dict[str, Any]:
    """The header's settings, with numbers read as numbers."""
    values: dict[str, Any] = {}
    for key, text in header.items():
        at = f"{where}, header {key}"
        if key in _NUMERIC_KEYS:
            values[key] = _float(text, key, at)
        elif key == "md_points":
            values[key] = [
                _float(depth, key, at) for depth in re.split(r"[,\s]+", text) if depth
            ]
        else:
            values[key] = text
    return values


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


def _osdu_step(interpolate: bool, interval: float | None) -> float | None:
    """One MD step covering both ``interpolate`` and ``MD_i.md_interval``."""
    steps = sorted(
        float(step) for step in (interval, _OSDU_STEP if interpolate else None) if step
    )
    if len(steps) == 2 and not math.isclose(
        steps[1] / steps[0], round(steps[1] / steps[0]), rel_tol=1e-9
    ):
        raise InvalidInputError(
            f"interpolate asks for a point every {_OSDU_STEP:g} of MD and "
            f"MD_i.md_interval every {interval:g}; neither is a multiple of the "
            "other, so no single spacing gives both"
        )
    return steps[0] if steps else None


def _require_crs_unit(crs: Any, wellhead: Sequence[float], unit: str) -> None:
    """Refuse a horizontal unit other than the CRS's, rather than rescale."""
    frame = LocalFrame.at(crs, float(wellhead[0]), float(wellhead[1]), 0.0)
    if not frame.factors.projected or not math.isclose(
        length_factor(unit), frame.horizontal_unit, rel_tol=1e-9
    ):
        raise UnitError(
            f"unitXY {unit!r} is not the unit of {frame.crs.name}'s horizontal "
            "axes; the wellhead is read in the CRS's own unit"
        )
