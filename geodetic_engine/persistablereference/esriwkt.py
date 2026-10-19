"""Reading and writing the ESRI WKT dialect, as a tree of bracketed nodes.

PROJ parses ESRI WKT for coordinate reference systems, so ``PROJCS``,
``GEOGCS``, ``VERTCS`` and ``COMPD_CS`` never need to be taken apart here. What
PROJ will not parse is ``GEOGTRAN``, ESRI's spelling of a coordinate
transformation, and it will not write one either. That leaves this package
having to read and write one grammar itself, and this module is the whole of
it::

    KEYWORD[ "quoted string" | number | KEYWORD[...] , ... ]

The grammar is read with a scanner rather than matched with patterns. A
``GEOGTRAN`` contains two nested ``GEOGCS`` definitions, each containing a
``DATUM`` containing a ``SPHEROID``, and a pattern that finds "the second
``SPHEROID``" is really asserting that nothing was added before it. Positions
are read from the tree instead, where a node's parent is unambiguous.

Integers and reals are kept apart, because ESRI writes them differently:
``AUTHORITY["EPSG",23032]`` alongside ``PARAMETER["Scale_Factor",0.9996]``.
Unquoted enumeration values such as an axis direction are kept apart from
quoted strings for the same reason: ``AXIS["Lat",north]`` has one of each, and
putting quotes round ``north`` on the way out would produce WKT no parser
accepts. Preserving all of this is what lets a payload read here be written
back unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import NoReturn

from geodetic_engine.persistablereference.errors import MalformedReferenceError


class Word(str):
    """An unquoted enumeration value, such as the ``north`` of an axis.

    A :class:`str` subclass, so it compares and reads as the text it is. The
    distinct type exists only so that it is written back without quotes.
    """

    __slots__ = ()


type Child = Node | Word | str | int | float

# Characters an unquoted keyword is made of. ESRI uses upper case throughout,
# but a keyword is matched case-insensitively wherever it is looked up.
_KEYWORD_EXTRA = "_"
# Characters a numeric literal is made of, before it is handed to int or float.
_NUMBER_CHARACTERS = "0123456789+-.eE"
# Characters one may begin with. An exponent marker is not among them, or the
# axis direction ``east`` would be read as the start of a number.
_NUMBER_START = "0123456789+-."

_MAX_LENGTH = 1 << 20
_MAX_DEPTH = 32


@dataclass(frozen=True, slots=True)
class Node:
    """One bracketed element of an ESRI WKT definition.

    Attributes:
        keyword: The element's keyword, for example ``"GEOGTRAN"``.
        children: Its comma-separated contents, in the order they were
            written. Strings, numbers and nested nodes all appear here.
    """

    keyword: str
    children: tuple[Child, ...]

    @property
    def name(self) -> str:
        """The element's quoted name, which ESRI always writes first.

        Returns:
            The name, or ``""`` when the element does not open with a quoted
            string.
        """
        first = self.children[0] if self.children else None
        if isinstance(first, Word) or not isinstance(first, str):
            return ""
        return first

    @property
    def values(self) -> tuple[float, ...]:
        """The element's numeric contents, in written order."""
        return tuple(
            float(child) for child in self.children if isinstance(child, int | float)
        )

    def nodes(self, keyword: str) -> tuple[Node, ...]:
        """Direct children with a given keyword, in written order.

        Args:
            keyword: Keyword to select, matched without case.

        Returns:
            The matching children. Empty when there are none.
        """
        wanted = keyword.casefold()
        return tuple(
            child
            for child in self.children
            if isinstance(child, Node) and child.keyword.casefold() == wanted
        )

    def node(self, keyword: str) -> Node | None:
        """The first direct child with a given keyword.

        Args:
            keyword: Keyword to select, matched without case.

        Returns:
            The child, or None when there is none.
        """
        found = self.nodes(keyword)
        return found[0] if found else None

    def descendants(self, keyword: str) -> tuple[Node, ...]:
        """Every node with a given keyword, at any depth, in written order.

        Args:
            keyword: Keyword to select, matched without case.

        Returns:
            The matching nodes, outermost first.
        """
        wanted = keyword.casefold()
        found: list[Node] = []
        if self.keyword.casefold() == wanted:
            found.append(self)
        for child in self.children:
            if isinstance(child, Node):
                found.extend(child.descendants(keyword))
        return tuple(found)


def read(text: str) -> Node:
    """Parse one ESRI WKT element.

    Args:
        text: The WKT, which must contain exactly one top-level element.

    Returns:
        The element, with its contents as nested nodes.

    Raises:
        MalformedReferenceError: If the text is not one well-formed element.

    Example:
        >>> node = read('GEOGTRAN["a",METHOD["Position_Vector"]]')
        >>> node.keyword, node.name
        ('GEOGTRAN', 'a')
        >>> node.node("METHOD").name
        'Position_Vector'
    """
    if len(text) > _MAX_LENGTH:
        raise MalformedReferenceError(f"ESRI WKT exceeds {_MAX_LENGTH} characters")
    scanner = _Scanner(text)
    node = scanner.node()
    scanner.end()
    return node


def write(node: Node) -> str:
    """Render an element back to ESRI WKT.

    Args:
        node: The element to render.

    Returns:
        The WKT, with no whitespace between elements, as ESRI writes it.

    Example:
        >>> write(Node("AUTHORITY", ("EPSG", 23032)))
        'AUTHORITY["EPSG",23032]'
    """
    contents = ",".join(_rendered(child) for child in node.children)
    return f"{node.keyword}[{contents}]"


def pretty(text: str, *, indent: int = 4) -> str:
    """Lay ESRI WKT out over indented lines, for reading.

    Only whitespace outside quoted strings changes: every name, number and
    keyword is kept exactly as written, so ``-0.00000556098`` is not respelled
    ``-5.56098e-06``. An element containing no other element stays on one line,
    every element inside another starts a line of its own, and closing brackets
    follow the last line, the way PROJ indents WKT. :func:`read` gives back the
    same element.

    Args:
        text: The WKT, which must contain exactly one well-formed element.
        indent: Spaces per level of nesting.

    Returns:
        The WKT over as many lines as it has nested elements, the first line
        unindented.

    Raises:
        MalformedReferenceError: If the text is not one well-formed element.

    Example:
        >>> print(pretty('GEOGCS["a",DATUM["b",SPHEROID["c",1.0,2.0]]]'))
        GEOGCS["a",
            DATUM["b",
                SPHEROID["c",1.0,2.0]]]
    """
    read(text)
    tokens = _tokens(text)
    element, _ = _laid_out(tokens, 0)
    return _pretty(element, 0, " " * indent)


# An element as written: its keyword, and its contents as elements or as the
# exact text of each literal.
type _Written = tuple[str, tuple[_Written | str, ...]]


def _tokens(text: str) -> list[str]:
    """Split WKT into brackets, commas, quoted strings and bare literals.

    Each token is the exact text it was written as; only the whitespace between
    tokens is dropped.
    """
    tokens: list[str] = []
    at = 0
    while at < len(text):
        character = text[at]
        if character.isspace():
            at += 1
        elif character in "[],":
            tokens.append(character)
            at += 1
        elif character == '"':
            end = at + 1
            while end < len(text):
                if text[end] == '"':
                    if text.startswith('"', end + 1):
                        end += 2
                        continue
                    break
                end += 1
            tokens.append(text[at : end + 1])
            at = end + 1
        else:
            end = at
            while end < len(text) and not (text[end].isspace() or text[end] in '[],"'):
                end += 1
            tokens.append(text[at:end])
            at = end
    return tokens


def _laid_out(tokens: list[str], at: int) -> tuple[_Written, int]:
    """Read one element from well-formed tokens, returning it and what follows."""
    keyword = tokens[at]
    at += 2
    children: list[_Written | str] = []
    while True:
        if at + 1 < len(tokens) and tokens[at + 1] == "[":
            child, at = _laid_out(tokens, at)
            children.append(child)
        else:
            children.append(tokens[at])
            at += 1
        if tokens[at] == "]":
            return (keyword, tuple(children)), at + 1
        at += 1


def _pretty(element: _Written, depth: int, unit: str) -> str:
    """Render one element at a depth of nesting."""
    keyword, children = element
    first = next(
        (index for index, child in enumerate(children) if isinstance(child, tuple)),
        None,
    )
    if first is None:
        return f"{keyword}[{','.join(str(child) for child in children)}]"
    head = ",".join(str(child) for child in children[:first])
    newline = "\n" + unit * (depth + 1)
    body = ("," + newline).join(
        _pretty(child, depth + 1, unit) if isinstance(child, tuple) else child
        for child in children[first:]
    )
    opening = f"{keyword}[{head}," if head else f"{keyword}["
    return f"{opening}{newline}{body}]"


def _rendered(child: Child) -> str:
    """Render one element's content."""
    if isinstance(child, Node):
        return write(child)
    if isinstance(child, Word):
        return str(child)
    if isinstance(child, str):
        # WKT, PROJ and ESRI write a quote inside a string twice; none treats a
        # backslash as an escape.
        escaped = child.replace('"', '""')
        return f'"{escaped}"'
    return repr(child)


class _Scanner:
    """A cursor over one WKT string."""

    __slots__ = ("_at", "_text")

    def __init__(self, text: str) -> None:
        self._text = text
        self._at = 0

    def node(self, keyword: str | None = None, depth: int = 1) -> Node:
        """Read one element, starting at its keyword unless one is given."""
        if depth > _MAX_DEPTH:
            self._fail(f"nesting exceeds {_MAX_DEPTH} levels")
        keyword = self._keyword() if keyword is None else keyword
        self._take("[")
        children: list[Child] = [self._child(depth)]
        while self._peek() == ",":
            self._at += 1
            children.append(self._child(depth))
        self._take("]")
        return Node(keyword, tuple(children))

    def end(self) -> None:
        """Assert the whole string has been consumed."""
        if self._peek() is not None:
            self._fail("unexpected trailing text")

    def _child(self, depth: int) -> Child:
        character = self._peek()
        if character is None:
            self._fail("element ends before its contents")
        if character == '"':
            return self._string()
        if character in _NUMBER_START:
            return self._number()
        keyword = self._keyword()
        # A keyword followed by a bracket opens an element; one that is not is
        # an enumeration value, which WKT writes unquoted.
        return self.node(keyword, depth + 1) if self._peek() == "[" else Word(keyword)

    def _keyword(self) -> str:
        self._skip_space()
        start = self._at
        while self._at < len(self._text):
            character = self._text[self._at]
            if not character.isalnum() and character not in _KEYWORD_EXTRA:
                break
            self._at += 1
        if self._at == start:
            self._fail("expected a keyword")
        return self._text[start : self._at]

    def _string(self) -> str:
        self._at += 1
        characters: list[str] = []
        while self._at < len(self._text):
            character = self._text[self._at]
            self._at += 1
            if character != '"':
                characters.append(character)
            elif self._text.startswith('"', self._at):
                characters.append(character)
                self._at += 1
            else:
                return "".join(characters)
        self._fail("string is never closed")

    def _number(self) -> int | float:
        start = self._at
        while self._at < len(self._text) and self._text[self._at] in _NUMBER_CHARACTERS:
            self._at += 1
        literal = self._text[start : self._at]
        try:
            value = float(literal)
        except ValueError:
            self._fail(f"{literal!r} is not a number")
        if not isfinite(value):
            self._fail("numeric values must be finite")
        try:
            # An integer literal is kept an integer so that it is written back
            # the way ESRI writes authority codes, without a decimal point.
            return int(literal)
        except ValueError:
            return value

    def _take(self, character: str) -> None:
        if self._peek() != character:
            self._fail(f"expected {character!r}")
        self._at += 1

    def _peek(self) -> str | None:
        self._skip_space()
        return self._text[self._at] if self._at < len(self._text) else None

    def _skip_space(self) -> None:
        while self._at < len(self._text) and self._text[self._at].isspace():
            self._at += 1

    def _fail(self, problem: str) -> NoReturn:
        raise MalformedReferenceError(
            f"ESRI WKT is not readable at character {self._at}: {problem}"
        )
