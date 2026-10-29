"""Opening a SQLite database for reading, wherever it is installed.

Python's sqlite3 can open a database read-only only through a ``file:`` URI,
and a URI gives three characters a meaning a path does not: ``#`` begins a
fragment, ``?`` a query and ``%`` an escape (https://www.sqlite.org/uri.html).
A path put into a URI as it is names another file when it holds one of them;
after a ``#`` or ``?``, ``mode=ro`` is lost as well, and SQLite creates that
file. A package installed under a directory such as ``C#`` was read that way.
"""

from __future__ import annotations

import os
import sqlite3

_ESCAPES = str.maketrans({"%": "%25", "?": "%3F", "#": "%23"})


def read_only(database: str | os.PathLike[str]) -> sqlite3.Connection:
    """Open a database file for reading only, whatever characters its path holds.

    The path goes into the URI as it is but for those three characters, so a
    relative path, a Windows drive or UNC path and a symbolic link are opened
    exactly as the same path given to ``open()`` would be.

    Args:
        database: The database file. A missing one is an error, never created.

    Returns:
        A connection that cannot write to the file.

    Raises:
        sqlite3.OperationalError: If the file cannot be opened.
    """
    path = os.fspath(database).translate(_ESCAPES)
    # SQLite would read what follows a leading "//" as the URI's authority,
    # and refuses any but "localhost"; an empty one keeps it in the path.
    authority = "//" if path.startswith("//") else ""
    return sqlite3.connect(f"file:{authority}{path}?mode=ro", uri=True)
