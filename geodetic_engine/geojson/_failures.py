"""Find the positions a transformation fails for, with only the public API.

Private to :mod:`geodetic_engine.geojson`.
:meth:`~geodetic_engine.geodesy.Transformation.transform_arrays` refuses a
whole batch when any one point fails, and does not always say which. This
module finds out, without a change to :mod:`geodetic_engine.geodesy`, by
transforming parts of the batch.

That rests on two properties of ``transform_arrays``, which the tests check:

* A point's result does not depend on which other points share its batch. So
  a part fails exactly when it holds a failing point, and the points of a part
  that succeeds come out bit for bit as they would in the whole batch.
* A failure that has nothing to do with the points also happens with none: a
  missing height, a missing epoch, a wrong number of values. :func:`probe`
  transforms an empty batch first, so that such a failure is raised as it is
  rather than blamed on a position.

Splitting costs nothing until something fails. :func:`first_failure` then
finds the first failing point in a number of calls logarithmic in the batch
size. :func:`isolate` finds every failing point; its cost grows with the
number of failures, which only callers that ask to keep going pay, and they
can cap it. A failure that only appears once points are transformed, such as
a grid that cannot be fetched, fails every point; the cap keeps that from
costing a call per point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

import numpy as np
from numpy.typing import NDArray

from geodetic_engine.errors import GeodeticEngineError
from geodetic_engine.geodesy import (
    ArrayTransformationResult,
    Transformation,
    TransformationFailedError,
)

type FloatArray = NDArray[np.float64]

SMALL_PART: Final = 8
"""A failing part at most this large is transformed point by point."""


@dataclass(frozen=True, slots=True)
class Failure:
    """A row that could not be transformed, and why.

    Attributes:
        index: The row, counted from 0 in the batch.
        error: The failure transforming that row on its own.
    """

    index: int
    error: TransformationFailedError


@dataclass(frozen=True, slots=True, eq=False)
class Isolated:
    """A batch transformed row by row where it had to be.

    Attributes:
        columns: One array per output axis. A row that failed holds NaN.
        failures: The rows that failed, in order.
    """

    columns: tuple[FloatArray, ...]
    failures: tuple[Failure, ...]

    @property
    def failed(self) -> NDArray[np.bool_]:
        """True for each row that failed."""
        mask = np.zeros(len(self.columns[0]) if self.columns else 0, dtype=np.bool_)
        mask[[failure.index for failure in self.failures]] = True
        return mask


class TooManyFailures(GeodeticEngineError):
    """More rows failed than the caller allows, so isolating them stopped.

    Attributes:
        failures: The first rows that failed, in order: one more than allowed.
    """

    def __init__(self, failures: tuple[Failure, ...]) -> None:
        super().__init__(f"more than {len(failures) - 1} rows failed")
        self.failures = failures

    def __reduce__(self) -> tuple[Any, ...]:
        return type(self), (self.failures,)


def probe(
    transformation: Transformation, width: int, coordinate_epoch: float | None
) -> ArrayTransformationResult:
    """Raise every failure that does not depend on the points.

    Args:
        transformation: The transformation to apply.
        width: Values per point.
        coordinate_epoch: As for ``transform_arrays``.

    Returns:
        The result of transforming no points. Its provenance is that of every
        batch of ``width`` values with this epoch, and its columns say how
        many values each result has.
    """
    return transformation.transform_arrays(
        np.empty((0, width), dtype=np.float64), coordinate_epoch=coordinate_epoch
    )


def first_failure(
    transformation: Transformation,
    rows: FloatArray,
    coordinate_epoch: float | None,
) -> Failure | None:
    """The first row that fails, found by halving.

    Each call transforms at most half the rows of the call before it, so the
    work is about that of transforming the batch twice.

    Args:
        transformation: The transformation to apply.
        rows: One row per point, already probed with :func:`probe`.
        coordinate_epoch: As for ``transform_arrays``.

    Returns:
        The first failing row, or None if none fails.
    """
    start, stop = 0, len(rows)
    if not _fails(transformation, rows, coordinate_epoch):
        return None
    while stop - start > 1:
        middle = (start + stop) // 2
        if _fails(transformation, rows[start:middle], coordinate_epoch):
            stop = middle
        else:
            start = middle
    return _single(transformation, rows, start, coordinate_epoch)


def isolate(
    transformation: Transformation,
    rows: FloatArray,
    coordinate_epoch: float | None,
    *,
    small_part: int = SMALL_PART,
    max_failures: int | None = None,
) -> Isolated:
    """Transform every row that can be, and say which cannot.

    One call when nothing fails. A part that fails is halved, and one of at
    most ``small_part`` rows is transformed row by row. Parts are taken in
    order, so failures are found in order.

    Args:
        transformation: The transformation to apply.
        rows: One row per point.
        coordinate_epoch: As for ``transform_arrays``.
        small_part: The largest failing part transformed row by row.
        max_failures: The most failing rows to isolate, or None for no limit.
            When every row fails, isolating them all costs a call per row.

    Raises:
        TooManyFailures: If more than ``max_failures`` rows fail.
        Whatever :func:`probe` raises: a failure no row is to blame for.
    """
    if max_failures is not None and max_failures < 0:
        raise ValueError(f"max_failures must not be negative, not {max_failures}")
    width = len(probe(transformation, rows.shape[1], coordinate_epoch).columns)
    count = len(rows)
    columns = tuple(np.full(count, np.nan) for _ in range(width))
    failures: list[Failure] = []
    pending = [(0, count)] if count else []
    while pending:
        start, stop = pending.pop()
        try:
            result = transformation.transform_arrays(
                rows[start:stop], coordinate_epoch=coordinate_epoch
            )
        except TransformationFailedError as error:
            size = stop - start
            if size == 1:
                failures.append(Failure(start, error))
                if max_failures is not None and len(failures) > max_failures:
                    raise TooManyFailures(tuple(failures)) from None
            elif size <= small_part:
                pending.extend((row, row + 1) for row in reversed(range(start, stop)))
            else:
                middle = (start + stop) // 2
                pending.extend(((middle, stop), (start, middle)))
            continue
        for column, values in zip(columns, result.columns, strict=True):
            column[start:stop] = values
    return Isolated(columns, tuple(failures))


def relocated(error: TransformationFailedError, path: str) -> TransformationFailedError:
    """``error``, of the same type, saying where its point is in the document.

    ``error`` is that of a point transformed on its own. When it names the
    point, ``path`` takes the place of its index.
    """
    if error.point is not None:
        message = f"{path} {error.detail}"
    else:
        message = f"{path}: {error}"
    try:
        return type(error)(message)
    except TypeError:
        return TransformationFailedError(message)


def _fails(
    transformation: Transformation,
    rows: FloatArray,
    coordinate_epoch: float | None,
) -> bool:
    try:
        transformation.transform_arrays(rows, coordinate_epoch=coordinate_epoch)
    except TransformationFailedError:
        return True
    return False


def _single(
    transformation: Transformation,
    rows: FloatArray,
    index: int,
    coordinate_epoch: float | None,
) -> Failure:
    try:
        transformation.transform_arrays(
            rows[index : index + 1], coordinate_epoch=coordinate_epoch
        )
    except TransformationFailedError as error:
        return Failure(index, error)
    raise RuntimeError(
        f"row {index} failed in a batch but not on its own: transforming a "
        "point no longer gives the same result whatever batch it is in"
    )
