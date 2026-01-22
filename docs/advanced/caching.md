# Caching

Caching reduces load on monitored systems and provides resilience during temporary failures. Watchpost's caching system stores check results and can return them when re-execution isn't needed or isn't possible.

## Why Caching

Caching serves several purposes:

- **Reduce load**: Avoid hitting APIs or databases on every Checkmk poll
- **Graceful degradation**: Return stale results when systems are temporarily unavailable
- **Performance**: Fast responses for frequently-polled checks
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
    name="Quick Check",
    service_labels={},
    environments=[PROD],
    cache_for="30s",  # (1)
)
def quick_check():
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

1. String format: `30s`, `5m`, `1h`, `1d` (seconds, minutes, hours, days).
2. Python `timedelta` for precise control.
3. `None` disables caching - check runs every time.

## How Caching Works

### Cache Keys

Each check result is cached using a composite key:

```
{check.name}:{environment.name}
```

For example, a check named `myapp.checks.api.health_check` targeting the `prod` environment would have the cache key:

```
myapp.checks.api.health_check:prod
```

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

**File structure:**

```
/var/cache/watchpost/
├── v1/
│   ├── ab/
│   │   └── abcdef123...  # Hashed cache key
│   └── cd/
│       └── cdef456...
```

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
        use_redis_ttl=True,  # (1)
        redis_key_infix="myapp",  # (2)
    ),
)
```

1. Use Redis's native TTL for automatic expiry (recommended).
2. Optional namespace to avoid key collisions.

**Characteristics:**

- Shared across all Watchpost instances
- Persists across restarts (depending on Redis config)
- Network latency for each operation
- Requires Redis infrastructure

**Redis key format:**

```
watchpost:cache:{infix}:v{version}:{hash}
```

**Best for:**

- Multi-instance deployments
- High availability setups
- Shared cache across environments

### ChainedStorage

Combines multiple backends for layered caching.

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

- Production deployments requiring both speed and persistence
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
    cache_for="30s",  # Short TTL for rapidly changing data
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

## Memoization for Helper Functions

For caching within checks (e.g., shared API calls), use the `Cache.memoize` decorator:

```python title="Illustrative example"
from datetime import timedelta
from watchpost.cache import Cache, InMemoryStorage

# Shared cache instance
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

This is useful when multiple checks need the same expensive data within a single execution cycle.

## Cache Inspection

The `CheckCache` class provides methods to inspect cached data:

```python title="Illustrative example" { "validate": false }
from watchpost.check import CheckCache
from watchpost.cache import InMemoryStorage

cache = CheckCache(InMemoryStorage())

# Get cached results for a check/environment
entry = cache.get_check_results_cache_entry(
    check=my_check,
    environment=PROD,
    return_expired=True,  # Include expired entries
)

if entry:
    print(f"Cached at: {entry.added_at}")
    print(f"TTL: {entry.ttl}")
    print(f"Expired: {entry.is_expired()}")
    for result in entry.value:
        print(f"  {result.service_name}: {result.check_state}")
```

## Best Practices

### Choosing Cache Duration

| Scenario | Recommended TTL |
|----------|-----------------|
| Real-time metrics | `30s` - `1m` |
| API health checks | `1m` - `5m` |
| Database status | `5m` - `15m` |
| Certificate expiry | `1h` - `1d` |
| Static configuration | `1d` or longer |

### Storage Backend Selection

| Deployment | Recommended Backend |
|------------|---------------------|
| Development | `InMemoryStorage` |
| Single instance, needs persistence | `DiskStorage` |
| Multiple instances | `RedisStorage` |
| Multiple instances, high performance | `ChainedStorage` (memory + Redis) |

### Cache Warming

For checks with long TTLs, consider running them at startup:

```python title="Illustrative example" { "validate": false }
# In your startup script
from myapp import app

# Run all checks once to warm the cache
app.run_checks_sync()
```

## Next Steps

- Learn about [Error Handlers](error-handlers.md) for handling check failures
- Explore [Scheduling Strategies](scheduling-strategies.md) for execution control
- See [Hostname Resolution](hostname-resolution.md) for routing results
