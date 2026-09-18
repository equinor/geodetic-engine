"""A persistableReference transforms exactly as the same definition named by code.

This is the claim the whole package rests on: a definition read out of an OSDU
payload has to move a point to the same place as the authority code beside it,
resolved against PROJ's own database. If the two ever disagree, one of them is
wrong and neither says which.

Each case in ``worked_examples.json`` states the same transformation twice --
once entirely through persistableReference payloads, once through authority
codes alone -- and both are run over points inside the area of use of the CRSs
they are given in. Comparing anywhere else would be comparing two
extrapolations.

Two shapes are covered, because OSDU publishes both: an early bound payload
carrying the CRS and its datum shift together, which leaves the operation
implicit in the CRS, and a CRS record published apart from the transformation
record, which leaves the operation to be submitted explicitly at the point of
use. Either way the payload's own parameters are what gets applied. Either end
may be the bound one, so a case can run a datum shift in reverse as readily as
forwards.

The file is data, so a new case is a new entry rather than a new test. Reading
one and building the CRSs it states is
:mod:`tests.persistablereference.utils.examples`; every call into the package
under test is made here, so a failing case can be stepped through from the test
that failed.
"""

from __future__ import annotations

from geodetic_engine.geodesy import TransformationResult, transform
from tests.persistablereference.utils import (
    BOUND,
    LATE_BOUND,
    Point,
    WorkedExample,
    separation_between_point_m,
)


def transform_by_reference(example: WorkedExample) -> TransformationResult:
    """Transform the case's points with everything built from payloads alone.

    An early bound payload carries its datum shift, so naming one would be
    naming what the CRS already settles: the operation argument stays empty and
    the transformation is taken implicitly from the bound end. A late bound
    case carries it on neither end, so the transformation the case states
    separately is submitted explicitly -- as the payload itself, whose
    parameters are then what PROJ applies.
    """
    src_crs = example.source_by_reference()
    trg_crs = example.target_by_reference()
    if src_crs.is_bound or trg_crs.is_bound:
        return transform(
            src_crs,
            trg_crs,
            list(example.points),
        )
    return transform(
        src_crs,
        trg_crs,
        list(example.points),
        operation=example.operation_by_reference(),
    )


def transform_by_code(example: WorkedExample) -> TransformationResult:
    """Transform the same points with every end named by authority code."""
    src_crs_code = example.by_code["source_crs"]
    trg_crs_code = example.by_code["target_crs"]
    op_code = example.by_code["operation"]
    return transform(
        src_crs_code,
        trg_crs_code,
        list(example.points),
        operation=op_code,
    )


def points_in_degrees(example: WorkedExample) -> tuple[Point, ...]:
    """The case's points as longitude and latitude, to compare with an area.

    A projected source is unprojected onto its own base frame, which names no
    transformation and so cannot itself be what a comparison below is reading.
    """
    base = example.geographic_base()
    if base is None:
        return example.points
    unprojected = transform(example.source_by_reference(), base, list(example.points))
    return tuple(tuple(point) for point in unprojected.coordinates)


def test_payloads_and_codes_agree(example: WorkedExample) -> None:
    """The payload route and the code route put every point in the same place."""
    from_payloads = transform_by_reference(example)
    from_codes = transform_by_code(example)

    for point, mine, theirs in zip(
        example.points, from_payloads.coordinates, from_codes.coordinates, strict=True
    ):
        apart = separation_between_point_m(from_codes.target_crs, mine, theirs)
        assert apart <= example.tolerance_m, (
            f"{example.name}: {point} lands {apart:.6f} m apart -- "
            f"payload {mine}, code {theirs}"
        )


def test_the_payload_route_applies_the_operation_the_case_names(
    example: WorkedExample,
) -> None:
    """Agreeing by accident is still disagreeing, so check what was applied.

    The payload states the operation's parameters rather than its code, so PROJ
    reports it under the name ESRI gave it, whether it arrived embedded in a
    bound CRS or as the operation argument. That this is the same operation the
    code route names is what the numbers above rest on.

    A datum shift is published in one direction only, so a case running the
    other way applies the same operation inverted, and PROJ says so in the name.
    """
    applied = transform_by_reference(example).operation
    stated = example.operation_by_reference().name

    collapsed = f"{stated} (collapsed to a single step)"
    assert applied.name in (
        stated,
        collapsed,
        f"Inverse of {stated}",
        f"Inverse of {collapsed}",
    )


def test_every_point_is_inside_the_areas_both_ends_are_published_for(
    example: WorkedExample,
) -> None:
    """A tolerance means nothing where both routes are extrapolating."""
    areas = example.areas_of_use()
    assert areas, example.by_code

    for longitude, latitude in points_in_degrees(example):
        for code, area in areas.items():
            west, south, east, north = area.bounds
            assert west <= longitude <= east, (
                f"{longitude} outside {code} {area.bounds}"
            )
            assert south <= latitude <= north, (
                f"{latitude} outside {code} {area.bounds}"
            )


def test_each_case_states_the_shape_its_kind_claims(example: WorkedExample) -> None:
    """A bound case carries its transformation; a late bound one states it apart."""
    assert example.kind in (BOUND, LATE_BOUND)
    assert (example.operation is None) == (example.kind == BOUND)
    # A bound case settles the datum change at exactly one end; a late bound one
    # settles it at neither, leaving the operation to be named at use.
    assert sum(example.bound_ends()) == (1 if example.kind == BOUND else 0)
