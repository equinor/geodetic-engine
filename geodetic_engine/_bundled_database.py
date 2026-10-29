"""Make the OSDU database shipped with this package the one PROJ reads.

A release wheel ships ``geodetic_engine/osdudb/_data/proj_data/proj.db``: the
proj.db of the pinned pyproj wheel with the official OSDU catalogue built into
it. When ``geodetic_engine`` is first imported, the first of these rules that
matches decides whether PROJ reads it:

a. ``GEODETIC_ENGINE_OSDU_DB=0``: nothing is changed.
b. ``pyproj.datadir.set_data_dir()`` or ``append_data_dir()`` was called
   before the import: nothing is changed.
c. ``PROJ_DATA``, or ``PROJ_LIB`` when ``PROJ_DATA`` is unset, names a
   directory that holds a ``proj.db``: nothing is changed.
d. Otherwise PROJ's search path becomes the bundled database's directory, then
   the ``PROJ_DATA``/``PROJ_LIB`` directories, then pyproj's own data
   directory, so grids and ``proj.ini`` are still found where they were.

That is the precedence of this project's pyproj wheels (``set_data_dir``, then
a ``PROJ_DATA`` holding a database, then the database the wheel bundles) with
the OSDU database in place of the wheel's. No file is written and the
environment is left alone, so a child process that imports the package applies
the same rules.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pyproj import datadir
from pyproj.exceptions import DataDirError

logger = logging.getLogger(__name__)

ENVIRONMENT_VARIABLE: Final = "GEODETIC_ENGINE_OSDU_DB"

# A directory of its own: PROJ reads the file called proj.db in each directory
# of its search path, so nothing else may be called that here.
BUNDLED_DIRECTORY: Final = (
    Path(__file__).resolve().parent / "osdudb" / "_data" / "proj_data"
)

_DATABASE: Final = "proj.db"
_OFF: Final = frozenset({"0", "false", "no", "off"})
_ON: Final = frozenset({"", "1", "true", "yes", "on"})
_CHANGED: Final = (
    "pyproj.datadir.set_data_dir() changed PROJ's search path after "
    "geodetic_engine chose the database"
)


@dataclass(frozen=True, slots=True)
class ActiveDatabase:
    """The ``proj.db`` PROJ reads, and why it is that one.

    Attributes:
        path: The database PROJ reads, the ``proj.db`` in the first directory
            of its search path. None when that directory holds none.
        bundled: Whether it is the OSDU database shipped with this package.
        reason: Why it is in use, or why the bundled database is not.
        search_path: Every directory PROJ searches, in order, for the
            database, grids and ``proj.ini``.
    """

    path: Path | None
    bundled: bool
    reason: str
    search_path: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _Choice:
    reason: str
    # PROJ's search path as the choice left it; a different one now means the
    # caller has set another since.
    seen: str | None
    # The search path before the bundled database was put first, to go back
    # to when it is switched off.
    restore: str | None = None


_lock = threading.Lock()
_choice: _Choice | None = None


def activate_at_import() -> None:
    """Apply rules a to d, once, when ``geodetic_engine`` is first imported."""
    global _choice
    with _lock:
        if _choice is not None:
            return
        reason, search = _decide()
        before = _data_dir()
        if search is not None:
            datadir.set_data_dir(os.pathsep.join(search))
        _choice = _Choice(
            reason, _data_dir(), restore=before if search is not None else None
        )
        active = _active()
    _log(active)


def active_database() -> ActiveDatabase:
    """Report the ``proj.db`` PROJ reads and why.

    Returns:
        The database, whether it is the bundled OSDU database, and the reason:
        the rule that chose it on import, a call of
        :func:`use_bundled_database`, or a later
        ``pyproj.datadir.set_data_dir()``.

    Example:
        >>> from geodetic_engine.osdudb import active_database
        >>> active_database().bundled  # doctest: +SKIP
        True
    """
    with _lock:
        _synchronise_thread()
        return _active()


def use_bundled_database(enabled: bool = True) -> ActiveDatabase:
    """Switch the OSDU database shipped with this package on or off.

    Overrides the choice made on import, whatever made it. Switched on, the
    bundled database's directory is put first on PROJ's search path and the
    other directories stay, so grids are still found. Switched off, PROJ's
    search path returns to what it was before the bundled database was put
    first, or else loses the bundled directory.

    A later ``pyproj.datadir.set_data_dir()`` overrides this in turn. pyproj
    keeps one PROJ context per thread: the calling thread switches at once,
    and another thread the next time it calls :func:`active_database` or
    resolves a CRS, operation or identifier through this package. A thread
    that only calls pyproj directly keeps the database it had.

    Args:
        enabled: True to read the bundled database, False to stop.

    Returns:
        The database PROJ now reads, as :func:`active_database` reports it.

    Raises:
        FileNotFoundError: If switching on, and this installation has no
            bundled database; or switching off, and no other directory on
            PROJ's search path holds a ``proj.db``.
    """
    global _choice
    with _lock:
        database = _bundled_database()
        current = _data_dir()
        directories = _split(current)
        restore: str | None = None
        if enabled:
            if database is None:
                raise FileNotFoundError(
                    f"this installation has no bundled OSDU database at "
                    f"{BUNDLED_DIRECTORY / _DATABASE}; the wheels on PyPI ship "
                    "one. In a source checkout, run "
                    "python scripts/build_bundled_osdudb.py, or build a "
                    "database of your own with geodetic-osdudb build --official"
                )
            bundled = str(database.parent)
            if not directories or not _same(directories[0], bundled):
                restore = current
            elif _choice is not None and current == _choice.seen:
                restore = _choice.restore
            datadir.set_data_dir(os.pathsep.join(_unique([bundled, *directories])))
            reason = "use_bundled_database() switched it on"
        else:
            if database is not None and any(
                _same(entry, database.parent) for entry in directories
            ):
                if (
                    _choice is not None
                    and _choice.restore is not None
                    and current == _choice.seen
                ):
                    search = _split(_choice.restore)
                else:
                    search = _database_first(
                        [e for e in directories if not _same(e, database.parent)]
                    )
                datadir.set_data_dir(os.pathsep.join(search))
            reason = "use_bundled_database(False) switched it off"
        _choice = _Choice(reason, _data_dir(), restore=restore)
        _synchronise_thread()
        active = _active()
    _log(active)
    return active


def _decide() -> tuple[str, list[str] | None]:
    """Why the bundled database is or is not used, and the search path to set."""
    # Read first: once this package has called set_data_dir() itself, a call
    # of the caller's own can no longer be told apart from it.
    explicit = _user_data_dir()
    raw = os.environ.get(ENVIRONMENT_VARIABLE)
    setting = (raw or "").strip().casefold()
    if setting in _OFF:
        return f"{ENVIRONMENT_VARIABLE}={raw} turns it off", None
    if setting not in _ON:
        logger.warning(
            "%s=%r is not a boolean; the bundled OSDU database stays on by default",
            ENVIRONMENT_VARIABLE,
            raw,
        )
    if explicit is not None:
        return (
            "pyproj.datadir.set_data_dir() or append_data_dir() was called "
            "before geodetic_engine was imported",
            None,
        )
    name, value = _environment_data_dir()
    entries = _split(value)
    for entry in entries:
        if (Path(entry) / _DATABASE).is_file():
            if len(entries) == 1:
                return f"{name}={entry} holds a proj.db", None
            return f"{name} lists {entry}, which holds a proj.db", None
    database = _bundled_database()
    if database is None:
        return (
            f"this installation has no bundled OSDU database at "
            f"{BUNDLED_DIRECTORY / _DATABASE}",
            None,
        )
    search = _unique([str(database.parent), *entries, *_split(_data_dir())])
    if entries:
        return f"it is the default, and {name} names no proj.db", search
    return "it is the default, and nothing chose another database", search


def _active() -> ActiveDatabase:
    current = _data_dir()
    directories = tuple(Path(entry) for entry in _split(current))
    path = None
    if directories and (candidate := directories[0] / _DATABASE).is_file():
        path = candidate.resolve()
    database = _bundled_database()
    bundled = path is not None and database is not None and path == database.resolve()
    if _choice is None:
        reason = "geodetic_engine has not chosen a database"
    elif current != _choice.seen:
        reason = _CHANGED
    else:
        reason = _choice.reason
    return ActiveDatabase(path, bundled, reason, directories)


def _synchronise_thread() -> None:
    """Give the calling thread's PROJ context the current search path.

    pyproj keeps one context per thread and set_data_dir() updates only the
    caller's, so a thread that used pyproj before another switched would
    otherwise report one database and read another.
    """
    # Imported here, since importing geodesy imports most of the package.
    from geodetic_engine.geodesy.database import database_identity

    with suppress(DataDirError):
        database_identity()


def _log(active: ActiveDatabase) -> None:
    if active.bundled:
        logger.info(
            "PROJ reads the bundled OSDU database %s: %s", active.path, active.reason
        )
    else:
        logger.info(
            "PROJ reads %s, not the bundled OSDU database: %s",
            active.path or "no proj.db",
            active.reason,
        )


def _bundled_database() -> Path | None:
    path = BUNDLED_DIRECTORY / _DATABASE
    return path if path.is_file() else None


def _user_data_dir() -> str | None:
    # pyproj keeps the set_data_dir() argument in a private global.
    # tests/test_bundled_database.py fails if a pyproj upgrade renames it.
    value = getattr(datadir, "_USER_PROJ_DATA", None)
    return None if value is None else str(value)


def _environment_data_dir() -> tuple[str, str]:
    """The variable pyproj reads its data directories from, and its value."""
    if "PROJ_DATA" in os.environ:
        return "PROJ_DATA", os.environ["PROJ_DATA"]
    return "PROJ_LIB", os.environ.get("PROJ_LIB", "")


def _data_dir() -> str | None:
    try:
        return datadir.get_data_dir()
    except DataDirError:
        return None


def _split(value: str | None) -> list[str]:
    return [entry for entry in (value or "").split(os.pathsep) if entry]


def _key(entry: str) -> str:
    return os.path.normcase(os.path.realpath(entry))


def _same(first: str | Path, second: str | Path) -> bool:
    return _key(str(first)) == _key(str(second))


def _unique(entries: list[str]) -> list[str]:
    seen: set[str] = set()
    found = []
    for entry in entries:
        if (key := _key(entry)) not in seen:
            seen.add(key)
            found.append(entry)
    return found


def _database_first(entries: list[str]) -> list[str]:
    """Move the first directory holding a proj.db to the front, as pyproj needs."""
    for index, entry in enumerate(entries):
        if (Path(entry) / _DATABASE).is_file():
            return [entry, *entries[:index], *entries[index + 1 :]]
    raise FileNotFoundError(
        "no directory on PROJ's search path but the bundled database's holds "
        "a proj.db to switch to"
    )
