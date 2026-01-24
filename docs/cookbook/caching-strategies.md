# Caching Strategies

This page covers advanced caching patterns beyond the basic `cache_for` parameter. For foundational concepts, see [Caching](../advanced/caching.md).

## Layered Caching with ChainedStorage

For production deployments requiring both speed and persistence, layer multiple storage backends:

```python title="Illustrative example"
import os
from watchpost import Watchpost, EnvironmentRegistry
from watchpost.cache import InMemoryStorage, RedisStorage, ChainedStorage

registry = EnvironmentRegistry()
PROD = registry.new("prod")

os.environ.setdefault("WATCHPOST_ENVIRONMENT", "prod") #! hidden
EXECUTION_ENV = registry[os.environ["WATCHPOST_ENVIRONMENT"]]

# Build storage layers
storages = [InMemoryStorage()]  # (1)

# Optionally add Redis for persistence/sharing
# Uncomment and configure for production:
# from redis import Redis
# if redis_host := os.environ.get("CACHE_REDIS_HOST"):
#     redis_client = Redis(
#         host=redis_host,
#         port=int(os.environ.get("CACHE_REDIS_PORT", 6379)),
#         db=int(os.environ.get("CACHE_REDIS_DB", 0)),
#     )
#     storages.append(
#         RedisStorage(
#             redis_client=redis_client,
#             use_redis_ttl=False,  # (2)
#         )
#     )

app = Watchpost(
    checks=[],
    execution_environment=EXECUTION_ENV,
    check_cache_storage=ChainedStorage(storages),  # (3)
)
```

1. In-memory is always first for speed.
2. Keep `use_redis_ttl=False` to allow expired results to be returned once for graceful degradation.
3. Chain tries layers in order on read, writes to all layers.

### Behavior

- **Read**: Checks in-memory first, then Redis if not found
- **Write**: Writes to all backends
- **Propagation**: When found in Redis but not memory, value is copied to memory
- **Benefit**: Fast reads from memory, persistence via Redis, survives restarts

### When to Use

- Multiple Watchpost instances need shared cache
- Cache should survive restarts
- You want the best of both worlds (speed + persistence)

### When Not to Use

- Single instance where restarts are acceptable (in-memory only is fine)
- No Redis available

### Environment-Based Configuration

A common pattern is to configure caching differently per deployment:

```python title="Illustrative example"
import os
from watchpost import Watchpost, EnvironmentRegistry
from watchpost.cache import InMemoryStorage, ChainedStorage, Storage

def build_cache_storage() -> Storage:
    """Build cache storage based on environment."""
    storages = [InMemoryStorage()]

    # Only add Redis in production
    # if os.environ.get("ENVIRONMENT") == "production":
    #     if redis_host := os.environ.get("REDIS_HOST"):
    #         from redis import Redis
    #         storages.append(
    #             RedisStorage(
    #                 Redis(host=redis_host),
    #                 redis_key_infix="watchpost",  # Namespace keys
    #             )
    #         )

    return ChainedStorage(storages) if len(storages) > 1 else storages[0]

registry = EnvironmentRegistry() #! hidden
PROD = registry.new("prod") #! hidden

app = Watchpost(
    checks=[],
    execution_environment=PROD,
    check_cache_storage=build_cache_storage(),
)
```

## Runtime Cache for Helper Functions

When multiple checks need the same expensive data, use a shared cache to avoid redundant API calls:

```python title="Illustrative example (cache.py)"
from watchpost.cache import Cache, InMemoryStorage

# Shared runtime cache for API responses
RUNTIME_CACHE = Cache(InMemoryStorage())
```

```python title="Illustrative example"
from datetime import timedelta
from watchpost import check, ok, Environment, Datasource
from watchpost.cache import Cache, InMemoryStorage

RUNTIME_CACHE = Cache(InMemoryStorage())
PROD = Environment("prod") #! hidden

class ApiClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def list_resources(self, resource_type: str) -> list[dict]: return [] #! hidden


@RUNTIME_CACHE.memoize(
    key="{resource_type}",  # (1)
    ttl=timedelta(minutes=15),
)
def list_resources(resource_type: str) -> list[dict]:
    """Fetch resources - cached across checks."""
    # Would call client.list_resources(resource_type)
    return []


@check(
    name="Resource Count",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def resource_count(client: ApiClient):
    # First call fetches, subsequent calls use cache
    resources = list_resources("servers")
    return ok(f"Found {len(resources)} servers")


@check(
    name="Resource Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def resource_status(client: ApiClient):
    # Uses cached result from above
    resources = list_resources("servers")
    for r in resources:
        yield ok(f"Server {r.get('name', 'unknown')} healthy", name_suffix=f" - {r.get('name', 'unknown')}")
```

1. Key template uses function arguments.

### How It Works

1. First check to call `list_resources()` makes the API call
2. Result is cached with the specified TTL
3. Other checks calling the same function get the cached value
4. After TTL expires, next call fetches fresh data

### When to Use

- Multiple checks need the same data
- API calls are expensive or rate-limited
- Data doesn't change within a single execution run

### When Not to Use

- Each check needs fresh data
- Data is check-specific

## Datasource-Level Caching for Tokens

OAuth tokens and other credentials that expire should be cached within the datasource:

```python title="Illustrative example"
from datetime import timedelta
from watchpost import Datasource, DatasourceUnavailable
from watchpost.cache import Cache, InMemoryStorage
import httpx


class OAuthClient(Datasource):
    scheduling_strategies = ()

    def __init__(self, client_id: str, client_secret: str, token_url: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token_url = token_url
        self._token_cache = Cache(InMemoryStorage())  # (1)

    def _get_token(self) -> str:
        cache_key = f"token:{self._client_id}"
        entry = self._token_cache.get(cache_key)
        if entry is not None:
            return entry.value

        # Fetch new token
        try:
            response = httpx.post(
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

        # Cache with TTL slightly less than actual expiry
        self._token_cache.store(
            cache_key,
            token,
            ttl=timedelta(seconds=expires_in - 60),  # (2)
        )

        return token
```

1. Each datasource instance has its own token cache.
2. Refresh 60 seconds before expiry to avoid race conditions.

See [Datasource Patterns](datasource-patterns.md) for the full pattern.

## Persistent Cache for Large Downloads

When checks need to download large files (sitemaps, configuration dumps, etc.), cache to disk:

```python title="Illustrative example"
from datetime import timedelta
from watchpost import check, ok, warn, Environment
from watchpost.cache import Cache, DiskStorage
import httpx
import tempfile #! hidden
import os #! hidden

temp_dir = tempfile.mkdtemp() #! hidden
PROD = Environment("prod") #! hidden

# Persistent cache for large downloads
DOWNLOAD_CACHE = Cache(DiskStorage(temp_dir))


def fetch_sitemap(url: str) -> str:
    """Fetch sitemap XML, cached to disk."""
    cache_key = f"sitemap:{url}"
    entry = DOWNLOAD_CACHE.get(cache_key)

    if entry is not None:
        return entry.value

    # Download the sitemap
    response = httpx.get(url, timeout=60.0)
    response.raise_for_status()
    content = response.text

    DOWNLOAD_CACHE.store(
        cache_key,
        content,
        ttl=timedelta(hours=6),
    )

    return content


@check(
    name="Sitemap Validity",
    service_labels={},
    environments=[PROD],
    cache_for="1h",
)
def sitemap_check():
    # For demo purposes, using a simple URL #! hidden
    sitemap = "<urlset>test</urlset>" #! hidden
    # sitemap = fetch_sitemap("https://example.com/sitemap.xml")
    # Validate sitemap...
    if "<urlset" in sitemap:
        return ok("Sitemap is valid XML")
    return warn("Sitemap may be malformed")
```

### When to Use

- Downloaded content is large (megabytes)
- Content changes infrequently
- You want to survive process restarts

### When Not to Use

- Content is small (use in-memory)
- Content changes frequently
- Disk space is limited

## Cache Key Design

When using `@cache.memoize` or manual caching, design keys carefully:

```python title="Illustrative example"
from datetime import timedelta
from watchpost.cache import Cache, InMemoryStorage

cache = Cache(InMemoryStorage())

# Good: Specific, includes all varying inputs
@cache.memoize(key="{environment}:{service}:{endpoint}", ttl=timedelta(minutes=5))
def check_endpoint(environment: str, service: str, endpoint: str) -> dict:
    return {}

# Avoid: Too generic, may cause collisions
# @cache.memoize(key="{endpoint}", ttl=timedelta(minutes=5))
# def check_endpoint(environment: str, service: str, endpoint: str):
#     ...  # Missing context!
```

**Key design principles:**

- Include all parameters that affect the result
- Use a consistent separator (`:` is common)
- Consider namespacing for different cache purposes

## Cache Invalidation

Watchpost's caching is TTL-based and doesn't support explicit invalidation. Design around this:

**Short TTL for volatile data:**

```python title="Illustrative example"
from watchpost import check, ok, Environment
PROD = Environment("prod") #! hidden

@check(
    name="Fast Changing Status",
    service_labels={},
    environments=[PROD],
    cache_for="2m",  # Re-check every 2 minutes
)
def fast_changing_status():
    return ok("Status OK")
```

**Longer TTL with manual checks:**

```python title="Illustrative example"
from watchpost import check, ok, warn, Environment
PROD = Environment("prod") #! hidden

@check(
    name="Slow Changing Config",
    service_labels={},
    environments=[PROD],
    cache_for="1h",  # Results cached for an hour
)
def slow_changing_config():
    # Check includes timestamp of last known change
    # config = fetch_config()
    # if config.last_modified > expected_last_modified:
    #     return warn("Config changed unexpectedly")
    return ok("Config unchanged")
```

## Best Practices

### Choosing TTL Values

| Cache Type | Recommended TTL |
|------------|-----------------|
| Check results (frequent changes) | `2m` - `5m` |
| Check results (stable data) | `15m` - `1h` |
| Runtime helper cache | `5m` - `15m` |
| OAuth tokens | Token expiry - 60s |
| Large file downloads | `1h` - `6h` |

### Storage Backend Selection

| Use Case | Recommended Backend |
|----------|---------------------|
| Check result cache (single instance) | `InMemoryStorage` |
| Check result cache (multi-instance) | `ChainedStorage` (memory + Redis) |
| Runtime helper cache | `InMemoryStorage` |
| OAuth tokens in datasource | `InMemoryStorage` |
| Large file downloads | `DiskStorage` |

### Avoid These Patterns

- **Caching mutable objects**: Cache stores references; modifying cached objects affects all users
- **Very long TTLs without monitoring**: Stale data may go unnoticed
- **Caching errors**: Let failures propagate to generate proper UNKNOWN results

## Next Steps

- Learn about [Error Handling Patterns](error-handling-patterns.md) for graceful degradation
- See [Datasource Patterns](datasource-patterns.md) for datasource-level caching
- Explore [Check Patterns](check-patterns.md) for common check implementations
