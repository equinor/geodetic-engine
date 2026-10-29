"""PROJ's search path, as this package sets it and each thread follows it.

pyproj keeps one PROJ context per thread, and ``pyproj.datadir.set_data_dir()``
re-points only the caller's. So this package sets the path under one lock and
counts each time it does, and a thread re-points its own context when the path
or the count has changed since it last did.

Nothing that could run code of the caller's, such as logging, an import or a
pyproj call that may warn, happens while this package holds the lock. A fork
waits for the lock, and such code may be waiting on the very thread that is
forking: a logging handler that forks holds its own lock while it does. So a
thread re-points its context without the lock, and the only pyproj call made
with it held is the switch's set_data_dir(), given a path that starts with a
proj.db.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Final

from pyproj import datadir
from pyproj.exceptions import DataDirError

type DatabaseIdentity = tuple[tuple[str, int, int, int, int, int], ...]

_DATABASE_NAME: Final = "proj.db"


class _Lock:
    """A re-entrant lock that a forked child does not release on the way out.

    The child starts with the lock free. A ``with`` block it inherits from
    the parent, still open when the fork happened, must not release it again.
    """

    def __init__(self) -> None:
        self.raw = threading.RLock()
        self._entered = threading.local()

    def __enter__(self) -> None:
        self.raw.acquire()
        if not hasattr(self._entered, "pids"):
            self._entered.pids = []
        self._entered.pids.append(os.getpid())

    def __exit__(self, *exception: object) -> None:
        if self._entered.pids.pop() == os.getpid():
            self.raw.release()


lock: Final = _Lock()
_generation = 0
_thread = threading.local()

if hasattr(os, "register_at_fork"):
    # A child forked while another thread held the lock would inherit it held,
    # with no thread left to release it, and perhaps a switch half made. So a
    # fork waits for the lock, and the child starts with it free.
    os.register_at_fork(
        before=lock.raw.acquire,
        after_in_parent=lock.raw.release,
        after_in_child=lock.raw._at_fork_reinit,  # type: ignore[attr-defined]
    )


def set_search_path(search: str) -> None:
    """Set PROJ's search path: the calling thread's context at once, others later.

    The caller holds :data:`lock`, and the first directory of ``search``
    holds a ``proj.db``, so that pyproj has no cause to warn.
    """
    global _generation
    # Counted before the path changes, and read after it by
    # database_identity(): a thread that sees the new path sees the new count,
    # even when the path is the one the thread itself last read.
    _generation += 1
    datadir.set_data_dir(search)


def database_identity() -> DatabaseIdentity:
    """Identify the active search path and on-disk database generations.

    pyproj keeps one PROJ context per thread, and setting the search path
    re-points only the caller's. So the calling thread's is re-pointed here
    when the path differs from the one it last read, and once after each
    switch made on import or by ``use_bundled_database()``: in between, the
    thread may have set a path of its own with pyproj, and the switch put back
    the very path it read before. A path put back by any other
    ``set_data_dir()`` call goes unnoticed.
    """
    while True:
        # The path before the count, which set_search_path() changes first.
        identity = _identity(datadir.get_data_dir())
        generation = _generation
        if getattr(_thread, "seen", None) == (identity, generation):
            return identity
        # What set_data_dir() calls to re-point the caller: it re-points this
        # thread alone, from the path as it is now, and sets no path itself,
        # so it cannot undo a switch. Made without the lock, as pyproj may warn.
        datadir._set_context_data_dir()
        if (_identity(datadir.get_data_dir()), _generation) == (identity, generation):
            _thread.seen = (identity, generation)
            return identity


def followed() -> bool:
    """Whether the calling thread reads the path as it is now.

    The caller holds :data:`lock`, so that no switch is under way.
    """
    try:
        search = datadir.get_data_dir()
    except DataDirError:
        return True
    return getattr(_thread, "seen", None) == (_identity(search), _generation)


def _identity(search: str) -> DatabaseIdentity:
    entries = []
    for directory in search.split(os.pathsep):
        path = (Path(directory) / _DATABASE_NAME).resolve()
        try:
            stat = path.stat()
        except OSError:
            entries.append((str(path), 0, 0, 0, 0, 0))
        else:
            entries.append(
                (
                    str(path),
                    stat.st_dev,
                    stat.st_ino,
                    stat.st_size,
                    stat.st_mtime_ns,
                    stat.st_ctime_ns,
                )
            )
    return tuple(entries)
