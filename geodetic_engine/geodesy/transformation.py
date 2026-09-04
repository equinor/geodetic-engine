"""Resolving and applying a coordinate transformation, with the rules enforced.

Four things are checked here that PROJ will not check for you, because PROJ's
job is to compute and this package's job is to refuse to compute something
untrustworthy:

1. If an operation was requested, the transformer that gets built is inspected
   to confirm it really contains that operation. PROJ will otherwise build a
   perfectly functional transformer using a different one.
2. A ballpark path is refused outright rather than returned as an approximate
   number with no usable accuracy statement.
3. A grid the operation depends on but that is not installed is an error, named
   specifically, rather than a quiet fall back to a grid-free operation.
4. A dynamic reference frame without a coordinate epoch is an error, because
   the ground has moved between epochs and assuming one displaces every result.

Coordinate **values** are always in ``xy`` order, in and out. The CRSs' declared
axis order is reported separately and is not changed by this; see
:mod:`geodetic_engine.geodesy.crs`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pyproj import CRS, Transformer
from pyproj.crs import CoordinateOperation
from pyproj.enums import TransformDirection
from pyproj.exceptions import CRSError, ProjError
from pyproj.transformer import TransformerGroup

from geodetic_engine.geodesy.crs import CoordinateReferenceSystem
from geodetic_engine.geodesy.errors import (
    AmbiguousOperationError,
    BallparkTransformationError,
    MissingCoordinateEpochError,
    MissingGridError,
    OperationNotAvailableError,
    TransformationFailedError,
)
from geodetic_engine.geodesy.operation import (
    AppliedOperation,
    AreaOfUse,
    GridUsage,
    OperationCandidate,
    OperationReference,
    OperationRequest,
    OperationRoute,
    base_authority,
    grid_usages,
    is_ballpark,
    operation_names,
    parse_operations,
    requires_epoch,
)
from geodetic_engine.geodesy.result import Coordinates, TransformationResult

_VERTICAL_DIRECTIONS = frozenset({"up", "down"})

# PROJ transforms at most x, y, z: a fourth spatial component has no meaning to
# it, so one extra value beyond what a CRS declares is tolerated (a height
# alongside a 2D horizontal CRS, carried through unchanged) but no more than
# that.
_MAX_COORDINATE_VALUES = 3

# Methods that restate axes rather than move coordinates. PROJ inserts these
# when it normalises axis order, and they are not the method a caller means.
_BOOKKEEPING_METHODS = frozenset(
    {
        "axis order reversal (2d)",
        "axis order reversal (geographic3d horizontal)",
        "change of vertical unit",
        "geographic3d to geographic2d conversion",
    }
)


@dataclass(frozen=True, slots=True)
class _Pipeline:
    """One or more PROJ transformers applied in sequence.

    A single operation is a pipeline of one. Chaining exists for the case where
    the requested operation is defined between different CRSs than the caller's
    pair, for example a datum shift published between geographic CRSs that a
    caller wants applied between two projected ones.
    """

    steps: tuple[tuple[Transformer, TransformDirection], ...]
    core: Transformer
    route: OperationRoute
    identified_by: OperationRequest | None = None
    """Operation that names this pipeline when the caller did not name one.

    Set for a bound CRS, whose definition states the operation itself. Kept
    apart from the caller's request so that a result reports what was applied
    without claiming it was asked for.
    """
    skip_introspection: bool = False
    """Whether this pipeline's operation is only knowable after a transform.

    Set for ``allow_any_operation=True``'s escape hatch, where PROJ is left to
    choose. That choice is deliberately lazy and area-dependent: PROJ builds a
    pipeline that selects among candidate operations per coordinate, so until
    a point has actually been transformed there is no single operation to
    report, and asking for one is what fails rather than any missing feature.

    Asking anyway is worse than useless. ``to_json_dict()`` raises, and the
    PROJ error it leaves behind is not cleared, so the next ``transform()``
    with ``errcheck=True`` re-reports that stale export failure as if the
    coordinates themselves had failed. The identity is therefore read after
    the fact instead, from :meth:`last_used`.
    """

    def run(
        self, columns: Sequence[Sequence[float]], epoch: float | None
    ) -> list[list[float]]:
        """Apply every step in order, carrying all coordinate components through."""
        values: list[list[float]] = [list(column) for column in columns]
        for transformer, direction in self.steps:
            values = _apply(transformer, values, epoch, direction)
        return values

    def last_used(self) -> Transformer | None:
        """The operation PROJ actually applied on the most recent transform.

        Only meaningful once :meth:`run` has been called, and only needed when
        :attr:`skip_introspection` deferred the question. PROJ resolves an
        area-dependent pipeline to a concrete operation per coordinate, so
        this is the only point at which "which operation was that?" has an
        answer.

        Returns:
            A transformer wrapping the operation applied, or None if PROJ
            cannot say.
        """
        try:
            return self.core.get_last_used_operation()
        except (ProjError, RuntimeError):
            return None

    @property
    def definition(self) -> dict[str, Any]:
        """PROJJSON of the operation that carries the caller's intent.

        Empty while the operation is still deferred; see
        :attr:`skip_introspection` for why it is not merely attempted and
        caught.
        """
        if self.skip_introspection:
            return {}
        try:
            return dict(self.core.to_json_dict())
        except (TypeError, ProjError):
            return {}

    @property
    def accuracy(self) -> float | None:
        """Stated accuracy in metres, or None when PROJ reports none."""
        value = self.core.accuracy
        return None if value is None or value < 0 else float(value)

    @property
    def text(self) -> str:
        """The PROJ pipeline definition that will be executed."""
        return " | ".join(transformer.definition for transformer, _ in self.steps)


class Transformation:
    """A resolved, reusable transformation between two CRSs.

    Resolution happens once, on construction, so every failure that can be
    detected without coordinates is raised before any coordinate is handed
    over. The built PROJ transformer is kept, so transforming many batches
    costs one resolution rather than one per batch.

    Coordinate values are in ``xy`` order in both directions: longitude then
    latitude for geographic CRSs, easting then northing for projected ones,
    then height. The CRSs' EPSG-declared axis order is reported by
    :attr:`source_crs` and :attr:`target_crs` and is frequently different.

    Example:
        Reusing one transformation for several batches, naming the operation
        so that PROJ cannot substitute another:

        >>> tfm = Transformation("EPSG:4326", "EPSG:25832", operation="EPSG:16032")
        >>> tfm.operation.authority_code
        'EPSG:16032'
        >>> first = tfm.transform([(10.75, 59.91)])
        >>> second = tfm.transform([(5.32, 60.39), (7.99, 58.15)])

        A dynamic reference frame needs an epoch, in decimal years:

        >>> tfm = Transformation("EPSG:4896", "EPSG:4938", operation="EPSG:6277")
        >>> result = tfm.transform([(1137080.2487, -214618.1963, 6252133.9585)],
        ...                        coordinate_epoch=1993.0)

        A compound target CRS can need a horizontal and a vertical operation
        both named: PROJ fuses the two into one unidentified step whenever
        each touches only part of the compound, so naming just one would
        leave the other chosen silently.

        >>> tfm = Transformation("EPSG:4979", "EPSG:6172",
        ...                      operation=["EPSG:11028", "EPSG:9484"])

        A datum change with no operation named is refused by default; passing
        ``allow_any_operation=True`` instead lets PROJ pick freely, including a
        ballpark, which the result then reports rather than hides:

        >>> tfm = Transformation("EPSG:4230", "EPSG:4326", allow_any_operation=True)
        >>> tfm.operation.route
        <OperationRoute.ANY_OPERATION: 'any_operation'>
    """

    __slots__ = (
        "_applied",
        "_grids",
        "_pipeline",
        "_requests",
        "_requires_epoch",
        "_source",
        "_target",
    )

    def __init__(
        self,
        source_crs: Any,
        target_crs: Any,
        operation: (
            str
            | int
            | OperationReference
            | Sequence[str | int | OperationReference]
            | None
        ) = None,
        *,
        allow_any_operation: bool = False,
    ) -> None:
        """Resolve a transformation.

        Args:
            source_crs: CRS the input coordinates are in; an authority code,
                WKT, a PROJ string or a :class:`pyproj.CRS`.
            target_crs: CRS to produce coordinates in.
            operation: EPSG coordinate operation to apply, as ``"EPSG:15670"``,
                a bare code, an OGC URN, an operation name, or an
                :class:`~geodetic_engine.geodesy.operation.OperationCandidate`
                from :func:`available_operations` -- the latter is the only
                way to pin down a candidate PROJ built with no EPSG id of its
                own. Or several, when a compound target CRS needs more than
                one to be pinned down (a horizontal and a vertical operation,
                most commonly). Several are a set, not a sequence: each is
                checked for independently against whatever pipeline PROJ
                built, so the order they are given in does not matter and
                does not change the result. When omitted, PROJ chooses, which
                is only permitted where no datum change is involved, unless
                ``allow_any_operation`` says otherwise.
            allow_any_operation: Lets PROJ decide and pick any operation it offers,
                including a ballpark, when ``operation`` is omitted and a datum
                change is involved. False by default: naming an operation is
                then mandatory for a datum change, and a ballpark is always
                refused. Has no effect when ``operation`` is given -- naming
                one is already an explicit choice. The choice PROJ made is
                still recorded on the result, ballpark or not, so it is never
                silent, only permitted.

        Raises:
            UnresolvableCRSError: If either CRS cannot be constructed.
            OperationNotAvailableError: If the requested operation(s) cannot
                be applied to this CRS pair.
            AmbiguousOperationError: If no operation was requested, the
                transformation involves a datum change, and
                ``allow_any_operation`` is False.
            BallparkTransformationError: If the only path is a ballpark and
                ``allow_any_operation`` is False.
            MissingGridError: If a grid the operation needs is not installed.
        """
        self._source = CoordinateReferenceSystem.from_user_input(source_crs)
        self._target = CoordinateReferenceSystem.from_user_input(target_crs)
        self._requests = () if operation is None else parse_operations(operation)

        pipeline = _resolve(
            self._source,
            self._target,
            self._requests,
            allow_any_operation=allow_any_operation,
        )
        definition = pipeline.definition

        unsatisfied = [r for r in self._requests if not r.is_satisfied_by(definition)]
        if unsatisfied:
            raise OperationNotAvailableError(
                f"{', '.join(str(r) for r in unsatisfied)} "
                f"{'was' if len(unsatisfied) == 1 else 'were'} requested for "
                f"{_label(self._source)} to {_label(self._target)}, but PROJ built "
                f"{_applied_label(definition)} instead; "
                "refusing to substitute a different operation"
            )

        ballpark = is_ballpark(definition) or (
            pipeline.core.description.lower().startswith("ballpark")
        )
        if ballpark and pipeline.route != OperationRoute.ANY_OPERATION:
            raise BallparkTransformationError(
                f"the only path from {_label(self._source)} to "
                f"{_label(self._target)} is a ballpark approximation "
                f"({pipeline.core.description!r}), which carries no usable "
                "accuracy; no result can be given"
            )

        operations = _constituent_operations(pipeline, definition)
        self._grids = grid_usages(operations)
        _require_grids(self._grids, self._source, self._target)

        self._requires_epoch = requires_epoch(definition, operations)
        self._applied = _describe(
            self._requests,
            pipeline,
            definition,
            operations,
            ballpark=ballpark,
            requires_epoch=self._requires_epoch,
        )
        self._pipeline = pipeline

    @property
    def source_crs(self) -> CoordinateReferenceSystem:
        """CRS the input coordinates are expressed in."""
        return self._source

    @property
    def target_crs(self) -> CoordinateReferenceSystem:
        """CRS the output coordinates are expressed in."""
        return self._target

    @property
    def operation(self) -> AppliedOperation:
        """Which operation is applied, and how it was arrived at."""
        return self._applied

    @property
    def grids(self) -> tuple[GridUsage, ...]:
        """Grid files this transformation depends on. All are installed."""
        return self._grids

    @property
    def requires_epoch(self) -> bool:
        """Whether a coordinate epoch must be supplied to transform.

        True when the operation reads the epoch, which is not the same as
        either CRS being dynamic: a Helmert without rates gives the same answer
        at every epoch.
        """
        return self._requires_epoch

    def transform(
        self,
        x: Iterable[Iterable[float]] | Iterable[float] | float,
        y: Iterable[float] | float | None = None,
        z: Iterable[float] | float | None = None,
        *,
        coordinate_epoch: float | None = None,
    ) -> TransformationResult:
        """Transform one point or a batch of points.

        Args:
            x: Either every point's values in one go -- a single point given
                flat such as ``(lon, lat)``, a list of tuples, or a 2D numpy
                array of shape ``(n_points, n_axes)`` -- when ``y`` is omitted;
                or just the first axis's values, matching
                :meth:`pyproj.Transformer.transform`'s ``xx, yy, zz``
                convention, when ``y`` is given.
            y: Second axis's values: a scalar for one point, or a sequence for
                a batch. Omit to pass ``x`` as the whole set of points instead.
            z: Third axis's values (for example a height), in the same shape
                as ``x`` and ``y``. A lone scalar is broadcast against the
                other axes, so one height can be given once for many
                horizontal points rather than repeated.
            coordinate_epoch: Decimal year the coordinates were observed at,
                for example ``2010.0``. Required when either CRS is dynamic.

        Returns:
            The transformed coordinates and their provenance. Output values are
            in ``xy`` order in the target CRS's axis units, with one value per
            axis the target CRS declares.

        Raises:
            TypeError: If ``z`` was given without ``y``.
            ValueError: If points disagree on how many values they carry, or
                that count is not the source CRS's declared dimension, or one
                more (a height alongside a 2D horizontal CRS, carried through
                unchanged).
            MissingCoordinateEpochError: If a dynamic CRS is involved and no
                epoch was given.
            TransformationFailedError: If PROJ could not produce a finite
                result, or cannot produce every axis the target CRS declares.

        Example:
            A single point can be given flat, without wrapping it in a list:

            >>> tfm = Transformation("EPSG:4979", "EPSG:3855", operation="EPSG:3858")
            >>> tfm.transform((-144.0, 72.0, 548.4082)).coordinates
            ((556.38...,),)

            Or as separate per-axis values, matching pyproj's own convention:

            >>> tfm.transform(-144.0, 72.0, 548.4082).coordinates
            ((556.38...,),)
        """
        if y is None:
            if z is not None:
                raise TypeError(
                    "z was given without y; pass x, y and z as separate "
                    "per-axis values, or x alone as the whole set of points"
                )
            columns = _columns(self._source, x)
        else:
            columns = _columns_from_axes(self._source, x, y, z)
        count = len(columns[0]) if columns else 0

        if self._requires_epoch and coordinate_epoch is None:
            raise MissingCoordinateEpochError(
                f"{self._applied.name!r} reads the coordinate epoch, so one "
                "must be supplied in decimal years; without it the result "
                "would be displaced by the motion between the true and "
                "assumed epochs"
            )

        try:
            produced = self._pipeline.run(columns, coordinate_epoch)
        except ProjError as error:
            raise TransformationFailedError(
                f"PROJ could not transform from {_label(self._source)} to "
                f"{_label(self._target)}: {error}"
            ) from error

        indices = _output_indices(self._target, len(produced))
        rows = tuple(
            tuple(produced[index][point] for index in indices) for point in range(count)
        )
        _require_finite(rows, self._source, self._target)

        applied, grids = self._applied, self._grids
        pipeline_text = self._pipeline.text
        if self._pipeline.skip_introspection:
            applied, grids, pipeline_text = self._resolve_applied()
            _require_epoch_after_the_fact(applied, coordinate_epoch)

        return TransformationResult(
            coordinates=Coordinates(rows, target_crs=self._target),
            source_crs=self._source,
            target_crs=self._target,
            operation=applied,
            grids=grids,
            coordinate_epoch=coordinate_epoch,
            pipeline=pipeline_text,
        )

    def _resolve_applied(
        self,
    ) -> tuple[AppliedOperation, tuple[GridUsage, ...], str]:
        """Read back the operation PROJ chose, now that a transform has run.

        Only for the ``allow_any_operation=True`` route, where PROJ selects
        per coordinate and so cannot answer before the fact. The answer
        belongs to the batch that was just transformed rather than to the
        transformation as a whole, since another batch elsewhere on Earth may
        legitimately get a different operation.
        """
        last = self._pipeline.last_used()
        if last is None:
            return self._applied, self._grids, self._pipeline.text

        try:
            definition = dict(last.to_json_dict())
        except (TypeError, ProjError):
            return self._applied, self._grids, last.definition

        operations = tuple(last.operations or ())
        resolved = _Pipeline(
            steps=self._pipeline.steps, core=last, route=self._pipeline.route
        )
        applied = _describe(
            self._requests,
            resolved,
            definition,
            operations,
            ballpark=is_ballpark(definition),
            requires_epoch=requires_epoch(definition, operations),
        )
        return applied, grid_usages(operations), last.definition

    def __repr__(self) -> str:
        return (
            f"Transformation({_label(self._source)} -> {_label(self._target)}, "
            f"operation={self._applied.authority_code or self._applied.name!r})"
        )


def _columns(
    crs: CoordinateReferenceSystem, points: Iterable[Iterable[float]] | Iterable[float]
) -> tuple[tuple[float, ...], ...]:
    """Reshape points into one tuple of values per axis, the shape PROJ wants.

    Args:
        crs: The CRS the points are expressed in, whose declared axis count
            bounds how many values each point may carry.
        points: A single point's values, given flat -- ``(lon, lat)`` -- or a
            batch: an iterable of coordinate iterables, each holding one
            point's values in ``xy`` order. A 2D numpy array of shape
            ``(n_points, n_axes)`` works, one row per point. A row may carry
            one value more than ``crs`` declares -- a height alongside a 2D
            horizontal CRS -- which is carried through unchanged rather than
            consumed, matching how :meth:`pyproj.Transformer.transform` accepts
            an optional ``zz`` regardless of what the CRS pair declares.

    Returns:
        One tuple of values per axis, so a whole batch crosses into PROJ in a
        single call instead of once per point.

    Raises:
        ValueError: If points disagree on how many values they carry, or that
            count is not ``crs``'s declared dimension, or one more.
    """
    # Materialised up front: points may be a one-shot iterable or a numpy array
    # (not a Sequence), and each axis is read once below.
    materialized = list(points)
    if materialized and not isinstance(materialized[0], Iterable):
        # A lone point given flat, e.g. (lon, lat), rather than [(lon, lat)].
        # Unambiguous whenever a point has more than one value: only a single
        # flat point looks like a list of bare numbers rather than of rows.
        materialized = [materialized]
    rows = [tuple(float(value) for value in point) for point in materialized]
    widths = {len(row) for row in rows}
    if len(widths) > 1:
        raise ValueError(f"points have differing numbers of values: {sorted(widths)}")

    width = widths.pop() if widths else crs.dimension
    _require_width(crs, width)
    return tuple(tuple(row[axis] for row in rows) for axis in range(width))


def _columns_from_axes(
    crs: CoordinateReferenceSystem,
    x: Iterable[float] | float,
    y: Iterable[float] | float,
    z: Iterable[float] | float | None,
) -> tuple[tuple[float, ...], ...]:
    """Reshape separate per-axis values into columns, broadcasting a lone scalar.

    Mirrors :meth:`pyproj.Transformer.transform`'s ``xx, yy, zz`` convention:
    each axis is either the whole batch's values, or a lone scalar applied to
    every point -- a fixed height for many horizontal points, for example,
    given once rather than repeated per point.

    Args:
        crs: The CRS the points are expressed in, whose declared axis count
            bounds how many axes may be given.
        x: First axis's values: a scalar for one point, or a sequence (a list
            or a 1D numpy array) for a batch.
        y: Second axis's values, in the same shape as ``x``.
        z: Third axis's values, if any, in the same shape as ``x`` and ``y``.

    Returns:
        One tuple of values per axis, so a whole batch crosses into PROJ in a
        single call instead of once per point.

    Raises:
        ValueError: If the sequence axes disagree on how many points they
            hold, or the number of axes given is not ``crs``'s declared
            dimension, or one more.
    """
    axes = (x, y) if z is None else (x, y, z)
    _require_width(crs, len(axes))

    values = [
        tuple(float(v) for v in axis) if isinstance(axis, Iterable) else None
        for axis in axes
    ]
    lengths = {len(column) for column in values if column is not None}
    if len(lengths) > 1:
        raise ValueError(f"axes have differing batch sizes: {sorted(lengths)}")
    count = lengths.pop() if lengths else 1

    return tuple(
        column if column is not None else (float(axis),) * count
        for column, axis in zip(values, axes, strict=True)
    )


def _require_width(crs: CoordinateReferenceSystem, width: int) -> None:
    """Check how many values a point carries against what ``crs`` allows.

    Raises:
        ValueError: If ``width`` is not ``crs``'s declared dimension, or one
            more (a height alongside a 2D horizontal CRS, carried through
            unchanged).
    """
    allowed = {crs.dimension}
    if crs.dimension < _MAX_COORDINATE_VALUES:
        allowed.add(crs.dimension + 1)
    if width not in allowed:
        raise ValueError(
            f"{width} values were given per point but {crs!r} declares "
            f"{crs.dimension} axes; {sorted(allowed)} values are accepted "
            "(the extra being a height PROJ carries through unchanged)"
        )


def transform(
    source_crs: Any,
    target_crs: Any,
    x: Iterable[Iterable[float]] | Iterable[float] | float,
    y: Iterable[float] | float | None = None,
    z: Iterable[float] | float | None = None,
    *,
    operation: (
        str | int | OperationReference | Sequence[str | int | OperationReference] | None
    ) = None,
    allow_any_operation: bool = False,
    coordinate_epoch: float | None = None,
) -> TransformationResult:
    """Transform points between two CRSs in one call.

    A thin front for :class:`Transformation` for the case where the
    transformation is used once. Resolved transformations are cached, so
    repeating the same call does not repeat the resolution. Prefer building a
    :class:`Transformation` directly when transforming many separate batches.

    Args:
        source_crs: CRS the input coordinates are in.
        target_crs: CRS to produce coordinates in.
        x: Either every point's values in one go -- a single point given flat
            such as ``(lon, lat)``, a list of tuples, or a 2D numpy array of
            shape ``(n_points, n_axes)`` -- when ``y`` is omitted; or just the
            first axis's values, matching
            :meth:`pyproj.Transformer.transform`'s ``xx, yy, zz`` convention,
            when ``y`` is given.
        y: Second axis's values: a scalar for one point, or a sequence for a
            batch. Omit to pass ``x`` as the whole set of points instead.
        z: Third axis's values (for example a height), in the same shape as
            ``x`` and ``y``. A lone scalar is broadcast against the other
            axes, so one height can be given once for many horizontal points
            rather than repeated.
        operation: EPSG coordinate operation to apply, for example
            ``"EPSG:15670"``, or an
            :class:`~geodetic_engine.geodesy.operation.OperationCandidate`
            from :func:`available_operations`. Or several, when a compound
            target CRS needs more than one pinned down -- order does not
            matter, see :class:`Transformation`. Required whenever a datum
            change is involved, unless ``allow_any_operation`` says
            otherwise.
        allow_any_operation: Whether PROJ may pick any operation it offers,
            including a ballpark, when ``operation`` is omitted and a datum
            change is involved. False by default. See
            :class:`Transformation` for the full explanation.
        coordinate_epoch: Decimal year, required when either CRS is dynamic.

    Returns:
        The transformed coordinates and their provenance.

    Raises:
        AmbiguousOperationError: If no operation was given, the
            transformation involves a datum change, and ``allow_any_operation``
            is False.

    Example:
        >>> result = transform(
        ...     "EPSG:4326", "EPSG:25832", (10.75, 59.91),
        ...     operation="EPSG:16032",
        ... )
        >>> result.operation.authority_code
        'EPSG:16032'
        >>> result.target_axes
        ('E', 'N')

        Or as separate per-axis values:

        >>> result = transform(
        ...     "EPSG:4326", "EPSG:25832", 10.75, 59.91, operation="EPSG:16032"
        ... )
        >>> result.coordinates
        ((597868.38..., 6642681.51...),)
    """
    resolved = _cached_transformation(
        _cache_key(source_crs),
        _cache_key(target_crs),
        operation
        if operation is None or isinstance(operation, (str, int, OperationCandidate))
        else tuple(operation),
        allow_any_operation,
    )
    return resolved.transform(x, y, z, coordinate_epoch=coordinate_epoch)


def available_operations(
    source_crs: Any,
    target_crs: Any,
    *,
    authority: str | None = "any",
    accuracy: float | None = None,
    allow_superseded: bool = True,
    allow_ballpark: bool = False,
) -> tuple[OperationCandidate, ...]:
    """List every coordinate operation PROJ offers between two CRSs.

    This package's equivalent of inspecting a
    :class:`pyproj.transformer.TransformerGroup` directly: every candidate is
    described, including a ballpark fallback or one whose grid is not
    installed, so an ``operation=`` argument for :class:`Transformation` can be
    chosen with full information instead of by trial and error. Nothing here
    is applied to coordinates or checked against a request.

    A deprecated EPSG operation is never among the candidates: PROJ's own
    operation search excludes deprecated operations unconditionally, with no
    option to include them, so there is no ``allow_deprecated`` filter here to
    match -- one would silently do nothing.

    Args:
        source_crs: CRS the input coordinates would be in.
        target_crs: CRS to produce coordinates in.
        authority: Restrict candidates to those published by this authority,
            for example ``"EPSG"``. ``"any"`` searches every authority without
            the preference PROJ otherwise gives the source/target CRS's own
            authority. Omitted by default, which applies that preference.
        accuracy: Discard candidates stated as less accurate than this, in
            metres. Omitted by default, so every accuracy is considered.
        allow_superseded: Whether to include an operation EPSG has marked as
            superseded by a newer one. True by default, since a superseded
            operation is still valid, just no longer preferred.
        allow_ballpark: Whether to include a ballpark approximation among the
            candidates. True by default, so its presence and its lack of a
            usable accuracy are visible here rather than only discovered when
            :class:`Transformation` refuses it.

    Returns:
        One candidate per operation PROJ offers, ordered as PROJ ranks them
        (most accurate/likely first). Pass any entry's
        :attr:`~geodetic_engine.geodesy.operation.OperationCandidate.authority_code`
        as ``Transformation``'s ``operation=`` argument.

    Example:
        >>> candidates = available_operations("EPSG:4230", "EPSG:4326")
        >>> candidates[0].authority_code
        'EPSG:1133'
        >>> most_accurate = available_operations(
        ...     "EPSG:4230", "EPSG:4326", accuracy=1.0, allow_ballpark=False
        ... )
        >>> all(c.accuracy is not None and c.accuracy <= 1.0 for c in most_accurate)
        True
    """
    source = CoordinateReferenceSystem.from_user_input(source_crs)
    target = CoordinateReferenceSystem.from_user_input(target_crs)
    group = TransformerGroup(
        source.crs,
        target.crs,
        always_xy=True,
        authority=authority,
        accuracy=accuracy,
        allow_ballpark=allow_ballpark,
        allow_superseded=allow_superseded,
        crs_extent_use="none",
        grid_check="none",
    )
    return tuple(_describe_candidate(transformer) for transformer in group.transformers)


def _describe_candidate(transformer: Transformer) -> OperationCandidate:
    """Describe one candidate transformer without applying or requesting it."""
    definition = transformer.to_json_dict()
    operations = tuple(transformer.operations or ())
    if not operations:
        try:
            operations = (CoordinateOperation.from_json_dict(definition),)
        except CRSError:
            operations = ()

    substantive = _substantive_operation(operations)
    node = substantive.to_json_dict() if substantive is not None else definition
    identifier = _identifier(node)
    method = node.get("method")
    area = transformer.area_of_use
    ballpark = is_ballpark(definition)
    grids = grid_usages(operations)
    return OperationCandidate(
        auth_name=None if identifier is None else identifier[0],
        code=None if identifier is None else identifier[1],
        name=str(node.get("name") or transformer.description),
        method_name=(
            str(method["name"])
            if isinstance(method, dict) and "name" in method
            else None
        ),
        accuracy=transformer.accuracy if transformer.accuracy >= 0 else None,
        area_of_use=(
            None
            if area is None
            else AreaOfUse(
                west=area.west,
                south=area.south,
                east=area.east,
                north=area.north,
                name=area.name,
            )
        ),
        ballpark=ballpark,
        requires_epoch=requires_epoch(definition, operations),
        grids=grids,
        usable=not ballpark and all(grid.available for grid in grids),
    )


def _cache_key(crs: Any) -> str:
    """Render a CRS input as a hashable definition string."""
    return CoordinateReferenceSystem.from_user_input(crs).definition


@lru_cache(maxsize=128)
def _cached_transformation(
    source: str,
    target: str,
    operation: (
        str
        | int
        | OperationReference
        | tuple[str | int | OperationReference, ...]
        | None
    ),
    allow_any_operation: bool,
) -> Transformation:
    """Resolve and cache a transformation by its textual inputs."""
    return Transformation(
        source, target, operation, allow_any_operation=allow_any_operation
    )


def _resolve(
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
    requests: tuple[OperationRequest, ...],
    *,
    allow_any_operation: bool,
) -> _Pipeline:
    """Build the pipeline that will be applied, recording how it was found."""
    if not requests:
        return _resolve_without_request(
            source, target, allow_any_operation=allow_any_operation
        )

    found = _from_transformer_group(source, target, requests)
    if found is not None:
        return _Pipeline(
            steps=((found, TransformDirection.FORWARD),),
            core=found,
            route=OperationRoute.TRANSFORMER_GROUP,
        )
    if len(requests) == 1:
        return _from_operation(source, target, requests[0])
    raise OperationNotAvailableError(
        f"{', '.join(str(r) for r in requests)} were requested for "
        f"{_label(source)} to {_label(target)}, but no candidate PROJ offers "
        "for this CRS pair applies all of them together; naming more than one "
        "operation is only supported among PROJ's own candidates, not chained "
        "by hand"
    )


def _resolve_without_request(
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
    *,
    allow_any_operation: bool,
) -> _Pipeline:
    """Let PROJ choose, refusing a datum change unless explicitly allowed."""
    if _datum_names(source.crs) != _datum_names(target.crs):
        bound = _from_bound_crs(source, target)
        if bound is not None:
            return bound
        if not allow_any_operation:
            raise AmbiguousOperationError(
                f"{_label(source)} to {_label(target)} involves a datum change, "
                "so the coordinate operation must be named; choosing one is a "
                "decision about accuracy and area of validity that cannot be "
                "made here (pass allow_any_operation=True to let PROJ choose "
                "anyway)"
            )
        transformer = Transformer.from_crs(
            source.crs, target.crs, always_xy=True, allow_ballpark=True
        )
        return _Pipeline(
            steps=((transformer, TransformDirection.FORWARD),),
            core=transformer,
            route=OperationRoute.ANY_OPERATION,
            skip_introspection=True,
        )
    transformer = Transformer.from_crs(
        source.crs, target.crs, always_xy=True, allow_ballpark=False
    )
    return _Pipeline(
        steps=((transformer, TransformDirection.FORWARD),),
        core=transformer,
        route=OperationRoute.PROJ_DEFAULT,
    )


def _from_bound_crs(
    source: CoordinateReferenceSystem, target: CoordinateReferenceSystem
) -> _Pipeline | None:
    """Use the transformation a bound CRS states as its own definition.

    A bound CRS names exactly one transformation to its hub, so there is no
    choice left for PROJ to make and nothing for the caller to disambiguate.
    That is early binding, and it is the one datum change this package will
    apply without being told which operation to use: the operation was declared
    by whoever defined the CRS, not guessed here.

    The operation is read out of the bound CRS and then resolved through the
    same transformer group as a named one, rather than letting the bound CRS
    build the transformer by itself. A bound CRS with a projected base needs
    the map projection applied around the datum shift, and going through the
    group is what supplies those steps and keeps the applied operation
    identifiable.

    Returns:
        The pipeline, or None if neither CRS is bound, in which case the datum
        change really is ambiguous.
    """
    request = _bound_operation(source) or _bound_operation(target)
    if request is None:
        return None
    found = _from_transformer_group(source, target, (request,))
    if found is None:
        return None
    return _Pipeline(
        steps=((found, TransformDirection.FORWARD),),
        core=found,
        route=OperationRoute.BOUND,
        identified_by=request,
    )


def _bound_operation(
    crs: CoordinateReferenceSystem,
) -> OperationRequest | None:
    """The operation a bound CRS embeds, by authority code or else by name.

    A collapsed concatenated operation carries no identifier of its own, having
    been synthesised rather than published, so the name is the only handle on
    it. See :mod:`geodetic_engine.geodesy.utils.helmert`.
    """
    if not crs.crs.is_bound:
        return None
    node = crs.crs.to_json_dict().get("transformation")
    if not isinstance(node, dict):
        return None
    identifier = node.get("id")
    if isinstance(identifier, dict):
        authority, code = identifier.get("authority"), identifier.get("code")
        if authority is not None and code is not None:
            return OperationRequest.parse(f"{base_authority(str(authority))}:{code}")
    name = node.get("name")
    return OperationRequest.parse(str(name)) if name else None


def _from_transformer_group(
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
    requests: tuple[OperationRequest, ...],
) -> Transformer | None:
    """Find a candidate PROJ offers that satisfies every one of the requests.

    Extent filtering and grid filtering are both turned off, so that an
    operation is not hidden merely because its grid is missing. A missing grid
    is then reported as a missing grid rather than as a missing operation.
    """
    group = TransformerGroup(
        source.crs,
        target.crs,
        always_xy=True,
        allow_ballpark=False,
        allow_superseded=True,
        crs_extent_use="none",
        grid_check="none",
    )
    for transformer in group.transformers:
        definition = transformer.to_json_dict()
        if all(request.is_satisfied_by(definition) for request in requests):
            return transformer
    return None


def _from_operation(
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
    request: OperationRequest,
) -> _Pipeline:
    """Build the named operation itself, wrapping it in same-datum conversions.

    Reached when the operation is not among the candidates for this CRS pair,
    which typically means it is published between geographic CRSs while the
    caller is working in projected ones.
    """
    try:
        core = Transformer.from_pipeline(request.urn or request.text, always_xy=True)
    except (ProjError, CRSError) as error:
        raise OperationNotAvailableError(
            f"{request} could not be built as a coordinate operation, and is "
            f"not among the operations PROJ offers for {_label(source)} to "
            f"{_label(target)}: {error}"
        ) from error

    ends = _operation_ends(core)
    if ends is None:
        raise OperationNotAvailableError(
            f"{request} does not declare the CRSs it operates between, so it "
            f"cannot be chained into {_label(source)} to {_label(target)}"
        )
    op_source, op_target = ends

    if _datum_names(source.crs) & _datum_names(op_source):
        direction = TransformDirection.FORWARD
        entry, exit_ = op_source, op_target
    elif _datum_names(source.crs) & _datum_names(op_target):
        direction = TransformDirection.INVERSE
        entry, exit_ = op_target, op_source
    else:
        raise OperationNotAvailableError(
            f"{request} operates between {op_source.name!r} and "
            f"{op_target.name!r}, neither of which shares a datum with "
            f"{_label(source)}; it cannot be applied here"
        )

    steps: list[tuple[Transformer, TransformDirection]] = []
    if _datum_names(source.crs) != _datum_names(entry) or source.crs != entry:
        steps.append(
            (_conversion(source.crs, entry, request), TransformDirection.FORWARD)
        )
    steps.append((core, direction))
    if _datum_names(target.crs) != _datum_names(exit_) or target.crs != exit_:
        steps.append(
            (_conversion(exit_, target.crs, request), TransformDirection.FORWARD)
        )

    return _Pipeline(steps=tuple(steps), core=core, route=OperationRoute.CHAINED)


def _conversion(source: CRS, target: CRS, request: OperationRequest) -> Transformer:
    """Build a step that changes representation without changing datum.

    Guards the chain: if this step would move between datums it would apply a
    datum shift the caller never asked for, on top of the one they did.
    """
    if _datum_names(source) != _datum_names(target):
        raise OperationNotAvailableError(
            f"applying {request} between {source.name!r} and {target.name!r} "
            "would require an additional, unrequested datum change; name the "
            "full operation instead"
        )
    return Transformer.from_crs(source, target, always_xy=True, allow_ballpark=False)


def _operation_ends(transformer: Transformer) -> tuple[CRS, CRS] | None:
    """The CRSs an operation is defined between, as full CRS objects."""
    source = transformer.source_crs
    target = transformer.target_crs
    if source is None or target is None:
        return None
    return CRS.from_wkt(source.to_wkt()), CRS.from_wkt(target.to_wkt())


def _datum_names(crs: CRS) -> frozenset[str]:
    """Names of every datum the CRS is built on, including compound components."""
    parts = crs.sub_crs_list or [crs]
    names = set()
    for part in parts:
        datum = part.datum
        if datum is not None:
            names.add(datum.name)
    return frozenset(names)


def _constituent_operations(
    pipeline: _Pipeline, definition: dict[str, Any]
) -> tuple[CoordinateOperation, ...]:
    """Every operation being applied, so their grids can be inspected.

    Read from what PROJ built, never from the EPSG registry. The registry names
    the grid the authority published, while PROJ substitutes its own
    distribution of it: EPSG:3858 cites
    ``Und_min2.5x2.5_egm2008_isw=82_WGS84_TideFree``, which is not installed,
    where PROJ actually reads ``us_nga_egm08_25.tif``, which is. Asking the
    registry would report a missing grid for a transformation that works.
    """
    found: list[CoordinateOperation] = []
    for transformer, _ in pipeline.steps:
        found.extend(transformer.operations or ())
        if pipeline.skip_introspection:
            continue
        try:
            found.append(CoordinateOperation.from_json_dict(transformer.to_json_dict()))
        except (CRSError, TypeError, ProjError):
            continue
    return tuple(found)


def _require_grids(
    grids: tuple[GridUsage, ...],
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
) -> None:
    """Refuse to transform when a grid the operation depends on is absent."""
    missing = [grid for grid in grids if not grid.available]
    if not missing:
        return
    described = ", ".join(
        f"{grid.name}"
        + (f" (from {grid.package_name})" if grid.package_name else "")
        + (f" at {grid.url}" if grid.url else "")
        for grid in missing
    )
    raise MissingGridError(
        f"transforming {_label(source)} to {_label(target)} needs "
        f"{len(missing)} grid file(s) that are not installed: {described}"
    )


def _require_epoch_after_the_fact(
    applied: AppliedOperation, coordinate_epoch: float | None
) -> None:
    """Refuse a result whose operation needed an epoch that was not supplied.

    The epoch rule is normally enforced before any coordinate is handed over.
    On the ``allow_any_operation=True`` route it cannot be: which operation
    PROJ picks is area-dependent and unknown until a point has been
    transformed, so whether it reads the epoch is unknown too. Checking
    afterwards still refuses the result rather than returning coordinates
    displaced by the motion between the true and assumed epochs.
    """
    if not applied.requires_epoch or coordinate_epoch is not None:
        return
    raise MissingCoordinateEpochError(
        f"PROJ chose {applied.name!r}, which reads the coordinate epoch, but "
        "none was supplied; pass coordinate_epoch in decimal years, or name "
        "an operation instead of allowing any"
    )


def _describe(
    requests: tuple[OperationRequest, ...],
    pipeline: _Pipeline,
    definition: dict[str, Any],
    operations: tuple[CoordinateOperation, ...],
    *,
    ballpark: bool,
    requires_epoch: bool = False,
) -> AppliedOperation:
    """Record which operation was applied, against what was asked for.

    Provenance comes from the requested operation's own node in the tree when
    exactly one was named, or from the operation a bound CRS names for
    itself. When more than one was named, no single node represents "the"
    operation -- the whole applied step does, since that is what actually
    fused them -- so the top-level definition is reported as-is. Otherwise it
    comes from the substantive step: normalising axis order renames the
    top-level operation, appending "(with axis order normalized for
    visualization)" to its name, so using it directly would leak that wording
    into a result the caller never asked to have annotated.
    """
    node = definition
    identifier_of = requests[0] if len(requests) == 1 else pipeline.identified_by
    if identifier_of is not None:
        matched = identifier_of.find_in(definition)
        if matched is not None:
            node = matched
    elif not requests:
        substantive = _substantive_operation(operations)
        if substantive is not None:
            node = substantive.to_json_dict()

    identifier = _identifier(node)
    steps = tuple(
        name for name in operation_names(definition) if name != definition.get("name")
    )
    method = node.get("method")
    return AppliedOperation(
        requested=None if not requests else " + ".join(r.text for r in requests),
        auth_name=None if identifier is None else identifier[0],
        code=None if identifier is None else identifier[1],
        name=str(node.get("name") or pipeline.core.description),
        method_name=(
            str(method["name"])
            if isinstance(method, dict) and "name" in method
            else _substantive_method(operations)
        ),
        accuracy=pipeline.accuracy,
        route=pipeline.route,
        ballpark=ballpark,
        requires_epoch=requires_epoch,
        steps=tuple(sorted(steps)),
        projjson=json.dumps(node),
    )


def _substantive_operation(
    operations: tuple[CoordinateOperation, ...],
) -> CoordinateOperation | None:
    """The operation that does the geodetic work, not the axis bookkeeping.

    Normalising axis order makes PROJ prepend an axis-reversal conversion, so
    the first step of a concatenated operation is often bookkeeping rather than
    the transformation the caller cares about.
    """
    for operation in operations:
        name = operation.method_name
        if name and name.strip().lower() not in _BOOKKEEPING_METHODS:
            return operation
    return None


def _substantive_method(operations: tuple[CoordinateOperation, ...]) -> str | None:
    """Name of the method the substantive operation applies, if any."""
    operation = _substantive_operation(operations)
    return None if operation is None else str(operation.method_name)


def _identifier(definition: dict[str, Any]) -> tuple[str, str] | None:
    """The authority code of the operation as a whole, if it has one."""
    identity = definition.get("id")
    if isinstance(identity, dict):
        authority = identity.get("authority")
        code = identity.get("code")
        if authority is not None and code is not None:
            return base_authority(str(authority)), str(code)
    return None


def _apply(
    transformer: Transformer,
    values: list[list[float]],
    epoch: float | None,
    direction: TransformDirection,
) -> list[list[float]]:
    """Run one transformer over every coordinate component at once."""
    count = len(values[0]) if values else 0
    arguments: dict[str, Any] = {
        "xx": values[0],
        "yy": values[1] if len(values) > 1 else [0.0] * count,
    }
    if len(values) > 2:
        arguments["zz"] = values[2]
    if epoch is not None:
        arguments["tt"] = [epoch] * count

    produced = transformer.transform(**arguments, direction=direction, errcheck=True)
    return [list(component) for component in produced[: len(values)]]


def _output_indices(
    target: CoordinateReferenceSystem, produced: int
) -> tuple[int, ...]:
    """Map PROJ's output components onto the axes the target CRS declares.

    PROJ returns as many components as it was given. The target CRS decides how
    many of them are coordinates in that CRS: a vertical CRS declares one axis
    and its value is the height component, not the first one. A source height
    supplied alongside a horizontal-only pair is one component more than the
    target declares; it is passed through rather than dropped, since the
    caller gave it deliberately and PROJ already carried it through unchanged.
    """
    if target.dimension == 1 and _is_vertical(target):
        if produced < 3:
            raise TransformationFailedError(
                f"{_label(target)} declares a height axis, but only {produced} "
                "coordinate components were supplied; give the source height too"
            )
        return (2,)
    if target.dimension > produced:
        raise TransformationFailedError(
            f"{_label(target)} declares {target.dimension} axes but only "
            f"{produced} coordinate components were produced; supply "
            f"{target.dimension} values per point"
        )
    if produced - target.dimension > 1:
        raise TransformationFailedError(
            f"{_label(target)} declares {target.dimension} axes but {produced} "
            "coordinate components were produced; at most one extra "
            "(a pass-through height) is carried through"
        )
    return tuple(range(produced))


def _is_vertical(crs: CoordinateReferenceSystem) -> bool:
    """Whether the CRS's single axis is a height or a depth."""
    return crs.axes[0].direction.lower() in _VERTICAL_DIRECTIONS


def _require_finite(
    rows: tuple[tuple[float, ...], ...],
    source: CoordinateReferenceSystem,
    target: CoordinateReferenceSystem,
) -> None:
    """Refuse to return an infinite or undefined coordinate.

    PROJ signals an unrepresentable result with infinity. Returned as-is it
    would propagate silently into whatever consumes it.
    """
    for index, row in enumerate(rows):
        if not all(math.isfinite(value) for value in row):
            raise TransformationFailedError(
                f"point {index} has no finite representation transforming "
                f"{_label(source)} to {_label(target)}; PROJ produced {row}"
            )


def _label(crs: CoordinateReferenceSystem) -> str:
    """Short identification of a CRS for error messages."""
    return crs.authority_code or crs.name


def _applied_label(definition: dict[str, Any]) -> str:
    """Short identification of the operation PROJ actually built."""
    identifier = _identifier(definition)
    name = definition.get("name") or "an unnamed operation"
    if identifier is None:
        return f"{name!r}"
    return f"{identifier[0]}:{identifier[1]} ({name!r})"
