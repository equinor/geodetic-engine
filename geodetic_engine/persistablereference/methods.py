"""What ESRI calls a transformation method, said in EPSG's terms.

A ``GEOGTRAN`` names its method and parameters in ESRI's vocabulary and states
no units at all, because ESRI fixes one unit per parameter kind: translations
are metres, rotations are arc-seconds, a scale difference is parts per million,
and an offset is degrees. EPSG names the same methods and parameters
differently, and states the unit explicitly. These tables are that
correspondence, and they are the only place in this package where one
vocabulary is turned into the other.

Everything here is a static table rather than a lookup in a vendor database.
The set is small, it changes at the pace of the EPSG register, and a table that
is wrong is caught by
the method and parameter code tests in ``tests/persistablereference/test_methods.py``,
which check every code against the transformations PROJ itself ships.

A method that is absent is refused by name, with the reason, rather than
translated into whichever EPSG method takes a similar list of parameters.
Several of the absent ones differ from a supported method only in a convention
or a datum-independent term, so a near miss here produces coordinates that are
plausible and wrong.

Grid-based methods are not in the table. ESRI names their grid by dataset,
sometimes with a regional prefix -- ``Dataset_conus``,
``Dataset_canada/Ntv2_0`` -- and the method, the EPSG parameters and the file
names that go with it are all read from PROJ's own database instead. See
:func:`grid`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from geodetic_engine.geodesy.database import GridDataset, grid_dataset
from geodetic_engine.persistablereference.errors import (
    UnresolvableGridError,
    UnsupportedMethodError,
)

type Unit = str | dict[str, Any]

# The four units a GEOGTRAN parameter is implicitly stated in. PROJJSON writes
# metre and degree as bare names and anything else as an object.
METRE: Unit = "metre"
DEGREE: Unit = "degree"
ARC_SECOND: Unit = {
    "type": "AngularUnit",
    "name": "arc-second",
    "conversion_factor": 4.84813681109536e-06,
}
PARTS_PER_MILLION: Unit = {
    "type": "ScaleUnit",
    "name": "parts per million",
    "conversion_factor": 1e-06,
}

# ESRI writes a grid reference as a parameter whose *name* carries the dataset,
# optionally behind a regional folder: PARAMETER["Dataset_canada/Ntv2_0",0.0].
GRID_PARAMETER_PREFIX = "Dataset_"


@dataclass(frozen=True, slots=True)
class Parameter:
    """One transformation parameter, in EPSG's terms.

    Attributes:
        code: EPSG parameter code, for example ``8605``.
        name: EPSG parameter name, for example ``"X-axis translation"``.
        unit: The unit ESRI states this parameter in, as PROJJSON writes it.
    """

    code: int
    name: str
    unit: Unit


@dataclass(frozen=True, slots=True)
class Method:
    """One transformation method, in EPSG's terms.

    Attributes:
        code: EPSG method code, for example ``9606``.
        name: EPSG method name.
        parameters: The ESRI parameter names this method takes, in the order
            EPSG states them. A ``GEOGTRAN`` stating any other set is refused.
    """

    code: int
    name: str
    parameters: tuple[str, ...]


_TRANSLATIONS = ("X_Axis_Translation", "Y_Axis_Translation", "Z_Axis_Translation")
_ROTATIONS = ("X_Axis_Rotation", "Y_Axis_Rotation", "Z_Axis_Rotation")
_HELMERT = (*_TRANSLATIONS, *_ROTATIONS, "Scale_Difference")
_PIVOT = (
    "X_Coordinate_of_Rotation_Origin",
    "Y_Coordinate_of_Rotation_Origin",
    "Z_Coordinate_of_Rotation_Origin",
)

PARAMETERS: dict[str, Parameter] = {
    "X_Axis_Translation": Parameter(8605, "X-axis translation", METRE),
    "Y_Axis_Translation": Parameter(8606, "Y-axis translation", METRE),
    "Z_Axis_Translation": Parameter(8607, "Z-axis translation", METRE),
    "X_Axis_Rotation": Parameter(8608, "X-axis rotation", ARC_SECOND),
    "Y_Axis_Rotation": Parameter(8609, "Y-axis rotation", ARC_SECOND),
    "Z_Axis_Rotation": Parameter(8610, "Z-axis rotation", ARC_SECOND),
    "Scale_Difference": Parameter(8611, "Scale difference", PARTS_PER_MILLION),
    "X_Coordinate_of_Rotation_Origin": Parameter(
        8617, "Ordinate 1 of evaluation point", METRE
    ),
    "Y_Coordinate_of_Rotation_Origin": Parameter(
        8618, "Ordinate 2 of evaluation point", METRE
    ),
    "Z_Coordinate_of_Rotation_Origin": Parameter(
        8667, "Ordinate 3 of evaluation point", METRE
    ),
    "Latitude_Offset": Parameter(8601, "Latitude offset", DEGREE),
    "Longitude_Offset": Parameter(8602, "Longitude offset", DEGREE),
}

METHODS: dict[str, Method] = {
    "Geocentric_Translation": Method(
        9603, "Geocentric translations (geog2D domain)", _TRANSLATIONS
    ),
    "Position_Vector": Method(
        9606, "Position Vector transformation (geog2D domain)", _HELMERT
    ),
    # "In the Projection Engine, the Coordinate Frame and Bursa-Wolf methods are
    # the same": ArcSDE 10.0 SDK, Geographic transformation methods.
    "Bursa_Wolf": Method(9607, "Coordinate Frame rotation (geog2D domain)", _HELMERT),
    "Coordinate_Frame": Method(
        9607, "Coordinate Frame rotation (geog2D domain)", _HELMERT
    ),
    "Molodensky_Badekas": Method(
        9636, "Molodensky-Badekas (CF geog2D domain)", (*_HELMERT, *_PIVOT)
    ),
    "Longitude_Rotation": Method(9601, "Longitude rotation", ("Longitude_Offset",)),
    "Geographic_2D_Offset": Method(
        9619, "Geographic2D offsets", ("Latitude_Offset", "Longitude_Offset")
    ),
}

# Methods that read a grid. The method itself is not fixed here: it is whatever
# PROJ's database says the named grid is read by, which is also where the EPSG
# parameter codes and the real file names come from.
GRID_METHODS = frozenset({"nadcon", "ntv2", "harn"})

# Why each unsupported method is refused. Stated per method, because "this is
# not supported" without the reason invites working around it by picking the
# nearest method that is.
REFUSED: dict[str, str] = {
    "Molodensky": (
        "ESRI states no semi-major axis or flattening difference for it, so "
        "the EPSG form cannot be stated without inferring both from the two "
        "ellipsoids"
    ),
    "Molodensky_Abridged": (
        "ESRI states no semi-major axis or flattening difference for it, so "
        "the EPSG form cannot be stated without inferring both from the two "
        "ellipsoids"
    ),
    "Molodensky_Badekas_Position_Vector": (
        "EPSG defines Molodensky-Badekas in the position vector convention "
        "only for geocentric coordinates, not for the geographic 2D domain a "
        "GEOGTRAN acts on"
    ),
    "Time_Based_Helmert_Position_Vector": (
        "a 14-parameter Helmert reads a coordinate epoch, which a "
        "persistableReference does not carry"
    ),
    "Time_Based_Helmert_Coordinate_Frame": (
        "a 14-parameter Helmert reads a coordinate epoch, which a "
        "persistableReference does not carry"
    ),
    "Time-specific_Position_Vector_transform_geocen": (
        "it is defined only at its own transformation epoch, which a "
        "persistableReference does not carry"
    ),
    "Reversible_polynomial_of_degree_4": (
        "EPSG's reversible polynomial takes evaluation-point and scaling "
        "terms that ESRI states under different names, and a mistranslated "
        "coefficient is not detectable from the result"
    ),
    "GEOCON": "it reads a grid format PROJ does not ship a reader for",
    "NADCON5": (
        "it reads a set of grids ESRI names as one dataset, which does not "
        "resolve to the single EPSG grid reference PROJ expects"
    ),
    "NTv2_Velocity": (
        "it reads a velocity grid that needs a coordinate epoch, which a "
        "persistableReference does not carry"
    ),
    "Null": "ESRI states no parameters for it, so there is nothing to translate",
    "Unit_Change": (
        "it states a change of unit rather than of datum, which belongs to the "
        "CRS definitions either side rather than to a transformation"
    ),
}


def method(name: str) -> Method:
    """Translate an ESRI method name into its EPSG method.

    Args:
        name: The name a ``GEOGTRAN`` states, matched without case.

    Returns:
        The EPSG method.

    Raises:
        UnsupportedMethodError: If the method reads a grid, which
            :func:`grid` handles instead, or is one this package refuses, or is
            not known at all.

    Example:
        >>> method("Position_Vector").code
        9606
    """
    wanted = name.casefold()
    for esri, found in METHODS.items():
        if esri.casefold() == wanted:
            return found
    if wanted in GRID_METHODS:
        raise UnsupportedMethodError(
            f"{name} reads a grid and has no parameters to translate; resolve "
            f"its dataset instead"
        )
    for esri, reason in REFUSED.items():
        if esri.casefold() == wanted:
            raise UnsupportedMethodError(f"{name} is not supported because {reason}")
    raise UnsupportedMethodError(
        f"{name} is not a transformation method this package knows; the ones "
        f"it does are {', '.join(sorted(METHODS))} and "
        f"{', '.join(sorted(GRID_METHODS))}"
    )


def parameter(name: str) -> Parameter:
    """Translate an ESRI parameter name into its EPSG parameter.

    Args:
        name: The name a ``PARAMETER`` states, matched without case.

    Returns:
        The EPSG parameter, with the unit ESRI implies for it.

    Raises:
        UnsupportedMethodError: If the parameter is not one this package knows.
    """
    wanted = name.casefold()
    for esri, found in PARAMETERS.items():
        if esri.casefold() == wanted:
            return found
    raise UnsupportedMethodError(
        f"{name} is not a transformation parameter this package knows"
    )


def is_grid_method(name: str) -> bool:
    """Whether an ESRI method name reads a grid rather than parameters."""
    return name.casefold() in GRID_METHODS


# What to call an EPSG method when writing ESRI WKT. Needed because the
# correspondence is not one to one: ESRI has two names for the seven parameter
# coordinate frame method, and reads three grid methods this package resolves
# through PROJ's database rather than through METHODS.
_WRITTEN_AS: dict[int, str] = {
    9606: "Position_Vector",
    9607: "Coordinate_Frame",
    9613: "NADCON",
    9615: "NTv2",
}


def esri_method(code: int) -> str:
    """The ESRI name to write an EPSG method under.

    Args:
        code: EPSG method code.

    Returns:
        The ESRI method name.

    Raises:
        UnsupportedMethodError: If ESRI has no name for the method, in which
            case there is no ``GEOGTRAN`` to write.
    """
    if (written := _WRITTEN_AS.get(code)) is not None:
        return written
    for name, found in METHODS.items():
        if found.code == code:
            return name
    raise UnsupportedMethodError(
        f"EPSG method {code} has no ESRI equivalent this package knows, so it "
        f"cannot be written as a GEOGTRAN"
    )


def esri_parameter(code: int) -> Parameter:
    """The ESRI name and implicit unit of an EPSG parameter.

    Args:
        code: EPSG parameter code.

    Returns:
        The parameter, whose ESRI name is looked up by :func:`esri_name`.

    Raises:
        UnsupportedMethodError: If ESRI has no equivalent parameter.
    """
    for found in PARAMETERS.values():
        if found.code == code:
            return found
    raise UnsupportedMethodError(
        f"EPSG parameter {code} has no ESRI equivalent this package knows"
    )


def esri_name(parameter: Parameter) -> str:
    """The ESRI name of a parameter this package knows."""
    return next(name for name, found in PARAMETERS.items() if found is parameter)


def factor(unit: Unit) -> float:
    """How many SI base units one of this unit is.

    Args:
        unit: A unit as PROJJSON states it, either a bare name or an object.

    Returns:
        The conversion factor to the SI base unit of the same quantity.

    Raises:
        UnsupportedMethodError: If the unit states no factor, which leaves no
            way to restate a value in it.
    """
    if isinstance(unit, str):
        if (known := _SHORTHAND.get(unit)) is not None:
            return known
        raise UnsupportedMethodError(f"unit {unit!r} states no conversion factor")
    found = unit.get("conversion_factor")
    if not isinstance(found, int | float):
        raise UnsupportedMethodError(f"unit {unit} states no conversion factor")
    return float(found)


# PROJJSON writes these three as a bare name rather than an object.
_SHORTHAND: dict[str, float] = {
    "metre": 1.0,
    "degree": 0.017453292519943295,
    "unity": 1.0,
}


def grid(dataset: str) -> GridDataset:
    """Resolve an ESRI dataset name to the grid transformation PROJ defines.

    Args:
        dataset: The parameter name a grid-based ``GEOGTRAN`` states, with or
            without its ``Dataset_`` prefix and regional folder.

    Returns:
        The method and grid file parameters PROJ's database states for it.

    Raises:
        UnresolvableGridError: If the database references no such grid, or
            references it from transformations that disagree, which leaves no
            single definition to build.

    Example:
        >>> grid("Dataset_canada/Ntv2_0").method_name  # doctest: +SKIP
        'NTv2'
    """
    name = dataset
    if name.casefold().startswith(GRID_PARAMETER_PREFIX.casefold()):
        name = name[len(GRID_PARAMETER_PREFIX) :]
    # The regional folder is ESRI's own filing, not part of the grid's name.
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if (found := grid_dataset(name)) is None:
        raise UnresolvableGridError(
            f"grid dataset {dataset!r} does not resolve to a single grid "
            f"transformation in PROJ's database"
        )
    return found
