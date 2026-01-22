# Results

Check results define the outcome of your monitoring checks. Watchpost provides several ways to construct results, from simple helper functions to a builder pattern for complex scenarios.

## CheckState Enum

Every result has a state that maps directly to Checkmk states:

| State | Value | Checkmk | Meaning |
|-------|-------|---------|---------|
| `OK` | 0 | Green | Everything is working correctly |
| `WARN` | 1 | Yellow | A warning condition exists |
| `CRIT` | 2 | Red | A critical problem detected |
| `UNKNOWN` | 3 | Orange | Unable to determine status |

```python title="Illustrative example"
from watchpost.result import CheckState

# Enum values
CheckState.OK      # Value: 0
CheckState.WARN    # Value: 1
CheckState.CRIT    # Value: 2
CheckState.UNKNOWN # Value: 3

# Comparison (useful for escalation logic)
if CheckState.WARN < CheckState.CRIT:
    print("WARN is less severe than CRIT")
```

## Helper Functions

The simplest way to create results is with the `ok`, `warn`, `crit`, and `unknown` helper functions:

### ok()

Return when everything is working correctly:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="API Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def api_check():
    return ok("API is healthy")  # (1)
```

1. Creates a result with `CheckState.OK` and the given summary.

### warn()

Return when a warning condition exists:

```python title="Illustrative example"
from watchpost import check, warn
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Disk Space",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def disk_check():
    usage = 85  # percent
    return warn(
        f"Disk usage at {usage}%",
        details="Consider cleaning up old logs",  # (1)
    )
```

1. The `details` parameter provides additional information visible in the Checkmk service view.

### crit()

Return when a critical problem is detected:

```python title="Illustrative example"
from watchpost import check, crit
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Database Connection",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def db_check():
    return crit(
        "Cannot connect to database",
        details={  # (1)
            "Host": "db.example.com",
            "Port": 5432,
            "Error": "Connection refused",
        },
    )
```

1. Details can be a dict, which is formatted as `key: value` lines.

### unknown()

Return when the check cannot determine the status:

```python title="Illustrative example"
from watchpost import check, unknown
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="External API",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def external_check():
    return unknown(
        "Unable to reach monitoring endpoint",
        details="The endpoint timed out after 30 seconds",
    )
```

## Helper Function Parameters

All helper functions accept the same parameters:

```python title="Illustrative example"
from watchpost import ok
from watchpost.result import Metric, Thresholds

result = ok(
    "Response time normal",           # summary (required)
    details="Measured from eu-west",  # (1)
    name_suffix=" - EU",              # (2)
    metrics=[                         # (3)
        Metric(
            name="response_time",
            value=150,
            levels=Thresholds(warning=200, critical=500),
        )
    ],
    alternative_hostname="eu-api",    # (4)
)
```

1. Additional details shown in the service view. Can be `str`, `dict`, or `Exception`.
2. Suffix appended to the service name, creating a separate service.
3. Metrics that become Checkmk performance data.
4. Override the hostname for this specific result.

## CheckResult Dataclass

For more control, create `CheckResult` objects directly:

```python title="Illustrative example"
from watchpost.result import CheckResult, CheckState, Metric

result = CheckResult(
    check_state=CheckState.OK,
    summary="All systems operational",
    details="Last checked at 14:30 UTC",
    name_suffix=None,
    metrics=[
        Metric(name="uptime_hours", value=720),
    ],
    hostname=None,
)
```

This is equivalent to using the helper functions but gives you access to all fields.

## OngoingCheckResult Builder

When a check validates multiple aspects but returns a single result, use the builder pattern:

```python title="Illustrative example"
from watchpost import check, build_result
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="System Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def system_check():
    result = build_result(
        ok_summary="All systems healthy",    # (1)
        fail_summary="System issues found",  # (2)
    )

    # Check CPU
    cpu_usage = 45
    if cpu_usage > 90:
        result.crit(f"CPU at {cpu_usage}%")
    elif cpu_usage > 70:
        result.warn(f"CPU at {cpu_usage}%")
    else:
        result.ok(f"CPU at {cpu_usage}%")

    # Check memory
    mem_usage = 65
    if mem_usage > 95:
        result.crit(f"Memory at {mem_usage}%")
    elif mem_usage > 80:
        result.warn(f"Memory at {mem_usage}%")
    else:
        result.ok(f"Memory at {mem_usage}%")

    # Check disk
    disk_usage = 72
    if disk_usage > 90:
        result.crit(f"Disk at {disk_usage}%")
    elif disk_usage > 70:
        result.warn(f"Disk at {disk_usage}%")  # (3)
    else:
        result.ok(f"Disk at {disk_usage}%")

    return result  # (4)
```

1. Summary shown if the final state is OK.
2. Summary shown if the final state is WARN, CRIT, or UNKNOWN.
3. Disk triggers a warning, so the final state will be WARN.
4. The builder automatically calculates the worst state (CRIT > UNKNOWN > WARN > OK).

### Builder Methods

The `OngoingCheckResult` builder provides:

```python title="Illustrative example"
from watchpost import build_result
from watchpost.result import Metric #! hidden

result = build_result(
    ok_summary="All checks passed",
    fail_summary="Issues detected",
    base_details="System: prod-server-01",  # (1)
    name_suffix=" - Node A",                # (2)
    metrics=[Metric(name="cpu", value=45)], # (3)
    alternative_hostname="prod-server-01",  # (4)
)

# Add partial results
result.ok("Database connected")
result.warn("Cache hit rate low", details="Consider increasing cache size")
result.crit("Disk nearly full")
result.unknown("Network check inconclusive")

# Access current state
current_state = result.check_state  # (5)
```

1. Base details included in every result.
2. Suffix appended to the service name.
3. Metrics for performance data.
4. Override hostname.
5. The `check_state` property returns the worst state so far.

### Returning the Builder

You can return the builder directly from a check - Watchpost converts it to a `CheckResult`:

```python title="Illustrative example"
from watchpost import check, build_result
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Multi-Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def multi_check():
    result = build_result("OK", "Issues found")
    result.ok("Check 1 passed")
    result.ok("Check 2 passed")
    return result  # (1)
```

1. Watchpost calls `result.to_check_result()` automatically.

## Returning Multiple Results

A single check can produce multiple Checkmk services by returning multiple results.

### Using a List

```python title="Illustrative example"
from watchpost import check, ok, warn
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Endpoint Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def multi_endpoint_check():
    endpoints = {
        "/api/users": 200,
        "/api/orders": 200,
        "/api/products": 503,
    }

    results = []
    for endpoint, status in endpoints.items():
        if status == 200:
            results.append(ok(f"Status {status}", name_suffix=f" {endpoint}"))
        else:
            results.append(warn(f"Status {status}", name_suffix=f" {endpoint}"))

    return results  # (1)
```

1. Creates three services:
   - "Endpoint Status /api/users"
   - "Endpoint Status /api/orders"
   - "Endpoint Status /api/products"

### Using a Generator

For large result sets or lazy evaluation, use a generator:

```python title="Illustrative example" { "validate": false }
from watchpost import check, ok, warn

@check(
    name="Container Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def container_check():
    containers = ["web-1", "web-2", "worker-1", "worker-2"]

    for container in containers:
        status = get_container_status(container)  # (1)
        if status == "running":
            yield ok(f"Running", name_suffix=f" {container}")
        else:
            yield warn(f"Status: {status}", name_suffix=f" {container}")
```

1. Each container is checked as results are yielded.

## name_suffix for Multiple Services

The `name_suffix` parameter creates service name variants from a single check:

```python title="Illustrative example"
from watchpost import check, ok, warn, crit
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Database",  # Base name
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def database_checks():
    results = []

    # Check 1: Connection pool
    pool_usage = 45
    if pool_usage > 90:
        results.append(crit(f"Pool at {pool_usage}%", name_suffix=" Connection Pool"))
    else:
        results.append(ok(f"Pool at {pool_usage}%", name_suffix=" Connection Pool"))

    # Check 2: Replication lag
    lag_seconds = 2
    if lag_seconds > 10:
        results.append(warn(f"Lag: {lag_seconds}s", name_suffix=" Replication"))
    else:
        results.append(ok(f"Lag: {lag_seconds}s", name_suffix=" Replication"))

    return results
```

This creates two Checkmk services:

- "Database Connection Pool"
- "Database Replication"

## Metrics and Thresholds

Add metrics to track numeric values and create graphs in Checkmk:

### Basic Metrics

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.result import Metric
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Response Time",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def response_check():
    latency = 150

    return ok(
        f"Latency: {latency}ms",
        metrics=[
            Metric(name="latency_ms", value=latency),
        ],
    )
```

### Metrics with Thresholds

Thresholds define warning and critical levels for automatic state calculation:

```python title="Illustrative example"
from watchpost import check, ok, warn, crit
from watchpost.result import Metric, Thresholds
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Response Time",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def response_check():
    latency = 250

    # Determine state based on thresholds
    if latency >= 500:
        state_fn = crit
    elif latency >= 200:
        state_fn = warn
    else:
        state_fn = ok

    return state_fn(
        f"Latency: {latency}ms",
        metrics=[
            Metric(
                name="latency_ms",
                value=latency,
                levels=Thresholds(warning=200, critical=500),  # (1)
            ),
        ],
    )
```

1. Thresholds are included in Checkmk performance data, enabling threshold visualization on graphs.

### Metrics with Boundaries

Boundaries define the valid range for a metric:

```python title="Illustrative example"
from watchpost.result import Metric, Thresholds

metric = Metric(
    name="cpu_percent",
    value=75,
    levels=Thresholds(warning=70, critical=90),
    boundaries=Thresholds(warning=0, critical=100),  # (1)
)
```

1. Despite being called `Thresholds`, this defines min/max bounds for the metric. The `warning` field is the minimum, `critical` is the maximum.

### Multiple Metrics

A result can include multiple metrics:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.result import Metric, Thresholds
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="System Resources",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def resource_check():
    return ok(
        "Resources within limits",
        metrics=[
            Metric(
                name="cpu_percent",
                value=45,
                levels=Thresholds(warning=70, critical=90),
            ),
            Metric(
                name="memory_percent",
                value=62,
                levels=Thresholds(warning=80, critical=95),
            ),
            Metric(
                name="disk_percent",
                value=55,
                levels=Thresholds(warning=70, critical=90),
            ),
        ],
    )
```

## Details Formatting

The `details` parameter accepts several formats:

### String Details

```python title="Illustrative example"
from watchpost import ok

ok(
    "Check passed",
    details="Detailed information about the check result.\nMultiple lines supported.",
)
```

### Dictionary Details

```python title="Illustrative example"
from watchpost import ok

ok(
    "Check passed",
    details={
        "Server": "prod-api-01",
        "Response Time": "150ms",
        "Status Code": 200,
    },
)
# Formats as:
# Server: prod-api-01
# Response Time: 150ms
# Status Code: 200
```

### Exception Details

```python title="Illustrative example" { "validate": false }
from watchpost import crit

try:
    risky_operation()
except Exception as e:
    return crit(
        "Operation failed",
        details=e,  # (1)
    )
```

1. Exceptions are formatted with full traceback information.

## Checkmk Integration

### State Mapping

| Watchpost | Checkmk State | Color | Typical Use |
|-----------|---------------|-------|-------------|
| `CheckState.OK` | OK | Green | Normal operation |
| `CheckState.WARN` | WARN | Yellow | Attention needed soon |
| `CheckState.CRIT` | CRIT | Red | Immediate action required |
| `CheckState.UNKNOWN` | UNKNOWN | Orange | Cannot determine state |

### Service Display

- **Summary**: Appears in service lists and dashboards
- **Details**: Shown when viewing the service specifically
- **Metrics**: Appear as performance data with graphs

### Performance Data

Metrics become Checkmk performance data:

```python title="Illustrative example"
from watchpost.result import Metric, Thresholds

Metric(
    name="response_time",      # → metric name
    value=150,                 # → current value
    levels=Thresholds(
        warning=200,           # → warning threshold
        critical=500,          # → critical threshold
    ),
)
```

This enables:

- Time-series graphs in Checkmk
- Threshold lines on graphs
- Alerting based on metric values

## Next Steps

- Explore [Scheduling Strategies](../advanced/scheduling-strategies.md) for execution control
- Learn about [Caching](../advanced/caching.md) for result persistence
- See [Check Patterns](../cookbook/check-patterns.md) for real-world examples
