"""The input: everything a trajectory is computed from, and ways to build it.

:class:`TrajectoryInput` holds the survey, the wellhead it hangs from, the CRS
and what the azimuths are measured from, which are required, and the optional
settings, which have defaults. There is one constructor for each form a survey
usually comes in, and every one of them ends in the same checks.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, TextIO, TypedDict, Unpack, overload

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray

from geodetic_engine.welltrajectory.csv_parser import (
    format_survey_file,
    read_survey_file,
)
from geodetic_engine.welltrajectory.datamodels.well_trajectory import WellTrajectory
from geodetic_engine.welltrajectory.errors import (
    InvalidInputError,
    InvalidSurveyError,
    UnitError,
)
from geodetic_engine.welltrajectory.methods import LocalFrame, Method, MethodName
from geodetic_engine.welltrajectory.minimum_curvature import _MAX_POINTS, _validated
from geodetic_engine.welltrajectory.survey import (
    NorthReference,
    Survey,
    Wellhead,
    angle_factor,
    length_factor,
)
from geodetic_engine.welltrajectory.trajectory import compute_trajectory

type FloatArray = NDArray[np.float64]

_OPTIONS = frozenset(
    ("md_unit", "angle_unit", "method", "z_unit", "md_step", "md_points", "name")
)

# OSDU method names, casefolded.
_OSDU_METHODS = {
    "azimuthalequidistant": Method.AZIMUTHAL_EQUIDISTANT,
    "gnl": Method.GRID_NORTH_LOCAL,
    "gridnorthlocal": Method.GRID_NORTH_LOCAL,
    "enu": Method.ENU,
    "lmp": Method.LMP,
    "leesmodifiedproposal": Method.LMP,
}
# The spacing "interpolate" asks for, in the MD unit: about as often as the
# service adds stations.
_OSDU_STEP = 100.0


class TrajectoryOptions(TypedDict, total=False):
    """The optional settings of a :class:`TrajectoryInput`, as keyword arguments.

    Every constructor of :class:`TrajectoryInput` takes them; any left out
    takes the default given here.

    Attributes:
        md_unit: Unit of the measured depths. ``"m"``.
        angle_unit: Unit of the inclinations and azimuths. ``"degree"``.
        method: How the offsets are georeferenced in the CRS:
            ``"AzimuthalEquidistant"``, the default, ``"GridNorthLocal"``,
            ``"ENU"`` or ``"LMP"``, or the :class:`Method` member; see
            :class:`Method` for what each does.
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
    method: Method | MethodName
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
    * :meth:`from_csv`: a CSV survey file, its settings in a header and its
      columns under any names.
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
        wellhead: The position of the first station, which TVD and the
            offsets count from: ``x`` and ``y`` in the CRS, and
            ``z``, its elevation, in :attr:`z_unit`.
        crs: The trajectory CRS: anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts, such as ``"EPSG:23031"``, WKT, an OSDU
            ``persistableReference`` or a bound CRS.
        north_reference: What the azimuths are measured from: grid north,
            ``"GN"``, or true north, ``"TN"``.
        method: How the offsets are georeferenced in the CRS, a
            :class:`Method`: ``AZIMUTHAL_EQUIDISTANT``, the default,
            ``GRID_NORTH_LOCAL``, ``ENU`` or ``LMP``.
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
        span = float(survey.md[-1] - survey.md[0])
        if self.md_step is not None and not span / self.md_step < _MAX_POINTS:
            raise InvalidInputError(
                f"md_step {self.md_step:g} gives more than {_MAX_POINTS:,} points "
                f"between MD {survey.md[0]:g} and {survey.md[-1]:g}"
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
        md_column: str | None = None,
        inclination_column: str | None = None,
        azimuth_column: str | None = None,
        delimiter: str | None = None,
        **options: Unpack[TrajectoryOptions],
    ) -> TrajectoryInput:
        """An input from a survey file: settings in a header, then a table.

        A CSV file reads, for instance::

            # A synthetic well, not a real one. This line is a comment.
            # name: Synthetic-1
            # crs: EPSG:23031
            # wellhead: 455000.0, 6785000.0, 32.0
            # north_reference: GN
            MD (m),Inclination,Azimuth,TVD
            0.0,0.0,0.0,0.0
            30.0,0.31,210.01,30.0
            ...

        and a survey report, as older software writes, is read as it is::

            WELL NAME: F-1
            North Reference: Grid
            SURVEY LIST
            MD        Inc       Azim      TVD       UTM E/W
            m RKB     deg       deg       m RKB     m
            145.90    0.00      0.00      145.90    435046.488
            ...

        The text is UTF-8, or else read as Latin-1.

        **Header.** Above the table, lines starting with ``#``. A line
        ``# key: value`` states a setting, and the value runs to the end of the
        line, colons and all. Keys are read in any case, with spaces, hyphens
        or underscores between their words: ``Wellhead X`` is ``wellhead_x``,
        and a trailing ``s`` is dropped.

        * ``crs``, required: the trajectory CRS, as for :attr:`crs`, on one
          line. Also ``coordinate_system``.
        * The wellhead, required, in the CRS's own units, either on one line,
          ``wellhead: x, y`` or ``wellhead: x, y, z`` (also ``origin``), or
          one value a line: ``wellhead_x`` (also ``x``, ``easting``,
          ``origin_x``), ``wellhead_y`` (also ``y``, ``northing``,
          ``origin_y``) and ``wellhead_z`` (also ``z``, ``elevation``,
          ``kb``, ``rkb``), the elevation in ``z_unit``, 0 if left out.
        * ``north_reference``, required: ``GN`` or ``TN``, or ``grid`` or
          ``true north``. Also ``north`` and ``azimuth_reference``.
        * ``md_unit``, ``angle_unit``, ``z_unit``, ``method``, ``md_step`` and
          ``name``: the optional settings of :class:`TrajectoryOptions`, with
          the same defaults. Also ``depth_unit`` for ``md_unit`` and
          ``well`` or ``well_name`` for ``name``.
        * ``md_points``: depths separated by commas.

        Any other line starting with ``#`` is a comment, such as
        ``# Created by: Petrel``, unless its key is so close to a known one
        that it is likely misspelt, as ``md_unti`` is: that is refused.

        Other lines above the table are free text, and skipped, except a line
        ``key: value`` giving the name or the north reference, such as
        ``North Reference: Grid``, when its value reads as that setting: the
        first of each states it, unless a ``#`` line does. Nothing else is
        read from free text, which may give positions and elevations of more
        than the wellhead.

        **Table.** It starts at the first line naming the measured depth and
        inclination columns, or else at the first line that is not a header
        line. The columns are separated by commas, semicolons or tabs,
        whichever that line uses, or else lined up with spaces, two or more
        ending a name such as ``UTM E/W``; ``delimiter`` names another. The
        measured depth, inclination and azimuth columns are found by their
        usual names, in any case: ``MD``,
        ``Measured Depth`` or ``Depth``; ``Inclination``, ``Inc`` or ``Incl``;
        ``Azimuth``, ``Azi`` or any name starting ``Az``. ``md_column``,
        ``inclination_column`` and ``azimuth_column`` name them instead. A
        table without an azimuth column is an inclination-only survey. Other
        columns are ignored.

        A unit in brackets after a column's name, as in ``MD (ft)`` or
        ``Inc [deg]``, is its unit, and so is one in a line of units right
        below the names, which holds no number, and may follow a unit with a
        word, as in ``m RKB``. Given both ways, the units must agree. The
        arguments take precedence; a header stating a different one is refused.

        Every further line is one station: plain numbers, with ``.`` as the
        decimal point and no thousands separator. Blank lines are skipped.

        The keyword arguments fill in what the header leaves out, and take
        precedence over what it states, so one file can be computed with
        other settings. :meth:`to_csv` writes this format.

        Args:
            source: The file: a path, or an open text stream.
            wellhead: The wellhead, replacing the header's.
            crs: The trajectory CRS, replacing ``crs``.
            north_reference: ``"GN"`` or ``"TN"``, replacing
                ``north_reference``.
            md_column: The column holding measured depths, by its name in
                the file, with or without the unit; found by its usual names
                if left out.
            inclination_column: The column holding inclinations, likewise.
            azimuth_column: The column holding azimuths, likewise.
            delimiter: The character separating the values, ``" "`` for
                columns lined up with spaces; found from the line naming the
                columns if left out.
            options: The optional settings, each replacing the header's; see
                :class:`TrajectoryOptions`.

        Raises:
            InvalidInputError: If the file does not follow the format, a
                column cannot be found or is found twice, or a required
                setting is in neither the header nor the arguments. The message
                names the line at fault.
            InvalidSurveyError: If the stations cannot describe a wellbore.
            UnitError: If a unit is not recognised, a line of units included.
            OSError: If the file cannot be read.
        """
        stated = read_survey_file(
            source,
            wellhead=wellhead,
            crs=crs,
            north_reference=north_reference,
            md_column=md_column,
            inclination_column=inclination_column,
            azimuth_column=azimuth_column,
            delimiter=delimiter,
            options=options,
        )
        return cls._assemble(
            stated.md,
            stated.inclination,
            stated.azimuth,
            stated.wellhead,
            stated.crs,
            stated.north_reference,
            stated.options,
        )

    @classmethod
    def from_osdu_payload(cls, payload: Mapping[str, Any] | str) -> TrajectoryInput:
        """An input from an OSDU ``convertTrajectory`` request body.

        The body's fields map onto the input one for one:

        * ``inputStations`` onto the survey, with ``inputKind`` ``"MD_Incl"``
          for an inclination-only one, whose stations give no azimuth;
        * ``referencePoint`` onto the wellhead, ``trajectoryCRS`` onto the CRS
          and ``azimuthReference`` onto the north reference, also read as
          ``"GRID_NORTH"`` or ``"TRUE_NORTH"``;
        * ``unitMD`` onto the MD unit, ``unitZ`` if it is left out, and
          ``unitZ`` onto :attr:`z_unit`; angles are in degrees;
        * ``method`` onto :attr:`method`, where ``"GNL"`` is
          ``GridNorthLocal`` and ``"LeesModifiedProposal"`` is ``LMP``;
        * ``interpolate`` onto :attr:`md_step`: true, also when it is left
          out, as the service takes it, asks for a point every 100 of the MD
          unit;
        * ``MD_i`` onto :attr:`md_points`: ``md_i`` as listed, or a point
          every ``md_interval`` from the first station, and the last, as the
          service expands it.

        ``unitXY`` is checked against the CRS and not stored: the wellhead is
        read in the CRS's own unit, never rescaled.

        The service returned the points ``MD_i`` asked for apart from the
        stations, as ``stations_i``. Here they are among the stations, in MD
        order, with :attr:`WellTrajectory.is_survey_station` False.

        Args:
            payload: The request body, as a mapping or as JSON text.

        Raises:
            InvalidInputError: If the payload is not a JSON object, a required
                field is missing or has the wrong shape, ``inputKind`` is not
                ``"MD_Incl_Azim"`` or ``"MD_Incl"``, an ``"MD_Incl"`` station
                gives an azimuth, ``interpolate`` is not a boolean, ``method``
                names no known method, ``MD_i`` gives both ``md_i`` and
                ``md_interval``, or ``md_interval`` is not a positive length
                or gives more than a million points.
            InvalidSurveyError: If the stations cannot describe a wellbore.
            UnitError: If a unit is not recognised, or ``unitXY`` is not the
                CRS's own unit.
        """
        if isinstance(payload, str):
            try:
                body = json.loads(payload)
            except json.JSONDecodeError as error:
                raise InvalidInputError(f"the payload is not JSON: {error}") from error
        else:
            body = payload
        if not isinstance(body, Mapping):
            raise InvalidInputError("the payload must be a JSON object")
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
        kind = str(body.get("inputKind", "MD_Incl_Azim"))
        if kind.casefold() not in ("md_incl_azim", "md_incl"):
            raise InvalidInputError(
                f"inputKind {kind!r} is not MD_Incl_Azim or MD_Incl"
            )
        inclination_only = kind.casefold() == "md_incl"
        keys = ["md", "inclination"] + ([] if inclination_only else ["azimuth"])
        if not isinstance(rows, list) or not all(
            isinstance(row, Mapping) and set(keys) <= row.keys() for row in rows
        ):
            raise InvalidInputError(
                f"inputStations must be a list of stations, each with {', '.join(keys)}"
            )
        if inclination_only and any(row.get("azimuth") is not None for row in rows):
            raise InvalidInputError(
                "inputKind MD_Incl is an inclination-only survey, but inputStations "
                "give azimuths: leave them out, or give inputKind MD_Incl_Azim"
            )
        reference = body["referencePoint"]
        if not isinstance(reference, Mapping) or not {"x", "y"} <= reference.keys():
            raise InvalidInputError("referencePoint must be a mapping with x and y")
        wellhead = _wellhead((reference["x"], reference["y"], reference.get("z", 0.0)))
        requested = body.get("MD_i") or {}
        if not isinstance(requested, Mapping):
            raise InvalidInputError("MD_i must be a mapping")
        listed, interval = requested.get("md_i") or [], requested.get("md_interval")
        if not isinstance(listed, list):
            raise InvalidInputError(f"MD_i.md_i must be a list, not {listed!r}")
        if listed and interval is not None:
            raise InvalidInputError("MD_i gives both md_i and md_interval; give one")
        if "unitXY" in body:
            _require_crs_unit(
                body["trajectoryCRS"], (wellhead.x, wellhead.y), body["unitXY"]
            )
        interpolate = body.get("interpolate")
        if interpolate is not None and not isinstance(interpolate, bool):
            raise InvalidInputError(
                f"interpolate must be true or false, not {interpolate!r}"
            )
        spacing = _step(interval, "MD_i.md_interval")

        well = cls.from_arrays(
            [row["md"] for row in rows],
            [row["inclination"] for row in rows],
            None if inclination_only else [row["azimuth"] for row in rows],
            wellhead=wellhead,
            crs=body["trajectoryCRS"],
            north_reference=body["azimuthReference"],
            md_unit=body["unitMD"] if "unitMD" in body else body["unitZ"],
            method=_OSDU_METHODS[method.casefold()],
            z_unit=body["unitZ"],
            md_step=None if interpolate is False else _OSDU_STEP,
            md_points=listed or None,
        )
        if spacing is None:
            return well
        return replace(well, md_points=_interval_depths(spacing, well.survey.md))

    def compute(self) -> WellTrajectory:
        """The trajectory: positions, angles and dogleg severity at every point.

        Raises:
            UnresolvableCRSError: If the CRS cannot be resolved.
            geodetic_engine.geodesy.UnsupportedCRSError: If the CRS has no
                geographic or projected horizontal part, its projection does
                not preserve angles at the wellhead, or grid azimuths are given
                in a geographic CRS.
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
        text = format_survey_file(
            {
                "name": self.name,
                "crs": self.crs,
                "wellhead_x": self.wellhead.x,
                "wellhead_y": self.wellhead.y,
                "wellhead_z": self.wellhead.z,
                "north_reference": self.north_reference,
                "md_unit": self.survey.md_unit,
                "angle_unit": self.survey.angle_unit,
                "z_unit": self.z_unit,
                "method": self.method,
                "md_step": self.md_step,
                "md_points": self.md_points,
            },
            self.to_dataframe(),
        )
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
    """The member of ``kind`` that ``value`` names, read as ``kind`` reads it."""
    try:
        return kind(str(value))
    except ValueError:
        choices = ", ".join(member.value for member in kind)
        raise InvalidInputError(
            f"{setting} must be one of {choices}, not {value!r}"
        ) from None


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


def _step(value: object, setting: str = "md_step") -> float | None:
    if value is None:
        return None
    try:
        step = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        step = math.nan
    if not (math.isfinite(step) and step > 0):
        raise InvalidInputError(f"{setting} must be a positive length, not {value!r}")
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
            if absent := [key for key in ("md", "inclination") if key not in row]:
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


def _interval_depths(spacing: float, md: FloatArray) -> FloatArray:
    """A depth every ``spacing`` from the first station, and the last one."""
    intervals = float(md[-1] - md[0]) / spacing
    if not intervals < _MAX_POINTS:
        raise InvalidInputError(
            f"MD_i.md_interval {spacing:g} gives more than {_MAX_POINTS:,} points"
        )
    depths = md[0] + spacing * np.arange(math.ceil(intervals - 1e-9))
    return np.union1d(np.minimum(depths, md[-1]), md[-1:])


def _require_crs_unit(crs: Any, wellhead: Sequence[float], unit: str) -> None:
    """Refuse a horizontal unit other than the CRS's, rather than rescale."""
    frame = LocalFrame.at(crs, float(wellhead[0]), float(wellhead[1]), 0.0)
    factor = length_factor if frame.factors.projected else angle_factor
    try:
        same = math.isclose(factor(unit), frame.horizontal_unit, rel_tol=1e-9)
    except UnitError:
        same = False
    if not same:
        raise UnitError(
            f"unitXY {unit!r} is not the unit of {frame.crs.name}'s horizontal "
            "axes; the wellhead is read in the CRS's own unit"
        )
