"""Replace the coordinates in ISO WKB, keeping measures.

Private to :mod:`geodetic_engine.geodesy.geometry`. Shapely reads and writes
M values, but :func:`shapely.set_coordinates` and :func:`shapely.transform`
drop them, so a geometry with M is rewritten here instead: its ISO WKB is
walked, and each position's values are overwritten in place, as many as its
own dimension has.
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


def replace_coordinates(
    wkb: bytes, rows: FloatArray, *, has_z: bool, has_m: bool
) -> bytes:
    """``wkb`` with each position's values replaced by a row of ``rows``.

    A position takes the values its own dimension has, so a member of a
    collection that has no z, say, gets only the x and y of its row.

    Args:
        wkb: ISO WKB, as :func:`shapely.to_wkb` writes with ``flavor="iso"``.
        rows: One row per position in document order, skipping empty points,
            as :func:`shapely.get_coordinates` gives them with ``include_z``
            and ``include_m`` set as ``has_z`` and ``has_m`` are.
        has_z: Whether ``rows`` has a z column, after x and y.
        has_m: Whether ``rows`` has an m column, last.

    Raises:
        ValueError: If the WKB is not ISO WKB this reads, holds another
            number of positions than ``rows``, or has a position with a z or
            m that ``rows`` lacks.
    """
    buffer = bytearray(wkb)
    writer = _Writer(buffer, rows, has_z=has_z, has_m=has_m)
    end = writer.geometry(0)
    if end != len(buffer) or writer.row != len(rows):
        raise ValueError(
            f"the WKB holds {writer.row} positions in {end} bytes, but "
            f"{len(rows)} positions in {len(buffer)} bytes were expected"
        )
    return bytes(buffer)


class _Writer:
    def __init__(
        self, buffer: bytearray, rows: FloatArray, *, has_z: bool, has_m: bool
    ) -> None:
        self._buffer = buffer
        self._rows = rows
        self._z = 2 if has_z else None
        self._m = (3 if has_z else 2) if has_m else None
        self.row = 0

    def geometry(self, offset: int) -> int:
        order = "<" if self._buffer[offset] == 1 else ">"
        (code,) = struct.unpack_from(f"{order}I", self._buffer, offset + 1)
        offset += 5
        kind, flag = code % 1000, code // 1000
        if flag not in _WIDTHS:
            raise ValueError(f"WKB geometry type {code} is not ISO WKB")
        if kind == _POINT:
            return self._positions(offset, order, flag, 1, point=True)
        if kind == _LINE_STRING:
            (count,) = struct.unpack_from(f"{order}I", self._buffer, offset)
            return self._positions(offset + 4, order, flag, count)
        if kind == _POLYGON:
            (rings,) = struct.unpack_from(f"{order}I", self._buffer, offset)
            offset += 4
            for _ in range(rings):
                (count,) = struct.unpack_from(f"{order}I", self._buffer, offset)
                offset = self._positions(offset + 4, order, flag, count)
            return offset
        if kind in _COLLECTIONS:
            (parts,) = struct.unpack_from(f"{order}I", self._buffer, offset)
            offset += 4
            for _ in range(parts):
                offset = self.geometry(offset)
            return offset
        raise ValueError(f"WKB geometry type {code} is not a linear geometry")

    def _positions(
        self, offset: int, order: str, flag: int, count: int, *, point: bool = False
    ) -> int:
        width = _WIDTHS[flag]
        fmt = f"{order}{width}d"
        size = 8 * width
        # The columns of a row this position takes: x, y, then its own z and m.
        wanted = [
            0,
            1,
            *([self._z] if flag in (1, 3) else []),
            *([self._m] if flag in (2, 3) else []),
        ]
        columns = [column for column in wanted if column is not None]
        for _ in range(count):
            values = struct.unpack_from(fmt, self._buffer, offset)
            # An empty point is written as NaN, and has no row.
            if not (point and all(np.isnan(values))):
                if len(columns) != width:
                    raise ValueError(
                        f"a position of WKB dimension {flag} has a z or m value "
                        "that the rows do not"
                    )
                row = self._rows[self.row, columns]
                struct.pack_into(fmt, self._buffer, offset, *row.tolist())
                self.row += 1
            offset += size
        return offset
