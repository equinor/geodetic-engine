"""Reading ``worked_examples.json`` and stating each case both ways.

The file states a transformation twice: once entirely through
persistableReference payloads, once through authority codes alone. Turning one
of its entries into the two definitions that can then be compared lives here.

Transforming them does not: the test module beside it makes every call into the
package under test itself, so a failing case can be stepped through from the
test rather than from here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyproj import CRS, Geod
from pyproj.aoi import AreaOfUse

from geodetic_engine.geodesy import CoordinateReferenceSystem
from geodetic_engine.persistablereference import (
    CrsReference,
    OperationReference,
    parse_persistable_reference,
)

EXAMPLES = Path(__file__).parents[1] / "worked_examples.json"

BOUND = "bound"
"""One early bound payload carrying the CRS and its datum shift together."""

LATE_BOUND = "late_bound"
"""A CRS record and a transformation record, published apart and named together."""

type Point = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class WorkedExample:
    """One case from the file, able to state itself either way.

    Attributes:
        name: What the case is called, for a failure message.
        kind: :data:`BOUND` or :data:`LATE_BOUND`.
        points: The points to transform, in the source CRS's own units.
        tolerance_m: How far apart the two routes may land, in metres.
        source: The source CRS payload and the OSDU record it came from.
        target: The target CRS payload and its record.
        operation: The transformation payload, for a late bound case only.
            Neither end carries it, so it is named alongside them instead.
        by_code: The same source, target and operation as authority codes.
    """

    name: str
    kind: str
    points: tuple[Point, ...]
    tolerance_m: float
    source: dict[str, Any]
    target: dict[str, Any]
    operation: dict[str, Any] | None
    by_code: dict[str, str]

    @property
    def identifier(self) -> str:
        """A short name for this case, used to label its parametrised runs."""
        ends = "-to-".join(
            self.by_code[end].replace(":", "") for end in ("source_crs", "target_crs")
        )
        return f"{self.kind}-{ends}"

    def source_by_reference(self) -> CRS:
        """Build the source CRS from its payload alone.

        An early bound payload comes back already carrying its datum shift. A
        late bound one comes back plain, for the transformation the case states
        separately to be named beside it rather than embedded in it.
        """
        return _crs_reference(self.source).to_crs()

    def target_by_reference(self) -> CRS:
        """Build the target CRS from its payload alone."""
        return _crs_reference(self.target).to_crs()

    def bound_ends(self) -> tuple[bool, bool]:
        """Whether the source and the target each carry a datum shift."""
        return (
            self.source_by_reference().is_bound,
            self.target_by_reference().is_bound,
        )

    def operation_by_reference(self) -> OperationReference:
        """The transformation the case states, wherever it states it.

        Raises:
            ValueError: If the case states none at either end.
        """
        if self.operation is not None:
            stated = parse_persistable_reference(self.operation["reference"])
            if not isinstance(stated, OperationReference):
                raise ValueError(f"{self.name}: the operation payload states no CT")
            return stated
        for end in (self.source, self.target):
            if (embedded := _crs_reference(end).operation) is not None:
                return embedded
        raise ValueError(f"{self.name}: no transformation is stated anywhere")

    def areas_of_use(self) -> dict[str, AreaOfUse]:
        """The area each end is published for, by the code that names it.

        Both ends matter: a point inside the source's area can still be outside
        the target's, and the comparison is only meaningful where neither route
        is extrapolating.
        """
        found: dict[str, AreaOfUse] = {}
        for code in (self.by_code["source_crs"], self.by_code["target_crs"]):
            crs = CoordinateReferenceSystem.from_user_input(code)
            if (area := crs.crs.area_of_use) is not None:
                found[code] = area
        return found

    def geographic_base(self) -> CRS | None:
        """The frame the case's points must be unprojected onto to read as degrees.

        None when the source is geographic and its points already are longitude
        and latitude. Otherwise the source's own base frame, so unprojecting is
        a change of representation and not of datum: no transformation has to
        be named and none of it depends on the routes under test.

        That frame may differ from the one an area of use is quoted against by
        the width of a datum shift, which is immaterial to a bounding box
        spanning degrees.
        """
        source = self.source_by_reference()
        if source.is_geographic:
            return None
        base = source.source_crs if source.is_bound else source
        return base.geodetic_crs


def examples() -> list[WorkedExample]:
    """Every case in the file, in the order it states them."""
    document = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    return [
        WorkedExample(
            name=case["name"],
            kind=case["kind"],
            points=tuple(tuple(point) for point in case["points"]),
            tolerance_m=float(case["tolerance_m"]),
            source=case["source_crs"],
            target=case["target_crs"],
            operation=case.get("operation"),
            by_code=case["by_code"],
        )
        for case in document["cases"]
    ]


def separation_between_point_m(
    crs: CoordinateReferenceSystem, first: Point, second: Point
) -> float:
    """How far apart two points in one CRS are, in metres.

    Degrees are not metres and a projected unit need not be either, so the
    comparison is made in the one unit a tolerance can be stated in.
    """
    if crs.is_geographic:
        _, _, distance = Geod(ellps="WGS84").inv(
            first[0], first[1], second[0], second[1]
        )
        return float(distance)
    factor = crs.axes[0].unit_conversion_factor
    squares = sum((a - b) ** 2 for a, b in zip(first, second, strict=False))
    return float(squares**0.5 * factor)


def _crs_reference(stated: dict[str, Any]) -> CrsReference:
    """Read a CRS payload, refusing anything that is not one.

    Raises:
        ValueError: If the payload states something other than a CRS.
    """
    reference = parse_persistable_reference(stated["reference"])
    if not isinstance(reference, CrsReference):
        raise ValueError(
            f"{stated.get('osdu_record')} states a "
            f"{reference.kind.name.lower().replace('_', ' ')}, not a CRS"
        )
    return reference
