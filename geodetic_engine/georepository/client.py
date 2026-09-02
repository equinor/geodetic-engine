"""HTTP client for the Georepository API.

Written against the Georepository OpenAPI document rather than reusing any
organisation-internal wrapper, so the workflow runs against any instance.

Two properties matter for correctness. Paging is 0-based and every page is
followed until the advertised ``TotalResults`` has been collected; a truncated
import would leave the resulting database quietly incomplete. And the API has
no server-side authority filter, so every collection must be enumerated in full
and filtered on ``DataSource`` here.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator
from typing import Any

import httpx

from geodetic_engine.georepository.auth import GeorepositoryCredential
from geodetic_engine.georepository.config import GeorepositoryConfig
from geodetic_engine.georepository.errors import (
    GeorepositoryApiError,
    PaginationTruncatedError,
)

logger = logging.getLogger(__name__)

_RETRY_STATUS = frozenset({502, 503, 504})
_MAX_ATTEMPTS = 3

# Query values for the {object}/export endpoint. formatVersion is deliberately
# not sent: the parameter is a small enum rather than a year, its default is the
# WKT2 rendering this workflow wants, and passing a year is rejected outright
# (formatVersion=1 yields WKT1, 2019 answers HTTP 500).
_WKT_FORMAT = "WKT"

# Export is implemented on the generic CRS collection only; the per-kind
# collections answer HTTP 404 for it, so a CRS href is rewritten onto this one.
_CRS_EXPORT_COLLECTION = "CoordRefSystem"

JsonObject = dict[str, Any]


def _export_url(href: str) -> str:
    """The export URL for an object, given its own URL.

    A CRS is rewritten onto the generic collection: the register implements
    export there and nowhere else, so ``GeodeticCoordRefSystem/4143/export``
    answers HTTP 404 where ``CoordRefSystem/4143/export`` answers the WKT.
    """
    prefix, _, code = href.rstrip("/").rpartition("/")
    root, _, collection = prefix.rpartition("/")
    if collection.endswith(_CRS_EXPORT_COLLECTION):
        prefix = f"{root}/{_CRS_EXPORT_COLLECTION}"
    return f"{prefix}/{code}/export"


class GeorepositoryClient:
    """Reads objects from a Georepository instance.

    Example:
        >>> client = GeorepositoryClient(config)  # doctest: +SKIP
        >>> systems = list(  # doctest: +SKIP
        ...     client.iter_collection("GeodeticCoordRefSystem")
        ... )
    """

    def __init__(
        self,
        config: GeorepositoryConfig,
        *,
        credential: GeorepositoryCredential | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._config = config
        self._credential = credential or GeorepositoryCredential(
            token_url=config.token_url,
            client_id=config.client_id,
            client_secret=config.client_secret,
            scope=config.scope,
            timeout=config.request_timeout,
            transport=transport,
        )
        self._client = httpx.Client(timeout=config.request_timeout, transport=transport)
        self._object_cache: dict[str, JsonObject] = {}

    def close(self) -> None:
        """Close the HTTP connection pools."""
        self._client.close()
        self._credential.close()

    def __enter__(self) -> GeorepositoryClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _request(self, url: str, params: dict[str, Any] | None = None) -> JsonObject:
        payload = self._request_raw(url, params)
        if not isinstance(payload, dict):
            raise GeorepositoryApiError(
                f"GET {url} returned {type(payload).__name__}, expected an object"
            )
        return payload

    def _request_raw(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self._fetch(url, params)
        try:
            return response.json()
        except ValueError as exc:
            raise GeorepositoryApiError(
                f"GET {url} returned a body that is not JSON"
            ) from exc

    def _request_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        """Fetch a resource that answers with text rather than JSON."""
        return self._fetch(url, params, accept="text/plain").text

    def _fetch(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        last_error: str = ""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._client.get(
                    url,
                    params=params,
                    headers={
                        "Accept": accept,
                        **self._credential.authorization_header(),
                    },
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt == _MAX_ATTEMPTS:
                    break
                continue

            if response.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS:
                last_error = f"HTTP {response.status_code}"
                continue
            if response.status_code != httpx.codes.OK:
                raise GeorepositoryApiError(
                    f"GET {url} returned HTTP {response.status_code}"
                )
            return response

        raise GeorepositoryApiError(
            f"GET {url} failed after {_MAX_ATTEMPTS} attempts: {last_error}"
        )

    def get_object(self, url: str) -> JsonObject:
        """Fetch a single object by absolute URL, caching the result.

        Scope, extent, unit and method objects are referenced by many parents;
        without caching the same object is refetched hundreds of times.

        Args:
            url: Absolute URL, typically taken from a ``Links`` or ``href`` field.

        Returns:
            The decoded JSON object.
        """
        if (cached := self._object_cache.get(url)) is not None:
            return cached
        payload = self._request(url)
        # Detail responses do not reliably carry a self link, and without one
        # there is no way back to per-object sub-resources such as /alias.
        if not payload.get("Links"):
            payload["Links"] = [{"rel": "self", "href": url}]
        self._object_cache[url] = payload
        return payload

    def resolve(self, link: JsonObject | None) -> JsonObject:
        """Follow a ``ChildLink``-shaped reference.

        Args:
            link: A mapping with an ``href`` key, or None.

        Returns:
            The referenced object, or an empty mapping when there is no link.
            An empty mapping is returned only for an absent link; a link that
            cannot be fetched raises.
        """
        if not link:
            return {}
        href = link.get("href")
        if not href:
            return {}
        return self.get_object(str(href))

    def self_href(self, item: JsonObject) -> str | None:
        """Return the canonical URL of an object, if it advertises one."""
        links = item.get("Links") or []
        for link in links:
            url = link.get("href")
            if url and str(link.get("rel") or "").casefold() in {"self", ""}:
                return str(url)
        if links and (url := links[0].get("href")):
            return str(url)
        return None

    def aliases(self, item: JsonObject) -> list[JsonObject]:
        """Fetch the alias records of an object.

        Every object type exposes ``{object}/alias``. The detail representation
        sometimes carries an inline ``Alias`` array as well; that is preferred
        when present to avoid a round trip.

        Args:
            item: A detail object carrying ``Links`` and possibly ``Alias``.

        Returns:
            ``Details``-shaped alias records, each with ``Alias`` and
            ``NamingSystem``. An empty list when the object has no aliases.
        """
        if inline := item.get("Alias"):
            return [record for record in inline if isinstance(record, dict)]
        href = self.self_href(item)
        if href is None:
            return []
        payload = self._request_raw(f"{href.rstrip('/')}/alias")
        if isinstance(payload, list):
            return [record for record in payload if isinstance(record, dict)]
        results = payload.get("Results") if isinstance(payload, dict) else None
        return [record for record in results or [] if isinstance(record, dict)]

    def detail(self, item: JsonObject) -> JsonObject:
        """Fetch the full object behind a search result.

        Collection endpoints return summaries; the fields needed to build a
        proj.db row only appear on the detail representation.

        Args:
            item: A search result carrying a ``Links`` array.

        Returns:
            The full object, or the input unchanged when it carries no link.
        """
        href = self.self_href(item)
        return self.get_object(href) if href else item

    def wkt(self, item: JsonObject) -> str | None:
        """Export an object as WKT2.

        The register's own rendering is used rather than one rebuilt from the
        object's fields, so that a CRS this workflow does not model in full
        still reaches PROJ exactly as the authority stated it.

        Args:
            item: A detail object carrying ``Links``.

        Returns:
            The WKT string, or None when the object advertises no link or the
            instance returns an empty body.

        Raises:
            GeorepositoryApiError: If the export request fails. A bound CRS is
                one such case: the endpoint exists but answers HTTP 501.

        Example:
            >>> client.wkt(client.detail(item))  # doctest: +SKIP
            'GEOGCRS["ED50",DATUM[...'
        """
        href = self.self_href(item)
        if href is None:
            return None
        payload = self._request_text(_export_url(href), {"format": _WKT_FORMAT})
        # A JSON-quoted string is returned by some deployments; a bare WKT body
        # by others. Both start with the object keyword once unwrapped.
        text = payload.strip()
        if text.startswith('"') and text.endswith('"'):
            with contextlib.suppress(ValueError):
                text = str(json.loads(text))
        return text or None

    def iter_collection(
        self,
        endpoint: str,
        *,
        authorities: frozenset[str] | None = None,
    ) -> Iterator[JsonObject]:
        """Yield every object in a collection endpoint, page by page.

        Args:
            endpoint: Endpoint name such as ``Transformation``.
            authorities: If given, only yield objects whose ``DataSource``
                matches one of these names, compared case-insensitively. The API
                offers no server-side equivalent.

        Yields:
            Search result objects in server order.

        Raises:
            PaginationTruncatedError: If the server advertises more results than
                were collected, or repeats a page, which indicates the ``page``
                parameter was ignored.
        """
        url = self._config.endpoint(endpoint)
        wanted = (
            frozenset(name.casefold() for name in authorities)
            if authorities is not None
            else None
        )
        page = 0
        collected = 0
        kept = 0
        total: int | None = None
        previous_page_keys: frozenset[tuple[Any, Any]] | None = None

        while True:
            payload = self._request(
                url,
                params={
                    "page": page,
                    "pageSize": self._config.page_size,
                    "includeWorld": "true",
                    "includeDeprecated": str(self._config.include_deprecated).lower(),
                },
            )
            results = payload.get("Results") or []
            if total is None:
                total = int(payload.get("TotalResults") or 0)

            if not results:
                break

            page_keys = frozenset(
                (item.get("Code"), item.get("DataSource")) for item in results
            )
            if page_keys == previous_page_keys:
                raise PaginationTruncatedError(
                    f"{url} returned the same page twice at page={page}; the "
                    "server appears to ignore the 'page' parameter, so the "
                    "import cannot be shown to be complete"
                )
            previous_page_keys = page_keys

            collected += len(results)
            for item in results:
                if (
                    wanted is None
                    or str(item.get("DataSource") or "").casefold() in wanted
                ):
                    kept += 1
                    yield item

            if collected >= total:
                break
            page += 1

        if total and collected < total:
            raise PaginationTruncatedError(
                f"{url} advertised {total} results but only {collected} were "
                f"returned across {page + 1} pages"
            )
        logger.info(
            "%s: %d of %d results kept over %d page(s)",
            endpoint,
            kept,
            total or 0,
            page + 1,
        )

    def latest_version(self) -> str | None:
        """Return the newest Georepository version name, for provenance.

        Returns:
            The ``Name`` of the most recent version history entry, or None when
            the instance exposes no version history.
        """
        entries = list(self.iter_collection("VersionHistory"))
        if not entries:
            return None
        newest = max(
            entries,
            key=lambda item: (
                str(item.get("VersionDate") or ""),
                int(item.get("Code") or 0),
            ),
        )
        name = newest.get("Name") or newest.get("VersionNumber")
        return str(name) if name is not None else None
