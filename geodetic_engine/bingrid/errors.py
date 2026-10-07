"""Exceptions raised while defining, checking or converting a seismic bin grid.

Every way a bin grid can fail to be trustworthy gets its own type, so a caller
can tell "these are not the corners of a grid" apart from "this CRS cannot carry
a grid" apart from "this parameter is out of range". Failures of the coordinate
conversion itself are the :mod:`geodetic_engine.geodesy` exceptions, raised
unchanged. Both hierarchies share
:class:`~geodetic_engine.errors.GeodeticEngineError`.
"""

from geodetic_engine.errors import GeodeticEngineError


class BinGridError(GeodeticEngineError):
    """Base class for all bin grid failures."""


class InvalidCornersError(BinGridError):
    """The corners given do not state a bin grid.

    Raised when there are not exactly four, when their inline and crossline
    numbers are not the minimum and maximum of each, in all four combinations,
    or when the numbers are not multiples of the node increments apart, so
    that the corners cannot all be bin nodes. Sorting such points into A, B, C
    and D anyway is how a grid ends up with a zero inline span and an infinite
    bin width.
    """


class DegenerateBinGridError(BinGridError):
    """The corner coordinates cannot belong to the corners they are labelled as.

    Raised for coordinates that are not finite, corners that coincide, and an
    outline A-B-D-C that is not convex or crosses itself, which is what corners
    swapped between labels look like. A bearing and handedness derived from such
    corners would be meaningless rather than merely inaccurate.
    """


class InvalidParameterError(BinGridError):
    """A bin grid parameter is outside the values it can take.

    Raised for a scale factor or bin width that is not finite and positive, a
    node increment that is not a positive integer, a value that is not finite,
    and an unknown handedness or transformation method.
    """


class UnsupportedCRSError(BinGridError):
    """The CRS is not one a bin grid can be defined on.

    A bin grid is designed on a map grid: its bearing is measured from grid
    north and its widths are distances on the grid. A geographic CRS has no
    such grid, a projected CRS whose axes are not easting and northing (a
    southing and westing, say) would have the bearing measured the wrong way
    round, and one that states its easting and northing in different units has
    no one unit for the distances. Also raised when the scale factor is to be
    derived from a projection that is not conformal at the grid: its scale
    there depends on direction, so no one scale factor makes both bin widths
    ground distances.
    """
