"""Connection settings for a Georepository instance.

Kept separate from the proj.db build configuration so that the API client can
be used on its own, and so that nothing about one organisation's instance is
compiled into the package.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final

from geodetic_engine.georepository.errors import GeorepositoryConfigError

logger = logging.getLogger(__name__)

# Declared as the API's OAuth2 scope by the Georepository OpenAPI document.
DEFAULT_SCOPE: Final = "GeoRepositoryAPI_Scope"

# The API's own default page size is 10, which would mean an excessive number of
# round trips when enumerating a whole collection.
DEFAULT_PAGE_SIZE: Final = 500

# The API path this client appends itself; an operator who copies the URL out of
# a browser or an API document usually has it included already.
_API_SUFFIX: Final = re.compile(r"/api(/v1)?/?$")


@dataclass(frozen=True, slots=True)
class GeorepositoryConfig:
    """How to reach and authenticate against a Georepository instance.

    Attributes:
        api_url: Base URL of the instance, without the ``/api`` suffix. Must be
            https, since client credentials are sent on every token request.
        client_id: OAuth2 client identifier.
        client_secret: OAuth2 client secret.
        token_url: OAuth2 token endpoint. Defaults to
            ``{api_url}/auth/connect/token``. It is a separate setting because
            the token endpoint is not described by the OpenAPI document and
            cannot be assumed to sit under the API host.
        scope: Scope requested for the client credentials grant.
        page_size: Results requested per page.
        request_timeout: Per-request timeout in seconds.
        include_deprecated: Ask the API to include deprecated objects.
    """

    api_url: str
    client_id: str
    client_secret: str
    token_url: str = ""
    scope: str = DEFAULT_SCOPE
    page_size: int = DEFAULT_PAGE_SIZE
    request_timeout: float = 60.0
    include_deprecated: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "api_url", self.api_url.rstrip("/"))
        if not self.api_url:
            raise GeorepositoryConfigError(
                "the Georepository instance URL is required and has no default"
            )
        if _API_SUFFIX.search(self.api_url):
            trimmed = _API_SUFFIX.sub("", self.api_url)
            logger.warning(
                "api_url %r includes the API path, which is added automatically; "
                "using %r",
                self.api_url,
                trimmed,
            )
            object.__setattr__(self, "api_url", trimmed)
        if not self.api_url.startswith("https://"):
            raise GeorepositoryConfigError(
                f"the Georepository URL must be https, got {self.api_url!r}"
            )
        if not self.client_id or not self.client_secret:
            raise GeorepositoryConfigError(
                "both a client id and a client secret are required"
            )
        if not self.token_url:
            object.__setattr__(self, "token_url", f"{self.api_url}/auth/connect/token")
        if self.page_size < 1:
            raise GeorepositoryConfigError("page_size must be positive")

    def __repr__(self) -> str:
        """Render without secrets, so the configuration can be logged."""
        return (
            f"GeorepositoryConfig(api_url={self.api_url!r}, "
            f"scope={self.scope!r}, page_size={self.page_size!r}, "
            "client_id='***', client_secret='***')"
        )

    def endpoint(self, name: str) -> str:
        """Return the absolute URL of a v1 collection endpoint.

        Args:
            name: Endpoint name such as ``GeodeticCoordRefSystem``.

        Returns:
            Absolute URL, for example
            ``https://example.org/api/v1/GeodeticCoordRefSystem``.
        """
        return f"{self.api_url}/api/v1/{name}"
