"""Supporting machinery for the geodesy package.

These are not coordinate transformations in their own right. They are the
operations *on* operations that the rest of the package needs: rewriting a
chain of coordinate operations into an equivalent single step, and the
numerical checks that prove such a rewrite did not change the answer.
"""

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
]
