# Georepository client

`geodetic_engine.georepository` is an HTTP client for a Georepository geodetic
registry. It was written
against the Georepository OpenAPI document, so it works with any instance, not
just one deployment.

**Use it when** you want to read a register's CRSs, datums and transformations
from Python. {doc}`projdb` uses it to build a custom `proj.db`, but it does not
depend on that.

**You do not need it** to transform coordinates. Transformations read a
`proj.db`, never the register.

```{admonition} Not executed
:class: note

The examples on this page need network access to a Georepository instance and
OAuth2 credentials, so they are not run during the documentation build.
```

## Configuration

{class}`~geodetic_engine.georepository.GeorepositoryConfig` holds everything
the client needs:

| Field | Default | Meaning |
|---|---|---|
| `api_url` | required | Base URL of the instance |
| `client_id`, `client_secret` | required | OAuth2 client credentials; see {ref}`georepository-credentials` |
| `token_url` | `{api_url}/auth/connect/token` | Identity server token endpoint |
| `scope` | `GeoRepositoryAPI_Scope` ({data}`~geodetic_engine.georepository.DEFAULT_SCOPE`) | Scope the client is granted |
| `page_size` | `500` ({data}`~geodetic_engine.georepository.DEFAULT_PAGE_SIZE`) | Objects per page when enumerating a collection |
| `request_timeout` | `60.0` | Seconds per HTTP request |
| `include_deprecated` | `True` | Whether collections include deprecated objects |

The configuration is validated on construction. An invalid one raises
{class}`~geodetic_engine.georepository.GeorepositoryConfigError`.

## Reading a collection

```python
from geodetic_engine.georepository import GeorepositoryClient, GeorepositoryConfig

config = GeorepositoryConfig(
    api_url="https://georepository.example.com",
    client_id=...,       # from the environment, never from source code
    client_secret=...,
)

with GeorepositoryClient(config) as client:
    for datum in client.iter_collection("Datum", authorities=frozenset({"YourAuthority"})):
        print(datum["Code"], datum["Name"])
```

{meth}`~geodetic_engine.georepository.GeorepositoryClient.iter_collection`
yields every object in a collection endpoint, following pages until the
advertised `TotalResults` is reached. The API has no server-side authority
filter, so `authorities=` filters on each object's `DataSource` on the client
side.

Other methods fetch detail around a search result:

| Method | Returns |
|---|---|
| {meth}`~geodetic_engine.georepository.GeorepositoryClient.detail` | The full object behind a search result |
| {meth}`~geodetic_engine.georepository.GeorepositoryClient.get_object` | One object by absolute URL, cached for the client's lifetime |
| {meth}`~geodetic_engine.georepository.GeorepositoryClient.resolve` | The object a `ChildLink`-shaped reference points to |
| {meth}`~geodetic_engine.georepository.GeorepositoryClient.aliases` | An object's alias records |
| {meth}`~geodetic_engine.georepository.GeorepositoryClient.wkt` | The object exported as WKT2 |
| {meth}`~geodetic_engine.georepository.GeorepositoryClient.versions` | The newest version of each dataset the register holds |
| {meth}`~geodetic_engine.georepository.GeorepositoryClient.latest_version` | The newest version name, for provenance |

## Paging is verified

A silently truncated list would leave an imported database silently
incomplete. The client raises
{class}`~geodetic_engine.georepository.PaginationTruncatedError` if the server
advertises more results than it returns, or ignores the `page` parameter.

## Retries and trusted origins

A request that fails to connect, or returns HTTP 502, 503 or 504, is retried,
up to three attempts. Every request URL, including ones the server returns in
links, must be HTTPS with the same host and port as `api_url`, and must have no
user-info or fragment. A malicious or misconfigured response therefore cannot
send the bearer token to another host.

## Response cache

When used by `geodetic-projdb`, responses are cached in a local SQLite file
next to the output database, so a rebuild does not fetch everything again. The
cache sits below the client as an `httpx` transport:

- Only `GET` responses with status `200` are cached. The OAuth2 token request
  is a `POST`, so a bearer token never reaches the disk, and a transient `502`
  is never stored as an answer.
- The register's version history is never cached, because it is what decides
  whether the rest of the cache is stale.

`--no-cache`, `--refresh-cache` and `--cache-db` on
{doc}`/cli/geodetic-projdb` control it.

(georepository-credentials)=

## Obtaining OAuth2 credentials

Ask your Georepository administrator for a **client credentials**
registration. You need:

- a client id and client secret for a machine account;
- the client granted the API scope, `GeoRepositoryAPI_Scope` unless your
  instance uses another;
- the token endpoint URL, if it is not `{your-instance}/auth/connect/token`.

The Georepository OpenAPI document advertises an *implicit* flow, which is for
interactive use in a browser. A server-to-server caller like this one uses the
client credentials grant against the identity server.
{class}`~geodetic_engine.georepository.GeorepositoryCredential` requests the
token with HTTP Basic authentication and reuses it until shortly before it
expires.

## Errors

| Exception | Raised when |
|---|---|
| {class}`~geodetic_engine.georepository.GeorepositoryConfigError` | The configuration is invalid |
| {class}`~geodetic_engine.georepository.GeorepositoryAuthError` | A token cannot be obtained |
| {class}`~geodetic_engine.georepository.GeorepositoryApiError` | A request fails, returns something that is not the expected JSON, or points to another origin |
| {class}`~geodetic_engine.georepository.PaginationTruncatedError` | Paging returned fewer objects than advertised |

All derive from {class}`~geodetic_engine.georepository.GeorepositoryError`.
