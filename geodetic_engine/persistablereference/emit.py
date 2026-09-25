"""Writing a CRS or a transformation back out as a persistableReference.

PROJ writes ESRI WKT for a coordinate reference system and nothing else. It
will not write a coordinate transformation in that dialect at all, and asking a
bound CRS for its ESRI WKT quietly returns the base CRS alone, with the datum
shift dropped and no error. So the ``GEOGTRAN`` is assembled here, element by
element, through :mod:`geodetic_engine.persistablereference.esriwkt`.

Two things have to be put back that PROJ's own definition does not state in
ESRI's terms:

* **The method and parameter names**, which come from the tables in
  :mod:`geodetic_engine.persistablereference.methods`.
* **The units.** A ``GEOGTRAN`` states none, because ESRI fixes one per
  parameter kind. EPSG states several of the same parameters in other units --
  rotations in microradians, a scale difference in parts per billion -- so
  every value is restated with its own conversion factor before it is written.

Anything that cannot be written exactly is refused. A definition that survives
this and is read back by
:func:`~geodetic_engine.persistablereference.parse_persistable_reference`
describes the same transformation; one that would not is an error rather than a
payload that looks right.
"""

from __future__ import annotations

import json
from math import isclose, isfinite, nan

from pyproj import CRS
from pyproj.crs import CoordinateOperation
from pyproj.enums import WktVersion
from pyproj.exceptions import CRSError

from geodetic_engine.geodesy.operation import has_inverted_step
from geodetic_engine.persistablereference import esriwkt, methods
from geodetic_engine.persistablereference.envelope import (
    AuthorityCode,
    JsonObject,
    Kind,
)
from geodetic_engine.persistablereference.errors import (
    MalformedReferenceError,
    UnsupportedMethodError,
    UnsupportedReferenceError,
)
from geodetic_engine.persistablereference.esriwkt import Node

_TRANSFORMATION_KEYWORD = "GEOGTRAN"
_OFFSET_METHODS = frozenset({"Longitude_Rotation", "Geographic_2D_Offset"})


def to_persistable_reference(
    value: CRS | CoordinateOperation,
    *,
    name: str = "",
    authority: AuthorityCode | None = None,
    version: str = "",
) -> str:
    """Write a CRS or transformation as a persistableReference payload.

    Args:
        value: The CRS to write, bound or not, or the transformation to write.
        name: Name to state, defaulting to the object's own.
        authority: Authority code to stamp on the payload. Omitted by default:
            a code is a claim about a register, and this package will not make
            one on the caller's behalf.
        version: Producer version to state. Omitted by default, because the
            ``PE_`` versions these payloads usually carry name a vendor's
            projection engine release that this package is not.

    Returns:
        The payload, as compact JSON.

    Raises:
        UnsupportedMethodError: If a transformation states a method or
            parameter ESRI has no equivalent for.
        UnsupportedReferenceError: If the object is one this dialect cannot
            state, such as a chain whose steps do not join.
        MalformedReferenceError: If PROJ will not write the CRS as ESRI WKT.

    Example:
        >>> payload = to_persistable_reference(CRS.from_epsg(23032))  # doctest: +SKIP
        >>> crs = parse_persistable_reference(payload).to_crs()  # doctest: +SKIP
        >>> crs.to_authority()  # doctest: +SKIP
        ('EPSG', '23032')
    """
    if isinstance(value, CRS):
        body = _crs_payload(value, name)
    else:
        body = _operation_payload(value, name)
    if authority is not None:
        body["authCode"] = {"auth": authority.authority, "code": authority.code}
    if version:
        body["ver"] = version
    return json.dumps(body, separators=(",", ":"))


def geogtran(operation: CoordinateOperation, name: str = "") -> Node:
    """State one coordinate transformation as an ESRI ``GEOGTRAN``.

    Args:
        operation: A single step transformation between two geographic CRSs.
        name: Name to state, defaulting to the operation's own.

    Returns:
        The ``GEOGTRAN``, ready to be written by
        :func:`~geodetic_engine.persistablereference.esriwkt.write`.

    Raises:
        UnsupportedMethodError: If ESRI has no equivalent for the method or one
            of its parameters.
        UnsupportedReferenceError: If the operation is not a single step
            between two geographic CRSs, states offsets across a prime
            meridian change that ESRI cannot state unambiguously, or states an
            accuracy that is not one non-negative number of metres.
        MalformedReferenceError: If PROJ will not write either CRS as ESRI WKT.
    """
    definition = operation.to_json_dict()
    if definition.get("type") != "Transformation":
        raise UnsupportedReferenceError(
            f"{_described(operation.name)} is a {definition.get('type')}, and a "
            f"{_TRANSFORMATION_KEYWORD} states one transformation"
        )
    # The method is resolved first so that an operation ESRI has no method for
    # is refused by naming the method, which is the actionable half, rather
    # than by naming whichever CRS it happens to go between.
    method = methods.esri_method(_method_code(definition, operation))
    children: list[esriwkt.Child] = [
        name or operation.name or _TRANSFORMATION_KEYWORD,
        _geographic(definition, "source_crs", operation.name),
        _geographic(definition, "target_crs", operation.name),
        Node("METHOD", (method,)),
    ]
    children.extend(_offsets(definition, method, operation.name))
    if isinstance(accuracy := definition.get("accuracy"), str | int | float):
        try:
            metres = float(accuracy)
        except ValueError:
            metres = nan
        if not isfinite(metres) or metres < 0:
            raise UnsupportedReferenceError(
                f"{_described(operation.name)} states accuracy {accuracy!r}, and "
                "OPERATIONACCURACY is one non-negative number of metres"
            )
        children.append(Node("OPERATIONACCURACY", (metres,)))
    if (stamped := _identifier(definition)) is not None:
        children.append(stamped)
    return Node(_TRANSFORMATION_KEYWORD, tuple(children))


def _crs_payload(crs: CRS, name: str) -> JsonObject:
    """State a CRS as an ``LBC``, or as an ``EBC`` when it is bound."""
    if not crs.is_bound:
        return _late_bound(crs, name)
    base, operation = crs.source_crs, crs.coordinate_operation
    if base is None or operation is None:
        raise MalformedReferenceError(
            f"{_described(crs.name)} is a bound CRS PROJ states neither a base "
            f"CRS nor a transformation for"
        )
    return {
        "type": Kind.EARLY_BOUND_CRS.value,
        "name": name or crs.name,
        "lateBoundCRS": _late_bound(base, ""),
        "singleCT": _operation_payload(operation, ""),
    }


def _late_bound(crs: CRS, name: str) -> JsonObject:
    """State a CRS on its own, as an ``LBC``."""
    return {
        "type": Kind.LATE_BOUND_CRS.value,
        "name": name or crs.name,
        "wkt": _esri_wkt(crs),
    }


def _operation_payload(operation: CoordinateOperation, name: str) -> JsonObject:
    """State a transformation as an ``ST``, or a chain as a ``CT``."""
    definition = operation.to_json_dict()
    if has_inverted_step(definition):
        # PROJJSON keeps the forward parameters (OSGeo/PROJ#4866).
        raise UnsupportedReferenceError(
            f"{_described(operation.name)} applies a datum transformation "
            "inverted, which PROJJSON states with its forward parameters, so "
            "writing it would state the transformation the wrong way round"
        )
    if definition.get("type") != "ConcatenatedOperation":
        return {
            "type": Kind.TRANSFORMATION.value,
            "name": name or operation.name,
            "wkt": esriwkt.write(geogtran(operation, name)),
        }
    steps = definition.get("steps") or ()
    return {
        "type": Kind.CONCATENATED_TRANSFORMATION.value,
        "name": name or operation.name,
        "policy": "Concatenated",
        "cts": [
            _operation_payload(CoordinateOperation.from_json_dict(step), "")
            for step in steps
        ],
    }


def _offsets(definition: JsonObject, method: str, described: str) -> list[Node]:
    """State the parameters, as ESRI reads offsets across a prime meridian."""
    parameters = _parameters(definition, described)
    if method not in _OFFSET_METHODS:
        return parameters
    radians = []
    for end in ("source_crs", "target_crs"):
        meridian = CRS.from_json_dict(definition[end]).prime_meridian
        if meridian is None:
            raise MalformedReferenceError(
                f"{_described(described)} goes between frames PROJ states no "
                "prime meridian for"
            )
        radians.append(meridian.longitude * float(meridian.unit_conversion_factor))
    if not (
        difference := (radians[0] - radians[1]) / methods.factor(methods.ARC_SECOND)
    ):
        return parameters
    stated = parameters[0].children[1]
    if (
        method == "Longitude_Rotation"
        and isinstance(stated, int | float)
        and isclose(stated, difference, abs_tol=1e-6)
    ):
        # ESRI writes a pure prime meridian change with no parameter at all.
        return []
    raise UnsupportedReferenceError(
        f"{_described(described)} states offsets between frames on different "
        "prime meridians, which a GEOGTRAN cannot state unambiguously"
    )


def _parameters(definition: JsonObject, described: str) -> list[Node]:
    """State an operation's parameters in ESRI's names and implicit units."""
    stated = list(definition.get("parameters") or ())
    if not stated:
        raise UnsupportedMethodError(
            f"{_described(described)} states no parameters, so there is nothing "
            f"for a {_TRANSFORMATION_KEYWORD} to carry"
        )
    files = [
        parameter["value"]
        for parameter in stated
        if isinstance(parameter.get("value"), str)
    ]
    if files:
        return [_dataset(files, stated, described)]
    return [_parameter(parameter, described) for parameter in stated]


def _dataset(files: list[str], stated: list[JsonObject], described: str) -> Node:
    """State a grid-based method's files as the one dataset ESRI names.

    EPSG states NADCON as two files, one per direction; ESRI states the pair as
    a single dataset, which only works when they share a name.
    """
    if len(files) != len(stated):
        raise UnsupportedMethodError(
            f"{_described(described)} mixes grid files with numeric parameters, "
            f"which ESRI states no {_TRANSFORMATION_KEYWORD} for"
        )
    stems = {name.rsplit(".", 1)[0] for name in files}
    if len(stems) != 1:
        raise UnsupportedMethodError(
            f"{_described(described)} reads {sorted(stems)}, which ESRI cannot "
            f"name as the single dataset a {_TRANSFORMATION_KEYWORD} carries"
        )
    return Node("PARAMETER", (f"{methods.GRID_PARAMETER_PREFIX}{stems.pop()}", 0.0))


def _parameter(stated: JsonObject, described: str) -> Node:
    """State one numeric parameter, restated in the unit ESRI implies."""
    identifier = stated.get("id")
    if not isinstance(identifier, dict) or identifier.get("authority") != "EPSG":
        raise UnsupportedMethodError(
            f"{_described(described)} states parameter {stated.get('name')!r} "
            f"with no EPSG code"
        )
    parameter = methods.esri_parameter(int(identifier["code"]))
    restated = (
        float(stated["value"])
        * methods.factor(stated.get("unit", "unity"))
        / methods.factor(parameter.unit)
    )
    return Node("PARAMETER", (methods.esri_name(parameter), restated))


def _geographic(definition: JsonObject, end: str, described: str) -> Node:
    """State one end of a transformation as an ESRI ``GEOGCS``."""
    stated = definition.get(end)
    if not isinstance(stated, dict):
        raise UnsupportedReferenceError(
            f"{_described(described)} states no {end.replace('_', ' ')}"
        )
    crs = CRS.from_json_dict(stated)
    node = esriwkt.read(_esri_wkt(crs))
    if node.keyword.casefold() != "geogcs":
        raise UnsupportedReferenceError(
            f"{_described(described)} goes between {node.keyword} definitions, "
            f"and a {_TRANSFORMATION_KEYWORD} goes between geographic ones"
        )
    return node


def _esri_wkt(crs: CRS) -> str:
    """Ask PROJ for a CRS in ESRI's dialect."""
    for component in crs.sub_crs_list or [crs]:
        datum = component.datum
        if datum is not None and datum.type_name.startswith("Dynamic"):
            raise UnsupportedReferenceError(
                f"{_described(crs.name)} has a dynamic datum; ESRI WKT cannot "
                "preserve its frame reference epoch"
            )
    try:
        written = crs.to_wkt(version=WktVersion.WKT1_ESRI)
    except CRSError as error:
        raise MalformedReferenceError(
            f"{_described(crs.name)} is a CRS PROJ will not write as ESRI WKT: {error}"
        ) from error
    if not written:
        raise MalformedReferenceError(
            f"{_described(crs.name)} is a CRS PROJ will not write as ESRI WKT"
        )
    return str(written)


def _method_code(definition: JsonObject, operation: CoordinateOperation) -> int:
    """The EPSG method code of a transformation."""
    method = definition.get("method")
    identifier = method.get("id") if isinstance(method, dict) else None
    if not isinstance(identifier, dict) or identifier.get("authority") != "EPSG":
        raise UnsupportedMethodError(
            f"{_described(operation.name)} states a method with no EPSG code, "
            f"so it has no ESRI equivalent to write"
        )
    return int(identifier["code"])


def _identifier(definition: JsonObject) -> Node | None:
    """State an operation's own authority code, when it has one."""
    identifier = definition.get("id")
    if not isinstance(identifier, dict):
        return None
    authority, code = identifier.get("authority"), identifier.get("code")
    if not isinstance(authority, str) or code is None:
        return None
    return Node("AUTHORITY", (authority, code if isinstance(code, int) else str(code)))


def _described(name: str) -> str:
    """Name an object for an error message."""
    return f"{name!r}" if name else "the definition given"
