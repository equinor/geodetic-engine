"""Helpers shared by test modules that read PROJ's own data directory."""

from __future__ import annotations

import os
from pathlib import Path

import pyproj


def installed_proj_db() -> Path:
    """The ``proj.db`` PROJ reads: the first on its search path that exists.

    ``PROJ_DATA`` may list several directories: the devcontainer's lists the
    installed data directory and then ``local/grids``, and a developer may put
    an opt-in patched copy (``link-local-grids.sh --patched-copy``) first by
    hand. PROJ reads the first database it finds, so this returns that one,
    not necessarily the stock installed file.
    """
    for directory in pyproj.datadir.get_data_dir().split(os.pathsep):
        candidate = Path(directory) / "proj.db"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("no proj.db on PROJ's search path")
