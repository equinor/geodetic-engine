"""A reported pipeline must reproduce the result it was reported with.

Every result carries the PROJ pipeline that produced it, so that the numbers can
be checked and recomputed after the fact. That is only provenance if the
pipeline is runnable and lands in the same place, which is what these tests
assert: this package's own transformation is the truth, and a bare
:class:`pyproj.Transformer` rebuilt from the reported text has to agree with it.

ED50 to WGS 84 is the exercise because EPSG publishes 36 operations between the
pair that PROJ can build, superseded ones included, among them two concatenated
operations (EPSG:8047 and EPSG:8569). PROJ offers a concatenated operation as a
candidate for the geographic pair but not once a projected end is involved, so
those are exactly the cases this package has to chain out of separate
transformers -- and therefore the cases where the single reported pipeline is
assembled here rather than read off something PROJ built.

That sweep is two dimensional throughout, since ED50 has no geographic 3D CRS,
so a second set of cases carries a height across a geocentric leg, a geoid, a
compound target and a dynamic frame at an epoch.
"""

from __future__ import annotations

from functools import cache, lru_cache

import pytest
from pyproj import Transformer

from geodetic_engine.geodesy import (
    OperationCandidate,
    Transformation,
    available_operations,
    transform,
)
from geodetic_engine.geodesy.operation import OperationRoute

ED50 = "EPSG:4230"
WGS84 = "EPSG:4326"

# EPSG codes ED50 / UTM zone N and WGS 84 / UTM zone N by zone number.
_ED50_UTM_BASE = 23000
_WGS84_UTM_BASE = 32600

# The zones ED50 / UTM is defined for. ED50 covers Europe, so an area of use
# never falls outside them; the clamp only keeps a rounding at an edge from
# naming a code that does not exist.
_FIRST_ED50_UTM_ZONE = 28
_LAST_ED50_UTM_ZONE = 38

# The two ED50 to WGS 84 operations EPSG publishes as concatenations rather
# than as a single step.
CONCATENATED = ("EPSG:8047", "EPSG:8569")

# Layouts that put a projected CRS at one or both ends. Named separately
# because these are the ones a concatenated operation has to be chained into.
PROJECTED_LAYOUTS = ("to projected", "from projected", "projected", "projected inverse")
LAYOUTS = ("geographic", "geographic inverse", *PROJECTED_LAYOUTS)


@lru_cache(maxsize=1)
def _candidates() -> dict[str, OperationCandidate]:
    """Every EPSG ED50 to WGS 84 operation PROJ can build, by authority code.

    Superseded operations are kept: a superseded operation is still valid and
    still applied when a caller names it, so its pipeline has to be reportable
    too. Ballpark is excluded because this package refuses it anyway.
    """
    candidates = available_operations(
        ED50, WGS84, allow_superseded=True, allow_ballpark=False
    )
    return {
        candidate.authority_code: candidate
        for candidate in candidates
        if candidate.authority_code
        and candidate.authority_code.startswith("EPSG:")
        and candidate.usable
    }


OPERATIONS = sorted(_candidates())


@cache
def _case(operation: str, layout: str) -> tuple[str, str, tuple[float, float]]:
    """The CRS pair and the input point for one operation and one layout.

    The point is the middle of the operation's own area of use, which is what
    keeps a grid-based operation reading its grid rather than failing outside
    it, and keeps a UTM end in a zone where the projection is well conditioned.

    Returns:
        ``(source, target, point)``, the point in the source CRS's units and in
        ``xy`` value order.
    """
    area = _candidates()[operation].area_of_use
    assert area is not None
    longitude = (area.west + area.east) / 2
    latitude = (area.south + area.north) / 2
    zone = min(
        max(int((longitude + 180) / 6) + 1, _FIRST_ED50_UTM_ZONE),
        _LAST_ED50_UTM_ZONE,
    )
    ed50_utm = f"EPSG:{_ED50_UTM_BASE + zone}"
    wgs84_utm = f"EPSG:{_WGS84_UTM_BASE + zone}"

    geographic = (longitude, latitude)
    # Projecting is a same-datum conversion, so neither needs an operation
    # named and neither introduces a second datum shift into the test point.
    in_ed50_utm = transform(ED50, ed50_utm, geographic).coordinates[0]
    in_wgs84_utm = transform(WGS84, wgs84_utm, geographic).coordinates[0]

    layouts: dict[str, tuple[str, str, tuple[float, ...]]] = {
        "geographic": (ED50, WGS84, geographic),
        "geographic inverse": (WGS84, ED50, geographic),
        "to projected": (ED50, wgs84_utm, geographic),
        "from projected": (ed50_utm, WGS84, in_ed50_utm),
        "projected": (ed50_utm, wgs84_utm, in_ed50_utm),
        "projected inverse": (wgs84_utm, ed50_utm, in_wgs84_utm),
    }
    source, target, point = layouts[layout]
    return source, target, (point[0], point[1])


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("operation", OPERATIONS)
def test_the_reported_pipeline_reproduces_the_result(
    operation: str, layout: str
) -> None:
    """Rebuilding the reported pipeline must land on the same coordinates."""
    source, target, point = _case(operation, layout)

    result = Transformation(source, target, operation=operation).transform([point])

    assert result.pipeline is not None
    replayed = Transformer.from_pipeline(result.pipeline).transform(*point)
    assert replayed == pytest.approx(result.coordinates[0], abs=1e-9)


def test_a_pure_conversion_reports_a_runnable_pipeline() -> None:
    """No datum change either: the map projection alone has to replay too."""
    point = (10.7522, 59.9139)

    result = transform("EPSG:4326", "EPSG:25832", point, operation="EPSG:16032")

    assert result.pipeline is not None
    replayed = Transformer.from_pipeline(result.pipeline).transform(*point)
    assert replayed == pytest.approx(result.coordinates[0], abs=1e-9)


def test_the_matrix_covers_the_concatenated_operations() -> None:
    """A shrinking candidate list must fail rather than quietly test less.

    The concatenated operations are the whole point of the exercise, so their
    absence has to be an error and not simply fewer parameters.
    """
    assert set(CONCATENATED) <= set(OPERATIONS)
    assert len(OPERATIONS) >= 30


def test_only_the_concatenated_operations_need_chaining() -> None:
    """Names which cases above actually exercise pipeline composition.

    A single-step operation is offered by PROJ for the projected pair too, so
    its result reports a pipeline PROJ itself built and composition is a
    pass-through. A concatenated one is not offered once a projected end is
    involved, so this package chains it and writes the single pipeline itself,
    inverting steps where the chain runs backwards. If that stops being true,
    the matrix stops testing composition and this test says so.
    """
    chained = {
        (operation, layout)
        for operation in OPERATIONS
        for layout in LAYOUTS
        if Transformation(
            *_case(operation, layout)[:2], operation=operation
        ).operation.route
        is OperationRoute.CHAINED
    }

    assert chained == {
        (operation, layout)
        for operation in CONCATENATED
        for layout in PROJECTED_LAYOUTS
    }


# A height reaches PROJ through machinery the ED50 sweep never touches: the
# push/pop that protects it across a 2D shift, the cart steps of a geocentric
# leg, the vgridshift of a geoid, and the time-dependent Helmert of a dynamic
# frame. Each case names which of PROJ's three output components the result
# keeps, since a vertical target declares only the height, PROJ's third.
THREE_DIMENSIONAL = [
    pytest.param(
        "EPSG:6319",
        "EPSG:4979",
        "ESRI:108363",
        (-96.0, 40.0, 300.0),
        None,
        (0, 1, 2),
        id="3D Helmert between geographic 3D CRSs",
    ),
    pytest.param(
        "EPSG:4979",
        "EPSG:4978",
        None,
        (10.75, 59.91, 150.0),
        None,
        (0, 1, 2),
        id="geographic to geocentric",
    ),
    pytest.param(
        "EPSG:4978",
        "EPSG:4979",
        None,
        (3149597.958983, 597969.856264, 5495586.583221),
        None,
        (0, 1, 2),
        id="geocentric to geographic",
    ),
    pytest.param(
        "EPSG:4326",
        "EPSG:25832",
        "EPSG:16032",
        (10.75, 59.91, 150.0),
        None,
        (0, 1, 2),
        id="height carried through a 2D pair",
    ),
    pytest.param(
        "EPSG:4979",
        "EPSG:3855",
        "EPSG:3858",
        (10.75, 59.91, 150.0),
        None,
        (2,),
        id="geoid height as the target",
    ),
    pytest.param(
        "EPSG:3855",
        "EPSG:4979",
        "EPSG:3858",
        (10.75, 59.91, 100.0),
        None,
        (0, 1, 2),
        id="geoid height as the source",
    ),
    pytest.param(
        "EPSG:4979",
        "EPSG:5972",
        None,
        (10.75, 59.91, 150.0),
        None,
        (0, 1, 2),
        id="compound projected target",
    ),
    pytest.param(
        "EPSG:7912",
        "EPSG:4937",
        None,
        (10.75, 59.91, 150.0),
        2015.0,
        (0, 1, 2),
        id="dynamic frame at a coordinate epoch",
    ),
]


@pytest.mark.parametrize(
    ("source", "target", "operation", "point", "epoch", "components"),
    THREE_DIMENSIONAL,
)
def test_the_reported_pipeline_reproduces_a_3d_result(
    source: str,
    target: str,
    operation: str | None,
    point: tuple[float, float, float],
    epoch: float | None,
    components: tuple[int, ...],
) -> None:
    """The height has to come back out of the reported pipeline as well."""
    transformation = Transformation(
        source, target, operation=operation, allow_any_operation=operation is None
    )

    result = transformation.transform([point], coordinate_epoch=epoch)

    assert result.pipeline is not None
    replayed = Transformer.from_pipeline(result.pipeline).transform(
        *point, **({"tt": epoch} if epoch is not None else {})
    )
    kept = tuple(float(replayed[index]) for index in components)
    assert kept == pytest.approx(result.coordinates[0], abs=1e-9)


def test_a_vertical_source_pipeline_states_the_transposition_it_needed() -> None:
    """PROJ workaround, see ``_order_horizontal_for_pipeline``.

    A vertical source has no horizontal axes for ``always_xy`` to normalise, so
    PROJ reads the accompanying position latitude-first and this package
    transposes it before handing the values over. That reordering is part of
    what ran, so it is part of what is reported: without it the pipeline
    replays the caller's ``xy`` values transposed and interpolates the geoid at
    the wrong point, which is a wrong height rather than an error.
    """
    swap = "proj=axisswap order=2,1"
    point = (10.75, 59.91, 100.0)
    transformation = Transformation("EPSG:3855", "EPSG:4979", operation="EPSG:3858")
    # Guards the premise: PROJ still leaves the residual swap this undoes, and
    # so already writes one of its own into the pipeline it built.
    built = transformation._pipeline
    assert built.reads_declared_horizontal
    assert built.text is not None

    result = transformation.transform([point])

    assert result.pipeline is not None
    assert result.pipeline.count(swap) == built.text.count(swap) + 1
    replayed = Transformer.from_pipeline(result.pipeline).transform(*point)
    assert tuple(float(value) for value in replayed) == pytest.approx(
        result.coordinates[0], abs=1e-9
    )
