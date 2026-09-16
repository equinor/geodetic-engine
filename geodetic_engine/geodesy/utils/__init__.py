"""Supporting machinery for the geodesy package.

These are not coordinate transformations in their own right. They are the
operations *on* operations that the rest of the package needs: rewriting a
chain of coordinate operations into an equivalent single step, restating one in
the units an abridged transformation assumes, and the numerical checks that
prove such a rewrite did not change the answer.
"""

from geodetic_engine.geodesy.utils.abridged import scale_in_parts_per_million
from geodetic_engine.geodesy.utils.helmert import (
    HelmertParameters,
    Rotation,
    collapse_concatenated,
    compose,
    helmert_parameters,
    is_collapsible,
)

__all__ = [
    "HelmertParameters",
    "Rotation",
    "collapse_concatenated",
    "compose",
    "helmert_parameters",
    "is_collapsible",
    "scale_in_parts_per_million",
]
