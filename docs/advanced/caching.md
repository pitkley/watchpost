# Caching

Watchpost's caching system serves two distinct purposes:

1. **Check result caching**: Watchpost caches check results to reduce load on monitored systems and provide resilience during temporary failures. This is configured via `check_cache_storage` on the application.

2. **Custom caching for check implementations**: Check functions often depend on intermediate results that are expensive to compute. You can create and use separate `Cache` instances for your own purposes within checks.

## Why Cache Check Results

Caching check results provides several benefits:

- **Reduce load**: Avoid hitting APIs or databases on every Checkmk poll
- **Graceful degradation**: Return stale results when systems are temporarily unavailable
- **Restart resilience**: When Watchpost restarts, cached results are immediately available rather than waiting for potentially long-running checks to recompute
- **Cost control**: Minimize API calls for rate-limited or metered services

## The cache_for Parameter

Every check specifies its cache duration via the `cache_for` parameter:

```python title="Illustrative example"
from datetime import timedelta
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

# String format
@check(
    name="API Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",  # (1)
)
def api_health():
    return ok("OK")

# Timedelta format
@check(
    name="Slow Check",
    service_labels={},
    environments=[PROD],
    cache_for=timedelta(minutes=10),  # (2)
)
def slow_check():
    return ok("OK")

# No caching
@check(
    name="Always Fresh",
    service_labels={},
    environments=[PROD],
    cache_for=None,  # (3)
)
def fresh_check():
    return ok("OK")
```

1. String format: `5m`, `1h`, `1d` (minutes, hours, days). Since Checkmk retrieves data at most once per minute, cache durations below 2 minutes provide little benefit.
2. Python `timedelta` for precise control.
3. `None` disables caching - check runs every time Checkmk retrieves data from Watchpost.

## How Caching Works

### Cache Flow

1. **Request arrives**: Checkmk polls Watchpost
2. **Cache lookup**: Check if valid cached result exists
3. **If cached**: Return cached result immediately
4. **If not cached or expired**: Run the check, cache results, return

### TTL Expiration

When a cache entry's TTL expires:

1. First request after expiration: Returns the expired result once (graceful degradation)
2. Entry is then removed from cache
3. Next request triggers a fresh check execution

This "return expired once" behavior prevents thundering herd problems and provides continuity during brief outages.

## Storage Backends

Watchpost provides pluggable storage backends for different deployment scenarios.

### InMemoryStorage

The default backend. Fast but not persistent or shared.

```python title="Illustrative example"
from watchpost import Watchpost
from watchpost.cache import InMemoryStorage
from watchpost import EnvironmentRegistry #! hidden
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden

app = Watchpost(
    checks=[],
    execution_environment=PROD,
    check_cache_storage=InMemoryStorage(),  # (1)
)
```

1. This is the default - you don't need to specify it explicitly.

**Characteristics:**

- Fastest read/write performance
- Lost on process restart
- Not shared between Watchpost instances
- Memory usage grows with number of checks and environments

**Best for:**

- Development and testing
- Single-instance deployments where restart is acceptable

### DiskStorage

Persists cache to the filesystem.

```python title="Illustrative example"
from watchpost import Watchpost
from watchpost.cache import DiskStorage
from watchpost import EnvironmentRegistry #! hidden
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden

app = Watchpost(
    checks=[],
    execution_environment=PROD,
    check_cache_storage=DiskStorage("/var/cache/watchpost"),  # (1)
)
```

1. Directory is created if it doesn't exist.

**Characteristics:**

- Survives process restarts
- Slower than in-memory (disk I/O)
- Not shared between hosts
- Uses pickle serialization

**Best for:**

- Single-instance deployments requiring persistence
- Development with restart tolerance

### RedisStorage

Stores cache in Redis for shared, persistent caching.

```python title="Illustrative example"
from redis import Redis
from watchpost import Watchpost
from watchpost.cache import RedisStorage
from watchpost import EnvironmentRegistry #! hidden
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden

redis_client = Redis(
    host="redis.example.com",
    port=6379,
    db=0,
)

app = Watchpost(
    checks=[],
    execution_environment=PROD,
    check_cache_storage=RedisStorage(
        redis_client=redis_client,
        use_redis_ttl=False,  # (1)
        redis_key_infix="myapp",  # (2)
    ),
)
```

1. When using `RedisStorage` for check result caching, keep `use_redis_ttl=False` (the default). This allows expired results to be returned once while a check re-executes, providing graceful degradation.
2. Optional namespace to avoid key collisions between multiple Watchpost applications.

**Characteristics:**

- Shared across all Watchpost instances (when using the same Redis instance)
- Persists across restarts (depending on Redis config)
- Requires Redis infrastructure

**Best for:**

- Multi-instance deployments
- High availability setups
- Shared cache across environments

!!! warning "Ensure all instances use the same Redis"
    When running multiple Watchpost instances, all must connect to the same Redis instance. Otherwise, results returned to Checkmk will be non-deterministic as different instances may have different cached values.

### ChainedStorage

Combines multiple backends for layered caching. This is the recommended approach for multi-instance deployments.

```python title="Illustrative example"
import os
from redis import Redis
from watchpost import Watchpost
from watchpost.cache import InMemoryStorage, RedisStorage, ChainedStorage
from watchpost import EnvironmentRegistry #! hidden
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden

# Build storage layers
storages = [InMemoryStorage()]  # (1)

if redis_host := os.environ.get("REDIS_HOST"):
    redis_client = Redis(host=redis_host)
    storages.append(RedisStorage(redis_client))  # (2)

app = Watchpost(
    checks=[],
    execution_environment=PROD,
    check_cache_storage=ChainedStorage(storages),  # (3)
)
```

1. First layer: fast in-memory cache.
2. Second layer: persistent Redis cache.
3. Chain tries layers in order.

**Behavior:**

- **Read**: Tries each storage in order, returns first hit
- **Write**: Writes to all storages
- **Propagation**: When found in a later storage, value is propagated to earlier storages

This gives you the speed of in-memory caching with the persistence and sharing of Redis.

**Best for:**

- Multi-instance production deployments
- Gradual rollout (in-memory only initially, add Redis later)

## Configuring the Cache

### Application-Level Storage

Set the storage backend when creating the application:

```python title="Illustrative example"
from watchpost import Watchpost
from watchpost.cache import DiskStorage
from watchpost import EnvironmentRegistry #! hidden
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden

app = Watchpost(
    checks=[],
    execution_environment=PROD,
    check_cache_storage=DiskStorage("/var/cache/watchpost"),
)
```

### Per-Check Cache Duration

Each check controls its own TTL:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Frequent Check",
    service_labels={},
    environments=[PROD],
    cache_for="2m",  # Short TTL for frequently changing data
)
def frequent_check():
    return ok("OK")

@check(
    name="Daily Check",
    service_labels={},
    environments=[PROD],
    cache_for="1d",  # Long TTL for stable data
)
def daily_check():
    return ok("OK")
```

### Disabling Cache via CLI

For debugging, disable caching at runtime:

```bash
watchpost --app myapp:app run-checks --no-cache
```

This forces all checks to execute fresh, ignoring cached results.

## Cache Behavior Edge Cases

### Expired Entries

When a cache entry expires:

1. First request: Returns the expired result (marked as stale)
2. Entry is removed from cache
3. Next request: Triggers fresh execution

This prevents a brief window of UNKNOWN results when checks are slow to re-execute.

### Failed Checks with Existing Cache

If a check fails (raises an exception) but a cached result exists:

- The cached result is **not** returned
- An error result (UNKNOWN or CRIT) is generated
- Cache entry may be invalidated

To preserve cached results on failure, use error handlers or catch exceptions within the check.

### Scheduling Strategy Interactions

When a scheduling strategy returns `SKIP`:

- Watchpost looks for a cached result
- If found: Returns the cached result (even if expired)
- If not found: Returns UNKNOWN

This allows strategies like maintenance windows to work with caching.

## Custom Caching for Check Implementations

While Watchpost uses caching internally for check results, check functions often depend on intermediate data that is expensive to compute. You're encouraged to create and use separate `Cache` instances for your own purposes.

### Runtime Cache (In-Memory)

For caching API responses or computed values within an execution cycle:

```python title="Illustrative example"
from datetime import timedelta
from watchpost.cache import Cache, InMemoryStorage

# Shared runtime cache for API responses
runtime_cache = Cache(InMemoryStorage())

@runtime_cache.memoize(
    key="{resource_type}",  # (1)
    ttl=timedelta(minutes=5),
)
def fetch_resources(resource_type: str) -> list:
    """Fetch resources - cached across checks."""
    return expensive_api_call(resource_type)
```

1. Key template uses function arguments.

This is useful when multiple checks need the same data. The first check fetches it, subsequent checks use the cached value.

### Persistent Cache (Disk)

For caching large files or data that should survive restarts:

```python title="Illustrative example"
from datetime import timedelta
from watchpost.cache import Cache, DiskStorage

# Persistent cache for large downloads
persistent_cache = Cache(DiskStorage("/var/cache/myapp"))

def get_sitemap(url: str) -> str:
    """Fetch sitemap XML, cached to disk."""
    cache_entry = persistent_cache.get(f"sitemap:{url}")  # (1)
    if cache_entry is not None:
        return cache_entry.value

    # Download the sitemap
    content = download_large_file(url)

    persistent_cache.store(  # (2)
        f"sitemap:{url}",
        content,
        ttl=timedelta(hours=6),
    )
    return content
```

1. Use `.get()` to retrieve cached values directly.
2. Use `.store()` to cache values with a TTL.

A real-world example: a check that validates a website's sitemap.xml, which with child sitemaps can require downloading very large XML files. Caching these to disk avoids repeated downloads.

### Using the Memoize Decorator

The `@cache.memoize` decorator provides a convenient way to cache function results:

```python title="Illustrative example"
from datetime import timedelta
from watchpost.cache import Cache, InMemoryStorage

cache = Cache(InMemoryStorage())

@cache.memoize(
    key="{client_id}:{resource}",  # (1)
    ttl=timedelta(minutes=10),
    return_expired=True,  # (2)
)
def get_resource_status(client_id: str, resource: str) -> dict:
    return api.get_status(client_id, resource)
```

1. Key is built from function arguments using format string placeholders.
2. `return_expired=True` returns stale data once while recomputing, providing graceful degradation.

### Using Store and Get Directly

For more control, use the cache's `.store()` and `.get()` methods:

```python title="Illustrative example"
from datetime import timedelta
from watchpost.cache import Cache, InMemoryStorage

cache = Cache(InMemoryStorage())

def process_data(data_id: str) -> dict:
    # Try to get from cache
    entry = cache.get(f"processed:{data_id}")
    if entry is not None:
        return entry.value

    # Compute the result
    result = expensive_computation(data_id)

    # Store with custom TTL
    cache.store(
        f"processed:{data_id}",
        result,
        ttl=timedelta(hours=1),
    )
    return result
```

## Best Practices

### Choosing Cache Duration

Since Checkmk retrieves data at most once per minute, cache durations below 2 minutes provide little benefit.

| Scenario | Recommended TTL |
|----------|-----------------|
| Frequently changing metrics | `2m` - `5m` |
| API health checks | `5m` - `15m` |
| Database status | `5m` - `15m` |
| Certificate expiry | `1h` - `1d` |
| Static configuration | `1d` or longer |

### Storage Backend Selection

| Deployment | Recommended Backend |
|------------|---------------------|
| Development | `InMemoryStorage` |
| Single instance, needs persistence | `DiskStorage` |
| Multiple instances | `ChainedStorage` (memory + Redis) |

## Next Steps

- Learn about [Error Handlers](error-handlers.md) for handling check failures
- Explore [Scheduling Strategies](scheduling-strategies.md) for execution control
- See [Hostname Resolution](hostname-resolution.md) for routing results
