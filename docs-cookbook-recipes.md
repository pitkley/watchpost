# Watchpost Cookbook Recipes

This document contains detailed, generalized recipes for the Cookbook documentation section. Each recipe includes:
- Problem statement
- Solution pattern
- Complete, copy-pasteable code examples
- When to use / when not to use

These recipes are derived from real-world Watchpost usage but generalized to protect implementation details.

---

## Table of Contents

1. [Project Organization](#1-project-organization)
2. [Dual Datasource-Factory Pattern](#2-dual-datasource-factory-pattern)
3. [Generator Checks for Multiple Services](#3-generator-checks-for-multiple-services)
4. [Result Builder for Multi-Validation Checks](#4-result-builder-for-multi-validation-checks)
5. [Environment-Specific Check Configuration](#5-environment-specific-check-configuration)
6. [Dynamic Hostname Resolution](#6-dynamic-hostname-resolution)
7. [Layered Caching Strategy](#7-layered-caching-strategy)
8. [Callable Credentials](#8-callable-credentials)
9. [Internal Datasource Caching (OAuth Tokens)](#9-internal-datasource-caching-oauth-tokens)
10. [Shared Runtime Cache for Helper Functions](#10-shared-runtime-cache-for-helper-functions)
11. [Custom HTTP Authentication Classes](#11-custom-http-authentication-classes)
12. [Graceful Degradation with DatasourceUnavailable](#12-graceful-degradation-with-datasourceunavailable)
13. [Data-Driven Check Configuration](#13-data-driven-check-configuration)
14. [Deadline/Expiration Tracking Pattern](#14-deadlineexpiration-tracking-pattern)
15. [Context Manager Datasources for HTTP Clients](#15-context-manager-datasources-for-http-clients)
16. [Container/Process Inventory Checks](#16-containerprocess-inventory-checks)

---

## 1. Project Organization

### Problem

As the number of checks grows, a flat file structure becomes difficult to navigate. You need a way to organize checks, datasources, and environments that scales.

### Solution

Organize by domain/service rather than by technical function:

```
myproject/
├── __init__.py           # App initialization, datasource registration
├── environments.py       # Centralized environment definitions
├── datasources.py        # All datasource classes
├── cache.py              # Shared cache instances
├── checks/
│   ├── __init__.py       # Re-exports or empty
│   ├── database.py       # All database-related checks
│   ├── api_services.py   # All API health checks
│   ├── backups.py        # All backup-related checks
│   ├── infrastructure.py # Server/container checks
│   └── certificates.py   # TLS certificate checks
```

### Example: `environments.py`

```python
from watchpost import Environment, EnvironmentRegistry

registry = EnvironmentRegistry()

DEV = registry.new(name="dev", hostname="dev-services")
STAGING = registry.new(name="staging", hostname="staging-services")
PROD = registry.new(name="prod", hostname="prod-services")

# Export for use in checks
ALL_ENVIRONMENTS = [DEV, STAGING, PROD]
```

### Example: `__init__.py`

```python
import os
from watchpost import Watchpost
from .environments import registry, PROD
from .datasources import DatabaseClient, ApiClient
from . import checks

# Determine execution environment from env var
EXECUTION_ENV = registry[os.environ["WATCHPOST_ENVIRONMENT"]]

app = Watchpost(
    checks=[checks],  # Module discovery
    execution_environment=EXECUTION_ENV,
)

app.register_datasource(DatabaseClient, host=os.environ["DB_HOST"])
app.register_datasource_factory(ApiClient)
```

### When to Use

- More than 5-10 checks
- Multiple developers working on monitoring
- Checks span multiple domains/services

### When Not to Use

- Small projects with few checks (single file is fine)

---

## 2. Dual Datasource-Factory Pattern

### Problem

You have a datasource that loads credentials from environment variables. You want to avoid boilerplate while keeping the class usable both as a direct datasource and via factory pattern.

### Solution

Make your class implement both `Datasource` and `DatasourceFactory`:

```python
import os
import boto3
from watchpost import Datasource, DatasourceFactory

class S3Client(Datasource, DatasourceFactory):
    """AWS S3 client that can be used directly or via factory."""

    def __init__(
        self,
        access_key_id: str,
        secret_access_key: str,
        region: str,
    ):
        self.client = boto3.client(
            "s3",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
        )

    @classmethod
    def new(cls) -> "S3Client":
        """Factory method that loads credentials from environment."""
        return cls(
            access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
            secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
            region=os.environ["AWS_REGION"],
        )

    def head_object(self, bucket: str, key: str) -> dict:
        return self.client.head_object(Bucket=bucket, Key=key)
```

### Registration

```python
# Register as factory (uses new() method)
app.register_datasource_factory(S3Client)

# OR register directly with explicit credentials
app.register_datasource(
    S3Client,
    access_key_id="...",
    secret_access_key="...",
    region="us-east-1",
)
```

### When to Use

- Datasource credentials come from environment variables
- You want a clean, single-class solution

### When Not to Use

- Multiple instances of the same datasource with different configs (use separate factory)

---

## 3. Generator Checks for Multiple Services

### Problem

You have a single logical check (e.g., "backup status") that should produce separate Checkmk services for each item being checked (e.g., each backup plan).

### Solution

Use a generator function with `name_suffix`:

```python
from watchpost import check, ok, warn, crit, Environment
from datetime import timedelta

@check(
    name="Backup Plan Status",
    environments=[PROD],
    cache_for="30m",
)
def backup_plan_status(backup_client: BackupClient):
    """Check status of all backup plans.

    Yields one result per plan, creating separate Checkmk services:
    - "Backup Plan Status - database-daily"
    - "Backup Plan Status - files-hourly"
    - etc.
    """
    plans = backup_client.list_plans()

    for plan in plans:
        name_suffix = f" - {plan.name}"
        last_run = backup_client.get_last_successful_run(plan.id)

        if last_run is None:
            yield crit(
                f"No successful backup found for {plan.name}",
                name_suffix=name_suffix,
            )
            continue

        age = datetime.now(tz=UTC) - last_run.completed_at

        if age > plan.critical_threshold:
            yield crit(
                f"Backup is {humanize.naturaldelta(age)} old",
                name_suffix=name_suffix,
                details=f"Last successful: {last_run.completed_at}",
            )
        elif age > plan.warning_threshold:
            yield warn(
                f"Backup is {humanize.naturaldelta(age)} old",
                name_suffix=name_suffix,
                details=f"Last successful: {last_run.completed_at}",
            )
        else:
            yield ok(
                f"Backup completed {humanize.naturaldelta(age)} ago",
                name_suffix=name_suffix,
            )
```

### When to Use

- One check should create multiple Checkmk services
- Iterating over a collection of similar items
- Each item needs independent alerting

### When Not to Use

- Single result is sufficient
- Items are so numerous that separate services would overwhelm Checkmk

---

## 4. Result Builder for Multi-Validation Checks

### Problem

A check needs to validate multiple conditions, and you want the result to reflect all failures rather than just the first one.

### Solution

Use `build_result()` to accumulate validations:

```python
from watchpost import check, build_result
from datetime import timedelta
import httpx

@check(
    name="Website Health",
    environments=[PROD],
    cache_for="5m",
)
def website_health(http: HttpClient):
    """Validate multiple aspects of website health."""
    response = httpx.get("https://example.com", follow_redirects=False)

    r = build_result(
        ok_summary="Website healthy",
        fail_summary="Website has issues",
        base_details=(
            f"URL: https://example.com\n"
            f"Status: {response.status_code}\n"
            f"Response time: {response.elapsed.total_seconds():.2f}s\n"
        ),
    )

    # Each validation can add warnings or criticals
    if response.status_code != 200:
        r.crit(f"HTTP status is {response.status_code}, expected 200")

    if response.elapsed > timedelta(seconds=2):
        r.warn(f"Response time {response.elapsed.total_seconds():.2f}s exceeds 2s threshold")

    if response.elapsed > timedelta(seconds=5):
        r.crit(f"Response time {response.elapsed.total_seconds():.2f}s exceeds 5s threshold")

    if "<!DOCTYPE html>" not in response.text:
        r.crit("Response does not appear to be HTML")

    content_length = len(response.content)
    if content_length < 1000:
        r.warn(f"Response suspiciously small: {content_length} bytes")

    # If no issues were added, result is OK
    # If any warn() called, result is WARN
    # If any crit() called, result is CRIT
    return r
```

### Output Examples

**All validations pass:**
```
OK - Website healthy
URL: https://example.com
Status: 200
Response time: 0.45s
```

**Multiple issues:**
```
CRIT - Website has issues
URL: https://example.com
Status: 503
Response time: 6.20s

- HTTP status is 503, expected 200
- Response time 6.20s exceeds 5s threshold
```

### When to Use

- Check validates multiple conditions
- All issues should be visible, not just the first
- You want automatic state escalation (OK → WARN → CRIT)

### When Not to Use

- Simple pass/fail check
- Validations are mutually exclusive (only one can fail)

---

## 5. Environment-Specific Check Configuration

### Problem

The same check runs across multiple environments, but thresholds or parameters differ per environment.

### Solution

Use a dictionary mapping environments to configuration, and inject the environment parameter:

```python
from watchpost import check, ok, warn, crit, Environment
from datetime import timedelta

# Configuration per environment
BACKUP_CONFIG = {
    DEV: {
        "database": {"warn": timedelta(hours=48), "crit": timedelta(hours=72)},
    },
    STAGING: {
        "database": {"warn": timedelta(hours=24), "crit": timedelta(hours=48)},
    },
    PROD: {
        "database": {"warn": timedelta(hours=2), "crit": timedelta(hours=6)},
        "files": {"warn": timedelta(hours=1), "crit": timedelta(hours=2)},
        "config": {"warn": timedelta(hours=24), "crit": timedelta(hours=48)},
    },
}

@check(
    name="Backup Age",
    environments=[DEV, STAGING, PROD],
    cache_for="30m",
)
def backup_age(environment: Environment, backup_client: BackupClient):
    """Check backup age with environment-specific thresholds."""
    config = BACKUP_CONFIG[environment]

    for backup_name, thresholds in config.items():
        age = backup_client.get_backup_age(backup_name)
        name_suffix = f" - {backup_name}"

        if age > thresholds["crit"]:
            yield crit(
                f"{backup_name} backup is critically old",
                name_suffix=name_suffix,
            )
        elif age > thresholds["warn"]:
            yield warn(
                f"{backup_name} backup is getting old",
                name_suffix=name_suffix,
            )
        else:
            yield ok(
                f"{backup_name} backup is fresh",
                name_suffix=name_suffix,
            )
```

### When to Use

- Same check logic applies to multiple environments
- Thresholds/parameters differ by environment
- You want to maintain configuration in code

### When Not to Use

- Configuration is identical across environments
- Configuration should come from external source (use env vars instead)

---

## 6. Dynamic Hostname Resolution

### Problem

A check runs against multiple environments, but results should be routed to different Checkmk hosts depending on the environment.

### Solution

Use a callable hostname strategy:

```python
from watchpost import check, ok, HostnameContext

def environment_based_hostname(ctx: HostnameContext) -> str | None:
    """Route results to different hosts based on environment."""
    hostname_map = {
        "prod": "production-services",
        "staging": "staging-services",
        "dev": None,  # Use default
    }
    return hostname_map.get(ctx.environment.name)

@check(
    name="API Health",
    environments=[DEV, STAGING, PROD],
    hostname=environment_based_hostname,
    cache_for="5m",
)
def api_health(api: ApiClient):
    # Results go to different Checkmk hosts per environment
    ...
```

### Alternative: Result-Level Hostname Override

For checks that produce multiple results where each should go to a different host:

```python
@check(
    name="Multi-Host Status",
    environments=[PROD],
    hostname="default-host",  # Fallback
)
def multi_host_status(client: MultiHostClient):
    for host in client.get_hosts():
        status = client.get_status(host)
        yield ok(
            f"Host {host.name} is healthy",
            name_suffix=f" - {host.name}",
            alternative_hostname=host.checkmk_hostname,  # Override per result
        )
```

### When to Use

- Results should route to different Checkmk hosts
- Hostname depends on environment or result content
- Multi-tenant or multi-region monitoring

### When Not to Use

- Single hostname is sufficient
- Hostname is static (use string directly)

---

## 7. Layered Caching Strategy

### Problem

You need caching that's both fast (in-memory) and persistent/shared (Redis), with graceful fallback.

### Solution

Use `ChainedStorage` to layer multiple backends:

```python
import os
from redis import Redis
from watchpost import Watchpost
from watchpost.cache import InMemoryStorage, RedisStorage, ChainedStorage

# Build storage layers
storages = [InMemoryStorage()]  # Always have in-memory for speed

# Optionally add Redis for persistence/sharing
if redis_host := os.environ.get("CACHE_REDIS_HOST"):
    redis_client = Redis(
        host=redis_host,
        port=int(os.environ.get("CACHE_REDIS_PORT", 6379)),
        db=int(os.environ.get("CACHE_REDIS_DB", 0)),
    )
    storages.append(
        RedisStorage(
            redis_client=redis_client,
            use_redis_ttl=False,  # Manage TTL ourselves for consistency
        )
    )

app = Watchpost(
    checks=[...],
    execution_environment=EXECUTION_ENV,
    check_cache_storage=ChainedStorage(storages),
)
```

### Behavior

- **Read**: Checks in-memory first, then Redis if not found
- **Write**: Writes to all backends
- **Benefit**: Fast reads from memory, persistence via Redis, survives restarts

### When to Use

- Multiple Watchpost instances need shared cache
- Cache should survive restarts
- You want best of both worlds (speed + persistence)

### When Not to Use

- Single instance, restarts are acceptable (in-memory only is fine)
- No Redis available

---

## 8. Callable Credentials

### Problem

You want credentials to be evaluated at registration time (not import time), enabling lazy loading or credential rotation.

### Solution

Pass a callable instead of a string:

```python
import os
from typing import Annotated
from watchpost import check, FromFactory

def get_api_token() -> str:
    """Callable that returns the token - evaluated at registration."""
    return os.environ["API_TOKEN"]

class ApiClient(DatasourceFactory, Datasource):
    def __init__(self, api_token: str):
        self.token = api_token

    @classmethod
    def new(cls, api_token: str | Callable[[], str]) -> "ApiClient":
        # Support both direct string and callable
        if callable(api_token):
            api_token = api_token()
        return cls(api_token=api_token)

# In check - token is fetched when FromFactory is resolved
@check(name="API Status", environments=[PROD])
def api_status(
    client: Annotated[ApiClient, FromFactory(ApiClient, api_token=get_api_token)],
):
    ...
```

### When to Use

- Credentials might not be available at import time
- You want to support credential rotation
- Testing with different credentials

### When Not to Use

- Credentials are static and always available
- Simpler direct registration is sufficient

---

## 9. Internal Datasource Caching (OAuth Tokens)

### Problem

Your datasource needs to obtain and cache OAuth tokens or other short-lived credentials.

### Solution

Use Watchpost's `Cache` internally within the datasource:

```python
from datetime import timedelta
import httpx
from watchpost import Datasource, DatasourceFactory, DatasourceUnavailable
from watchpost.cache import Cache, InMemoryStorage

class OAuthApiClient(Datasource, DatasourceFactory):
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
            ttl=timedelta(seconds=expires_in - 60),
        )

        return token

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make authenticated request."""
        token = self._get_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        return self._http.request(method, url, headers=headers, **kwargs)

    @classmethod
    def new(cls) -> "OAuthApiClient":
        return cls(
            client_id=os.environ["OAUTH_CLIENT_ID"],
            client_secret=os.environ["OAUTH_CLIENT_SECRET"],
            token_url=os.environ["OAUTH_TOKEN_URL"],
        )
```

### When to Use

- Datasource needs to manage short-lived credentials
- Token refresh should be automatic and cached
- Multiple checks share the same datasource instance

### When Not to Use

- Simple API key authentication (no refresh needed)
- Token management is handled externally

---

## 10. Shared Runtime Cache for Helper Functions

### Problem

Multiple checks call the same expensive helper function (e.g., listing all resources). You want to cache the result within a single execution run.

### Solution

Create a shared cache instance and use `@cache.memoize`:

```python
# cache.py
from watchpost.cache import Cache, InMemoryStorage

RUNTIME_CACHE = Cache(InMemoryStorage())
```

```python
# checks/backups.py
from datetime import timedelta
from ..cache import RUNTIME_CACHE

@RUNTIME_CACHE.memoize(
    key="{client_id}:{resource_type}",
    ttl=timedelta(minutes=15),
)
def list_resources(client: ApiClient, resource_type: str) -> list[dict]:
    """Fetch resources - cached across checks within same execution."""
    return client.list_resources(resource_type)

@check(name="Resource Count", environments=[PROD])
def resource_count(client: ApiClient):
    # First call fetches, subsequent calls use cache
    resources = list_resources(client, "servers")
    return ok(f"Found {len(resources)} servers")

@check(name="Resource Status", environments=[PROD])
def resource_status(client: ApiClient):
    # Uses cached result from above
    resources = list_resources(client, "servers")
    for r in resources:
        yield ok(f"Server {r['name']} healthy", name_suffix=f" - {r['name']}")
```

### When to Use

- Multiple checks need the same data
- API calls are expensive or rate-limited
- Data doesn't change within a single execution run

### When Not to Use

- Each check needs fresh data
- Data is check-specific

---

## 11. Custom HTTP Authentication Classes

### Problem

You need to authenticate with APIs using non-standard authentication (custom headers, service tokens, etc.).

### Solution

Create reusable `httpx.Auth` subclasses:

```python
import httpx

class BearerTokenAuth(httpx.Auth):
    """Standard Bearer token authentication."""

    def __init__(self, token: str):
        self.token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token}"
        yield request


class CustomHeaderAuth(httpx.Auth):
    """Authentication via custom header (e.g., X-API-Key)."""

    def __init__(self, header_name: str, header_value: str):
        self.header_name = header_name
        self.header_value = header_value

    def auth_flow(self, request: httpx.Request):
        request.headers[self.header_name] = self.header_value
        yield request


class ServiceAccountAuth(httpx.Auth):
    """Two-header authentication for service accounts."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret

    def auth_flow(self, request: httpx.Request):
        request.headers["X-Client-ID"] = self.client_id
        request.headers["X-Client-Secret"] = self.client_secret
        yield request
```

### Usage in Datasource

```python
class MyApiClient(Datasource):
    def __init__(self, api_token: str):
        self._auth = BearerTokenAuth(api_token)
        self._base_url = "https://api.example.com"

    def get(self, path: str) -> dict:
        with httpx.Client(base_url=self._base_url, auth=self._auth) as client:
            response = client.get(path)
            response.raise_for_status()
            return response.json()
```

### When to Use

- Non-standard authentication schemes
- Reuse authentication across multiple datasources
- Clean separation of auth logic

### When Not to Use

- Standard Basic or Bearer auth (httpx has built-in support)

---

## 12. Graceful Degradation with DatasourceUnavailable

### Problem

External services may be temporarily unavailable. You want checks to report UNKNOWN rather than failing entirely.

### Solution

Catch connection errors and raise `DatasourceUnavailable`:

```python
import httpx
from watchpost import Datasource, DatasourceUnavailable

class ExternalApiClient(Datasource):
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def get_status(self) -> dict:
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    f"{self.base_url}/status",
                    headers={"X-API-Key": self.api_key},
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as e:
            raise DatasourceUnavailable("API request timed out") from e
        except httpx.ConnectError as e:
            raise DatasourceUnavailable("Cannot connect to API") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                # Server error - temporary, use UNKNOWN
                raise DatasourceUnavailable(f"API returned {e.response.status_code}") from e
            # Client error - likely a real problem, let it propagate
            raise
```

### Effect in Checkmk

When `DatasourceUnavailable` is raised:
- Check result is UNKNOWN (not CRIT)
- Message indicates the datasource issue
- Distinguishes "can't check" from "check failed"

### When to Use

- External services may have transient failures
- Network issues shouldn't trigger false alerts
- You want to distinguish "unreachable" from "unhealthy"

### When Not to Use

- Failures should always alert (critical dependencies)
- You want specific error handling per failure type

---

## 13. Data-Driven Check Configuration

### Problem

You have many similar items to check (containers, services, endpoints) with different expected states or severities.

### Solution

Define configuration as module-level data structures:

```python
from watchpost import check, ok, warn, crit

# Map item name to severity function for failures
EXPECTED_RUNNING = {
    "web-server": crit,      # Critical if down
    "background-worker": warn,  # Warning if down
    "metrics-collector": warn,
    "log-aggregator": crit,
}

# Items to ignore (not monitored)
IGNORED = {"test-container", "debug-service"}

@check(name="Service Status", environments=[PROD], cache_for="5m")
def service_status(docker: DockerClient):
    services = docker.list_services()

    # Track unexpected services
    unknown_running = []

    for service in services:
        if service.name in IGNORED:
            continue

        if service.name in EXPECTED_RUNNING:
            severity_fn = EXPECTED_RUNNING[service.name]

            if service.status != "running":
                yield severity_fn(
                    f"{service.name} is {service.status}",
                    name_suffix=f" - {service.name}",
                )
            else:
                yield ok(
                    f"{service.name} is running",
                    name_suffix=f" - {service.name}",
                )
        elif service.status == "running":
            unknown_running.append(service.name)

    # Report unexpected running services
    if unknown_running:
        yield warn(
            f"Unknown services running: {', '.join(unknown_running)}",
            name_suffix=" - unknown",
        )
    else:
        yield ok("No unknown services running", name_suffix=" - unknown")
```

### When to Use

- Many similar items with different expected states
- Configuration changes frequently
- Different items have different severity levels

### When Not to Use

- Configuration should come from external source
- Dynamic discovery is preferred over static lists

---

## 14. Deadline/Expiration Tracking Pattern

### Problem

You need to monitor various deadlines (certificate expiry, license renewal, subscription dates) with consistent alerting logic.

### Solution

Create a dataclass for deadline configuration and a generic check:

```python
from dataclasses import dataclass
from datetime import date, timedelta, datetime, UTC
from watchpost import check, ok, warn, crit, HostnameInput

@dataclass
class Deadline:
    """Configuration for a tracked deadline."""
    target_date: date
    warn_before: timedelta
    crit_before: timedelta
    alternative_hostname: HostnameInput | None = None

# Define all tracked deadlines
DEADLINES = {
    "TLS Certificate - api.example.com": Deadline(
        target_date=date(2025, 6, 15),
        warn_before=timedelta(days=30),
        crit_before=timedelta(days=7),
    ),
    "Software License - Enterprise Suite": Deadline(
        target_date=date(2025, 12, 31),
        warn_before=timedelta(days=60),
        crit_before=timedelta(days=14),
    ),
    "Domain Renewal - example.com": Deadline(
        target_date=date(2026, 3, 1),
        warn_before=timedelta(days=90),
        crit_before=timedelta(days=30),
        alternative_hostname="dns-services",
    ),
}

@check(
    name="Deadline",
    environments=[PROD],
    cache_for="1d",
    hostname="misc-checks",
)
def deadline_check(local: Local):
    """Check all tracked deadlines."""
    today = datetime.now(tz=UTC).date()

    for name, dl in DEADLINES.items():
        remaining = dl.target_date - today

        details = (
            f"Deadline: {dl.target_date}\n"
            f"Remaining: {remaining.days} days\n"
            f"Warning threshold: {dl.warn_before.days} days\n"
            f"Critical threshold: {dl.crit_before.days} days"
        )

        if remaining < timedelta(0):
            yield crit(
                f"EXPIRED {abs(remaining.days)} days ago",
                name_suffix=f" - {name}",
                details=details,
                alternative_hostname=dl.alternative_hostname,
            )
        elif remaining < dl.crit_before:
            yield crit(
                f"Expires in {remaining.days} days",
                name_suffix=f" - {name}",
                details=details,
                alternative_hostname=dl.alternative_hostname,
            )
        elif remaining < dl.warn_before:
            yield warn(
                f"Expires in {remaining.days} days",
                name_suffix=f" - {name}",
                details=details,
                alternative_hostname=dl.alternative_hostname,
            )
        else:
            yield ok(
                f"Valid for {remaining.days} more days",
                name_suffix=f" - {name}",
                alternative_hostname=dl.alternative_hostname,
            )
```

### When to Use

- Multiple deadlines to track
- Consistent alerting thresholds
- Deadlines are known in advance

### When Not to Use

- Deadlines are discovered dynamically (e.g., from API)
- Single deadline with unique logic

---

## 15. Context Manager Datasources for HTTP Clients

### Problem

You want to ensure HTTP connections are properly closed after use, especially in long-running ASGI deployments.

### Solution

Implement context manager protocol in datasource:

```python
from contextlib import contextmanager
import httpx
from watchpost import Datasource, DatasourceFactory

class ApiClient(Datasource, DatasourceFactory):
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
        return cls(
            base_url=os.environ["API_BASE_URL"],
            api_token=os.environ["API_TOKEN"],
        )
```

### Usage in Check

```python
@check(name="API Status", environments=[PROD])
def api_status(api: ApiClient):
    with api.client() as client:
        response = client.get("/health")
        if response.status_code == 200:
            return ok("API is healthy")
        return crit(f"API returned {response.status_code}")
```

### Async Variant

```python
from contextlib import asynccontextmanager
import httpx

class AsyncApiClient(Datasource, DatasourceFactory):
    def __init__(self, base_url: str, api_token: str):
        self._base_url = base_url
        self._api_token = api_token

    @asynccontextmanager
    async def client(self):
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_token}"},
        ) as client:
            yield client

# Usage
@check(name="API Status", environments=[PROD])
async def api_status(api: AsyncApiClient):
    async with api.client() as client:
        response = await client.get("/health")
        ...
```

### When to Use

- HTTP clients should be short-lived
- Connection pooling not needed/wanted
- Clean resource management

### When Not to Use

- Single long-lived connection is preferred
- High-frequency checks (connection overhead)

---

## 16. Container/Process Inventory Checks

### Problem

You want to monitor running containers/processes, ensuring expected ones are running and alerting on unexpected ones.

### Solution

Combine expected inventory with discovery:

```python
from watchpost import check, ok, warn, crit, build_result

# Expected containers with their failure severity
EXPECTED_CONTAINERS = {
    "nginx": crit,
    "postgres": crit,
    "redis": warn,
    "worker": warn,
}

# Containers to ignore (e.g., temporary, debug)
IGNORED_CONTAINERS = {"debug-shell", "migration-runner"}

@check(name="Container Status", environments=[PROD], cache_for="5m")
def container_status(docker: DockerClient):
    containers = docker.list_containers(all=True)
    seen_expected = set()

    # Builder for unknown containers summary
    unknown_summary = build_result(
        ok_summary="No unknown containers",
        fail_summary="Unknown containers detected",
        name_suffix=" - inventory",
    )

    for container in containers:
        name = container.name

        # Skip ignored
        if name in IGNORED_CONTAINERS:
            continue

        # Check expected containers
        if name in EXPECTED_CONTAINERS:
            seen_expected.add(name)
            severity_fn = EXPECTED_CONTAINERS[name]

            if container.status != "running":
                yield severity_fn(
                    f"{name} is {container.status}",
                    name_suffix=f" - {name}",
                    details=f"Expected: running\nActual: {container.status}",
                )
            elif container.health and container.health != "healthy":
                yield severity_fn(
                    f"{name} is unhealthy",
                    name_suffix=f" - {name}",
                    details=f"Health: {container.health}",
                )
            else:
                yield ok(f"{name} is healthy", name_suffix=f" - {name}")

        # Track unexpected running containers
        elif container.status == "running":
            unknown_summary.warn(f"Unexpected container: {name}")

    # Check for missing expected containers
    missing = set(EXPECTED_CONTAINERS.keys()) - seen_expected
    for name in missing:
        severity_fn = EXPECTED_CONTAINERS[name]
        yield severity_fn(
            f"{name} not found",
            name_suffix=f" - {name}",
            details="Container does not exist",
        )

    # Yield unknown containers summary
    yield unknown_summary
```

### Result Structure

This produces services like:
- `Container Status - nginx` (OK/CRIT based on status)
- `Container Status - postgres` (OK/CRIT)
- `Container Status - redis` (OK/WARN)
- `Container Status - worker` (OK/WARN)
- `Container Status - inventory` (OK/WARN for unknowns)

### When to Use

- Known set of expected containers/processes
- Want to detect drift from expected state
- Need per-item granularity plus summary

### When Not to Use

- Fully dynamic environments (use different approach)
- Too many items for individual services

---

## Summary

These recipes cover the most common patterns found in production Watchpost deployments:

| Category | Recipes |
|----------|---------|
| **Organization** | Project structure, centralized registries |
| **Datasources** | Dual pattern, factories, context managers, internal caching |
| **Checks** | Generators, result builders, data-driven configuration |
| **Configuration** | Environment-specific, callable credentials, data structures |
| **Caching** | Layered storage, runtime memoization |
| **Error Handling** | DatasourceUnavailable, graceful degradation |
| **Advanced** | Dynamic hostnames, deadline tracking, inventory checks |

Each recipe is self-contained and can be combined with others as needed.
