"""OAuth2 client credentials authentication for a Georepository instance.

The Georepository OpenAPI document declares an implicit flow for interactive
use; server-to-server callers use the client credentials grant against the
identity server, which is not part of the documented API surface and is
therefore configured separately.
"""

from __future__ import annotations

import time

import httpx

from geodetic_engine.georepository.errors import GeorepositoryAuthError

# Refresh this many seconds before expiry so a long request cannot start with a
# token that expires mid-flight.
_RENEWAL_SKEW_SECONDS = 120.0


class GeorepositoryCredential:
    """Fetches and caches an OAuth2 access token.

    The token is requested with the client credentials grant, authenticating
    with HTTP Basic as required by the identity server, and reused until it is
    within the renewal skew of expiring.

    Example:
        >>> credential = GeorepositoryCredential(  # doctest: +SKIP
        ...     token_url="https://georepo.example.com/auth/connect/token",
        ...     client_id="...",
        ...     client_secret="...",
        ...     scope="GeoRepositoryAPI_Scope",
        ... )
        >>> credential.authorization_header()  # doctest: +SKIP
        {'Authorization': 'Bearer ...'}
    """

    def __init__(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._token: str | None = None
        self._token_type = "Bearer"
        self._expires_at = 0.0

    def __repr__(self) -> str:
        """Render without the secret or the token itself."""
        return (
            f"GeorepositoryCredential(token_url={self._token_url!r}, "
            f"scope={self._scope!r}, client_id='***', client_secret='***')"
        )

    def authorization_header(self) -> dict[str, str]:
        """Return an ``Authorization`` header, refreshing the token if needed.

        Returns:
            A single-entry mapping suitable for merging into request headers.

        Raises:
            GeorepositoryAuthError: If a token cannot be obtained.
        """
        if self._token is None or time.monotonic() >= self._expires_at:
            self._refresh()
        return {"Authorization": f"{self._token_type} {self._token}"}

    def _refresh(self) -> None:
        try:
            response = self._client.post(
                self._token_url,
                data={"grant_type": "client_credentials", "scope": self._scope},
                auth=(self._client_id, self._client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GeorepositoryAuthError(
                f"could not reach the token endpoint at {self._token_url}: {exc}"
            ) from exc

        if response.status_code != httpx.codes.OK:
            # The body may echo the client id but never the secret.
            raise GeorepositoryAuthError(
                f"token request to {self._token_url} failed with HTTP "
                f"{response.status_code}; check the client credentials and that "
                f"the client is granted the {self._scope!r} scope"
            )

        payload = response.json()
        token = payload.get("access_token") or payload.get("token")
        if not token:
            raise GeorepositoryAuthError(
                f"token endpoint {self._token_url} returned no access_token"
            )
        self._token = str(token)
        self._token_type = str(payload.get("token_type") or "Bearer")
        expires_in = float(payload.get("expires_in", 3600))
        self._expires_at = time.monotonic() + max(
            expires_in - _RENEWAL_SKEW_SECONDS, 0.0
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> GeorepositoryCredential:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
