# Error Handling Patterns

This page covers patterns for graceful error handling in Watchpost. For foundational concepts about error handlers, see [Error Handlers](../advanced/error-handlers.md).

## DatasourceUnavailable for Temporary Failures

The `DatasourceUnavailable` exception distinguishes "can't check" from "check failed." When raised, Watchpost first attempts to return a previously cached result. If no cached result is available, the check result becomes UNKNOWN rather than CRIT:

```python title="Illustrative example"
import httpx
from watchpost import Datasource, DatasourceUnavailable


class ExternalApiClient(Datasource):
    scheduling_strategies = ()

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
                raise DatasourceUnavailable(
                    f"API returned {e.response.status_code}"
                ) from e
            # Client error (4xx) - likely a real problem, let it propagate
            raise
```

### Which Exceptions to Catch

| Exception Type | Action | Reasoning |
|----------------|--------|-----------|
| `TimeoutException` | `DatasourceUnavailable` | Network/server slow, may recover |
| `ConnectError` | `DatasourceUnavailable` | Can't reach server, may recover |
| `HTTPStatusError` (5xx) | `DatasourceUnavailable` | Server error, may recover |
| `HTTPStatusError` (4xx) | Re-raise | Client/config error, needs investigation |
| `JSONDecodeError` | Re-raise | Response format error, needs investigation |

### Effect in Checkmk

When `DatasourceUnavailable` is raised:

1. **If cached results exist**: Watchpost returns the previously cached result. For truly temporary blips, the issue never surfaces in Checkmk, reducing noise to a minimum.

2. **If cache is expired or empty**: The check result becomes **UNKNOWN** (not CRIT). The message indicates the datasource issue, and operators see "can't determine status" rather than a false alarm.

This two-tier approach means temporary network hiccups are silently handled via caching, while persistent issues are surfaced as UNKNOWN after the cache expires.

## Validation with Specific Error Messages

Provide actionable error details that help operators diagnose issues:

```python title="Illustrative example"
from watchpost import Datasource, DatasourceUnavailable


class DatabaseClient(Datasource):
    scheduling_strategies = ()

    def __init__(self, host: str, port: int, database: str):
        self.host = host
        self.port = port
        self.database = database

    def _connect(self): #! hidden
        return None #! hidden

    def query(self, sql: str) -> list[dict]:
        try:
            # Connection attempt
            connection = self._connect()
        except ConnectionRefusedError:
            raise DatasourceUnavailable(
                f"Connection refused to {self.host}:{self.port}. "
                "Is the database server running?"
            )
        except TimeoutError:
            raise DatasourceUnavailable(
                f"Connection to {self.host}:{self.port} timed out. "
                "Check network connectivity and firewall rules."
            )
        except Exception as e:
            if "auth" in str(e).lower():
                raise DatasourceUnavailable(
                    f"Authentication failed for database {self.database}. "
                    "Check credentials in environment variables."
                ) from e
            raise

        # Query errors are check failures, not unavailability
        return []
```

**Good error messages include:**

- What happened
- Where it happened (host, port, resource)
- Suggested remediation

## Handling Partial Failures

When a check queries multiple resources and some fail:

```python title="Illustrative example"
from watchpost import check, ok, warn, crit, unknown, Environment, Datasource, DatasourceUnavailable
PROD = Environment("prod") #! hidden

class ApiClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def check_endpoint(self, endpoint: str): return type("Status", (), {"healthy": True, "reason": ""})() #! hidden


@check(
    name="Endpoint Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def endpoint_health(api: ApiClient):
    endpoints = ["users", "products", "orders", "payments"]
    failures: list[str] = []

    for endpoint in endpoints:
        try:
            status = api.check_endpoint(endpoint)
            if status.healthy:
                yield ok(f"{endpoint} is healthy", name_suffix=f" - {endpoint}")
            else:
                yield crit(
                    f"{endpoint} is unhealthy: {status.reason}",
                    name_suffix=f" - {endpoint}",
                )
        except DatasourceUnavailable as e:
            # Individual endpoint unreachable
            yield unknown(
                f"Cannot check {endpoint}: {e}",
                name_suffix=f" - {endpoint}",
            )
            failures.append(endpoint)

    # Summary result
    if failures:
        yield warn(
            f"{len(failures)} endpoints unreachable",
            name_suffix=" - summary",
            details=f"Failed: {', '.join(failures)}",
        )
    else:
        yield ok("All endpoints checked", name_suffix=" - summary")
```

This pattern:

- Continues checking remaining endpoints after failures
- Reports individual statuses (OK, CRIT, or UNKNOWN)
- Provides a summary of overall health

## Error Handlers for Multi-Result Checks

When a check normally produces multiple results but fails entirely, use error handlers to expand the error:

```python title="Illustrative example"
from watchpost import check, ok, Environment, Datasource
from watchpost.check import expand_by_hostname
PROD = Environment("prod") #! hidden

class MultiHostClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def get_status(self, host: str): return "ok" #! hidden


@check(
    name="Host Status",
    service_labels={},
    environments=[PROD],
    hostname="default-host",
    error_handlers=(
        expand_by_hostname(["host-a", "host-b", "host-c"]),  # (1)
    ),
    cache_for="5m",
)
def host_status(client: MultiHostClient):
    """Check all hosts. If check fails entirely, error shows on all hosts."""
    for host in ["host-a", "host-b", "host-c"]:
        status = client.get_status(host)
        yield ok(
            f"{host} is healthy",
            name_suffix=f" - {host}",
            alternative_hostname=host,  # (2)
        )
```

1. If the check fails (raises an exception), the error result is duplicated to all three hosts.
2. Normal results go to their respective hosts.

### expand_by_name_suffix

For checks that produce multiple services by name suffix:

```python title="Illustrative example"
from watchpost import check, ok, Environment, Datasource
from watchpost.check import expand_by_name_suffix
PROD = Environment("prod") #! hidden

class DockerClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def get_container(self, name: str): return type("Container", (), {"status": "running"})() #! hidden


@check(
    name="Service Status",
    service_labels={},
    environments=[PROD],
    error_handlers=(
        expand_by_name_suffix([" - api", " - worker", " - scheduler"]),
    ),
    cache_for="5m",
)
def service_status(docker: DockerClient):
    """Check all services. If check fails, error shows on all service names."""
    services = ["api", "worker", "scheduler"]
    for svc in services:
        container = docker.get_container(svc)
        yield ok(f"{svc} is running", name_suffix=f" - {svc}")
```

If the check raises an exception, UNKNOWN results appear for:

- `Service Status - api`
- `Service Status - worker`
- `Service Status - scheduler`

### When to Use Error Handlers

| Scenario | Error Handler |
|----------|---------------|
| Check produces results for multiple Checkmk hosts | `expand_by_hostname()` |
| Check produces multiple services via name_suffix | `expand_by_name_suffix()` |
| Custom error transformation needed | Implement `ErrorHandler` protocol |

## Graceful Degradation Strategies

### Strategy 1: Return Cached Results on Failure

Watchpost's built-in caching returns stale results once when a check fails. Combine with appropriate `cache_for`:

```python title="Illustrative example"
from watchpost import check, ok, Environment
PROD = Environment("prod") #! hidden

@check(
    name="External API",
    service_labels={},
    environments=[PROD],
    cache_for="15m",  # (1)
)
def external_api_check():
    # If this check fails, the cached result from up to 15 minutes ago
    # is returned once, providing graceful degradation
    return ok("API is healthy")
```

1. If the check fails, the cached result from up to 15 minutes ago is returned once.

### Strategy 2: Fallback Values

For non-critical metrics, use fallback values:

```python title="Illustrative example"
from watchpost import check, ok, warn, Environment, Datasource, DatasourceUnavailable
PROD = Environment("prod") #! hidden

class ApiClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def get_detailed_metrics(self): return type("M", (), {"response_time": 100})() #! hidden
    def get_basic_metrics(self): return type("M", (), {"response_time": 100})() #! hidden


@check(
    name="Optional Metrics",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def optional_metrics(api: ApiClient):
    try:
        metrics = api.get_detailed_metrics()
    except DatasourceUnavailable:
        # Fall back to basic metrics
        try:
            metrics = api.get_basic_metrics()
            return warn(
                "Using basic metrics (detailed unavailable)",
                details=f"Response time: {metrics.response_time}ms",
            )
        except DatasourceUnavailable:
            # Can't get any metrics
            raise

    return ok(
        "Metrics collected",
        details=f"Response time: {metrics.response_time}ms",
    )
```

### Strategy 3: Circuit Breaker Pattern

For datasources that may be slow or flaky:

```python title="Illustrative example"
import time
from watchpost import Datasource, DatasourceUnavailable


class CircuitBreakerClient(Datasource):
    scheduling_strategies = ()

    def __init__(
        self,
        base_url: str,
        failure_threshold: int = 3,
        reset_timeout: int = 60,
    ):
        self.base_url = base_url
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._failures = 0
        self._last_failure_time = 0.0
        self._circuit_open = False

    def _make_request(self, path: str) -> dict: #! hidden
        return {} #! hidden

    def request(self, path: str) -> dict:
        # Check if circuit should reset
        if self._circuit_open:
            if time.time() - self._last_failure_time > self.reset_timeout:
                self._circuit_open = False
                self._failures = 0
            else:
                remaining = int(
                    self.reset_timeout - (time.time() - self._last_failure_time)
                )
                raise DatasourceUnavailable(
                    f"Circuit open - too many failures. Retry in {remaining}s"
                )

        try:
            response = self._make_request(path)
            self._failures = 0  # Reset on success
            return response
        except Exception as e:
            self._failures += 1
            self._last_failure_time = time.time()
            if self._failures >= self.failure_threshold:
                self._circuit_open = True
            raise DatasourceUnavailable(f"Request failed: {e}") from e
```

## Testing Error Handling

Verify error handling works correctly:

```python title="Illustrative example"
import pytest
from watchpost import DatasourceUnavailable, Datasource
import httpx #! hidden


class ExternalApiClient(Datasource): #! hidden
    scheduling_strategies = () #! hidden
    def __init__(self, base_url: str): self.base_url = base_url #! hidden
    def get_status(self) -> dict: #! hidden
        raise DatasourceUnavailable("timed out") #! hidden


def test_timeout_raises_unavailable():
    client = ExternalApiClient(base_url="https://slow.example.com")

    with pytest.raises(DatasourceUnavailable) as exc_info:
        client.get_status()

    assert "timed out" in str(exc_info.value)
```

## Best Practices

### Do

- Use `DatasourceUnavailable` for transient failures (network, timeouts, server errors)
- Include actionable details in error messages
- Handle partial failures gracefully
- Use error handlers for multi-result checks
- Test error paths explicitly

### Don't

- Catch all exceptions as `DatasourceUnavailable` (let bugs propagate)
- Silently swallow errors (always log or report)
- Use `DatasourceUnavailable` for configuration errors
- Assume failures are always transient

## Next Steps

- Learn about [Error Handlers](../advanced/error-handlers.md) for transforming error results
- See [Datasource Patterns](datasource-patterns.md) for building robust datasources
- Explore [Caching Strategies](caching-strategies.md) for graceful degradation via caching
