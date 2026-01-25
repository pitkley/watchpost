# Project Organization

As your Watchpost project grows beyond a handful of checks, a clear organizational structure becomes essential. This page covers patterns for structuring your code in a way that scales with your monitoring needs.

## Domain-Driven File Organization

Organize checks by the service or domain they monitor, not by technical function:

```
myproject/
├── __init__.py           # App initialization, datasource registration
├── environments.py       # Centralized environment definitions
├── datasources.py        # All datasource classes (or organized by domain)
├── cache.py              # Shared cache instances
├── checks/
│   ├── __init__.py       # Re-exports or empty
│   ├── database.py       # All database-related checks
│   ├── api_services.py   # All API health checks
│   ├── backups.py        # All backup-related checks
│   ├── infrastructure.py # Server/container checks
│   └── certificates.py   # TLS certificate checks
```

This structure has several advantages:

- **Discoverability**: When looking for a check, you know which file to look in
- **Ownership**: Teams can own entire files that match their domain
- **Minimal conflicts**: Developers working on different domains rarely touch the same files

## Centralized Environment Registry

Define all environments in a single file to ensure consistency and prevent typos:

```python title="Illustrative example (environments.py)"
from watchpost import Environment, EnvironmentRegistry

registry = EnvironmentRegistry()

DEV = registry.new(name="dev", hostname="dev-services")
STAGING = registry.new(name="staging", hostname="staging-services")
PROD = registry.new(name="prod", hostname="prod-services")

# Export for use in checks
ALL_ENVIRONMENTS = [DEV, STAGING, PROD]
```

Import environments from this central location in your check files:

```python title="Illustrative example (checks/database.py)"
# from ..environments import PROD, STAGING, ALL_ENVIRONMENTS
```

**Benefits:**

- Single source of truth for environment names and hostnames
- IDE autocompletion works correctly
- Typos cause import errors rather than silent bugs

## Application Initialization

The application entry point should focus on wiring things together:

```python title="Illustrative example (__init__.py)"
import os
from watchpost import Watchpost, EnvironmentRegistry, Datasource

# Set up environments
registry = EnvironmentRegistry()
DEV = registry.new("dev")
PROD = registry.new("prod")

# Set up environment variable
os.environ.setdefault("WATCHPOST_ENVIRONMENT", "prod")

# Determine execution environment from env var
EXECUTION_ENVIRONMENT = registry[os.environ["WATCHPOST_ENVIRONMENT"]]

app = Watchpost(
    checks=[],  # Pass modules for automatic discovery
    execution_environment=EXECUTION_ENVIRONMENT,
)

# Register datasources with constructor arguments
# app.register_datasource(DatabaseClient, host=os.environ["DB_HOST"])
# app.register_datasource_factory(ApiClient)
```

## Module Discovery

Watchpost can automatically discover checks from modules:

```python title="Illustrative example"
from watchpost import Watchpost, EnvironmentRegistry #! hidden
registry = EnvironmentRegistry() #! hidden
PROD = registry.new("prod") #! hidden
import types #! hidden
checks = types.ModuleType("checks") #! hidden

# Discover all checks in the 'checks' package
app = Watchpost(
    checks=[checks],  # (1)
    execution_environment=PROD,
)
```

1. Pass a module to `checks` for automatic discovery of all `@check`-decorated functions.

When you pass a module to `checks`, Watchpost:

1. Scans the module for `@check`-decorated functions
2. Recursively scans submodules
3. Deduplicates any checks that appear multiple times

You can also be explicit by importing specific modules or mix explicit checks with module discovery.

## Organizing Datasources

### Single File Approach

For smaller projects, keep all datasources in one file:

```python title="Illustrative example (datasources.py)"
from watchpost import Datasource, DatasourceFactory

class DatabaseClient(Datasource):
    scheduling_strategies = ()
    def __init__(self, host: str): #! hidden
        pass #! hidden

class ApiClient(Datasource, DatasourceFactory):
    scheduling_strategies = ()
    @classmethod #! hidden
    def new(cls) -> "ApiClient": #! hidden
        return cls() #! hidden

class BackupService(Datasource):
    scheduling_strategies = ()
```

### Domain-Split Approach

For larger projects, organize datasources by domain:

```
myproject/
├── datasources/
│   ├── __init__.py       # Re-exports all datasources
│   ├── database.py       # DatabaseClient
│   ├── api.py            # ApiClient, InternalApiClient
│   └── cloud.py          # S3Client, CloudWatchClient
```

Re-export from the package `__init__.py` to provide a clean import interface.

## Shared Cache Instances

When multiple checks need to share cached data, define cache instances centrally:

```python title="Illustrative example (cache.py)"
from watchpost.cache import Cache, InMemoryStorage

# Runtime cache for API responses shared across checks
RUNTIME_CACHE = Cache(InMemoryStorage())
```

Use in checks with the `@memoize` decorator or direct `get`/`store` calls:

```python title="Illustrative example"
from datetime import timedelta
from watchpost.cache import Cache, InMemoryStorage

RUNTIME_CACHE = Cache(InMemoryStorage())

# Option 1: Using the memoize decorator
@RUNTIME_CACHE.memoize(
    key="{resource_type}",
    ttl=timedelta(minutes=15),
)
def list_resources(resource_type: str) -> list:
    """Fetch resources - cached across checks within same execution."""
    return []  # Would call client.list_resources(resource_type)


# Option 2: Using get/store directly for more control
def get_service_config(service_name: str) -> dict:
    """Fetch config with manual cache management."""
    cache_key = f"config:{service_name}"

    # Try to get from cache
    entry = RUNTIME_CACHE.get(cache_key)
    if entry is not None:
        return entry.value

    # Fetch and cache
    config = {}  # Would fetch from API
    RUNTIME_CACHE.store(cache_key, config, ttl=timedelta(minutes=30))
    return config
```

See [Caching Strategies](caching-strategies.md) for more details.

## Environment Variables

Use environment variables for configuration that varies between deployments:

```python title="Illustrative example"
import os
from watchpost import Watchpost, EnvironmentRegistry

registry = EnvironmentRegistry()
DEV = registry.new("dev")
PROD = registry.new("prod")

# Set defaults for example #! hidden
os.environ.setdefault("WATCHPOST_ENVIRONMENT", "prod") #! hidden
os.environ.setdefault("DB_HOST", "localhost") #! hidden

# Required configuration
EXECUTION_ENVIRONMENT = registry[os.environ["WATCHPOST_ENVIRONMENT"]]
DB_HOST = os.environ["DB_HOST"]

# Optional configuration with defaults
CACHE_TTL = os.environ.get("CACHE_TTL", "5m")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

app = Watchpost(
    checks=[],
    execution_environment=EXECUTION_ENVIRONMENT,
)

# Register datasources using the configuration
# app.register_datasource(DatabaseClient, host=DB_HOST)
```

**Common environment variables:**

| Variable | Purpose |
|----------|---------|
| `WATCHPOST_ENVIRONMENT` | Execution environment name |
| `DB_HOST`, `DB_PORT`, etc. | Database connection |
| `API_TOKEN`, `API_KEY` | API credentials |
| `REDIS_HOST` | Cache backend |

## When to Split Files

Consider splitting when:

- A file feels too large to navigate comfortably
- Multiple developers frequently edit the same file
- You want to enable/disable entire categories of checks
- Different teams own different monitoring domains

Don't over-organize:

- A single file with 10 checks is fine
- Avoid deeply nested directory structures
- Start simple and refactor when pain is felt

!!! tip "Reorganizing is safe"
    Moving check functions between files causes no friction in Checkmk as long as the `@check(name=...)` and any `name_suffix` values stay unchanged. Checkmk identifies services by name, not by where they're defined in your code.

## Example: Complete Project Structure

Here's a complete example for a medium-sized project:

```
acme_monitoring/
├── __init__.py
├── environments.py
├── datasources.py
├── cache.py
├── checks/
│   ├── __init__.py
│   ├── api.py           # External API health
│   ├── database.py      # Database checks
│   ├── infrastructure.py # Servers, containers
│   └── compliance.py    # Certificates, licenses
└── auth/
    ├── __init__.py
    └── oauth.py         # Custom auth classes
```

```python title="Illustrative example (__init__.py)"
import os
from watchpost import Watchpost, EnvironmentRegistry
from watchpost.cache import InMemoryStorage, ChainedStorage

# Set up environments
registry = EnvironmentRegistry()
PROD = registry.new("prod")

# Set defaults for example #! hidden
os.environ.setdefault("WATCHPOST_ENVIRONMENT", "prod") #! hidden

EXECUTION_ENVIRONMENT = registry[os.environ["WATCHPOST_ENVIRONMENT"]]

# Build cache storage
storages = [InMemoryStorage()]
# Optionally add Redis:
# if redis_host := os.environ.get("REDIS_HOST"):
#     storages.append(RedisStorage(Redis(host=redis_host)))

app = Watchpost(
    checks=[],  # Pass check modules here
    execution_environment=EXECUTION_ENVIRONMENT,
    check_cache_storage=ChainedStorage(storages),
)

# Register datasources
# app.register_datasource(DatabaseClient, host=os.environ["DB_HOST"])
# app.register_datasource_factory(ApiClient)
```

## Next Steps

- Learn about [Datasource Patterns](datasource-patterns.md) for building robust datasources
- See [Check Patterns](check-patterns.md) for common check implementations
- Explore [Caching Strategies](caching-strategies.md) for advanced caching
