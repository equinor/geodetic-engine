"""Seismic bin grids: P6 parameters, four-corner QC and squaring, conversion.

A bin grid maps the (inline, crossline) numbers of seismic bin centres onto the
map grid of a projected CRS. This package knows two ways of stating one and
converts between them:

* :class:`P6Parameters` -- the IOGP P6/11 parameters of EPSG methods 9666 and
  1049, with vectorised conversion between bin grid and map grid
  (:meth:`~P6Parameters.to_map`, :meth:`~P6Parameters.to_bin`);
* :class:`BinGridCorners` -- the four-point definition, corners A, B, C, D.

Public entry points:

* :func:`convert_bin_grid` -- QC four corners, optionally convert them to
  another CRS, square them up, and give them in WGS 84.
* :func:`square_up` / :func:`derive_p6` -- the same fit, without a CRS.
* :func:`corners_from_p6` -- the four corners of a P6-defined grid.
* :func:`outline` -- the grid's outline as a counterclockwise polygon ring.

Nothing here reads or writes OSDU JSON.

Example:
    >>> from geodetic_engine.bingrid import convert_bin_grid
    >>> result = convert_bin_grid(
    ...     [(1, 1000, 500000.0, 3000000.0), (1, 2000, 500000.0, 3100000.0),
    ...      (101, 1000, 600000.0, 3000000.0), (101, 2000, 600000.0, 3100000.0)],
    ...     "EPSG:32615",
    ... )
    >>> p = result.parameters
    >>> round(p.scale_factor, 6), round(p.bin_width_i, 3), round(p.bin_width_j, 4)
    (0.999631, 1000.369, 100.0369)
    >>> round(result.max_mislocation.di, 9), round(result.max_mislocation.dj, 9)
    (0.0, 0.0)
"""

from geodetic_engine.bingrid.conversion import BinGridResult, convert_bin_grid
from geodetic_engine.bingrid.corners import (
    BinGridCorner,
    BinGridCorners,
    corners_from_p6,
)
from geodetic_engine.bingrid.errors import (
    BinGridError,
    DegenerateBinGridError,
    InvalidCornersError,
    InvalidParameterError,
    UnsupportedCRSError,
)
from geodetic_engine.bingrid.outline import BinGridOutline, outline, outline_of
from geodetic_engine.bingrid.p6 import Handedness, P6Parameters
from geodetic_engine.bingrid.squaring import (
    CornerResidual,
    MaxMislocation,
    SquaringResult,
    derive_p6,
    square_up,
)

__all__ = [
    "BinGridCorner",
    "BinGridCorners",
    "BinGridError",
    "BinGridOutline",
    "BinGridResult",
    "CornerResidual",
    "DegenerateBinGridError",
    "Handedness",
    "InvalidCornersError",
    "InvalidParameterError",
    "MaxMislocation",
    "P6Parameters",
    "SquaringResult",
    "UnsupportedCRSError",
    "convert_bin_grid",
    "corners_from_p6",
    "derive_p6",
    "outline",
    "outline_of",
    "square_up",
]
