"""Make the OSDU database shipped with this package the one PROJ reads.

A release wheel ships ``geodetic_engine/osdudb/_data/proj_data/proj.db``: the
proj.db of the pinned pyproj wheel with the official OSDU catalogue built into
it. When ``geodetic_engine`` is first imported, the first of these rules that
matches decides whether PROJ reads it:

a. ``GEODETIC_ENGINE_OSDU_DB=0``: nothing is changed, and no installed
   distribution's database is looked for.
b. ``pyproj.datadir.set_data_dir()`` or ``append_data_dir()`` was called
   before the import: nothing is changed.
c. ``PROJ_DATA``, or ``PROJ_LIB`` when ``PROJ_DATA`` is unset, names a
   directory that holds a ``proj.db``: PROJ's search path becomes the
   variable's directories, the first that holds a ``proj.db`` first. That is
   pyproj's search path already when pyproj read the variable on import and
   its first directory holds the ``proj.db``, and nothing is changed. Any
   other search path, say for a value set after pyproj was imported, is
   replaced, with a warning; but if the variable's ``proj.db`` records no
   database layout, another major one, or an older minor one than the
   ``proj.db`` PROJ reads, which PROJ would refuse, nothing is changed, with a
   warning.
d. Otherwise PROJ's search path becomes the bundled database's directory, then
   the ``PROJ_DATA``/``PROJ_LIB`` directories, then pyproj's own data
   directory, so grids and ``proj.ini`` are still found where they were.

An installed distribution can provide the bundled database in the shipped
one's place, such as a wheel of an organisation's register that
scripts/build_organisation_projdb.py writes: it names a directory that holds a
``proj.db`` alone under the entry point group ``geodetic_engine.proj_data``.
It is passed over, with a warning, if it cannot be loaded, its module raising
an exception or calling ``sys.exit()``, if it is for another PROJ release than
pyproj links, if PROJ would refuse its database layout, judged by the
``proj.db`` that came with that PROJ, or if more than one distribution
provides one; and every one is, if the installed distributions' entry points
cannot be read.

That is the precedence of this project's pyproj wheels (``set_data_dir``, then
a ``PROJ_DATA`` holding a database, then the database the wheel bundles) with
the OSDU database in place of the wheel's, applied to the environment as it is
on import: the environment a child process starts with. No file is written and
the environment is left alone, so a child process that imports the package
applies the same rules.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import sys
from contextlib import closing, suppress
from dataclasses import dataclass
from functools import cache
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Final

from pyproj import datadir, proj_version_str
from pyproj.exceptions import DataDirError

from geodetic_engine import _search_path
from geodetic_engine._sqlite import read_only

logger = logging.getLogger(__name__)

ENVIRONMENT_VARIABLE: Final = "GEODETIC_ENGINE_OSDU_DB"
ENTRY_POINT_GROUP: Final = "geodetic_engine.proj_data"

# A directory of its own: PROJ reads the file called proj.db in each directory
# of its search path, so nothing else may be called that here.
BUNDLED_DIRECTORY: Final = (
    Path(__file__).resolve().parent / "osdudb" / "_data" / "proj_data"
)

_DATABASE: Final = "proj.db"
_LAYOUT: Final = ("DATABASE.LAYOUT.VERSION.MAJOR", "DATABASE.LAYOUT.VERSION.MINOR")
_OFF: Final = frozenset({"0", "false", "no", "off"})
_ON: Final = frozenset({"", "1", "true", "yes", "on"})
_CHANGED: Final = (
    "pyproj.datadir.set_data_dir() changed PROJ's search path after "
    "geodetic_engine chose the database"
)
# What another distribution's code may raise without ending this import:
# sys.exit() included, KeyboardInterrupt not.
_FOREIGN: Final = (Exception, SystemExit)


@dataclass(frozen=True, slots=True)
class ActiveDatabase:
    """The ``proj.db`` PROJ reads, and why it is that one.

    Attributes:
        path: The database PROJ reads, the ``proj.db`` in the first directory
            of its search path. None when that directory holds none.
        bundled: Whether it is the bundled database: the one an installed
            distribution provides, or else the OSDU database shipped with
            this package.
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


_choice: _Choice | None = None


@dataclass(frozen=True, slots=True)
class _Provided:
    """The database an installed distribution provides, or why none is read."""

    path: Path | None = None
    provider: str | None = None
    problem: str | None = None


def activate_at_import() -> None:
    """Apply rules a to d, once, when ``geodetic_engine`` is first imported."""
    global _choice
    # Logged once the lock is released; see _search_path.
    warnings: list[str] = []
    raw = os.environ.get(ENVIRONMENT_VARIABLE)
    # Loading a provider imports its module, so it is found before the lock is
    # taken; and not at all under rule a, so that no code of another
    # distribution's runs on an import that turned the bundled database off.
    database = _shipped_database() if _off(raw) else _bundled_database()
    with _search_path.lock:
        if _choice is not None:
            return
        reason, search = _decide(raw, warnings)
        before = _data_dir()
        if search is not None:
            _search_path.set_search_path(os.pathsep.join(search))
        _choice = _Choice(
            reason, _data_dir(), restore=before if search is not None else None
        )
        active = _active(database)
    for warning in warnings:
        logger.warning("%s", warning)
    _log(active)


def active_database() -> ActiveDatabase:
    """Report the ``proj.db`` PROJ reads and why.

    Returns:
        The database, whether it is the bundled database, and the reason:
        the rule that chose it on import, a call of
        :func:`use_bundled_database`, or a later
        ``pyproj.datadir.set_data_dir()``.

    Example:
        >>> from geodetic_engine.osdudb import active_database
        >>> active_database().bundled  # doctest: +SKIP
        True
    """
    return _reading()


def use_bundled_database(enabled: bool = True) -> ActiveDatabase:
    """Switch the bundled database on or off.

    The bundled database is the one an installed distribution provides, or
    else the OSDU database shipped with this package. This overrides the
    choice made on import, whatever made it. Switched on, the
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
    provided = _provided()
    if enabled and provided.problem is not None:
        logger.warning("%s", provided.problem)
    with _search_path.lock:
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
            _search_path.set_search_path(
                os.pathsep.join(_unique([bundled, *directories]))
            )
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
                    search = _database_first(_split(_choice.restore))
                else:
                    search = _database_first(
                        [e for e in directories if not _same(e, database.parent)]
                    )
                _search_path.set_search_path(os.pathsep.join(search))
            reason = "use_bundled_database(False) switched it off"
        _choice = _Choice(reason, _data_dir(), restore=restore)
    active = _reading()
    _log(active)
    return active


def _reading() -> ActiveDatabase:
    """Re-point the calling thread's PROJ context, then report what it reads.

    The thread re-points without the lock, so the report is taken under it
    only once no switch has landed in between.
    """
    database = _bundled_database()
    while True:
        with suppress(DataDirError):
            _search_path.database_identity()
        with _search_path.lock:
            if _search_path.followed():
                return _active(database)


def _off(raw: str | None) -> bool:
    """Whether a value of the variable turns the bundled database off."""
    return (raw or "").strip().casefold() in _OFF


def _decide(raw: str | None, warnings: list[str]) -> tuple[str, list[str] | None]:
    """Why the bundled database is or is not used, and the search path to set.

    Args:
        raw: The value of the variable that turns it off, None if unset.
        warnings: What to warn about is added here, for the caller to log.
    """
    # Read first: once this package has called set_data_dir() itself, a call
    # of the caller's own can no longer be told apart from it.
    explicit = _user_data_dir()
    if _off(raw):
        return f"{ENVIRONMENT_VARIABLE}={raw} turns it off", None
    if (raw or "").strip().casefold() not in _ON:
        warnings.append(
            f"{ENVIRONMENT_VARIABLE}={raw!r} is not a boolean; the bundled OSDU "
            "database stays on by default"
        )
    if explicit is not None:
        return (
            "pyproj.datadir.set_data_dir() or append_data_dir() was called "
            "before geodetic_engine was imported",
            None,
        )
    name, value = _environment_data_dir()
    entries = _split(value)
    for index, entry in enumerate(entries):
        if (Path(entry) / _DATABASE).is_file():
            if len(entries) == 1:
                reason = f"{name}={entry} holds a proj.db"
            else:
                reason = f"{name} lists {entry}, which holds a proj.db"
            # pyproj passes PROJ the proj.db of the first directory only.
            search = [entry, *entries[:index], *entries[index + 1 :]]
            # pyproj keeps the value verbatim when it reads it, and reads it
            # once: a value it has not read, or read with no proj.db first, is
            # applied, so that this process reads what a new one would.
            current = _split(_data_dir())
            if current == search:
                return reason, None
            if (refused := _refusal(name, entry, current)) is not None:
                warnings.append(refused)
                return refused, None
            warnings.append(
                f"PROJ's search path becomes {os.pathsep.join(search)}, from "
                f"{name}; it was {os.pathsep.join(current) or 'unset'}"
            )
            return reason, search
    provided = _provided()
    if provided.problem is not None:
        warnings.append(provided.problem)
    database = _bundled_database()
    if database is None:
        return (
            f"this installation has no bundled OSDU database at "
            f"{BUNDLED_DIRECTORY / _DATABASE}",
            None,
        )
    search = _unique([str(database.parent), *entries, *_split(_data_dir())])
    default = (
        "it is the default"
        if provided.path is None
        else f"{provided.provider} provides it"
    )
    if entries:
        return f"{default}, and {name} names no proj.db", search
    return f"{default}, and nothing chose another database", search


def _active(database: Path | None) -> ActiveDatabase:
    """What PROJ reads, ``database`` being the bundled database, if any."""
    current = _data_dir()
    directories = tuple(Path(entry) for entry in _split(current))
    path = None
    if directories and (candidate := directories[0] / _DATABASE).is_file():
        path = candidate.resolve()
    bundled = path is not None and database is not None and path == database.resolve()
    if _choice is None:
        reason = "geodetic_engine has not chosen a database"
    elif current != _choice.seen:
        reason = _CHANGED
    else:
        reason = _choice.reason
    return ActiveDatabase(path, bundled, reason, directories)


def _log(active: ActiveDatabase) -> None:
    if active.bundled:
        logger.info(
            "PROJ reads the bundled database %s: %s", active.path, active.reason
        )
    else:
        logger.info(
            "PROJ reads %s, not the bundled database: %s",
            active.path or "no proj.db",
            active.reason,
        )


def _bundled_database() -> Path | None:
    """The bundled database: an installed distribution's, or else the shipped one."""
    if (provided := _provided()).path is not None:
        return provided.path
    return _shipped_database()


def _shipped_database() -> Path | None:
    path = BUNDLED_DIRECTORY / _DATABASE
    return path if path.is_file() else None


@cache
def _provided() -> _Provided:
    """The database an installed distribution provides, found once.

    Loading an entry point imports its module, so this is first called
    before the lock is taken; see _search_path.
    """
    # Every installed distribution's metadata is read, and one that is
    # malformed, however unrelated, must not fail this import either.
    try:
        points = sorted(entry_points(group=ENTRY_POINT_GROUP), key=_provider)
    except _FOREIGN as error:
        return _Provided(
            problem=f"no proj.db under {ENTRY_POINT_GROUP} is read: reading the "
            f"installed distributions' entry points raised {error!r}"
        )
    if not points:
        return _Provided()
    if len(points) > 1:
        names = ", ".join(_provider(point) for point in points)
        return _Provided(
            problem=f"{names} each provide a proj.db under {ENTRY_POINT_GROUP}, "
            "so none of them is read: uninstall all but one"
        )
    (point,) = points
    provider = _provider(point)
    # Another distribution's code: whatever it raises must not fail this import.
    # Resolved now: PROJ would read a relative one from the working directory
    # of the moment.
    try:
        database = (Path(point.load()) / _DATABASE).resolve()
    except _FOREIGN as error:
        return _Provided(
            problem=f"{provider} provides no proj.db that can be read: loading "
            f"{point.value} raised {error!r}"
        )
    if not database.is_file():
        return _Provided(problem=f"{provider} provides no proj.db at {database}")
    release = _release(database)
    if release != proj_version_str:
        built = "an unknown PROJ release" if release is None else f"PROJ {release}"
        return _Provided(
            problem=f"{provider} provides a proj.db for {built}, not the PROJ "
            f"{proj_version_str} pyproj links, so it is not read: install its "
            f"build for PROJ {proj_version_str}"
        )
    # Not the proj.db PROJ reads now: one the caller chose may have a later
    # minor layout than PROJ needs, and the answer is kept for good.
    reference = _linked_database() or _first_database(_split(_data_dir()))
    if (
        reference is not None
        and (refused := _refused_layout(database, reference)) is not None
    ):
        layout, expected = refused
        return _Provided(
            problem=f"{provider} provides a proj.db with {layout}, which PROJ "
            f"cannot open: it needs {expected} or a later minor one, so it is "
            "not read"
        )
    return _Provided(database, provider)


def _provider(point: EntryPoint) -> str:
    distribution = point.dist
    if distribution is None:
        return point.name
    return f"{distribution.name} {distribution.version}"


def _release(database: Path) -> str | None:
    """The PROJ release a proj.db was built for, or None if it records none."""
    try:
        with closing(read_only(database)) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'PROJ.VERSION'"
            ).fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else str(row[0])


def _linked_database() -> Path | None:
    """The proj.db that came with the PROJ pyproj links, or None if not found.

    Looked for where pyproj looks when nothing chose its data directory, but
    for PROJ_DATA and PROJ_LIB, which may name any database: in pyproj's wheel,
    under the environment's prefix, then beside the proj program on PATH. It
    must be for the PROJ release pyproj links.
    """
    directories = [
        Path(datadir.__file__).absolute().parent / "proj_dir" / "share" / "proj",
        Path(sys.prefix, "share", "proj"),
        Path(sys.prefix, "Library", "share", "proj"),
    ]
    program = shutil.which("proj", path=sys.prefix) or shutil.which("proj")
    if program is not None:
        directories.append(Path(program).parent.parent / "share" / "proj")
    for directory in directories:
        database = directory / _DATABASE
        if database.is_file() and _release(database) == proj_version_str:
            return database.resolve()
    return None


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


def _refusal(name: str, entry: str, current: list[str]) -> str | None:
    """Why the proj.db a variable names is not applied, or None to apply it."""
    candidate = Path(entry) / _DATABASE
    reading = _first_database(current)
    if reading is None or (refused := _refused_layout(candidate, reading)) is None:
        return None
    layout, expected = refused
    return (
        f"{name} names {candidate}, with {layout}, so PROJ keeps reading "
        f"{reading.resolve()}, with {expected}"
    )


def _first_database(directories: list[str]) -> Path | None:
    """The proj.db of the first directory that holds one, or None if none does."""
    return next(
        (
            Path(directory) / _DATABASE
            for directory in directories
            if (Path(directory) / _DATABASE).is_file()
        ),
        None,
    )


def _refused_layout(database: Path, reference: Path) -> tuple[str, str] | None:
    """Why PROJ would refuse a proj.db for its layout, or None if it would not.

    PROJ refuses a database that records no layout version, another major one,
    or a minor one older than it was built for. The layout of ``reference``, a
    proj.db PROJ opens, stands in for that, being at least as new; if it
    records none, nothing is refused.

    Returns:
        The database's layout, and the reference's.
    """
    expected = _layout(reference)
    if expected is None:
        return None
    found = _layout(database)
    if found is not None and found[0] == expected[0] and found[1] >= expected[1]:
        return None
    layout = "no database layout" if found is None else _layout_name(found)
    return layout, _layout_name(expected)


def _layout(database: Path) -> tuple[int, int] | None:
    """The database layout version a proj.db records, or None if none."""
    try:
        with closing(read_only(database)) as connection:
            rows = connection.execute(
                "SELECT key, value FROM metadata WHERE key IN (?, ?)", _LAYOUT
            ).fetchall()
        found = dict(rows)
        return int(found[_LAYOUT[0]]), int(found[_LAYOUT[1]])
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        return None


def _layout_name(layout: tuple[int, int]) -> str:
    return f"database layout {layout[0]}.{layout[1]}"
