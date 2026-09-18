"""The JSON envelope an OSDU persistableReference arrives in.

A persistableReference is a JSON object carrying one definition -- a CRS, a
coordinate operation or a unit -- as ESRI WKT plus the authority code, name and
version of whatever produced it. The envelope is what this module reads; the
WKT inside it is left to :mod:`geodetic_engine.persistablereference.esriwkt`.

Three things about the format make reading it less obvious than
``json.loads``:

* **It is often URL-encoded**, sometimes twice, because it is passed as a query
  parameter before it is stored. A JSON document never starts with ``%``, so a
  leading one is the signal. Decoding is bounded rather than repeated until the
  text stops changing: an ESRI WKT name may legitimately contain a ``%``, and
  decoding to a fixed point turns a payload the caller never sent into one this
  package would act on.
* **Key casing drifts** between producers, so ``scaleOffset`` and
  ``ScaleOffset`` both appear. Keys are matched without case.
* **The kind is not always stated.** ``type`` is the discriminator, but some
  payloads omit it, in which case the kind is settled by which members are
  present.

The payload arrives from another system, so its length and nesting depth are
capped before it is parsed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import unquote

from geodetic_engine.persistablereference.errors import (
    MalformedReferenceError,
    UnsupportedReferenceError,
)

JsonObject = dict[str, Any]

# One definition, however verbose its WKT, is far below this.
_MAX_LENGTH = 1 << 20
# The deepest a real payload nests is a concatenated CT: envelope, cts list,
# one CT, its authCode. An order of magnitude of headroom over that.
_MAX_DEPTH = 32
# Enough for a payload that was encoded on its way through two services.
_MAX_DECODES = 2
_MARKERS = frozenset(
    {
        "authcode",
        "lateboundcrs",
        "singlect",
        "compoundct",
        "cts",
        "scaleoffset",
        "abcd",
        "wkt",
    }
)


class Kind(StrEnum):
    """The kind of definition a persistableReference states."""

    LATE_BOUND_CRS = "LBC"
    """A CRS on its own, with no transformation attached."""

    EARLY_BOUND_CRS = "EBC"
    """A CRS packaged with the transformation that ties it to a hub."""

    TRANSFORMATION = "ST"
    """A single coordinate transformation, as ESRI ``GEOGTRAN``."""

    CONCATENATED_TRANSFORMATION = "CT"
    """Transformations applied in sequence through intermediate frames."""

    UNIT_SCALE_OFFSET = "USO"
    """A unit converting to its SI base by a scale and an offset."""

    UNIT_ABCD = "UAD"
    """A unit converting to its SI base by a rational polynomial."""


@dataclass(frozen=True, slots=True)
class AuthorityCode:
    """An authority and code naming one object.

    The authority is frequently not EPSG: OSDU publishes its own codes, and so
    do the systems that write these payloads. It records where a definition
    came from and is never used to look one up.
    """

    authority: str
    code: str

    def __str__(self) -> str:
        return f"{self.authority}:{self.code}"


@dataclass(frozen=True, slots=True)
class Envelope:
    """A decoded persistableReference, before its WKT has been read.

    Attributes:
        raw: The payload exactly as it arrived.
        data: The decoded JSON object.
        kind: Which of the six kinds it states.
        name: Name the producer gave the definition, or ``""``.
        authority_code: Authority code the producer stamped on it, if any.
        version: Producer version string, for example ``"PE_10_9_1"``.
    """

    raw: str
    data: JsonObject
    kind: Kind
    name: str
    authority_code: AuthorityCode | None
    version: str


def looks_like_reference(value: str) -> bool:
    """Whether a string is shaped like a persistableReference.

    Inspects top-level JSON keys after bounded URL decoding. The embedded
    definition is not validated, so a matching payload can still be malformed.

    Opening as a JSON object is not enough to go on: PROJJSON opens the same
    way, and claiming one of those would stop a perfectly good CRS definition
    from resolving. So a member only this format has must be present too.

    Args:
        value: Any user-supplied definition.

    Returns:
        True if the string opens as a JSON object carrying a member that
        belongs to this format, encoded or not.

    Example:
        >>> looks_like_reference('{"type":"LBC","wkt":"GEOGCS[...]"}')
        True
        >>> looks_like_reference("EPSG:4326")
        False
        >>> looks_like_reference('{"type":"GeographicCRS","name":"WGS 84"}')
        False
    """
    try:
        text = _decoded(value)
    except MalformedReferenceError:
        return value.lstrip().startswith(("{", "%"))
    if not text.startswith("{"):
        return False
    try:
        data = _object(text)
    except MalformedReferenceError:
        return True
    return text_field(data, "type").upper() in Kind or bool(
        _MARKERS.intersection(key.casefold() for key in data)
    )


def decode(raw: str) -> Envelope:
    """Read a persistableReference payload into its envelope.

    Args:
        raw: The payload, plain or URL-encoded JSON.

    Returns:
        The decoded envelope.

    Raises:
        MalformedReferenceError: If the payload is too large, too deeply
            nested, not a JSON object, or states no recognisable kind.
        UnsupportedReferenceError: If it states a kind this package does not
            model.

    Example:
        >>> decode('{"type":"LBC","name":"WGS 84","wkt":"GEOGCS[...]"}').kind
        <Kind.LATE_BOUND_CRS: 'LBC'>
    """
    data = _object(raw)
    return Envelope(
        raw=raw,
        data=data,
        kind=kind_of(data),
        name=text_field(data, "name"),
        authority_code=authority_code(field(data, "authCode")),
        version=text_field(data, "ver", "version"),
    )


def kind_of(data: JsonObject) -> Kind:
    """Decide which kind of definition an object states.

    Args:
        data: A decoded persistableReference object.

    Returns:
        The kind, taken from ``type`` when it names one, and from the members
        present when it does not.

    Raises:
        MalformedReferenceError: If neither settles the question.
        UnsupportedReferenceError: If ``type`` names a kind not modelled here.
    """
    stated = text_field(data, "type").upper()
    if stated:
        try:
            return Kind(stated)
        except ValueError:
            raise UnsupportedReferenceError(
                f"persistableReference states type {stated!r}, which is not one "
                f"of {', '.join(kind.value for kind in Kind)}"
            ) from None
    if field(data, "lateBoundCRS") is not None:
        return Kind.EARLY_BOUND_CRS
    if field(data, "cts") is not None:
        return Kind.CONCATENATED_TRANSFORMATION
    if field(data, "scaleOffset") is not None:
        return Kind.UNIT_SCALE_OFFSET
    if field(data, "abcd") is not None:
        return Kind.UNIT_ABCD
    if (wkt := text_field(data, "wkt")).lstrip().upper().startswith("GEOGTRAN"):
        return Kind.TRANSFORMATION
    if wkt:
        return Kind.LATE_BOUND_CRS
    raise MalformedReferenceError(
        "persistableReference states no type and carries no member that identifies one"
    )


def field(data: JsonObject, *names: str) -> Any:
    """The value under the first of ``names`` the object carries.

    Keys are matched without case, because producers disagree on it.

    Args:
        data: A decoded object.
        *names: Candidate keys, in order of preference.

    Returns:
        The value, or None when the object carries none of the names.
    """
    for name in names:
        wanted = name.casefold()
        for key, value in data.items():
            if key.casefold() == wanted:
                return value
    return None


def text_field(data: JsonObject, *names: str) -> str:
    """The string value under the first of ``names`` the object carries.

    Args:
        data: A decoded object.
        *names: Candidate keys, in order of preference.

    Returns:
        The value as a string, or ``""`` when absent or not a string.
    """
    value = field(data, *names)
    return value if isinstance(value, str) else ""


def object_field(data: JsonObject, *names: str) -> JsonObject | None:
    """The object under the first of ``names`` the object carries.

    A nested member is sometimes supplied as a JSON string rather than an
    object, so a string value is decoded before it is returned.

    Args:
        data: A decoded object.
        *names: Candidate keys, in order of preference.

    Returns:
        The nested object, or None when absent or not an object or string.

    Raises:
        MalformedReferenceError: If a string is not a readable object or
            exceeds the payload limits.
    """
    value = field(data, *names)
    if isinstance(value, str):
        value = _object(value)
    return value if isinstance(value, dict) else None


def authority_code(value: Any) -> AuthorityCode | None:
    """Read an ``authCode`` member.

    Args:
        value: The member's value, an object with ``auth`` and ``code``.

    Returns:
        The authority code, or None when either half is missing.
    """
    if not isinstance(value, dict):
        return None
    authority = str(field(value, "auth", "authority") or "").strip()
    code = str(field(value, "code") or "").strip()
    return AuthorityCode(authority, code) if authority and code else None


def _decoded(raw: str) -> str:
    """Undo the URL encoding a payload may still be carrying."""
    if len(raw) > _MAX_LENGTH * 3**_MAX_DECODES:
        raise MalformedReferenceError(
            "encoded persistableReference is over the size limit"
        )
    text = raw.strip()
    for _ in range(_MAX_DECODES):
        if not text.startswith("%"):
            break
        text = unquote(text).strip()
    return text


def _object(raw: str) -> JsonObject:
    """Read an object with the same limits for outer and string-encoded members."""
    text = _decoded(raw)
    if len(text) > _MAX_LENGTH:
        raise MalformedReferenceError(
            f"persistableReference is {len(text)} characters, over the "
            f"{_MAX_LENGTH} a single definition is allowed"
        )
    if (depth := _depth(text)) > _MAX_DEPTH:
        raise MalformedReferenceError(
            f"persistableReference nests {depth} levels deep, over the "
            f"{_MAX_DEPTH} a single definition is allowed"
        )
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise MalformedReferenceError(
            f"persistableReference is not readable JSON: {error}"
        ) from error
    if not isinstance(data, dict):
        raise MalformedReferenceError(
            f"persistableReference is a JSON {type(data).__name__}, not an object"
        )
    return data


def _depth(text: str) -> int:
    """Deepest JSON nesting in a document, ignoring brackets inside strings.

    The brackets that matter are the JSON ones. Counting the ones in the
    embedded ESRI WKT would report a projected CRS as ten levels deep.
    """
    depth = deepest = 0
    in_string = escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "{[":
            depth += 1
            deepest = max(deepest, depth)
        elif character in "}]":
            depth -= 1
    return deepest
