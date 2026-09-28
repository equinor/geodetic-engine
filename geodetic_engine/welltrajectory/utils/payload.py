"""A trajectory request as one JSON body, onto the generic API.

A thin mapping and nothing more: every decision is
:func:`~geodetic_engine.welltrajectory.compute_trajectory`'s. The body's field
names (``trajectoryCRS``, ``inputStations``, ``MD_i``, ...) are mapped onto
its arguments one for one.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any, NamedTuple

from geodetic_engine.persistablereference import to_persistable_reference
from geodetic_engine.welltrajectory.errors import InvalidSurveyError, UnitError
from geodetic_engine.welltrajectory.methods import Method
from geodetic_engine.welltrajectory.survey import Survey, Wellhead, length_factor
from geodetic_engine.welltrajectory.trajectory import (
    WellTrajectory,
    compute_trajectory,
)

# Method names a request may use, casefolded.
_METHODS = {
    "azimuthalequidistant": Method.AZIMUTHAL_EQUIDISTANT,
    "gnl": Method.GRID_NORTH_LOCAL,
    "gridnorthlocal": Method.GRID_NORTH_LOCAL,
    "enu": Method.ENU,
    "lmp": Method.LMP,
}
# What ``"interpolate": true`` resamples at, in the survey's MD unit.
INTERPOLATION_STEP = 100.0


class PayloadResult(NamedTuple):
    """What a request produces.

    Attributes:
        stations: The survey stations, plus resampled ones if the request set
            ``interpolate``.
        stations_i: The points ``MD_i`` asked for, if it asked.
        local_crs: The local CRS the offsets were read in, as a
            persistableReference, if the method used one.
    """

    stations: WellTrajectory
    stations_i: WellTrajectory | None
    local_crs: str | None


def from_payload(payload: Mapping[str, Any] | str) -> PayloadResult:
    """Compute a trajectory from one JSON request body.

    Args:
        payload: The request body, as a mapping or as JSON text.

    Returns:
        The trajectory, and the interpolated points if any were asked for.

    Raises:
        KeyError: If a required field is missing.
        ValueError: If ``method`` names no known method.
        InvalidSurveyError: If ``MD_i`` gives both a list and an interval, or
            a depth outside the survey.
        UnitError: If ``unitXY`` is not the trajectory CRS's own unit.
    """
    body: Mapping[str, Any] = (
        json.loads(payload) if isinstance(payload, str) else payload
    )
    rows = body["inputStations"]
    inclination_only = body.get("inputKind", "MD_Incl_Azim") == "MD_Incl"
    survey = Survey(
        [row["md"] for row in rows],
        [row["inclination"] for row in rows],
        None if inclination_only else [row.get("azimuth") for row in rows],
        md_unit=body.get("unitMD") or body["unitZ"],
    )
    reference = body["referencePoint"]
    method = str(body.get("method", Method.AZIMUTHAL_EQUIDISTANT))
    if method.casefold() not in _METHODS:
        raise ValueError(f"{method!r} is not one of {sorted(_METHODS)}")

    trajectory = compute_trajectory(
        survey,
        Wellhead(reference["x"], reference["y"], reference.get("z", 0.0)),
        body["trajectoryCRS"],
        north=body["azimuthReference"],
        method=_METHODS[method.casefold()],
        z_unit=body["unitZ"],
        md_step=INTERPOLATION_STEP if body.get("interpolate") else None,
    )
    if unit := body.get("unitXY"):
        _require_crs_unit(trajectory, unit)

    local = trajectory.local_crs
    return PayloadResult(
        stations=trajectory,
        stations_i=_requested_points(trajectory, body.get("MD_i") or {}),
        local_crs=to_persistable_reference(local.crs) if local else None,
    )


def _requested_points(
    trajectory: WellTrajectory, request: Mapping[str, Any]
) -> WellTrajectory | None:
    listed, interval = request.get("md_i") or [], request.get("md_interval")
    if listed and interval:
        raise InvalidSurveyError("MD_i gives both md_i and md_interval; give one")
    if interval:
        return trajectory.resample(float(interval), include_survey=False)
    return trajectory.interpolate(listed) if listed else None


def _require_crs_unit(trajectory: WellTrajectory, unit: str) -> None:
    """Refuse a horizontal unit other than the CRS's, rather than rescale."""
    if not trajectory.factors.projected or not math.isclose(
        length_factor(unit), trajectory.frame.horizontal_unit, rel_tol=1e-9
    ):
        raise UnitError(
            f"unitXY {unit!r} is not the unit of {trajectory.crs.name}'s "
            "horizontal axes; wellhead coordinates are read in the CRS's own unit"
        )
