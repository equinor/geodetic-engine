"""Fixtures for testing against the published transformation dataset.

The dataset stores each point in the CRS's **EPSG-declared** axis order, while
this package accepts and returns values in ``xy`` order. The two differ for
most geographic CRSs, so every record is reordered on the way in and on the way
out. That reordering is derived from the axis directions the wrapper itself
reports, not from a hardcoded assumption about which CRSs are latitude first,
and it is tested directly in ``test_axis_order.py`` because a bug in it would
show up as a transformation failure somewhere else entirely.

Expected values are a consensus across independent engines, not a recording of
PROJ, so they are compared with a tolerance in metres and never for equality.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pyproj import Geod

from geodetic_engine.geodesy import (
    CoordinateReferenceSystem,
    MissingGridError,
    OperationNotAvailableError,
    TransformationFailedError,
)

DATASET = Path(__file__).parent.parent / "testdataset"

# Enough cases per file to exercise every method and CRS family in it without
# the default run being dominated by the dataset. The full sweep runs under
# "-m dataset".
DEFAULT_SAMPLE = 120

_EASTINGS = frozenset({"east", "west"})
_NORTHINGS = frozenset({"north", "south"})
_VERTICALS = frozenset({"up", "down"})
_ANGULAR = frozenset({"degree", "grad", "radian"})

_FALLBACK_GEOD = Geod(ellps="WGS84")


def load_records(filename: str) -> list[dict[str, Any]]:
    """Read every record from one dataset file.

    Args:
        filename: File name within ``tests/testdataset``, for example
            ``"vertical.jsonl"``.

    Returns:
        The records, in file order.
    """
    path = DATASET / filename
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def dataset_params(filename: str, sample: int = DEFAULT_SAMPLE) -> list[Any]:
    """Parametrise over one dataset file, sampling deterministically.

    Every record becomes a parameter. Those outside the sample are marked
    ``dataset`` so the default run skips them and ``-m dataset`` runs the lot.
    The sample is an evenly spaced stride over file order, so it is stable
    across runs and machines and spreads across the CRS families in the file
    rather than clustering at the start.

    Args:
        filename: File name within ``tests/testdataset``.
        sample: How many records to include in the default run.

    Returns:
        A list of :func:`pytest.param` values.
    """
    records = load_records(filename)
    if len(records) <= sample:
        chosen = set(range(len(records)))
    else:
        step = len(records) / sample
        chosen = {int(index * step) for index in range(sample)}

    params = []
    for index, record in enumerate(records):
        marks = [] if index in chosen else [pytest.mark.dataset]
        if record.get("proj_expectation") == "xfail":
            # PROJ could not produce this record when the dataset was built.
            # Absence from "agreeing" is not the same thing: a longitude
            # rotation reported unwrapped past 180 degrees excludes PROJ there
            # while still being the same point to the nanometre.
            # Unexpected successes and unrelated exception types fail the gate.
            marks.append(
                pytest.mark.xfail(
                    reason=(
                        "PROJ was unsuccessful when the dataset was generated: "
                        f"{record.get('proj_failure_reason')}"
                    ),
                    strict=True,
                    raises=(
                        MissingGridError,
                        OperationNotAvailableError,
                        TransformationFailedError,
                    ),
                )
            )
        params.append(pytest.param(record, id=record["case_id"], marks=marks))
    return params


def xy_permutation(crs: CoordinateReferenceSystem) -> tuple[int, ...]:
    """Positions of the CRS's declared axes in ``xy`` value order.

    Delegates to the wrapper's own
    :attr:`~geodetic_engine.geodesy.crs.CoordinateReferenceSystem.value_axis_order`
    rather than working it out again here. Reimplementing it would mean the
    tests could agree with a bug in the library by making the same mistake.

    Args:
        crs: The CRS whose axes are being ordered.

    Returns:
        A permutation of ``range(crs.dimension)``.

    Example:
        >>> xy_permutation(CoordinateReferenceSystem.from_user_input("EPSG:4326"))
        (1, 0)
    """
    return crs.value_axis_order


def to_xy(crs: CoordinateReferenceSystem, values: Sequence[float]) -> tuple[float, ...]:
    """Reorder one point from the CRS's declared axis order into ``xy`` order."""
    return tuple(values[index] for index in xy_permutation(crs))


def to_declared(
    crs: CoordinateReferenceSystem, values: Sequence[float]
) -> tuple[float, ...]:
    """Reorder one point from ``xy`` order back into the CRS's declared order."""
    permutation = xy_permutation(crs)
    restored = [0.0] * len(permutation)
    for position, index in enumerate(permutation):
        restored[index] = values[position]
    return tuple(restored)


def residual_metres(
    crs: CoordinateReferenceSystem,
    produced: Sequence[float],
    expected: Sequence[float],
) -> float:
    """Distance in metres between two points, both in the CRS's declared order.

    Angular axes are compared as a geodesic distance on the CRS's own
    ellipsoid, because a difference in degrees means a different distance at
    every latitude and on every ellipsoid. Linear axes are scaled to metres by
    their own unit factor. Horizontal and vertical residuals combine as the
    length of the resulting three-dimensional offset.

    Args:
        crs: The CRS both points are expressed in.
        produced: The coordinates produced, in declared axis order.
        expected: The coordinates expected, in declared axis order.

    Returns:
        The separation in metres.
    """
    axes = crs.axes
    directions = [axis.direction.lower() for axis in axes]
    east = next((i for i, d in enumerate(directions) if d in _EASTINGS), None)
    north = next((i for i, d in enumerate(directions) if d in _NORTHINGS), None)

    offsets: list[float] = []
    horizontal = {east, north} - {None}

    if east is not None and north is not None:
        if axes[east].unit_name.lower() in _ANGULAR:
            geod = crs.crs.get_geod() or _FALLBACK_GEOD
            _, _, distance = geod.inv(
                math.degrees(produced[east] * axes[east].unit_conversion_factor),
                math.degrees(produced[north] * axes[north].unit_conversion_factor),
                math.degrees(expected[east] * axes[east].unit_conversion_factor),
                math.degrees(expected[north] * axes[north].unit_conversion_factor),
            )
            offsets.append(distance)
        else:
            offsets.append(
                math.hypot(
                    (produced[east] - expected[east])
                    * axes[east].unit_conversion_factor,
                    (produced[north] - expected[north])
                    * axes[north].unit_conversion_factor,
                )
            )

    for index, axis in enumerate(axes):
        if index in horizontal:
            continue
        offsets.append(
            abs(produced[index] - expected[index]) * axis.unit_conversion_factor
        )

    return math.sqrt(sum(offset * offset for offset in offsets))
