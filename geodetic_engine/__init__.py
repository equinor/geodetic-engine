"""Geodetic transformation engine built on PROJ."""

from importlib.metadata import version as _version

from geodetic_engine._bundled_database import activate_at_import as _activate
from geodetic_engine._environment import require_pinned_proj as _require_pinned_proj

_require_pinned_proj()
# Before any other code of this package's touches PROJ's search path, so that a
# set_data_dir() call of the caller's own is still recognised as one.
_activate()

# The PyPI name, which differs from the import name.
__version__ = _version("pygeodetic-engine")
