# Check Patterns

This page covers common patterns for writing checks. For foundational concepts, see [Checks](../fundamentals/checks.md) and [Results](../fundamentals/results.md).

## Generator Checks for Multiple Services

When a single logical check should produce separate Checkmk services for each item (e.g., one service per backup plan, per endpoint, per container), use a generator with `name_suffix`:

```python title="Illustrative example"
from datetime import datetime, timedelta
from watchpost import check, ok, warn, crit, Environment, Datasource
from watchpost.check import expand_by_name_suffix
PROD = Environment("prod") #! hidden

class BackupClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def list_plans(self): return [] #! hidden
    def get_last_successful_run(self, plan_id: str): return None #! hidden

# Define known backup plans for error handler
BACKUP_PLANS = ["database-daily", "files-hourly", "config-weekly"]


@check(
    name="Backup Plan Status -",  # (1)
    service_labels={},
    environments=[PROD],
    error_handlers=[expand_by_name_suffix(BACKUP_PLANS)],  # (2)
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
        last_run = backup_client.get_last_successful_run(plan.id)

        if last_run is None:
            yield crit(
                f"No successful backup found for {plan.name}",
                name_suffix=plan.name,  # (3)
            )
            continue

        age = datetime.now() - last_run.completed_at
        critical_threshold = timedelta(days=2) #! hidden
        warning_threshold = timedelta(days=1) #! hidden

        if age > critical_threshold:
            yield crit(
                f"Backup is {age} old",
                name_suffix=plan.name,
                details=f"Last successful: {last_run.completed_at}",
            )
        elif age > warning_threshold:
            yield warn(
                f"Backup is {age} old",
                name_suffix=plan.name,
                details=f"Last successful: {last_run.completed_at}",
            )
        else:
            yield ok(
                f"Backup completed {age} ago",
                name_suffix=plan.name,
            )
```

1. Include the separator ` -` in the check name, so service names read naturally.
2. Error handlers ensure all services get an error result if the check fails entirely.
3. The `name_suffix` is just the item name without the separator.

**When to use:**

- One check should create multiple Checkmk services
- Iterating over a collection of similar items
- Each item needs independent alerting

**When not to use:**

- Single result is sufficient
- Items are so numerous that separate services would overwhelm Checkmk

## Result Builder for Multi-Validation Checks

When a check validates multiple conditions and you want the result to reflect all failures (not just the first), use `build_result()`:

```python title="Illustrative example"
from datetime import timedelta
import httpx
from watchpost import check, build_result, Environment
PROD = Environment("prod") #! hidden


@check(
    name="Website Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def website_health():
    """Validate multiple aspects of website health."""
    response = httpx.get("https://httpbin.org/status/200", follow_redirects=False) #! hidden
    # response = httpx.get("https://example.com", follow_redirects=False)

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
        r.warn(f"Response time {response.elapsed.total_seconds():.2f}s exceeds 2s")

    if response.elapsed > timedelta(seconds=5):
        r.crit(f"Response time {response.elapsed.total_seconds():.2f}s exceeds 5s")

    content_length = len(response.content)
    if content_length < 10:
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
- Response time 6.20s exceeds 5s
```

**When to use:**

- Check validates multiple conditions
- All issues should be visible, not just the first
- You want automatic state escalation (OK → WARN → CRIT)

**When not to use:**

- Simple pass/fail check
- Validations are mutually exclusive (only one can fail)

## Environment-Specific Configuration

When the same check runs across multiple environments but thresholds or parameters differ, use a dictionary mapping environments to configuration:

```python title="Illustrative example"
from datetime import timedelta
from watchpost import check, ok, warn, crit, Environment, Datasource
from watchpost.check import expand_by_name_suffix

DEV = Environment("dev")
STAGING = Environment("staging")
PROD = Environment("prod")

class BackupClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def get_backup_age(self, name: str) -> timedelta: return timedelta(hours=1) #! hidden

# Configuration per environment
BACKUP_CONFIG: dict[Environment, dict[str, dict[str, timedelta]]] = {
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

# All possible backup names across environments for error handler
ALL_BACKUP_NAMES = ["database", "files", "config"]


@check(
    name="Backup Age -",
    service_labels={},
    environments=[DEV, STAGING, PROD],
    error_handlers=[expand_by_name_suffix(ALL_BACKUP_NAMES)],
    cache_for="30m",
)
def backup_age(environment: Environment, backup_client: BackupClient):
    """Check backup age with environment-specific thresholds."""
    config = BACKUP_CONFIG[environment]

    for backup_name, thresholds in config.items():
        age = backup_client.get_backup_age(backup_name)

        if age > thresholds["crit"]:
            yield crit(
                f"{backup_name} backup is critically old",
                name_suffix=backup_name,
            )
        elif age > thresholds["warn"]:
            yield warn(
                f"{backup_name} backup is getting old",
                name_suffix=backup_name,
            )
        else:
            yield ok(
                f"{backup_name} backup is fresh",
                name_suffix=backup_name,
            )
```

**When to use:**

- Same check logic applies to multiple environments
- Thresholds or parameters differ by environment
- You want to maintain configuration in code

**When not to use:**

- Configuration is identical across environments
- Configuration should come from an external source (use env vars instead)

## Dynamic Hostname Resolution

When check results should route to different Checkmk hosts depending on environment or result content, use a callable hostname strategy:

```python title="Illustrative example"
from watchpost import check, ok, Environment, Datasource
from watchpost.hostname import HostnameContext

DEV = Environment("dev")
STAGING = Environment("staging")
PROD = Environment("prod")

class ApiClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden


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
    service_labels={},
    environments=[DEV, STAGING, PROD],
    hostname=environment_based_hostname,
    cache_for="5m",
)
def api_health(api: ApiClient):
    # Results go to different Checkmk hosts per environment
    return ok("API is healthy")
```

### Result-Level Hostname Override

For checks that produce multiple results where each should go to a different host:

```python title="Illustrative example"
from watchpost import check, ok, Environment, Datasource
from watchpost.check import expand_by_name_suffix
PROD = Environment("prod") #! hidden

class MultiHostClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def get_hosts(self): return [] #! hidden
    def get_status(self, host: object): return None #! hidden

# Known hosts for error handler
KNOWN_HOSTS = ["host-a", "host-b", "host-c"]


@check(
    name="Multi-Host Status -",
    service_labels={},
    environments=[PROD],
    hostname="default-host",  # Fallback
    error_handlers=[expand_by_name_suffix(KNOWN_HOSTS)],
    cache_for="5m",
)
def multi_host_status(client: MultiHostClient):
    for host in client.get_hosts():
        status = client.get_status(host)
        yield ok(
            f"Host {host.name} is healthy",
            name_suffix=host.name,
            alternative_hostname=host.checkmk_hostname,  # Override per result
        )
```

**When to use:**

- Results should route to different Checkmk hosts
- Hostname depends on environment or result content
- Multi-tenant or multi-region monitoring

**When not to use:**

- Single hostname is sufficient
- Hostname is static (use string directly)

## Data-Driven Check Configuration

When you have many similar items to check with different expected states or severities, define configuration as module-level data structures:

```python title="Illustrative example"
from watchpost import check, ok, warn, crit, Environment, Datasource
from watchpost.check import expand_by_name_suffix
from typing import Callable
from watchpost.result import CheckResult
PROD = Environment("prod") #! hidden

class DockerClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def list_services(self): return [] #! hidden

# Map item name to severity function for failures
EXPECTED_RUNNING: dict[str, Callable[..., CheckResult]] = {
    "web-server": crit,       # Critical if down
    "background-worker": warn,  # Warning if down
    "metrics-collector": warn,
    "log-aggregator": crit,
}

# Items to ignore (not monitored)
IGNORED = {"test-container", "debug-service"}

# All service names for error handler (including "unknown" summary)
SERVICE_SUFFIXES = [*EXPECTED_RUNNING.keys(), "unknown"]


@check(
    name="Service Status -",
    service_labels={},
    environments=[PROD],
    error_handlers=[expand_by_name_suffix(SERVICE_SUFFIXES)],
    cache_for="5m",
)
def service_status(docker: DockerClient):
    services = docker.list_services()

    # Track unexpected services
    unknown_running: list[str] = []

    for service in services:
        if service.name in IGNORED:
            continue

        if service.name in EXPECTED_RUNNING:
            severity_fn = EXPECTED_RUNNING[service.name]

            if service.status != "running":
                yield severity_fn(
                    f"{service.name} is {service.status}",
                    name_suffix=service.name,
                )
            else:
                yield ok(
                    f"{service.name} is running",
                    name_suffix=service.name,
                )
        elif service.status == "running":
            unknown_running.append(service.name)

    # Report unexpected running services
    if unknown_running:
        yield warn(
            f"Unknown services running: {', '.join(unknown_running)}",
            name_suffix="unknown",
        )
    else:
        yield ok("No unknown services running", name_suffix="unknown")
```

**When to use:**

- Many similar items with different expected states
- Configuration changes frequently
- Different items have different severity levels

**When not to use:**

- Configuration should come from an external source
- Dynamic discovery is preferred over static lists

## Deadline and Expiration Tracking

Create a reusable pattern for tracking deadlines (certificate expiry, license renewal, subscription dates):

```python title="Illustrative example"
from dataclasses import dataclass
from datetime import date, timedelta, datetime, UTC
from watchpost import check, ok, warn, crit, Environment, Datasource
from watchpost.check import expand_by_name_suffix
from watchpost.hostname import HostnameInput
PROD = Environment("prod") #! hidden

class Local(Datasource): #! hidden
    scheduling_strategies = () #! hidden


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
        target_date=date(2027, 6, 15),
        warn_before=timedelta(days=30),
        crit_before=timedelta(days=7),
    ),
    "Software License - Enterprise Suite": Deadline(
        target_date=date(2027, 12, 31),
        warn_before=timedelta(days=60),
        crit_before=timedelta(days=14),
    ),
    "Domain Renewal - example.com": Deadline(
        target_date=date(2028, 3, 1),
        warn_before=timedelta(days=90),
        crit_before=timedelta(days=30),
        alternative_hostname="dns-services",
    ),
}


@check(
    name="Deadline -",
    service_labels={},
    environments=[PROD],
    hostname="misc-checks",
    error_handlers=[expand_by_name_suffix(list(DEADLINES.keys()))],
    cache_for="1d",
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
                name_suffix=name,
                details=details,
                alternative_hostname=dl.alternative_hostname,
            )
        elif remaining < dl.crit_before:
            yield crit(
                f"Expires in {remaining.days} days",
                name_suffix=name,
                details=details,
                alternative_hostname=dl.alternative_hostname,
            )
        elif remaining < dl.warn_before:
            yield warn(
                f"Expires in {remaining.days} days",
                name_suffix=name,
                details=details,
                alternative_hostname=dl.alternative_hostname,
            )
        else:
            yield ok(
                f"Valid for {remaining.days} more days",
                name_suffix=name,
                alternative_hostname=dl.alternative_hostname,
            )
```

**When to use:**

- Multiple deadlines to track
- Consistent alerting thresholds
- Deadlines are known in advance

**When not to use:**

- Deadlines are discovered dynamically (e.g., from API)
- Single deadline with unique logic

## Container/Process Inventory Checks

Monitor running containers or processes, ensuring expected ones are running and alerting on unexpected ones:

```python title="Illustrative example"
from typing import Callable
from watchpost import check, ok, warn, crit, build_result, Environment, Datasource
from watchpost.check import expand_by_name_suffix
from watchpost.result import CheckResult
PROD = Environment("prod") #! hidden

class DockerClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def list_containers(self, all: bool = False): return [] #! hidden

# Expected containers with their failure severity
EXPECTED_CONTAINERS: dict[str, Callable[..., CheckResult]] = {
    "nginx": crit,
    "postgres": crit,
    "redis": warn,
    "worker": warn,
}

# Containers to ignore (e.g., temporary, debug)
IGNORED_CONTAINERS = {"debug-shell", "migration-runner"}

# All container names for error handler (including "inventory" summary)
CONTAINER_SUFFIXES = [*EXPECTED_CONTAINERS.keys(), "inventory"]


@check(
    name="Container Status -",
    service_labels={},
    environments=[PROD],
    error_handlers=[expand_by_name_suffix(CONTAINER_SUFFIXES)],
    cache_for="5m",
)
def container_status(docker: DockerClient):
    containers = docker.list_containers(all=True)
    seen_expected: set[str] = set()

    # Builder for unknown containers summary
    unknown_summary = build_result(
        ok_summary="No unknown containers",
        fail_summary="Unknown containers detected",
        name_suffix="inventory",
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
                    name_suffix=name,
                    details=f"Expected: running\nActual: {container.status}",
                )
            elif hasattr(container, "health") and container.health != "healthy":
                yield severity_fn(
                    f"{name} is unhealthy",
                    name_suffix=name,
                    details=f"Health: {container.health}",
                )
            else:
                yield ok(f"{name} is healthy", name_suffix=name)

        # Track unexpected running containers
        elif container.status == "running":
            unknown_summary.warn(f"Unexpected container: {name}")

    # Check for missing expected containers
    missing = set(EXPECTED_CONTAINERS.keys()) - seen_expected
    for name in missing:
        severity_fn = EXPECTED_CONTAINERS[name]
        yield severity_fn(
            f"{name} not found",
            name_suffix=name,
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

**When to use:**

- Known set of expected containers/processes
- Want to detect drift from expected state
- Need per-item granularity plus summary

**When not to use:**

- Fully dynamic environments (use different approach)
- Too many items for individual services

## Next Steps

- Learn about [Caching Strategies](caching-strategies.md) for advanced caching
- See [Error Handling Patterns](error-handling-patterns.md) for graceful degradation
- Explore [Datasource Patterns](datasource-patterns.md) for building robust datasources
