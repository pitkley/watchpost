# Concepts

This page introduces the core concepts you'll work with in Watchpost. Understanding these concepts will help you design effective monitoring checks and organize your code.

## Execution vs Target Environments

One of Watchpost's key design principles is the separation between **execution environments** and **target environments**.

* **Execution environment**: The environment where your Watchpost application is running.

* **Target environment**: The environment that a check is monitoring.

These can be the same or different. For example:

- **Central monitoring**: A single Watchpost instance running in a `monitoring` environment checks services across `dev`, `staging`, and `production` environments.
- **In-cluster monitoring**: A Watchpost instance runs inside a Kubernetes cluster and monitors services in that same cluster (execution environment == target environment).

```
┌─────────────────────────────────────────────────────────┐
│ Execution Environment: monitoring                       │
│                                                         │
│  Watchpost App                                          │
│  ├─ Check: API Health → targets: [dev, staging, prod]   │
│  ├─ Check: DB Status  → targets: [dev, staging, prod]   │
│  └─ Check: Queue Depth → targets: [prod]                │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

When you create a Watchpost application, you specify the execution environment:

```python title="Illustrative example"
from watchpost import Watchpost
from watchpost.environment import Environment

MONITORING = Environment("monitoring")

app = Watchpost(
    checks=[...],
    execution_environment=MONITORING,  # Where this app runs
)
```

When you define a check, you specify which environments it targets:

```python title="Illustrative example"
from watchpost import check
from watchpost.environment import Environment #! hidden
DEV = Environment("dev") #! hidden
STAGING = Environment("staging") #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="API Health",
    service_labels={},
    environments=[DEV, STAGING, PROD],  # What this check monitors
    cache_for=None,
)
def api_health_check():
    ...
```

Watchpost uses [scheduling strategies](advanced/scheduling-strategies.md) to decide whether a check should run based on the execution and target environments. This lets you control scenarios like "only run this check when executing inside the cluster" or "skip this check for development environments."

## Check Lifecycle

A check goes through several phases from definition to execution:

    Registration → Scheduling → Execution → Output

**1. Registration**

Checks are registered when you decorate a function with `@check` and add it to your Watchpost application:

```python title="Illustrative example"
from watchpost import check, Watchpost #! hidden
from watchpost.environment import Environment #! hidden
PROD = Environment("prod") #! hidden
MyDatasource = ... #! hidden
@check(
    name="Service Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def service_health_check(datasource: MyDatasource):
    ...

app = Watchpost(
    checks=[service_health_check],
    execution_environment=PROD,
)
```

At startup, Watchpost discovers all checks (including checks in modules) and validates their configuration.

**2. Scheduling**

Before running a check, Watchpost evaluates [scheduling strategies](advanced/scheduling-strategies.md) to decide whether the check should run:

- **SCHEDULE**: Run the check now
- **SKIP**: Don't run, use cached results instead
- **DONT_SCHEDULE**: Never run from this environment

For example, if a check targets `PROD` but the execution environment is `DEV`, a scheduling strategy might return `DONT_SCHEDULE`.

**3. Execution**

If scheduled, Watchpost:

1. Instantiates the datasources required by the check.
2. Injects datasources into the check function parameters.
3. Executes the check.
4. Handles any errors and caches the results.

Checks can be defined as sync functions (`def my_check(...)`) or as async functions (`async def my_check(...)`).
Watchpost automatically detects which you have chosen and executes your function appropriately.

If you define your function as sync, you are free to execute anything that might block, as your check is executed on a thread-pool.

If you define your function as async your function must not block, as is common in async functions.
If you have an activity that needs to block within an async function, make use of [`loop.run_in_executor`](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.run_in_executor).

**4. Output**

After execution, Watchpost generates Checkmk-compatible output in [piggyback format](https://docs.checkmk.com/latest/en/piggyback.html):

- Each check result becomes a Checkmk service
- Service name comes from the `name` parameter in `@check`
- Service labels come from the `service_labels` parameter
- Metrics become Checkmk performance data
- Results are associated with the provided (determined by [hostname resolution](advanced/hostname-resolution.md))

## Dependency Injection Model

Watchpost uses Python type hints for dependency injection. Checks declare what datasources they need, and Watchpost provides them at execution time.

**Datasources** are classes that encapsulate external dependencies:

```python title="Illustrative example"
from watchpost.datasource import Datasource

class ApiDatasource(Datasource):
    scheduling_strategies = ()

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def get_health(self):
        # Implementation
        ...
```

**Checks** declare datasource dependencies via type-annotated parameters:

```python title="Illustrative example"
from watchpost.result import warn, ok
from watchpost import check #! hidden
from watchpost.environment import Environment #! hidden
PROD = Environment("prod") #! hidden
ApiDatasource = ... #! hidden

@check(
    name="API Health",
    service_labels={},
    environments=[PROD],
    cache_for=None,
)
async def api_health_check(api: ApiDatasource):  # (1)
    status = await api.get_health()
    if status.healthy:
        return ok("API is healthy")
    return warn("API is degraded")
```

1. Watchpost sees the `api: ApiDatasource` parameter and injects an instance of `ApiDatasource` when the check runs.

**Registration** connects datasources to the application:

```python title="Illustrative example"
from watchpost import Watchpost #! hidden
from watchpost.datasource import Datasource #! hidden
api_health_check = ... #! hidden
PROD = ... #! hidden
class ApiDatasource(Datasource): #! hidden
    scheduling_strategies = () #! hidden
app = Watchpost(checks=[api_health_check], execution_environment=PROD)
app.register_datasource(ApiDatasource, base_url="https://api.example.com")
```

This pattern keeps checks focused on monitoring logic while datasources handle the complexity of connecting to external systems.

For more complex scenarios, you can use the [factory pattern](fundamentals/datasources.md#datasource-factories) to create datasources with different configurations per check:

```python title="Illustrative example"
from watchpost import check #! hidden
from watchpost.datasource import Datasource #! hidden
PROD = ... #! hidden
class ApiDatasource(Datasource): #! hidden
    scheduling_strategies = () #! hidden
from typing import Annotated, override
from watchpost.datasource import DatasourceFactory, FromFactory

class ApiFactory(DatasourceFactory):
    scheduling_strategies = ()
    
    @override
    def new(cls, base_url) -> ApiDatasource:
        return ApiDatasource(base_url=base_url)

@check(
    name="Auth API Health",
    service_labels={},
    environments=[PROD],
    cache_for=None,
)
def auth_api_check(
    api: Annotated[ApiDatasource, FromFactory(ApiFactory, "https://api.example.com")],
):
    ...
```

## Mapping to Checkmk

Watchpost generates output that Checkmk can consume. Understanding this mapping helps you design effective checks.

| Watchpost Concept | Checkmk Concept | Example |
|-------------------|-----------------|---------|
| `@check(name=...)` | Service name | `"API Health"` becomes service "API Health" |
| `@check(service_labels=...)` | Service labels | `{"component": "api"}` becomes label `component:api` |
| `CheckResult.state` | Service state | `CheckState.OK` → 0 (OK), `CheckState.CRIT` → 2 (CRIT) |
| `CheckResult.summary` | Service summary | `"API is healthy"` → shows in service list |
| `CheckResult.details` | Service details | Multi-line details → shows in service view |
| `CheckResult.metrics` | Performance data | `Metric(name="response_time", value=150)` → graphs in Checkmk |
| Hostname | Piggyback host | Determines which Checkmk host shows the service |

**Example check result flow:**

```python title="Illustrative example"
from watchpost import check #! hidden
PROD = ... #! hidden
ApiDatasource = ... #! hidden
@check(
    name="API Response Time",
    service_labels={"component": "api", "tier": "backend"},
    environments=[PROD],
    cache_for="10m",
)
def api_response_time_check(api: ApiDatasource):
    response_time = api.measure_response_time()

    return ok(
        f"Response time: {response_time}ms",
        metrics=[
            Metric(
                name="response_time",
                value=response_time,
                unit="ms",
                thresholds=Thresholds(warn=200, crit=500),
            )
        ],
    )
```

This creates:

- A Checkmk service named **"API Response Time"**
- With labels **component:api** and **tier:backend**
- State **OK** (green) if response time is below 200ms
- State **WARN** (yellow) if response time is 200-499ms
- State **CRIT** (red) if response time is 500ms or higher
- A **performance graph** showing response time trends

**Multiple environments create multiple services:**

```python title="Illustrative example"
from watchpost import check #! hidden
from watchpost.environment import Environment #! hidden
DEV = ... #! hidden
STAGING = ... #! hidden
PROD = ... #! hidden
ApiDatasource = ... #! hidden
DatabaseDatasource = ... #! hidden
@check(
    name="Database Connections",
    service_labels={},
    environments=[DEV, STAGING, PROD],  # Three target environments
    cache_for=None,
)
def db_connections_check(
    environment: Environment, # (1)
    db: DatabaseDatasource,
):
    ...
```

1. You can use dependency injection to receive the environment currently being checked as a parameter.

This creates **three separate Checkmk services**, one for each environment (assuming different hostnames per environment):

- "Database Connections" on host `dev-services`
- "Database Connections" on host `staging-services`
- "Database Connections" on host `prod-services`

## Next Steps

Now that you understand the core concepts, you're ready to build your first Watchpost application:

- Follow the [getting started guide](getting-started.md) to create a working check
- Learn about [environments](fundamentals/environments.md) in detail
- Explore [datasources](fundamentals/datasources.md) patterns
- Understand [check configuration](fundamentals/checks.md) options
