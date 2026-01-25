# Datasource Patterns

This page covers common patterns for building robust, reusable datasources. For foundational concepts, see [Datasources](../fundamentals/datasources.md).

## Dual Datasource + Factory Pattern

When a datasource loads credentials from environment variables, you can eliminate boilerplate by implementing both `Datasource` and `DatasourceFactory` in a single class:

```python title="Illustrative example"
import os
from watchpost import Datasource, DatasourceFactory


class S3Client(Datasource, DatasourceFactory):
    """AWS S3 client that can be used directly or via factory."""

    scheduling_strategies = ()

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        region: str,
    ):
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region
        # Would initialize boto3 client here

    @classmethod
    def new(cls) -> "S3Client":
        """Factory method that loads credentials from environment."""
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "test") #! hidden
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test") #! hidden
        os.environ.setdefault("AWS_REGION", "us-east-1") #! hidden
        return cls(
            access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            region=os.environ["AWS_REGION"],
        )

    def head_object(self, bucket: str, key: str) -> dict:
        # Would call self.client.head_object(Bucket=bucket, Key=key)
        return {}
```

### Registration Options

Register as a factory when credentials come from environment:

```python title="Illustrative example"
from watchpost import Watchpost, EnvironmentRegistry, Datasource, DatasourceFactory #! hidden
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden
class S3Client(Datasource, DatasourceFactory): #! hidden
    scheduling_strategies = () #! hidden
    @classmethod #! hidden
    def new(cls) -> "S3Client": return cls() #! hidden

app = Watchpost(checks=[], execution_environment=PROD) #! hidden
app.register_datasource_factory(S3Client)
```

Or register directly with explicit credentials:

```python title="Illustrative example"
from watchpost import Watchpost, EnvironmentRegistry, Datasource #! hidden
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden
class S3Client(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def __init__(self, access_key_id: str, secret_access_key: str, region: str): pass #! hidden

app = Watchpost(checks=[], execution_environment=PROD) #! hidden
app.register_datasource(
    S3Client,
    access_key_id="AKIA...",
    secret_access_key="...",
    region="us-east-1",
)
```

**When to use:**

- Datasource credentials come from environment variables
- You want a clean, single-class solution

**When not to use:**

- Multiple instances of the same datasource with different configs (use a separate factory)

## Context Manager for HTTP Clients

Ensure HTTP connections are properly closed, especially in long-running ASGI deployments:

```python title="Illustrative example"
import os
from contextlib import contextmanager
import httpx
from watchpost import Datasource, DatasourceFactory


class ApiClient(Datasource, DatasourceFactory):
    scheduling_strategies = ()

    def __init__(self, base_url: str, api_token: str):
        self._base_url = base_url
        self._api_token = api_token

    @contextmanager
    def client(self):
        """Context manager that yields configured httpx client."""
        with httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=30.0,
        ) as client:
            yield client

    @classmethod
    def new(cls) -> "ApiClient":
        os.environ.setdefault("API_BASE_URL", "https://api.example.com") #! hidden
        os.environ.setdefault("API_TOKEN", "test-token") #! hidden
        return cls(
            base_url=os.environ["API_BASE_URL"],
            api_token=os.environ["API_TOKEN"],
        )
```

### Usage in Checks

```python title="Illustrative example"
from watchpost import check, ok, Datasource, Environment #! hidden
PROD = Environment("prod") #! hidden
from contextlib import contextmanager #! hidden
import httpx #! hidden
class ApiClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    @contextmanager #! hidden
    def client(self): #! hidden
        with httpx.Client() as c: yield c #! hidden

@check(
    name="API Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def api_status(api: ApiClient):
    with api.client() as client:
        response = client.get("/health")
        if response.status_code == 200:
            return ok("API is healthy")
        return ok(f"API returned {response.status_code}")
```

### Async Variant

```python title="Illustrative example"
from contextlib import asynccontextmanager
import httpx
from watchpost import Datasource, DatasourceFactory


class AsyncApiClient(Datasource, DatasourceFactory):
    scheduling_strategies = ()

    def __init__(self, base_url: str, api_token: str):
        self._base_url = base_url
        self._api_token = api_token

    @asynccontextmanager
    async def client(self):
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=30.0,
        ) as client:
            yield client

    @classmethod #! hidden
    def new(cls) -> "AsyncApiClient": #! hidden
        return cls(base_url="https://api.example.com", api_token="test") #! hidden
```

**When to use:**

- HTTP clients should be short-lived
- Connection pooling is not needed
- Clean resource management is important

**When not to use:**

- Single long-lived connection is preferred
- High-frequency checks where connection overhead matters

## Internal Caching for OAuth Tokens

Datasources that need OAuth tokens can cache them internally:

```python title="Illustrative example"
import os
from datetime import timedelta
import httpx
from watchpost import Datasource, DatasourceFactory, DatasourceUnavailable
from watchpost.cache import Cache, InMemoryStorage


class OAuthApiClient(Datasource, DatasourceFactory):
    scheduling_strategies = ()

    def __init__(self, client_id: str, client_secret: str, token_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self._token_cache = Cache(InMemoryStorage())
        self._http = httpx.Client()

    def _get_access_token(self) -> str:
        """Get token from cache or fetch new one."""
        cache_key = f"token:{self.client_id}"
        entry = self._token_cache.get(cache_key)

        if entry is not None:
            return entry.value

        # Fetch new token
        try:
            response = self._http.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            response.raise_for_status()
        except httpx.RequestError as e:
            raise DatasourceUnavailable("Failed to obtain OAuth token") from e

        data = response.json()
        token = data["access_token"]
        expires_in = data.get("expires_in", 3600)

        # Cache with TTL slightly less than actual expiry
        self._token_cache.store(
            cache_key,
            token,
            ttl=timedelta(seconds=expires_in - 60),  # (1)
        )

        return token

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Make authenticated request."""
        token = self._get_access_token()
        headers = dict(kwargs.pop("headers", {}))  # type: ignore[arg-type]
        headers["Authorization"] = f"Bearer {token}"
        return self._http.request(method, url, headers=headers, **kwargs)

    @classmethod
    def new(cls) -> "OAuthApiClient":
        os.environ.setdefault("OAUTH_CLIENT_ID", "test") #! hidden
        os.environ.setdefault("OAUTH_CLIENT_SECRET", "test") #! hidden
        os.environ.setdefault("OAUTH_TOKEN_URL", "https://auth.example.com/token") #! hidden
        return cls(
            client_id=os.environ["OAUTH_CLIENT_ID"],
            client_secret=os.environ["OAUTH_CLIENT_SECRET"],
            token_url=os.environ["OAUTH_TOKEN_URL"],
        )
```

1. Cache for slightly less than the token's actual lifetime to avoid using expired tokens.

**When to use:**

- Datasource needs to manage short-lived credentials
- Token refresh should be automatic and cached
- Multiple checks share the same datasource instance

**When not to use:**

- Simple API key authentication (no refresh needed)
- Token management is handled externally

## Custom HTTP Authentication Classes

Create reusable `httpx.Auth` subclasses for non-standard authentication:

```python title="Illustrative example"
import httpx
from typing import Generator


class BearerTokenAuth(httpx.Auth):
    """Standard Bearer token authentication."""

    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


class CustomHeaderAuth(httpx.Auth):
    """Authentication via custom header (e.g., X-API-Key)."""

    def __init__(self, header_name: str, header_value: str):
        self.header_name = header_name
        self.header_value = header_value

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers[self.header_name] = self.header_value
        yield request


class ServiceAccountAuth(httpx.Auth):
    """Two-header authentication for service accounts."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["X-Client-ID"] = self.client_id
        request.headers["X-Client-Secret"] = self.client_secret
        yield request
```

### Usage in Datasource

```python title="Illustrative example"
import httpx
from typing import Generator #! hidden
from watchpost import Datasource
class BearerTokenAuth(httpx.Auth): #! hidden
    def __init__(self, token: str): self.token = token #! hidden
    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]: #! hidden
        request.headers["Authorization"] = f"Bearer {self.token}" #! hidden
        yield request #! hidden


class MyApiClient(Datasource):
    scheduling_strategies = ()

    def __init__(self, api_token: str):
        self._auth = BearerTokenAuth(api_token)
        self._base_url = "https://api.example.com"

    def get(self, path: str) -> dict:
        with httpx.Client(base_url=self._base_url, auth=self._auth) as client:
            response = client.get(path)
            response.raise_for_status()
            return response.json()
```

**When to use:**

- Non-standard authentication schemes
- Reuse authentication across multiple datasources
- Clean separation of auth logic

**When not to use:**

- Standard Basic or Bearer auth (httpx has built-in support)

## Callable Credentials

Pass callables instead of strings when credentials should be evaluated at factory resolution time (not import time):

```python title="Illustrative example"
import os
from typing import Callable
from watchpost import Datasource, DatasourceFactory


class ApiClient(Datasource, DatasourceFactory):
    """Datasource that accepts callable credentials."""

    scheduling_strategies = ()

    def __init__(self, api_token: str):
        self.token = api_token

    @classmethod
    def new(cls, api_token: str | Callable[[], str]) -> "ApiClient":
        # Resolve callable at factory time
        if callable(api_token):
            api_token = api_token()
        return cls(api_token=api_token)
```

### Usage with FromFactory

Define the credential-fetching function at the call site where you use `FromFactory`:

```python title="Illustrative example"
import os
from typing import Annotated, Callable
from watchpost import check, ok, FromFactory, Environment, Datasource, DatasourceFactory
PROD = Environment("prod") #! hidden

class ApiClient(Datasource, DatasourceFactory): #! hidden
    scheduling_strategies = () #! hidden
    def __init__(self, api_token: str): self.token = api_token #! hidden
    @classmethod #! hidden
    def new(cls, api_token: str | Callable[[], str]) -> "ApiClient": #! hidden
        if callable(api_token): api_token = api_token() #! hidden
        return cls(api_token=api_token) #! hidden


def get_api_token() -> str:
    """Fetch token from environment - evaluated when check runs, not at import."""
    os.environ.setdefault("API_TOKEN", "test-token") #! hidden
    return os.environ["API_TOKEN"]


@check(
    name="API Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def api_status(
    client: Annotated[ApiClient, FromFactory(ApiClient, api_token=get_api_token)],
):
    # Token is fetched when FromFactory is resolved, not at import time
    return ok("API OK")
```

**When to use:**

- Credentials might not be available at import time
- You want to support credential rotation
- Testing with different credentials

**When not to use:**

- Credentials are static and always available
- Simpler direct registration is sufficient

## Combining Patterns

These patterns can be combined. Here's a datasource that uses multiple patterns:

```python title="Illustrative example"
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Callable, AsyncGenerator
import httpx
from watchpost import Datasource, DatasourceFactory, DatasourceUnavailable
from watchpost.cache import Cache, InMemoryStorage


class RobustApiClient(Datasource, DatasourceFactory):
    """A production-ready API client combining multiple patterns."""

    scheduling_strategies = ()

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        token_url: str,
    ):
        self._base_url = base_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._token_cache = Cache(InMemoryStorage())

    def _get_token(self) -> str:
        """Get cached token or fetch new one."""
        cache_key = f"token:{self._client_id}"
        entry = self._token_cache.get(cache_key)
        if entry is not None:
            return entry.value

        try:
            with httpx.Client() as client:
                response = client.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
                response.raise_for_status()
        except httpx.RequestError as e:
            raise DatasourceUnavailable(f"Token fetch failed: {e}") from e

        data = response.json()
        token = data["access_token"]
        expires_in = data.get("expires_in", 3600)

        self._token_cache.store(
            cache_key, token, ttl=timedelta(seconds=expires_in - 60)
        )
        return token

    @asynccontextmanager
    async def client(self) -> AsyncGenerator[httpx.AsyncClient, None]:
        """Async context manager for authenticated requests."""
        token = self._get_token()
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        ) as client:
            yield client

    @classmethod
    def new(
        cls,
        client_id: str | Callable[[], str] | None = None,
        client_secret: str | Callable[[], str] | None = None,
    ) -> "RobustApiClient":
        """Factory supporting callable credentials."""
        os.environ.setdefault("API_BASE_URL", "https://api.example.com") #! hidden
        os.environ.setdefault("API_CLIENT_ID", "test") #! hidden
        os.environ.setdefault("API_CLIENT_SECRET", "test") #! hidden
        os.environ.setdefault("API_TOKEN_URL", "https://auth.example.com/token") #! hidden
        # Resolve callables
        if client_id is None:
            client_id = os.environ["API_CLIENT_ID"]
        elif callable(client_id):
            client_id = client_id()

        if client_secret is None:
            client_secret = os.environ["API_CLIENT_SECRET"]
        elif callable(client_secret):
            client_secret = client_secret()

        return cls(
            base_url=os.environ["API_BASE_URL"],
            client_id=client_id,
            client_secret=client_secret,
            token_url=os.environ["API_TOKEN_URL"],
        )
```

## Next Steps

- Learn about [Check Patterns](check-patterns.md) for common check implementations
- See [Error Handling Patterns](error-handling-patterns.md) for graceful degradation
- Explore [Caching Strategies](caching-strategies.md) for advanced caching
