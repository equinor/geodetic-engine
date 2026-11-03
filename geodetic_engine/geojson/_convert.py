"""Convert every position of a GeoJSON document to another CRS.

Private to :mod:`geodetic_engine.geojson`, which exports
:func:`convert_geojson`. The positions of each source CRS, with and without
a height, are transformed in one call to
:meth:`~geodetic_engine.geodesy.Transformation.transform_arrays`, so the
operation, grids, epoch and range checks are those of points; they are then
rounded, written back in the order the output flavour wants, and every
``bbox`` there was is computed again.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import numpy as np
from numpy.typing import NDArray

from geodetic_engine.geodesy import (
    ArrayTransformationResult,
    CoordinateReferenceSystem,
    Transformation,
    TransformationFailedError,
)
from geodetic_engine.geodesy._failures import first_failure, isolate, probe, relocated
from geodetic_engine.geodesy._rounding import Decimals, round_columns
from geodetic_engine.geojson._model import (
    ANYCRS_MEMBERS,
    ANYCRS_PREFIX,
    COORD_REF_SYS,
    JSONFG_CORE,
    LEGACY_CRS,
    Document,
    Flavour,
    Geometry,
    Kind,
    read_document,
)
from geodetic_engine.geojson._scope import (
    CRS84,
    CRS84H,
    METRE,
    Output,
    Source,
    Target,
    read_sources,
    read_target,
)
from geodetic_engine.geojson.errors import GeoJsonCrsError
from geodetic_engine.units import Unit

type FloatArray = NDArray[np.float64]
type OnError = Literal["raise", "withdraw", "nan"]
type Orientation = Literal["ccw", "cw"]

ON_ERRORS: Final = ("raise", "withdraw", "nan")
_ORIENTATIONS: Final = ("ccw", "cw")
_JSONFG_PREFIXES: Final = (
    "http://www.opengis.net/spec/json-fg-1/",
    "https://www.opengis.net/spec/json-fg-1/",
)
_HEIGHT_DIRECTIONS: Final = frozenset({"up", "down"})


@dataclass(frozen=True, slots=True, eq=False)
class GeoJsonResult:
    """A converted GeoJSON document and how it was converted.

    Attributes:
        document: The converted document, a new ``dict``: the input is never
            modified. With ``on_error="nan"``, a failed position holds NaN,
            which JSON cannot represent; :func:`json.dumps` writes it as
            ``NaN``, as the OSDU service does.
        success_count: Positions converted and written.
        total_count: Positions there were to convert.
        transformations: The provenance of each transformation applied, one
            per source CRS and number of values per position, as
            :meth:`~geodetic_engine.geodesy.ArrayTransformationResult.metadata_json_dict`
            gives it.
        operations_applied: A line of text per transformation and unit
            conversion, as the OSDU service reports them.
        failures: Each position that failed, by JSON path, with its error.
    """

    document: dict[str, Any]
    success_count: int
    total_count: int
    transformations: tuple[dict[str, Any], ...] = field(repr=False)
    operations_applied: tuple[str, ...]
    failures: tuple[tuple[str, TransformationFailedError], ...] = ()


def convert_geojson(
    document: object,
    target_crs: object,
    *,
    source_crs: object = None,
    operation: Any = None,
    target_unit_z: str | None = None,
    coordinate_epoch: float | None = None,
    output: Output = "auto",
    on_error: OnError = "raise",
    decimals: Decimals = None,
    orient: Orientation | None = None,
    fallback_operation: Any = None,
) -> GeoJsonResult:
    """Convert every position of a GeoJSON document to another CRS.

    The document is RFC 7946 GeoJSON, OSDU AnyCrs GeoJSON or OGC JSON-FG, with
    a ``FeatureCollection``, a ``Feature`` or a geometry at its root. Its
    structure, ``id``, ``properties``, foreign members and key order are
    kept; positions are converted, each ``bbox`` is computed again, and the
    members stating the CRS are written for the target.

    Where the positions are comes from the document: RFC 7946 is WGS 84
    (CRS84, or CRS84h with heights), AnyCrs states its CRS on its collection,
    a GeoJSON 2008 ``crs`` member names one, and a JSON-FG ``place`` is in the
    CRS of its nearest ``coordRefSys``, in that CRS's axis order.

    Args:
        document: The document: a mapping, JSON text, or an object with a
            ``__geo_interface__``.
        target_crs: The CRS to convert to, as anything
            :meth:`~geodetic_engine.geodesy.CoordinateReferenceSystem.from_user_input`
            accepts, an OSDU record id or persistableReference included. A
            compound CRS converts heights between vertical datums.
        source_crs: The CRS agreed with whoever wrote the document, for one
            that does not state its own (RFC 7946's \"prior arrangement\"). One
            that does must state the same.
        operation: The operation to apply, as for
            :class:`~geodetic_engine.geodesy.Transformation`; required for a
            datum change. The OSDU service's ``transformation``.
        target_unit_z: The unit to write heights in, as
            :func:`~geodetic_engine.units.length_unit` reads it: a symbol, an
            OSDU ``UnitOfMeasure`` id or a unit persistableReference. The
            OSDU service's ``toUnitZ``.
        coordinate_epoch: As for
            :meth:`~geodetic_engine.geodesy.Transformation.transform`; a
            JSON-FG ``coordRefSys`` may state it instead.
        output: ``\"auto\"`` keeps the document's family: AnyCrs stays AnyCrs,
            and plain GeoJSON is written as JSON-FG unless the target is WGS
            84 longitude and latitude. ``\"geojson\"``, ``\"anycrs\"``,
            ``\"jsonfg\"`` and ``\"jsonfg-plus\"`` choose one; ``\"jsonfg-plus\"``
            also gives each feature a ``geometry`` in WGS 84.
        on_error: What a position that cannot be transformed does:
            ``\"raise\"`` the error, naming the position by its JSON path;
            ``\"withdraw\"`` its feature's geometry, writing it as null; or
            write ``\"nan\"`` in its place, as the OSDU service does.
        decimals: Round converted values half up to this many decimals, one
            number for all, or one per value in ``xy`` order then height.
        orient: None keeps the vertex order as transformed; ``\"ccw\"`` winds
            exterior rings counterclockwise and holes clockwise (RFC 7946's
            right-hand rule), and ``\"cw\"`` the other way.
        fallback_operation: For ``\"jsonfg-plus\"``, the operation from the
            source CRS to WGS 84, if one is needed for the fallback geometry.

    Returns:
        The converted document, the counts of positions converted, and the
        provenance of every transformation applied.

    Raises:
        TypeError: If ``document`` is not a mapping, JSON text or an object
            with a ``__geo_interface__``.
        GeoJsonError: If the document is not GeoJSON this reads, or its CRS
            is not stated or contradicts ``source_crs``.
        ValueError: If an option is not valid for the document or the
            target, for example heights converted to a unit the output cannot
            state; if heights of no stated unit would become heights of a
            three-dimensional CRS; or if JSON-FG would state one epoch for
            positions at different epochs of a dynamic target.
        GeodesyError: If the transformation is refused: an operation must
            be named, a grid is missing, an epoch is needed.
        TransformationFailedError: With ``on_error=\"raise\"``, if a position
            cannot be transformed, naming it by its JSON path.

    Example:
        >>> result = convert_geojson(
        ...     {\"type\": \"Point\", \"coordinates\": [2.5, 63.5]},
        ...     \"EPSG:32631\",
        ...     decimals=2,
        ... )
        >>> result.document[\"coordinates\"], result.document[\"coordRefSys\"]
        ([475108.05, 7041396.93], 'http://www.opengis.net/def/crs/EPSG/0/32631')
    """
    if on_error not in ON_ERRORS:
        raise ValueError(f"on_error must be one of {ON_ERRORS}, not {on_error!r}")
    if orient is not None and orient not in _ORIENTATIONS:
        raise ValueError(f"orient must be one of {_ORIENTATIONS}, not {orient!r}")
    read = read_document(document)
    target = read_target(
        target_crs, source=read.flavour, output=output, target_unit_z=target_unit_z
    )
    converter = _Converter(
        read,
        target,
        read_sources(read, source_crs),
        operation=operation,
        fallback_operation=fallback_operation,
        coordinate_epoch=coordinate_epoch,
        on_error=on_error,
        decimals=decimals,
        orient=orient,
    )
    return converter.run()


@dataclass(slots=True)
class _Group:
    """The positions of one source CRS with one number of values."""

    source: Source
    dimension: int
    crs: CoordinateReferenceSystem
    members: list[int]
    rows: FloatArray = field(init=False)
    starts: NDArray[np.intp] = field(init=False)
    owners: NDArray[np.intp] = field(init=False)


class _Converter:
    def __init__(
        self,
        document: Document,
        target: Target,
        sources: Sequence[Source],
        *,
        operation: Any,
        fallback_operation: Any,
        coordinate_epoch: float | None,
        on_error: OnError,
        decimals: Decimals,
        orient: Orientation | None,
    ) -> None:
        self._document = document
        self._target = target
        self._sources = sources
        self._operation = operation
        self._fallback_operation = fallback_operation
        self._epoch = coordinate_epoch
        self._on_error: OnError = on_error
        self._decimals = decimals
        self._orient = orient
        self._transformations: dict[tuple[int, int, bool], Transformation] = {}
        self._provenance: list[dict[str, Any]] = []
        self._operations: list[str] = []
        self._failures: list[tuple[str, TransformationFailedError]] = []
        # Failed positions, by geometry: a position failing in the WGS 84
        # fallback as well counts once.
        self._failed: dict[int, set[int]] = {}
        self._written: dict[int, FloatArray] = {}
        self._fallback: dict[int, FloatArray] = {}
        self._z_unit: Unit | None = None
        self._epochs: dict[int, float | None] = {}
        self._place = target.flavour is Flavour.JSONFG and not target.lonlat
        self._primary = self._primaries()

    def run(self) -> GeoJsonResult:
        document = self._document
        groups = self._groups()
        converted: list[tuple[_Group, FloatArray]] = []
        for group in groups:
            converted.append((group, self._convert(group)))
            if self._needs_fallback(group):
                self._convert_fallback(group)
        fallbacks = self._fallback_objects()
        for group, rows in converted:
            self._write(group, rows)
        self._place_features(fallbacks)
        self._withdraw()
        self._write_bounding_boxes()
        self._write_members()
        withdrawn = document.withdrawn
        total = success = 0
        for index in self._primary:
            count = document.geometries[index].count
            total += count
            if index not in withdrawn:
                success += count - len(self._failed.get(index, ()))
        return GeoJsonResult(
            document=document.root,
            success_count=success,
            total_count=total,
            transformations=tuple(self._provenance),
            operations_applied=tuple(self._operations),
            failures=tuple(self._failures),
        )

    def _primaries(self) -> frozenset[int]:
        """The geometries to convert: each feature's place, else its geometry."""
        document = self._document
        if document.flavour is not Flavour.JSONFG:
            return frozenset(range(len(document.geometries)))
        primary = set()
        for index, geometry in enumerate(document.geometries):
            if geometry.feature is None:
                primary.add(index)
                continue
            has_place = document.features[geometry.feature].target.get("place")
            if geometry.placed == (has_place is not None):
                primary.add(index)
        return frozenset(primary)

    def _groups(self) -> list[_Group]:
        groups: dict[tuple[int, int], _Group] = {}
        for index in sorted(self._primary):
            geometry = self._document.geometries[index]
            if not geometry.count:
                continue
            source = self._sources[index]
            key = (id(source), geometry.dimension)
            if key not in groups:
                crs = source.crs_for(geometry.dimension)
                groups[key] = _Group(source, geometry.dimension, crs, [])
            groups[key].members.append(index)
        for group in groups.values():
            geometries = [self._document.geometries[i] for i in group.members]
            counts = np.array([g.count for g in geometries], dtype=np.intp)
            group.rows = np.concatenate([g.positions for g in geometries])
            group.starts = np.concatenate([[0], np.cumsum(counts)[:-1]]).astype(np.intp)
            group.owners = np.repeat(np.arange(len(geometries)), counts)
        return list(groups.values())

    def _convert(self, group: _Group) -> FloatArray:
        """The group's positions in the target CRS, in value order."""
        source, crs = group.source, group.crs
        rows = self._value_order(group.rows, crs, source.axis_order)
        transformation = self._transformation(crs, group.dimension, fallback=False)
        if group.dimension == 3:
            rows[:, 2] = _height_input(
                rows[:, 2], source, crs, transformation.target_crs
            )
        epoch = self._epoch_for(source)
        self._epochs.update(dict.fromkeys(group.members, epoch))
        columns = self._run(transformation, rows, epoch, group)
        if len(columns) != group.dimension:
            raise ValueError(
                f"{_label(transformation.target_crs)} gives {len(columns)} values "
                f"per position for {group.dimension}, which is not a GeoJSON position"
            )
        out = np.column_stack(columns)
        if group.dimension == 3:
            out[:, 2] = self._heights(out[:, 2], source, crs, transformation)
        return _rounded(out, self._decimals)

    def _convert_fallback(self, group: _Group) -> None:
        """WGS 84 positions for the fallback geometry of ``jsonfg-plus``."""
        source, crs = group.source, group.crs
        rows = self._value_order(group.rows, crs, source.axis_order)
        if source.crs is not None:
            if group.dimension == 3:
                # RFC 7946 heights are metres, whatever the source CRS carries.
                rows[:, 2] = _height_input(
                    rows[:, 2],
                    source,
                    crs,
                    CoordinateReferenceSystem.from_user_input(CRS84H),
                )
            transformation = self._transformation(crs, group.dimension, fallback=True)
            columns = self._run(transformation, rows, self._epoch_for(source), group)
            rows = np.column_stack(columns)
        for position, index in enumerate(group.members):
            if self._feature_lacks_geometry(index):
                start = int(group.starts[position])
                count = self._document.geometries[index].count
                self._fallback[index] = rows[start : start + count]

    def _needs_fallback(self, group: _Group) -> bool:
        if not (self._place and self._target.plus):
            return False
        return any(self._feature_lacks_geometry(index) for index in group.members)

    def _feature_lacks_geometry(self, index: int) -> bool:
        feature = self._document.geometries[index].feature
        if feature is None:
            return False
        target = self._document.features[feature].target
        return target.get("place") is None or target.get("geometry") is None

    def _transformation(
        self, crs: CoordinateReferenceSystem, dimension: int, *, fallback: bool
    ) -> Transformation:
        key = (id(crs), dimension, fallback)
        if key not in self._transformations:
            if fallback:
                wgs84 = CRS84H if crs.dimension == 3 else CRS84
                self._transformations[key] = Transformation(
                    crs, wgs84, self._fallback_operation
                )
            else:
                self._transformations[key] = Transformation(
                    crs, self._target_for(dimension), self._operation
                )
        return self._transformations[key]

    def _target_for(self, dimension: int) -> CoordinateReferenceSystem:
        """The target CRS, without its height axis for positions without one."""
        crs = self._target.crs
        if dimension == 2 and crs.dimension == 3:
            if self._place:
                raise ValueError(
                    f"JSON-FG positions have as many values as their CRS has axes, "
                    f"and these have two where {_label(crs)} has three; give a "
                    "two-dimensional target CRS, or write output='anycrs'"
                )
            return CoordinateReferenceSystem.from_user_input(crs.crs.to_2d())
        if dimension == 3 and crs.dimension == 2 and self._place:
            raise ValueError(
                f"JSON-FG positions have as many values as their CRS has axes, and "
                f"{_label(crs)} has no height; give a three-dimensional target CRS, "
                "such as a compound one, or write output='anycrs'"
            )
        return crs

    def _epoch_for(self, source: Source) -> float | None:
        if source.epoch is None:
            epoch = self._epoch
        elif self._epoch is None or math.isclose(self._epoch, source.epoch):
            epoch = source.epoch
        else:
            raise GeoJsonCrsError(
                source.stated_by,
                f"the document states the coordinate epoch {source.epoch}, not "
                f"coordinate_epoch, {self._epoch}",
            )
        return epoch

    def _run(
        self,
        transformation: Transformation,
        rows: FloatArray,
        epoch: float | None,
        group: _Group,
    ) -> tuple[FloatArray, ...]:
        """Transformed columns; a failed row is NaN unless that raises."""
        provenance = probe(transformation, rows.shape[1], epoch)
        failed = 0
        if self._on_error == "raise":
            try:
                result = transformation.transform_arrays(rows, coordinate_epoch=epoch)
            except TransformationFailedError as error:
                row, failing = error.point, error
                if row is None or not 0 <= row < len(rows):
                    failure = first_failure(transformation, rows, epoch)
                    if failure is None:
                        raise
                    row, failing = failure.index, failure.error
                raise relocated(failing, self._path(group, row)) from error
            columns = result.columns
        else:
            isolated = isolate(transformation, rows, epoch)
            columns = isolated.columns
            failed = len(isolated.failures)
            for failure in isolated.failures:
                path = self._path(group, failure.index)
                located = relocated(failure.error, path)
                position = int(group.owners[failure.index])
                index = group.members[position]
                if (
                    self._on_error == "withdraw"
                    and self._document.geometries[index].feature is None
                ):
                    # A geometry outside any feature has no feature to withdraw.
                    raise located from failure.error
                failed_here = self._failed.setdefault(index, set())
                local = failure.index - int(group.starts[position])
                if local not in failed_here:
                    failed_here.add(local)
                    self._failures.append((path, located))
        self._record(transformation, provenance, len(rows), failed)
        return columns

    def _record(
        self,
        transformation: Transformation,
        provenance: ArrayTransformationResult,
        count: int,
        failed: int,
    ) -> None:
        self._provenance.append(provenance.metadata_json_dict())
        operation = transformation.operation
        name = operation.name
        if operation.authority_code:
            name = f"{name} ({operation.authority_code})"
        line = (
            f"{count} positions from {_label(transformation.source_crs)} to "
            f"{_label(transformation.target_crs)} using {name}"
        )
        if failed:
            line += f"; {failed} failed"
        self._operations.append(line)

    def _path(self, group: _Group, row: int) -> str:
        position = int(group.owners[row])
        geometry = self._document.geometries[group.members[position]]
        return geometry.position_path(row - int(group.starts[position]))

    def _value_order(
        self, rows: FloatArray, crs: CoordinateReferenceSystem, axis_order: str
    ) -> FloatArray:
        if axis_order == "xy":
            return rows.copy()
        return rows[:, _permutation(crs, rows.shape[1], to_values=True)]

    def _heights(
        self,
        heights: FloatArray,
        source: Source,
        crs: CoordinateReferenceSystem,
        transformation: Transformation,
    ) -> FloatArray:
        """Heights in the unit to write them in, noting that unit."""
        produced = transformation.target_crs
        unit: Unit | None
        if produced.dimension == 3:
            unit = _third_unit(produced)
        elif _height_axis(crs) is not None or crs.dimension == 3:
            unit = _third_unit(crs)
        else:
            unit = source.z_unit
        wanted = self._target.z_unit
        if wanted is not None:
            if unit is None:
                raise ValueError(
                    f"{source.stated_by} does not state the unit of its heights, "
                    f"so they cannot be written in {wanted.symbol!r}"
                )
            if unit.scale != wanted.scale:
                heights = heights * (unit.scale / wanted.scale)
                line = f"heights converted from {unit.symbol} to {wanted.symbol}"
                if line not in self._operations:
                    self._operations.append(line)
            unit = wanted
        self._z_unit = self._z_unit or unit
        return heights

    def _fallback_objects(self) -> dict[int, Any]:
        """For ``jsonfg-plus``, each feature's WGS 84 geometry, by feature."""
        objects: dict[int, Any] = {}
        for index, rows in self._fallback.items():
            self._document.geometries[index].write(rows.tolist())
        for index in self._fallback:
            feature = self._document.geometries[index].feature
            if feature is None or feature in objects:
                continue
            target = self._document.features[feature].target
            place = target.get("place")
            copied = copy.deepcopy(
                place if place is not None else target.get("geometry")
            )
            _plain_types(copied)
            _rebox(copied)
            objects[feature] = copied
        return objects

    def _write(self, group: _Group, rows: FloatArray) -> None:
        crs = self._transformation(
            group.crs, group.dimension, fallback=False
        ).target_crs
        for position, index in enumerate(group.members):
            geometry = self._document.geometries[index]
            start = int(group.starts[position])
            values = rows[start : start + geometry.count]
            if self._orient is not None:
                values = _oriented(geometry, values, self._orient)
            if self._place:
                values = values[:, _permutation(crs, values.shape[1], to_values=False)]
            self._written[index] = values
            geometry.write(values.tolist())

    def _place_features(self, fallbacks: dict[int, Any]) -> None:
        """Put each feature's converted geometry where the output flavour wants it."""
        for number, feature in enumerate(self._document.features):
            target = feature.target
            place = target.get("place")
            primary = place if place is not None else target.get("geometry")
            secondary = target.get("geometry") if place is not None else None
            if self._place:
                if primary is None:
                    continue
                if secondary is None:
                    secondary = fallbacks.get(number)
                _put(target, "geometry", secondary)
                _put(target, "place", primary, after="geometry")
            else:
                _put(target, "geometry", primary)
                target.pop("place", None)

    def _withdraw(self) -> None:
        if self._on_error != "withdraw":
            return
        for index in sorted(self._failed):
            feature = self._document.geometries[index].feature
            if feature is not None:
                self._document.withdraw(self._document.features[feature])

    def _write_bounding_boxes(self) -> None:
        document = self._document
        withdrawn = document.withdrawn
        # A feature's or collection's bbox; one inside a geometry object
        # bounds that object's own positions.
        containers = {id(feature.target) for feature in document.features}
        if document.kind is Kind.FEATURE_COLLECTION:
            containers.add(id(document.root))
        for box in document.bounding_boxes:
            if box.target.get("bbox") is None:
                continue
            indices = [i for i in box.geometries if i not in withdrawn]
            if id(box.target) in containers:
                fallback = [i for i in indices if i in self._fallback or self._kept(i)]
                chosen = fallback if self._place and fallback else indices
                rows = [self._bbox_rows(i) for i in chosen]
            else:
                rows = [self._own_rows(i) for i in indices]
            bounds = _box(rows, len(box.target["bbox"]) // 2)
            if bounds is None:
                box.target.pop("bbox", None)
            else:
                box.target["bbox"] = bounds

    def _kept(self, index: int) -> bool:
        """Whether a geometry is a feature's WGS 84 geometry kept beside its place."""
        return index not in self._primary

    def _bbox_rows(self, index: int) -> FloatArray | None:
        if index in self._fallback:
            return self._fallback[index]
        return self._own_rows(index)

    def _own_rows(self, index: int) -> FloatArray | None:
        """The positions a geometry is written with."""
        if index in self._written:
            return self._written[index]
        if self._place and self._kept(index):
            return self._document.geometries[index].positions
        return None

    def _write_members(self) -> None:
        """Write the members that state the CRS, as the output flavour does."""
        document = self._document
        root = document.root
        flavour = self._target.flavour
        document.retype(
            Flavour.ANYCRS if flavour is Flavour.ANYCRS else Flavour.GEOJSON
        )
        root.pop(LEGACY_CRS, None)
        for target, _ in document.typed:
            if target is not root:
                target.pop(COORD_REF_SYS, None)
        if flavour is not Flavour.JSONFG:
            _drop_jsonfg_conformance(root)
            root.pop(COORD_REF_SYS, None)
        if flavour is Flavour.ANYCRS:
            self._write_anycrs(root)
            return
        for key in ANYCRS_MEMBERS:
            root.pop(key, None)
        if flavour is Flavour.GEOJSON:
            return
        conformance = root.get("conformsTo")
        uris = list(conformance) if isinstance(conformance, list) else []
        if JSONFG_CORE not in uris:
            uris.insert(0, JSONFG_CORE)
        _put(root, "conformsTo", uris)
        if self._place:
            self._write_coord_ref_sys(root)
        else:
            root.pop(COORD_REF_SYS, None)

    def _write_coord_ref_sys(self, root: dict[str, Any]) -> None:
        """The target's ``coordRefSys``, with the epoch of a dynamic target.

        JSON-FG states one ``coordRefSys`` for a feature collection, on the
        collection, so positions at different epochs cannot be written in one.
        """
        if not self._target.crs.is_dynamic:
            _put(root, COORD_REF_SYS, self._target.coord_ref_sys(None))
            return
        distinct = set(self._epochs.values())
        if len(distinct) > 1:
            stated = ", ".join(sorted(str(epoch) for epoch in distinct))
            raise ValueError(
                f"JSON-FG states one coordinate epoch for a document, and these "
                f"positions in the dynamic CRS {_label(self._target.crs)} are at "
                f"epochs {stated}; convert them separately, or to a static CRS"
            )
        epoch = next(iter(distinct), None)
        _put(root, COORD_REF_SYS, self._target.coord_ref_sys(epoch))

    def _write_anycrs(self, root: dict[str, Any]) -> None:
        target = self._target
        if target.record_id is None:
            root.pop("CoordinateReferenceSystemID", None)
        else:
            _put(root, "CoordinateReferenceSystemID", target.record_id)
        _put(root, "persistableReferenceCrs", target.crs_reference())
        heights = 3 in self._document.dimensions
        vertical = target.vertical_reference()
        # Heights carried through stay in the vertical CRS they were stated in.
        carried = (
            vertical is None
            and heights
            and self._target.crs.dimension == 2
            and any(source.vertical is not None for source in self._sources)
        )
        if not carried:
            root.pop("VerticalCoordinateReferenceSystemID", None)
            if vertical is None:
                root.pop("persistableReferenceVerticalCrs", None)
            else:
                _put(root, "persistableReferenceVerticalCrs", vertical)
        root.pop("VerticalUnitID", None)
        if heights and self._z_unit is not None:
            _put(root, "persistableReferenceUnitZ", self._z_unit.reference())
        else:
            root.pop("persistableReferenceUnitZ", None)


def _put(
    target: dict[str, Any], key: str, value: Any, *, after: str | None = None
) -> None:
    """Set a member, in its place if it has one, else after ``after`` or last."""
    if key in target or after is None or after not in target:
        target[key] = value
        return
    items = list(target.items())
    target.clear()
    for name, item in items:
        target[name] = item
        if name == after:
            target[key] = value


def _drop_jsonfg_conformance(root: dict[str, Any]) -> None:
    conformance = root.get("conformsTo")
    if not isinstance(conformance, list):
        return
    kept = [
        uri
        for uri in conformance
        if not (isinstance(uri, str) and uri.startswith(_JSONFG_PREFIXES))
    ]
    if kept:
        root["conformsTo"] = kept
    else:
        root.pop("conformsTo")


def _permutation(
    crs: CoordinateReferenceSystem, width: int, *, to_values: bool
) -> list[int]:
    """Columns that reorder positions between declared and value order."""
    order = crs.value_axis_order
    count = min(width, len(order))
    moved = list(order[:count])
    if sorted(moved) != list(range(count)):
        raise ValueError(
            f"positions of {width} values cannot be put in the declared order of "
            f"{_label(crs)}, which has {len(order)} axes"
        )
    if not to_values:
        moved = [int(axis) for axis in np.argsort(moved)]
    return [*moved, *range(count, width)]


def _height_axis(crs: CoordinateReferenceSystem) -> int | None:
    for index, axis in enumerate(crs.axes):
        if axis.direction.lower() in _HEIGHT_DIRECTIONS:
            return index
    return None


def _to_axis_unit(
    heights: FloatArray, unit: Unit | None, crs: CoordinateReferenceSystem
) -> FloatArray:
    """Third values in the unit of the CRS's height axis, or its third axis."""
    if unit is None or (_height_axis(crs) is None and crs.dimension != 3):
        return heights
    axis = _third_unit(crs)
    if axis.scale == unit.scale:
        return heights
    return heights * (unit.scale / axis.scale)


def _height_input(
    heights: FloatArray,
    source: Source,
    crs: CoordinateReferenceSystem,
    produced: CoordinateReferenceSystem,
) -> FloatArray:
    """Third values in the unit of the axis that takes them.

    That of the source CRS when it has three axes. A third value a 2D source
    CRS lacks is carried through to a 2D target, and read by PROJ as the third
    axis of a 3D one, so it is put in that axis's unit.
    """
    if crs.dimension == 3 or produced.dimension != 3:
        return _to_axis_unit(heights, source.z_unit, crs)
    if source.z_unit is None:
        raise ValueError(
            f"{source.stated_by} does not state the unit of its heights, so they "
            f"cannot be written as heights of {_label(produced)}"
        )
    unit = _third_unit(produced)
    if unit.scale == source.z_unit.scale:
        return heights
    return heights * (source.z_unit.scale / unit.scale)


def _third_unit(crs: CoordinateReferenceSystem) -> Unit:
    """The unit of a 3D CRS's height axis, or of its third axis without one."""
    index = _height_axis(crs)
    axis = crs.axes[2 if index is None else index]
    if axis.unit_conversion_factor == 1.0:
        return METRE
    return Unit(axis.unit_name, "length", axis.unit_conversion_factor)


def _box(rows: Sequence[FloatArray | None], axes: int) -> list[float] | None:
    """The bbox of the finite positions in ``rows``, or None for none."""
    present = [r[np.isfinite(r).all(axis=1)] for r in rows if r is not None]
    low: list[float] = []
    high: list[float] = []
    for axis in range(axes):
        values = [r[:, axis] for r in present if r.shape[1] > axis and len(r)]
        if not values:
            break
        joined = np.concatenate(values)
        low.append(float(joined.min()))
        high.append(float(joined.max()))
    return None if len(low) < 2 else low + high


def _rebox(value: Any) -> None:
    """Compute again the ``bbox`` of a geometry object and of its members."""
    if not isinstance(value, dict):
        return
    for member in value.get("geometries") or ():
        _rebox(member)
    if value.get("bbox") is None:
        return
    bounds = _box(_object_rows(value), len(value["bbox"]) // 2)
    if bounds is None:
        value.pop("bbox")
    else:
        value["bbox"] = bounds


def _object_rows(value: Any) -> list[FloatArray]:
    """The positions of a geometry object, one array per geometry."""
    if not isinstance(value, dict):
        return []
    if "geometries" in value:
        return [
            rows
            for member in value["geometries"] or ()
            for rows in _object_rows(member)
        ]
    positions = _json_positions(value.get("coordinates"))
    return [np.array(positions, dtype=np.float64)] if positions else []


def _json_positions(value: Any) -> list[list[float]]:
    if not isinstance(value, list) or not value:
        return []
    if isinstance(value[0], int | float):
        return [value]
    return [position for item in value for position in _json_positions(item)]


def _plain_types(value: Any) -> None:
    """Name the types of a geometry object and its members as GeoJSON does."""
    if not isinstance(value, dict):
        return
    kind = value.get("type")
    if isinstance(kind, str):
        value["type"] = kind.removeprefix(ANYCRS_PREFIX)
    for member in value.get("geometries") or ():
        _plain_types(member)


def _rounded(rows: FloatArray, decimals: Decimals) -> FloatArray:
    if decimals is None:
        return rows
    columns = round_columns([rows[:, axis] for axis in range(rows.shape[1])], decimals)
    return np.column_stack(columns)


def _oriented(geometry: Geometry, rows: FloatArray, orient: Orientation) -> FloatArray:
    """``rows`` with each ring of a polygon wound as ``orient`` asks."""
    out = rows.copy()
    start = 0
    for polygon in _ring_sizes(geometry):
        for number, size in enumerate(polygon):
            ring = out[start : start + size]
            area = float(
                np.dot(ring[:-1, 0], ring[1:, 1]) - np.dot(ring[1:, 0], ring[:-1, 1])
            )
            counterclockwise = area > 0
            if counterclockwise != ((number == 0) == (orient == "ccw")):
                out[start : start + size] = ring[::-1]
            start += size
    return out


def _ring_sizes(geometry: Geometry) -> list[list[int]]:
    """The number of positions in each ring, polygon by polygon."""
    shape = geometry.shape
    if geometry.kind not in (Kind.POLYGON, Kind.MULTI_POLYGON) or not isinstance(
        shape, tuple
    ):
        return []
    polygons = [shape] if geometry.kind is Kind.POLYGON else list(shape)
    return [
        [size for size in polygon if isinstance(size, int)]
        for polygon in polygons
        if isinstance(polygon, tuple)
    ]


def _label(crs: CoordinateReferenceSystem) -> str:
    return crs.authority_code or repr(crs.name)
