"""Replace the coordinates in ISO WKB, keeping measures.

Private to :mod:`geodetic_engine.geodesy.geometry`. Shapely reads and writes
M values, but :func:`shapely.set_coordinates` and :func:`shapely.transform`
drop them, so a geometry with M is rewritten here instead: its ISO WKB is
walked, and each position's x, y and z are overwritten in place while its m
stays as it was.
"""

from __future__ import annotations

import struct
from typing import Final

import numpy as np
from numpy.typing import NDArray

type FloatArray = NDArray[np.float64]

_POINT: Final = 1
_LINE_STRING: Final = 2
_POLYGON: Final = 3
_COLLECTIONS: Final = frozenset({4, 5, 6, 7})
# Values per position by the ISO dimension flag: XY, XYZ, XYM, XYZM.
_WIDTHS: Final = {0: 2, 1: 3, 2: 3, 3: 4}


def replace_coordinates(wkb: bytes, rows: FloatArray) -> bytes:
    """``wkb`` with each position's leading values replaced by a row of ``rows``.

    Args:
        wkb: ISO WKB, as :func:`shapely.to_wkb` writes with ``flavor="iso"``.
        rows: One row per position in document order, skipping empty points,
            as :func:`shapely.get_coordinates` gives them; as many columns as
            there are values to replace: x and y, and z if the geometry has it.

    Raises:
        ValueError: If the WKB is not ISO WKB this reads, or holds another
            number of positions than ``rows``.
    """
    buffer = bytearray(wkb)
    writer = _Writer(buffer, rows)
    end = writer.geometry(0)
    if end != len(buffer) or writer.row != len(rows):
        raise ValueError(
            f"the WKB holds {writer.row} positions in {end} bytes, but "
            f"{len(rows)} positions in {len(buffer)} bytes were expected"
        )
    return bytes(buffer)


class _Writer:
    def __init__(self, buffer: bytearray, rows: FloatArray) -> None:
        self._buffer = buffer
        self._rows = rows
        self.row = 0

    def geometry(self, offset: int) -> int:
        order = "<" if self._buffer[offset] == 1 else ">"
        (code,) = struct.unpack_from(f"{order}I", self._buffer, offset + 1)
        offset += 5
        kind, flag = code % 1000, code // 1000
        if flag not in _WIDTHS:
            raise ValueError(f"WKB geometry type {code} is not ISO WKB")
        width = _WIDTHS[flag]
        if kind == _POINT:
            return self._positions(offset, order, width, 1, point=True)
        if kind == _LINE_STRING:
            (count,) = struct.unpack_from(f"{order}I", self._buffer, offset)
            return self._positions(offset + 4, order, width, count)
        if kind == _POLYGON:
            (rings,) = struct.unpack_from(f"{order}I", self._buffer, offset)
            offset += 4
            for _ in range(rings):
                (count,) = struct.unpack_from(f"{order}I", self._buffer, offset)
                offset = self._positions(offset + 4, order, width, count)
            return offset
        if kind in _COLLECTIONS:
            (parts,) = struct.unpack_from(f"{order}I", self._buffer, offset)
            offset += 4
            for _ in range(parts):
                offset = self.geometry(offset)
            return offset
        raise ValueError(f"WKB geometry type {code} is not a linear geometry")

    def _positions(
        self, offset: int, order: str, width: int, count: int, *, point: bool = False
    ) -> int:
        fmt = f"{order}{width}d"
        size = 8 * width
        replaced = self._rows.shape[1]
        if replaced > width:
            raise ValueError(
                f"{replaced} values per position cannot replace a position of "
                f"{width}; a collection's members differ in z or m"
            )
        for _ in range(count):
            values = struct.unpack_from(fmt, self._buffer, offset)
            # An empty point is written as NaN, and has no row.
            if not (point and all(np.isnan(values))):
                row = self._rows[self.row]
                struct.pack_into(
                    f"{order}{replaced}d", self._buffer, offset, *row.tolist()
                )
                self.row += 1
            offset += size
        return offset
