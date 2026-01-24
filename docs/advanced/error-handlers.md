# Error Handlers

Error handlers transform error results when checks fail to execute. They're essential for checks that normally produce multiple results - ensuring that failures are reported consistently across all expected services.

## The Problem

Consider a check that monitors multiple hosts:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Host Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def host_status():
    for host in ["web-01", "web-02", "web-03"]:
        status = check_host(host)  # (1)
        yield ok(f"Host healthy", name_suffix=f" - {host}")
```

1. What if this line raises an exception before any results are yielded?

If the check fails early (e.g., network error, authentication failure), Watchpost produces a single error result. But Checkmk expects three services (`Host Status - web-01`, `Host Status - web-02`, `Host Status - web-03`). Without error handlers:

- Only one UNKNOWN/CRIT service appears
- Other services go stale or disappear
- Alerting becomes inconsistent

Error handlers solve this by expanding the single error result across all expected services.

## The ErrorHandler Protocol

An error handler is a callable that transforms error results:

```python title="Protocol signature" { "validate": false }
def __call__(
    self,
    check: Check,
    environment: Environment,
    results: list[ExecutionResult],
) -> list[ExecutionResult]:
    ...
```

Parameters:

- `check`: The check definition (access to service name, labels, strategies)
- `environment`: The environment where the check was supposed to run
- `results`: The current list of error results to transform

Returns a list of `ExecutionResult` objects. Typically this is longer than the input list (expanding one error to many).

## Built-in Error Handlers

### expand_by_hostname()

Expands error results across multiple hostnames:

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
    for host in ["host-a", "host-b", "host-c"]:
        status = check_host(host)
        yield ok(f"Host healthy", alternative_hostname=host)
```

1. If the check fails, the error result is duplicated to all three hostnames.

**Before expansion:**

```
1 error result → hostname not set
```

**After expansion:**

```
3 error results:
  → hostname: host-a
  → hostname: host-b
  → hostname: host-c
```

Each hostname receives the same error state, summary, and details.

### expand_by_name_suffix()

Expands error results across multiple service name suffixes:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.check import expand_by_name_suffix
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

ENDPOINTS = ["/api/users", "/api/orders", "/api/products"]

@check(
    name="Endpoint Status -",  # (1)
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    error_handlers=[
        expand_by_name_suffix([f" {ep}" for ep in ENDPOINTS]),  # (2)
    ],
)
def endpoint_status():
    for endpoint in ENDPOINTS:  # (3)
        status = check_endpoint(endpoint)
        yield ok(f"Endpoint healthy", name_suffix=f" {endpoint}")
```

1. Include the separator in the service name for cleaner formatting.
2. Use the same `ENDPOINTS` constant to ensure consistency.
3. Loop uses the same constant, so error handlers always match actual results.

**Before expansion:**

```
1 error result → service_name: "Endpoint Status -"
```

**After expansion:**

```
3 error results:
  → service_name: "Endpoint Status - /api/users"
  → service_name: "Endpoint Status - /api/orders"
  → service_name: "Endpoint Status - /api/products"
```

## Chaining Error Handlers

Multiple error handlers can be chained. Each handler receives the output of the previous one:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.check import expand_by_hostname, expand_by_name_suffix
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

HOSTS = ["host-a", "host-b"]
SERVICES = ["API", "DB"]

@check(
    name="Service Status -",  # (1)
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    error_handlers=[
        expand_by_hostname(HOSTS),  # (2)
        expand_by_name_suffix([f" {s}" for s in SERVICES]),  # (3)
    ],
)
def service_status():
    for host in HOSTS:
        for service in SERVICES:
            yield ok(f"OK", alternative_hostname=host, name_suffix=f" {service}")
```

1. Include the separator in the service name.
2. First expands 1 → 2 results (by hostname).
3. Then expands 2 → 4 results (by suffix).

**Expansion chain:**

```
1 result
  ↓ expand_by_hostname
2 results (host-a, host-b)
  ↓ expand_by_name_suffix
4 results:
  - host-a / "Service Status - API"
  - host-a / "Service Status - DB"
  - host-b / "Service Status - API"
  - host-b / "Service Status - DB"
```

## Custom Error Handlers

For complex scenarios, implement your own error handler:

```python title="Illustrative example"
from watchpost.check import Check
from watchpost.result import ExecutionResult
from watchpost import Environment

def expand_by_customers(customers: list[str]):
    """Expand errors to customer-specific services."""

    def handler(
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
    ) -> list[ExecutionResult]:
        expanded = []
        for result in results:
            for customer in customers:
                expanded.append(
                    ExecutionResult(
                        piggyback_host=f"{customer}-services",
                        service_name=f"{result.service_name} - {customer}",
                        service_labels=result.service_labels,
                        environment_name=result.environment_name,
                        check_state=result.check_state,
                        summary=result.summary,
                        details=result.details,
                        metrics=result.metrics,
                        check_definition=result.check_definition,
                    )
                )
        return expanded

    return handler
```

**Key principles:**

- Create new `ExecutionResult` objects - don't modify inputs
- Preserve fields you don't intentionally modify (state, summary, details, metrics)
- The check state (CRIT, UNKNOWN) should usually be preserved

## When to Use Error Handlers

### Use When

- Check yields multiple results with different hostnames
- Check yields multiple results with different name suffixes
- Consistent error reporting across all expected services is required
- You need "all or nothing" alerting behavior

### Don't Use When

- Check produces a single result
- Different services should have different error behavior
- Error aggregation/summarization is preferred over expansion

## Error Handler vs. Exception Handling

Error handlers and in-check exception handling serve different purposes:

**In-check exception handling:**

```python title="Illustrative example"
from watchpost import check, ok, crit
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Robust Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def robust_check():
    for host in ["host-a", "host-b", "host-c"]:
        try:
            status = check_host(host)
            yield ok(f"OK", name_suffix=f" - {host}")
        except Exception as e:
            yield crit(f"Failed: {e}", name_suffix=f" - {host}")  # (1)
```

1. Error is caught and converted to a result per-host.

**Error handlers:**

Handle failures that occur *before* any results are yielded - network initialization, authentication, early validation failures.

**Best practice:** Use both:

- Catch recoverable per-item errors within the check
- Use error handlers for catastrophic failures that prevent any iteration

## Practical Example

Complete example combining multiple patterns:

```python title="Illustrative example"
from watchpost import check, ok, warn, crit, Datasource, DatasourceUnavailable
from watchpost.check import expand_by_hostname, expand_by_name_suffix
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

class HostChecker(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def get_hosts(self): pass #! hidden
    def check_service(self, host, service): pass #! hidden

HOSTS = ["web-01", "web-02", "web-03"]
SERVICES = ["nginx", "postgres", "redis"]

@check(
    name="Infrastructure -",  # (1)
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    error_handlers=[
        expand_by_hostname(HOSTS),  # (2)
        expand_by_name_suffix([f" {s}" for s in SERVICES]),  # (3)
    ],
)
def infrastructure_check(checker: HostChecker):
    # If this raises, error handlers expand the error
    hosts = checker.get_hosts()

    for host in HOSTS:
        for service in SERVICES:
            try:
                status = checker.check_service(host, service)
                if status.healthy:
                    yield ok(
                        f"{service} healthy",
                        alternative_hostname=host,
                        name_suffix=f" {service}",
                    )
                else:
                    yield warn(
                        f"{service} degraded",
                        alternative_hostname=host,
                        name_suffix=f" {service}",
                    )
            except Exception as e:
                # Per-service errors handled here
                yield crit(
                    f"{service} check failed: {e}",
                    alternative_hostname=host,
                    name_suffix=f" {service}",
                )
```

1. Include the separator in the service name.
2. Catastrophic failures expand to all hosts.
3. Then expand to all services per host (3 hosts × 3 services = 9 results).

**Result:**

- Normal execution: 9 services, each with its own status
- Per-service failure: That specific service shows CRIT, others OK
- Catastrophic failure (e.g., `get_hosts()` raises): All 9 services show UNKNOWN

## Checkmk Integration

Error handlers ensure Checkmk sees consistent services:

**Without error handlers:**

```
Host Status - web-01: OK
Host Status - web-02: OK
Host Status - web-03: OK
```

↓ Check fails completely ↓

```
Host Status: UNKNOWN (single service, wrong name)
```

**With error handlers:**

```
Host Status - web-01: OK
Host Status - web-02: OK
Host Status - web-03: OK
```

↓ Check fails completely ↓

```
Host Status - web-01: UNKNOWN
Host Status - web-02: UNKNOWN
Host Status - web-03: UNKNOWN
```

Services remain consistent, and alerts fire for all expected hosts.

## Next Steps

- Learn about [Scheduling Strategies](scheduling-strategies.md) for execution control
- Explore [Hostname Resolution](hostname-resolution.md) for routing results
- See [Caching](caching.md) for result persistence
