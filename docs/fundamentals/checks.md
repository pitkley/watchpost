# Checks

Checks are the core building blocks of Watchpost. They define what to monitor, how to evaluate it, and where the results should appear in Checkmk.

## The @check Decorator

Use the `@check` decorator to define a monitoring check:

```python
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="API Health",
    service_labels={"component": "api"},
    environments=[PROD],
    cache_for="5m",
)
def api_health_check():
    return ok("API is healthy")
```

The decorator wraps your function and attaches metadata that Watchpost uses to schedule, execute, and report results.

## Check Parameters Reference

### name (required)

The Checkmk service name for this check:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Database Connection Pool",  # (1)
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def db_pool_check():
    return ok("Pool is healthy")
```

1. This becomes the service name visible in Checkmk. Choose descriptive names that identify what's being monitored.

### service_labels (required)

Labels attached to the Checkmk service:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="API Health",
    service_labels={
        "component": "api",
        "tier": "backend",
        "team": "platform",
    },
    environments=[PROD],
    cache_for="5m",
)
def api_check():
    return ok("OK")
```

Labels enable filtering and grouping in Checkmk. Use an empty dict `{}` if you don't need labels.

### environments (required)

The target environments this check monitors:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
DEV = Environment("dev") #! hidden
STAGING = Environment("staging") #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="API Health",
    service_labels={},
    environments=[DEV, STAGING, PROD],  # (1)
    cache_for="5m",
)
def api_check():
    return ok("OK")
```

1. The check runs once per target environment, creating separate services in Checkmk for each (depending on hostname configuration).

### cache_for (required)

How long to cache results before re-running the check:

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

1. String format supports units like `30s`, `5m`, `1h`, `1d`.
2. You can also use Python `timedelta` objects.
3. Use `None` to disable caching (check runs every time).

### hostname (optional)

Override the hostname (piggyback host) for results:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

# Static hostname
@check(
    name="Service Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    hostname="my-service-host",  # (1)
)
def static_hostname_check():
    return ok("OK")

# Template string
@check(
    name="Service Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    hostname="{service_name}-{environment.name}",  # (2)
)
def template_hostname_check():
    return ok("OK")
```

1. All results from this check go to the host "my-service-host" in Checkmk.
2. Template strings support variables. See [Hostname Resolution](../advanced/hostname-resolution.md).

### scheduling_strategies (optional)

Control where and when the check runs:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.scheduling_strategy import MustRunInTargetEnvironmentStrategy
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="In-Cluster Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    scheduling_strategies=[
        MustRunInTargetEnvironmentStrategy(),  # (1)
    ],
)
def cluster_check():
    return ok("OK")
```

1. This check only runs when the execution environment matches the target environment.

See [Scheduling Strategies](../advanced/scheduling-strategies.md) for all available strategies.

### error_handlers (optional)

Transform error results when checks fail:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.check import expand_by_hostname
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Multi-Host Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    error_handlers=[
        expand_by_hostname(["host-a", "host-b", "host-c"]),  # (1)
    ],
)
def multi_host_check():
    # If this fails, UNKNOWN appears for all three hosts
    return ok("OK")
```

1. If the check raises an exception, the error result is duplicated across all specified hostnames.

See [Error Handlers](../advanced/error-handlers.md) for details.

## Sync vs Async Checks

Watchpost supports both synchronous and asynchronous check functions:

### Synchronous Checks

```python title="Illustrative example"
import urllib.request
from watchpost import check, ok, crit
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="HTTP Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def sync_http_check():  # (1)
    with urllib.request.urlopen("https://example.com") as response:
        if response.status == 200:
            return ok("Site is up")
        return crit(f"Unexpected status: {response.status}")
```

1. Sync checks run on a thread pool, so blocking operations are fine.

### Asynchronous Checks

```python title="Illustrative example"
import httpx
from watchpost import check, ok, crit
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="HTTP Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
async def async_http_check():  # (1)
    async with httpx.AsyncClient() as client:
        response = await client.get("https://example.com")
        if response.status_code == 200:
            return ok("Site is up")
        return crit(f"Unexpected status: {response.status_code}")
```

1. Async checks run on an asyncio event loop. Avoid blocking operations.

### When to Use Which

- **Use sync** when your dependencies are blocking (database drivers, some SDKs)
- **Use async** when you need concurrent operations or your dependencies are async-native (httpx, aiohttp)

## Check Function Signatures

### Datasource Parameters

Declare datasource dependencies with type hints:

```python title="Illustrative example"
from watchpost import check, ok, Datasource
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

class ApiDatasource(Datasource):
    scheduling_strategies = ()

class DatabaseDatasource(Datasource):
    scheduling_strategies = ()

@check(
    name="Full Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
async def full_check(
    api: ApiDatasource,       # (1)
    db: DatabaseDatasource,   # (2)
):
    # Both datasources are injected automatically
    return ok("OK")
```

1. Watchpost injects a registered `ApiDatasource` instance.
2. Multiple datasources can be injected.

### Environment Parameter

Optionally receive the current target environment:

```python title="Illustrative example"
from watchpost import check, ok, Environment
PROD = Environment("prod") #! hidden
STAGING = Environment("staging") #! hidden

@check(
    name="Env-Aware Check",
    service_labels={},
    environments=[PROD, STAGING],
    cache_for="5m",
)
def env_check(environment: Environment):  # (1)
    thresholds = {"prod": 100, "staging": 200}
    threshold = thresholds[environment.name]
    return ok(f"Threshold is {threshold}")
```

1. The parameter must be named `environment` and typed as `Environment`.

### Return Types

Check functions can return:

- **Single result**: `CheckResult` or `OngoingCheckResult`
- **Multiple results**: `list[CheckResult | OngoingCheckResult]`
- **Generator**: `Generator[CheckResult | OngoingCheckResult]`

See [Results](results.md) for details on constructing results.

## Check Discovery

Watchpost can discover checks in several ways:

### Explicit Registration

Pass check functions directly:

```python title="Illustrative example"
from watchpost import Watchpost, check, ok, EnvironmentRegistry
ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden

@check(name="Check A", service_labels={}, environments=[PROD], cache_for="5m")
def check_a():
    return ok("OK")

@check(name="Check B", service_labels={}, environments=[PROD], cache_for="5m")
def check_b():
    return ok("OK")

app = Watchpost(
    checks=[check_a, check_b],
    execution_environment=PROD,
)
```

### Module Discovery

Pass a module to discover all checks in it and its submodules:

```python title="Illustrative example (app.py)" { "validate": false }
from watchpost import Watchpost

from myapp import checks  # Module containing checks

app = Watchpost(
    checks=[checks],  # (1)
    execution_environment=PROD,
)
```

1. Watchpost recursively scans the module for `@check`-decorated functions.

Given this structure:

```
myapp/
├── checks/
│   ├── __init__.py
│   ├── api.py      # Contains @check functions
│   └── database.py # Contains @check functions
```

All checks in `api.py` and `database.py` are discovered.

### Mixed Registration

Combine explicit and module-based registration:

```python title="Illustrative example" { "validate": false }
from watchpost import Watchpost, check, ok

from myapp import checks

@check(name="Inline Check", service_labels={}, environments=[PROD], cache_for="5m")
def inline_check():
    return ok("OK")

app = Watchpost(
    checks=[
        inline_check,  # Explicit
        checks,        # Module discovery
    ],
    execution_environment=PROD,
)
```

## CLI Commands

### run-checks

Execute all checks and display results:

```bash
watchpost --app myapp:app run-checks
```

Options:

```bash
# Async execution (default)
watchpost --app myapp:app run-checks --asynchronous-check-execution

# Sync execution (for debugging)
watchpost --app myapp:app run-checks --synchronous-check-execution

# Disable caching
watchpost --app myapp:app run-checks --no-cache

# Filter by name prefix
watchpost --app myapp:app run-checks --filter-prefix "API"

# Filter by name substring
watchpost --app myapp:app run-checks --filter-contains "Health"
```

### list-checks

List all discovered checks:

```console
$ watchpost --app myapp:app list-checks
myapp.checks.api.api_health_check(api: myapp.ApiDatasource)
myapp.checks.database.db_check(db: myapp.DatabaseDatasource)
```

### verify-check-configuration

Validate that all checks can be scheduled:

```console
$ watchpost --app myapp:app verify-check-configuration
All checks verified successfully.
```

This catches configuration errors like impossible scheduling strategy combinations.

## Checkmk Integration

### Service Names

The `name` parameter becomes the Checkmk service name exactly as specified:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="My Application / Database Pool",  # (1)
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def db_check():
    return ok("OK")
```

1. Creates a service named "My Application / Database Pool" in Checkmk.

### Service Labels

Labels enable filtering and grouping in Checkmk:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="API Health",
    service_labels={
        "cmk/component": "api",      # (1)
        "cmk/tier": "backend",
        "team": "platform",
    },
    environments=[PROD],
    cache_for="5m",
)
def api_check():
    return ok("OK")
```

1. Labels prefixed with `cmk/` have special meaning in Checkmk. Consult Checkmk documentation for standard labels.

### Multiple Environments

A check targeting multiple environments creates multiple Checkmk services (one per environment/hostname combination):

```python title="Illustrative example"
from watchpost import check, ok, EnvironmentRegistry

ENVIRONMENTS = EnvironmentRegistry()
DEV = ENVIRONMENTS.new("dev", hostname="dev-services")
STAGING = ENVIRONMENTS.new("staging", hostname="staging-services")
PROD = ENVIRONMENTS.new("prod", hostname="prod-services")

@check(
    name="API Health",
    service_labels={},
    environments=[DEV, STAGING, PROD],  # (1)
    cache_for="5m",
)
def api_check():
    return ok("OK")
```

1. Creates three services:
   - "API Health" on host `dev-services`
   - "API Health" on host `staging-services`
   - "API Health" on host `prod-services`

## Next Steps

- Learn about [Results](results.md) and how to construct check output
- Understand [Scheduling Strategies](../advanced/scheduling-strategies.md) for execution control
- Explore [Hostname Resolution](../advanced/hostname-resolution.md) for customizing service placement
