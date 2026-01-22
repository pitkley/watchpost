# Environments

Environments are a core concept in Watchpost that represent logical contexts in which checks run. Understanding how to define and work with environments is essential for building effective monitoring solutions.

## The Environment Class

An `Environment` represents a logical deployment or monitoring context such as `dev`, `staging`, `prod`, or `monitoring`. Each environment has a name and can carry additional configuration.

```python
from watchpost import Environment

PROD = Environment("prod")
STAGING = Environment("staging")
DEV = Environment("dev")
```

### Environment Parameters

The `Environment` class accepts several optional parameters:

```python
from watchpost import Environment

PROD = Environment(
    "prod",
    hostname="prod-services",  # (1)
    region="eu-central-1",     # (2)
    cluster="main",
)
```

1. The `hostname` parameter sets a default hostname for checks running against this environment. See [Hostname Resolution](../advanced/hostname-resolution.md) for details.
2. Any additional keyword arguments become metadata accessible via `environment.metadata`.

### Accessing Metadata

Checks can access environment metadata to customize behavior:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod", region="eu-central-1") #! hidden
DEV = Environment("dev", region="us-west-2") #! hidden
MyDatasource = ... #! hidden

@check(
    name="Regional Check",
    service_labels={},
    environments=[PROD, DEV],
    cache_for="5m",
)
def regional_check(environment: Environment, ds: MyDatasource):
    region = environment.metadata.get("region", "unknown")
    # Use region-specific logic
    return ok(f"Check passed in {region}")
```

## EnvironmentRegistry

The `EnvironmentRegistry` provides a centralized way to manage environments. Using a registry helps prevent typos and ensures consistency across your application.

### Creating a Registry

```python
from watchpost import EnvironmentRegistry

ENVIRONMENTS = EnvironmentRegistry()

# Create and register environments in one step
DEV = ENVIRONMENTS.new("dev", hostname="dev-services")
STAGING = ENVIRONMENTS.new("staging", hostname="staging-services")
PROD = ENVIRONMENTS.new("prod", hostname="prod-services")
```

### Registry Operations

The registry supports dictionary-like access:

```python title="Illustrative example"
from watchpost import EnvironmentRegistry
registry = EnvironmentRegistry() #! hidden
DEV = registry.new("dev") #! hidden
STAGING = registry.new("staging") #! hidden
PROD = registry.new("prod") #! hidden

# Look up by name
prod_env = registry["prod"]

# Check if environment exists
if "staging" in registry:
    print("Staging environment is registered")

# Iterate over all environments
for env in registry:
    print(f"Environment: {env.name}")

# Get with default
test_env = registry.get("test", default=DEV)
```

### Adding Existing Environments

You can also add pre-created environments to a registry:

```python title="Illustrative example"
from watchpost import Environment, EnvironmentRegistry

MONITORING = Environment("monitoring", hostname="monitoring-host")

registry = EnvironmentRegistry()
registry.add(MONITORING)
```

## Execution vs Target Environments

Watchpost distinguishes between two types of environments:

- **Execution environment**: Where the Watchpost application is running
- **Target environment**: What a check is monitoring

### Setting the Execution Environment

When creating a Watchpost application, you specify where it runs:

```python title="Illustrative example"
from watchpost import EnvironmentRegistry, Watchpost

ENVIRONMENTS = EnvironmentRegistry()
MONITORING = ENVIRONMENTS.new("monitoring")
PROD = ENVIRONMENTS.new("prod")
STAGING = ENVIRONMENTS.new("staging")

# This app runs in the monitoring environment
app = Watchpost(
    checks=[...],
    execution_environment=MONITORING,  # (1)
)
```

1. The `execution_environment` tells Watchpost where this application instance is running. This affects scheduling decisions.

### Setting Target Environments

Checks declare which environments they monitor:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden
STAGING = Environment("staging") #! hidden
DEV = Environment("dev") #! hidden
MyDatasource = ... #! hidden

@check(
    name="API Health",
    service_labels={},
    environments=[PROD, STAGING, DEV],  # (1)
    cache_for="5m",
)
def api_health_check(ds: MyDatasource):
    return ok("API is healthy")
```

1. This check monitors three environments. Watchpost runs the check once per target environment.

### Common Patterns

**Central Monitoring**

A single Watchpost instance monitors multiple environments from a dedicated monitoring server:

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

```python title="Illustrative example"
from watchpost import EnvironmentRegistry, Watchpost

ENVIRONMENTS = EnvironmentRegistry()
MONITORING = ENVIRONMENTS.new("monitoring")
DEV = ENVIRONMENTS.new("dev", hostname="dev-services")
STAGING = ENVIRONMENTS.new("staging", hostname="staging-services")
PROD = ENVIRONMENTS.new("prod", hostname="prod-services")

app = Watchpost(
    checks=[...],
    execution_environment=MONITORING,
)
```

**In-Cluster Monitoring**

The Watchpost instance runs inside the same environment it monitors. This is common for Kubernetes deployments where checks need cluster-internal access:

```
┌─────────────────────────────────────────────┐
│ Kubernetes Cluster: prod                     │
│                                             │
│  Watchpost Pod (execution_environment=prod) │
│  └─ Check: Pod Health → targets: [prod]     │
│                                             │
└─────────────────────────────────────────────┘
```

```python title="Illustrative example"
from watchpost import EnvironmentRegistry, Watchpost

ENVIRONMENTS = EnvironmentRegistry()
PROD = ENVIRONMENTS.new("prod", hostname="prod-services")

# Running inside the prod cluster
app = Watchpost(
    checks=[...],
    execution_environment=PROD,  # Same as target
)
```

For checks that must run inside their target environment, see [MustRunInTargetEnvironmentStrategy](../advanced/scheduling-strategies.md).

**Multi-Region Deployment**

Each region runs its own Watchpost instance, monitoring only that region:

```python title="Illustrative example"
import os
from watchpost import EnvironmentRegistry, Watchpost

ENVIRONMENTS = EnvironmentRegistry()
EU = ENVIRONMENTS.new("eu", hostname="eu-services", region="eu-central-1")
US = ENVIRONMENTS.new("us", hostname="us-services", region="us-east-1")
ASIA = ENVIRONMENTS.new("asia", hostname="asia-services", region="ap-southeast-1")

# Determine which environment we're in from config
current_region = os.environ.get("REGION", "eu")
execution_env = ENVIRONMENTS[current_region]

app = Watchpost(
    checks=[...],
    execution_environment=execution_env,
)
```

## Environments in Checks

Checks can optionally receive the current target environment as a parameter:

```python title="Illustrative example"
from watchpost import check, ok, warn, Environment
PROD = Environment("prod") #! hidden
STAGING = Environment("staging") #! hidden
DEV = Environment("dev") #! hidden
MyDatasource = ... #! hidden

@check(
    name="Threshold Check",
    service_labels={},
    environments=[PROD, STAGING, DEV],
    cache_for="5m",
)
def threshold_check(environment: Environment, ds: MyDatasource):  # (1)
    # Different thresholds per environment
    thresholds = {
        "prod": 100,
        "staging": 200,
        "dev": 500,
    }
    threshold = thresholds.get(environment.name, 100)

    value = ds.get_metric()
    if value > threshold:
        return warn(f"Value {value} exceeds threshold {threshold}")
    return ok(f"Value {value} within threshold")
```

1. Add `environment: Environment` as a parameter to receive the current target environment. Watchpost injects it automatically.

## Checkmk Integration

Environments affect how services appear in Checkmk:

- **Hostname resolution**: Environment-level hostnames become the default piggyback host for services
- **Multiple services**: A check targeting multiple environments creates separate Checkmk services (one per environment × hostname combination)

For example, a check targeting `[PROD, STAGING]` with environment-level hostnames produces:

- Service "API Health" on host `prod-services`
- Service "API Health" on host `staging-services`

See [Hostname Resolution](../advanced/hostname-resolution.md) for the full hostname hierarchy.

## Next Steps

- Learn about [Datasources](datasources.md) for connecting to external systems
- Understand [Scheduling Strategies](../advanced/scheduling-strategies.md) for controlling check execution
- Explore [Hostname Resolution](../advanced/hostname-resolution.md) for customizing service placement
