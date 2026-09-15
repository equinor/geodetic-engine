"""A local cache of Georepository responses, so a rebuild is not a re-fetch.

A build spends almost all of its time waiting on the network, and nearly all of
those requests ask for objects that have not changed. Enumerating every CRS in
the register to find this authority's annotations is the worst of it: thousands
of individual fetches of other authorities' objects, repeated in full on every
build.

The cache sits underneath the API client as an ``httpx`` transport, so it is
below the credential, the retry loop and the trusted-origin check. Nothing about
how a response is obtained, authorised or validated changes; only whether it
travels.

Two rules keep it honest:

Only ``GET`` is cached. The same transport object is handed to both the API
client and :class:`~geodetic_engine.georepository.auth.GeorepositoryCredential`,
whose token request is a ``POST``, so restricting by method is what keeps a
bearer token off disk. Only ``200`` is cached, so a transient ``502`` the retry
loop would have recovered from is never persisted as an answer.

The version history is never cached. It is the one thing a build reads to decide
whether the rest of the cache is stale, and a cached answer to that question can
only ever confirm itself.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType

import httpx

logger = logging.getLogger(__name__)

# Endpoint whose answer decides whether the rest of the cache may be trusted.
_NEVER_CACHED = "/VersionHistory"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS response (
    url          TEXT NOT NULL,
    accept       TEXT NOT NULL,
    status       INTEGER NOT NULL,
    content_type TEXT,
    body         BLOB NOT NULL,
    fetched_at   REAL NOT NULL,
    PRIMARY KEY (url, accept)
);
CREATE TABLE IF NOT EXISTS register_version (
    data_source TEXT PRIMARY KEY,
    version     TEXT NOT NULL,
    recorded_at REAL NOT NULL
);
"""


class CacheMode(StrEnum):
    """What a build does with the response cache.

    Attributes:
        USE: Serve what is cached and store what is not. The default.
        REFRESH: Fetch everything afresh and overwrite the cache, so the next
            build is fast again.
        OFF: Ignore the cache entirely, reading nothing and writing nothing.
    """

    USE = "use"
    REFRESH = "refresh"
    OFF = "off"


@dataclass(slots=True)
class CacheStats:
    """How much of a build was served locally, for the build report."""

    hits: int = 0
    misses: int = 0
    stored: int = 0
    versions: dict[str, str] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """Render for the build report, hit rate included."""
        requests = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stored": self.stored,
            "hit_rate": round(self.hits / requests, 3) if requests else 0.0,
            "versions_when_cached": dict(sorted(self.versions.items())),
            "stale_against": sorted(self.stale),
        }


class ResponseCache:
    """Responses from one register, kept in a SQLite file.

    Bodies are stored compressed: the register answers in JSON, which is both
    the bulk of the cache and highly compressible, and the decompression cost is
    far below the request it replaces.

    Example:
        >>> with ResponseCache(Path("build/proj.db.cache")) as cache:  # doctest: +SKIP
        ...     cache.record_versions({"EPSG": "12.053", "Equinor": "1.103"})
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.stats = CacheStats()
        # A future thread pool over the annotation pass would share this
        # connection, so it is guarded rather than bound to its creating thread.
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        """Commit and close the underlying database."""
        with self._lock:
            self._connection.commit()
            self._connection.close()

    def __enter__(self) -> ResponseCache:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def read(self, url: str, accept: str) -> tuple[int, str | None, bytes] | None:
        """Return a stored response, or None when nothing is stored for it."""
        with self._lock:
            row = self._connection.execute(
                "SELECT status, content_type, body FROM response "
                "WHERE url = ? AND accept = ?",
                (url, accept),
            ).fetchone()
        if row is None:
            self.stats.misses += 1
            return None
        self.stats.hits += 1
        return int(row[0]), row[1], zlib.decompress(row[2])

    def write(
        self, url: str, accept: str, status: int, content_type: str | None, body: bytes
    ) -> None:
        """Store a response, replacing any earlier one for the same request."""
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO response "
                "(url, accept, status, content_type, body, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (url, accept, status, content_type, zlib.compress(body), time.time()),
            )
            self._connection.commit()
        self.stats.stored += 1

    def versions(self) -> dict[str, str]:
        """The register versions the cached responses were fetched against."""
        with self._lock:
            rows = self._connection.execute(
                "SELECT data_source, version FROM register_version"
            ).fetchall()
        return {str(source): str(version) for source, version in rows}

    def record_versions(self, versions: dict[str, str]) -> None:
        """Record the register versions the cache now reflects."""
        now = time.time()
        with self._lock:
            self._connection.executemany(
                "INSERT OR REPLACE INTO register_version "
                "(data_source, version, recorded_at) VALUES (?, ?, ?)",
                [(source, version, now) for source, version in versions.items()],
            )
            self._connection.commit()

    def clear(self) -> None:
        """Drop every cached response, keeping the recorded versions."""
        with self._lock:
            self._connection.execute("DELETE FROM response")
            self._connection.commit()


class CachingTransport(httpx.BaseTransport):
    """Serves ``GET`` requests from a :class:`ResponseCache` when it can.

    Args:
        cache: Where responses are read from and written to.
        wrapped: Transport used on a miss. Defaults to a plain HTTPS transport.
        refresh: Fetch every request afresh and overwrite what is stored, so a
            build can rebuild the cache without discarding it first.
    """

    def __init__(
        self,
        cache: ResponseCache,
        *,
        wrapped: httpx.BaseTransport | None = None,
        refresh: bool = False,
    ) -> None:
        self._cache = cache
        self._wrapped = wrapped or httpx.HTTPTransport()
        self._refresh = refresh

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """Answer from the cache, or fetch and store."""
        if not self._is_cacheable(request):
            return self._wrapped.handle_request(request)

        url, accept = str(request.url), request.headers.get("Accept", "")
        if not self._refresh and (stored := self._cache.read(url, accept)) is not None:
            status, content_type, body = stored
            headers = {"Content-Type": content_type} if content_type else {}
            return httpx.Response(status, headers=headers, content=body)

        response = self._wrapped.handle_request(request)
        if response.status_code != httpx.codes.OK:
            return response
        body = response.read()
        self._cache.write(
            url,
            accept,
            response.status_code,
            response.headers.get("Content-Type"),
            body,
        )
        return response

    def close(self) -> None:
        """Close the wrapped transport. The cache outlives it."""
        self._wrapped.close()

    @staticmethod
    def _is_cacheable(request: httpx.Request) -> bool:
        """Whether a request may be served from, or written to, the cache."""
        return request.method == "GET" and _NEVER_CACHED not in request.url.path


def is_newer(live: str, cached: str) -> bool:
    """Whether a register version is later than the one a cache was built on.

    Versions are dotted numbers such as ``1.103``, sometimes written with a
    leading ``v``. Anything that does not parse that way is compared as text,
    which at worst reports a difference as a change -- the safe direction for a
    staleness warning.

    Example:
        >>> is_newer("v1.103", "v1.102")
        True
        >>> is_newer("12.053", "12.053")
        False
    """

    def parts(version: str) -> tuple[int, ...] | None:
        cleaned = version.strip().lstrip("vV")
        pieces = cleaned.split(".")
        if not all(piece.isdigit() for piece in pieces) or not cleaned:
            return None
        return tuple(int(piece) for piece in pieces)

    live_parts, cached_parts = parts(live), parts(cached)
    if live_parts is None or cached_parts is None:
        return live.strip() != cached.strip()
    return live_parts > cached_parts
