"""Geodetic transformation engine built on PROJ."""

from importlib.metadata import version as _version

from geodetic_engine._environment import require_pinned_proj as _require_pinned_proj

_require_pinned_proj()

# The PyPI name, which differs from the import name.
__version__ = _version("pygeodetic-engine")
