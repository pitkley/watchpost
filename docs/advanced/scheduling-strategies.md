# Scheduling Strategies

Scheduling strategies control **where** and **when** checks run. They enable scenarios like in-cluster checks that must run from within the environment they monitor, or checks that should only execute from a central monitoring location.

## The Scheduling Problem

Consider these scenarios:

- A Kubernetes health check that must run from inside the cluster it monitors
- A database check that can only run from a specific network zone
- A check that monitors multiple environments but should only run from a central monitoring server

Without scheduling strategies, you'd need separate Watchpost deployments for each scenario. Strategies let you express these constraints declaratively.

## SchedulingDecision Enum

When a strategy evaluates a check, it returns one of three decisions:

| Decision | Value | Meaning |
|----------|-------|---------|
| `SCHEDULE` | 0 | Run the check now |
| `SKIP` | 1 | Don't run now, use cached results if available |
| `DONT_SCHEDULE` | 2 | Never run from this execution environment |

```python title="Illustrative example"
from watchpost.scheduling_strategy import SchedulingDecision

# Decision values for comparison
SchedulingDecision.SCHEDULE      # Run the check
SchedulingDecision.SKIP          # Skip temporarily, reuse cache
SchedulingDecision.DONT_SCHEDULE # Never run from here
```

### SCHEDULE vs SKIP vs DONT_SCHEDULE

- **SCHEDULE**: The check is eligible to run. This is the normal case.
- **SKIP**: Conditions are temporarily unfavorable (e.g., maintenance window). Watchpost reuses the last cached result if available, otherwise reports UNKNOWN.
- **DONT_SCHEDULE**: This execution environment should never run this check. No result is produced for the check from this environment.

## Strategy Sources

Strategies can be attached at multiple levels:

1. **Check-level**: In the `@check` decorator
2. **Datasource-level**: On the `Datasource` class via `scheduling_strategies` attribute
3. **Application defaults**: Applied to all checks automatically

When multiple strategies apply, they are all evaluated and the **strictest decision wins**.

```python title="Illustrative example"
from watchpost import check, ok, Datasource
from watchpost.scheduling_strategy import MustRunInTargetEnvironmentStrategy
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

# Strategy on datasource
class KubernetesClient(Datasource):
    scheduling_strategies = (
        MustRunInTargetEnvironmentStrategy(),  # (1)
    )

# Strategy on check
@check(
    name="API Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    scheduling_strategies=[
        MustRunInTargetEnvironmentStrategy(),  # (2)
    ],
)
def api_health():
    return ok("OK")
```

1. All checks using this datasource inherit this strategy.
2. Applied only to this specific check.

## Built-in Strategies

### MustRunInGivenExecutionEnvironmentStrategy

Restricts which execution environments can run a check. If the current execution environment is not in the allowed set, returns `DONT_SCHEDULE`.

```python title="Illustrative example"
from watchpost import check, ok, Datasource, EnvironmentRegistry
from watchpost.scheduling_strategy import MustRunInGivenExecutionEnvironmentStrategy

ENVIRONMENTS = EnvironmentRegistry()
MONITORING = ENVIRONMENTS.new("monitoring")
PROD = ENVIRONMENTS.new("prod")
STAGING = ENVIRONMENTS.new("staging")

class CentralizedDatasource(Datasource):
    """Datasource that can only be accessed from the monitoring environment."""
    scheduling_strategies = (
        MustRunInGivenExecutionEnvironmentStrategy(MONITORING),  # (1)
    )

@check(
    name="Central Check",
    service_labels={},
    environments=[PROD, STAGING],  # (2)
    cache_for="5m",
)
def central_check(ds: CentralizedDatasource):
    return ok("OK")
```

1. This check can only execute from the `MONITORING` environment.
2. But it monitors both `PROD` and `STAGING` target environments.

**Use case**: Checks that require access to systems only reachable from specific locations (e.g., internal APIs, databases behind firewalls).

### MustRunInTargetEnvironmentStrategy

Requires that the execution environment equals the target environment. The check must run "in-cluster" - from within the environment it monitors.

```python title="Illustrative example"
from watchpost import check, ok, Datasource, EnvironmentRegistry
from watchpost.scheduling_strategy import MustRunInTargetEnvironmentStrategy

ENVIRONMENTS = EnvironmentRegistry()
PROD = ENVIRONMENTS.new("prod")
STAGING = ENVIRONMENTS.new("staging")

class LocalKubernetesClient(Datasource):
    """Kubernetes client that connects to the local cluster."""
    scheduling_strategies = (
        MustRunInTargetEnvironmentStrategy(),  # (1)
    )

@check(
    name="Pod Health",
    service_labels={},
    environments=[PROD, STAGING],
    cache_for="5m",
)
def pod_health(k8s: LocalKubernetesClient):
    return ok("All pods healthy")
```

1. When targeting `PROD`, the check only runs if execution environment is also `PROD`. When targeting `STAGING`, it only runs if execution environment is `STAGING`.

**Use case**: Kubernetes checks that use in-cluster authentication, checks that need local filesystem access, or any check that must run from within the environment being monitored.

### MustRunAgainstGivenTargetEnvironmentStrategy

Restricts which target environments a check can monitor. If the requested target is not in the allowed set, returns `DONT_SCHEDULE`.

```python title="Illustrative example"
from watchpost import check, ok, Datasource, EnvironmentRegistry
from watchpost.scheduling_strategy import MustRunAgainstGivenTargetEnvironmentStrategy

ENVIRONMENTS = EnvironmentRegistry()
PROD = ENVIRONMENTS.new("prod")
STAGING = ENVIRONMENTS.new("staging")
DEV = ENVIRONMENTS.new("dev")

class ProductionOnlyDatasource(Datasource):
    """Datasource that only makes sense for production monitoring."""
    scheduling_strategies = (
        MustRunAgainstGivenTargetEnvironmentStrategy(PROD),  # (1)
    )

@check(
    name="Production Metric",
    service_labels={},
    environments=[PROD, STAGING, DEV],  # (2)
    cache_for="5m",
)
def production_metric(ds: ProductionOnlyDatasource):
    return ok("OK")
```

1. This datasource only allows monitoring `PROD`.
2. Even though the check lists all environments, it will only run against `PROD` because of the datasource's strategy.

**Use case**: Datasources that are environment-specific (e.g., production-only APIs, staging-specific test endpoints).

## Strategy Composition

Multiple strategies are evaluated together, and the **strictest decision wins**:

```
SCHEDULE < SKIP < DONT_SCHEDULE
```

If any strategy returns `DONT_SCHEDULE`, that's the final decision regardless of what other strategies return.

```python title="Illustrative example"
from watchpost import Datasource, EnvironmentRegistry
from watchpost.scheduling_strategy import (
    MustRunInGivenExecutionEnvironmentStrategy,
    MustRunAgainstGivenTargetEnvironmentStrategy,
)

ENVIRONMENTS = EnvironmentRegistry()
MONITORING = ENVIRONMENTS.new("monitoring")
PROD = ENVIRONMENTS.new("prod")
STAGING = ENVIRONMENTS.new("staging")

class RestrictedDatasource(Datasource):
    """Datasource with multiple constraints."""
    scheduling_strategies = (
        MustRunInGivenExecutionEnvironmentStrategy(MONITORING),  # (1)
        MustRunAgainstGivenTargetEnvironmentStrategy(PROD, STAGING),  # (2)
    )
```

1. Must execute from `MONITORING`.
2. Can only target `PROD` or `STAGING`.

Both constraints must be satisfied for the check to run.

## Conflict Detection

The `DetectImpossibleCombinationStrategy` validates that strategies don't create impossible configurations. It's included in the application defaults and raises `InvalidCheckConfiguration` at startup when conflicts are detected.

### Example Conflicts

**Conflicting execution environments:**

```python title="Illustrative example" { "validate": false }
from watchpost import Datasource, EnvironmentRegistry
from watchpost.scheduling_strategy import MustRunInGivenExecutionEnvironmentStrategy

ENVIRONMENTS = EnvironmentRegistry()
ENV_A = ENVIRONMENTS.new("env-a")
ENV_B = ENVIRONMENTS.new("env-b")

class DatasourceA(Datasource):
    scheduling_strategies = (
        MustRunInGivenExecutionEnvironmentStrategy(ENV_A),  # Must run from A
    )

class DatasourceB(Datasource):
    scheduling_strategies = (
        MustRunInGivenExecutionEnvironmentStrategy(ENV_B),  # Must run from B
    )

# This check uses both datasources - impossible to satisfy both!
# Raises InvalidCheckConfiguration at startup.
```

**Impossible current==target with disjoint sets:**

```python title="Illustrative example" { "validate": false }
from watchpost import Datasource, EnvironmentRegistry
from watchpost.scheduling_strategy import (
    MustRunInTargetEnvironmentStrategy,
    MustRunInGivenExecutionEnvironmentStrategy,
    MustRunAgainstGivenTargetEnvironmentStrategy,
)

ENVIRONMENTS = EnvironmentRegistry()
MONITORING = ENVIRONMENTS.new("monitoring")
PROD = ENVIRONMENTS.new("prod")

class ImpossibleDatasource(Datasource):
    scheduling_strategies = (
        MustRunInTargetEnvironmentStrategy(),  # Requires execution == target
        MustRunInGivenExecutionEnvironmentStrategy(MONITORING),  # Must run from MONITORING
        MustRunAgainstGivenTargetEnvironmentStrategy(PROD),  # Must target PROD
    )
    # Impossible: can't have execution == target when execution must be MONITORING
    # and target must be PROD (they're different!)
```

## Custom Strategies

Implement the `SchedulingStrategy` protocol to create custom strategies:

```python title="Illustrative example"
from watchpost.scheduling_strategy import SchedulingStrategy, SchedulingDecision
from watchpost.check import Check
from watchpost import Environment

class MaintenanceWindowStrategy(SchedulingStrategy):
    """Skip checks during maintenance windows."""

    def __init__(self, maintenance_checker):
        self.maintenance_checker = maintenance_checker

    def schedule(
        self,
        check: Check,
        current_execution_environment: Environment,
        target_environment: Environment,
    ) -> SchedulingDecision:
        if self.maintenance_checker.is_in_maintenance(target_environment):
            return SchedulingDecision.SKIP  # (1)
        return SchedulingDecision.SCHEDULE
```

1. During maintenance, return cached results instead of running checks.

### Protocol Requirements

Your strategy must implement the `schedule` method with this signature:

```python title="Protocol signature" { "validate": false }
def schedule(
    self,
    check: Check,
    current_execution_environment: Environment,
    target_environment: Environment,
) -> SchedulingDecision:
    ...
```

Parameters:

- `check`: The check definition being evaluated
- `current_execution_environment`: Where Watchpost is running
- `target_environment`: What the check is monitoring

Return one of the three `SchedulingDecision` values, or raise `InvalidCheckConfiguration` if the configuration is invalid.

## CLI Commands

### verify-check-configuration

Validates that all checks can be scheduled somewhere:

```console
$ watchpost --app myapp:app verify-check-configuration
All checks verified successfully.
```

If conflicts exist:

```console
$ watchpost --app myapp:app verify-check-configuration
Error: Invalid check configuration detected

Check: myapp.checks.api_health
Reason: Conflicting execution-environment constraints: no common execution
environment across MustRunInGivenExecutionEnvironment strategies.
```

Run this command in CI/CD to catch configuration errors early.

## Checkmk Integration

Scheduling strategies affect what Checkmk sees:

- **SCHEDULE**: Normal results appear in Checkmk
- **SKIP**: Cached results (possibly stale) appear, or UNKNOWN if no cache
- **DONT_SCHEDULE**: No results from this execution environment; the check effectively doesn't exist from Checkmk's perspective on this node

When running multiple Watchpost instances across environments, each instance reports only the checks it's allowed to run. Checkmk aggregates results from all instances.

## Next Steps

- Learn about [Hostname Resolution](hostname-resolution.md) for routing results to Checkmk hosts
- Explore [Caching](caching.md) to understand how `SKIP` decisions interact with cached results
- See [Error Handlers](error-handlers.md) for handling check failures
