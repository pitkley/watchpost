# Datasources

Datasources encapsulate external dependencies that your checks need to access. They provide a clean abstraction layer between your monitoring logic and the systems you're monitoring, while also encapsulating compatibility requirements such as scheduling strategies that control where checks using this datasource can run.

## The Datasource Base Class

Create a datasource by subclassing `Datasource`:

```python
from watchpost import Datasource

class ApiDatasource(Datasource):
    scheduling_strategies = ()  # (1)

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def get_health(self) -> dict:
        # Implementation to call the API
        ...
```

1. The `scheduling_strategies` attribute is required. Use an empty tuple `()` for no constraints, or specify strategies that control where checks using this datasource can run. See [Scheduling Strategies](../advanced/scheduling-strategies.md).

## Registering Datasources

Register datasources with your Watchpost application using `register_datasource`:

```python title="Illustrative example"
from watchpost import Datasource, Watchpost, EnvironmentRegistry

class ApiDatasource(Datasource):
    scheduling_strategies = ()

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

ENVIRONMENTS = EnvironmentRegistry()
PROD = ENVIRONMENTS.new("prod")

app = Watchpost(
    checks=[...],
    execution_environment=PROD,
)

# Register with constructor arguments
app.register_datasource(
    ApiDatasource,
    base_url="https://api.example.com",
    api_key="secret-key",
)
```

Watchpost instantiates the datasource when checks need it, passing the provided arguments to the constructor.

## Dependency Injection

Checks declare datasource dependencies via type-annotated parameters. Watchpost automatically injects the appropriate instance:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Datasource, Environment #! hidden
PROD = Environment("prod") #! hidden

class ApiDatasource(Datasource):
    scheduling_strategies = ()
    # ... #! hidden

class DatabaseDatasource(Datasource):
    scheduling_strategies = ()
    # ... #! hidden

@check(
    name="Full System Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
async def full_system_check(
    api: ApiDatasource,        # (1)
    db: DatabaseDatasource,    # (2)
):
    api_status = await api.get_health()
    db_status = await db.get_status()
    return ok("All systems operational")
```

1. Watchpost sees the `ApiDatasource` type hint and injects a registered instance.
2. Multiple datasources can be injected into a single check.

### Environment Parameter

Checks can also receive the current target environment:

```python title="Illustrative example"
from watchpost import check, ok, Environment
PROD = Environment("prod") #! hidden
STAGING = Environment("staging") #! hidden
from watchpost import Datasource #! hidden
class ApiDatasource(Datasource): #! hidden
    scheduling_strategies = () #! hidden

@check(
    name="Environment-Aware Check",
    service_labels={},
    environments=[PROD, STAGING],
    cache_for="5m",
)
async def env_check(
    environment: Environment,  # (1)
    api: ApiDatasource,
):
    # Use environment to customize behavior
    return ok(f"Check passed for {environment.name}")
```

1. Add `environment: Environment` to receive the current target environment.

## DatasourceUnavailable Exception

When a datasource cannot connect to its external system, raise `DatasourceUnavailable`:

```python title="Illustrative example"
import httpx

from watchpost import Datasource, DatasourceUnavailable


class ApiDatasource(Datasource):
    scheduling_strategies = ()

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def get_health(self) -> dict:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/health",
                    timeout=10.0,
                )
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as e:
            raise DatasourceUnavailable(
                f"API request timed out: {e}"
            ) from e
        except httpx.ConnectError as e:
            raise DatasourceUnavailable(
                f"Cannot connect to API: {e}"
            ) from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise DatasourceUnavailable(
                    f"API server error: {e}"
                ) from e
            raise  # Re-raise 4xx errors as check failures
```

When `DatasourceUnavailable` is raised:

- The check result becomes `UNKNOWN` state
- The exception message appears in the service details
- Cached results (if available) may be used as fallback

This distinguishes "the check ran and found a problem" from "the check couldn't run because the system is unreachable."

## Datasource Factories

For scenarios where you need the same datasource type with different configurations, use the factory pattern.

### The DatasourceFactory Protocol

A factory is any class with a `new` class method that returns a `Datasource`:

```python title="Illustrative example"
from watchpost import Datasource, DatasourceFactory


class ApiDatasource(Datasource):
    scheduling_strategies = ()

    def __init__(self, base_url: str):
        self.base_url = base_url


class ApiFactory(DatasourceFactory):
    scheduling_strategies = ()  # (1)

    @classmethod
    def new(cls, service_name: str) -> ApiDatasource:  # (2)
        return ApiDatasource(
            base_url=f"https://{service_name}.api.example.com"
        )
```

1. Factories can also declare scheduling strategies. If a datasource leaves its `scheduling_strategies` as `...` (ellipsis), the factory's strategies are used.
2. The `new` method receives arguments specified in `FromFactory` annotations.

### Using FromFactory

Use `Annotated` with `FromFactory` to request a factory-created datasource:

```python title="Illustrative example"
from typing import Annotated

from watchpost import check, ok, FromFactory
from watchpost import Environment, Datasource, DatasourceFactory #! hidden
PROD = Environment("prod") #! hidden

class ApiDatasource(Datasource):
    scheduling_strategies = ()
    def __init__(self, base_url: str): #! hidden
        self.base_url = base_url #! hidden

class ApiFactory(DatasourceFactory):
    scheduling_strategies = ()
    @classmethod #! hidden
    def new(cls, service_name: str) -> ApiDatasource: #! hidden
        return ApiDatasource( #! hidden
            base_url=f"https://{service_name}.api.example.com" #! hidden
        ) #! hidden

@check(
    name="Auth API Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
async def auth_api_check(
    api: Annotated[ApiDatasource, FromFactory(ApiFactory, "auth")],  # (1)
):
    ...
    return ok("OK") #! hidden

@check(
    name="Users API Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
async def users_api_check(
    api: Annotated[ApiDatasource, FromFactory(ApiFactory, "users")],  # (2)
):
    ...
    return ok("OK") #! hidden
```

1. Creates an `ApiDatasource` for `https://auth.api.example.com`
2. Creates a different `ApiDatasource` for `https://users.api.example.com`

### Registering Factories

Register factories with `register_datasource_factory`:

```python title="Illustrative example"
from watchpost import Watchpost, EnvironmentRegistry
from watchpost import DatasourceFactory, Datasource #! hidden
PROD = EnvironmentRegistry().new("prod") #! hidden
class ApiDatasource(Datasource): #! hidden
    scheduling_strategies = () #! hidden
class ApiFactory(DatasourceFactory): #! hidden
    scheduling_strategies = () #! hidden

app = Watchpost(
    checks=[...],
    execution_environment=PROD,
)

app.register_datasource_factory(ApiFactory)
```

### Combined Datasource and Factory

A common pattern is to have a class that acts as both datasource and factory. This example wraps AWS boto3 clients:

```python title="Illustrative example"
import sys #! hidden
from types import ModuleType #! hidden
from typing import Any #! hidden
class FakeBoto3: #! hidden
    @staticmethod #! hidden
    def client(service_name: str, region_name: str) -> Any: #! hidden
        class FakeClient: #! hidden
            def describe_instances(self) -> dict: #! hidden
                return {"Reservations": []} #! hidden
        return FakeClient() #! hidden
sys.modules["boto3"] = FakeBoto3()  # type: ignore #! hidden
from typing import Annotated

import boto3

from watchpost import Datasource, DatasourceFactory, Watchpost, check, ok, FromFactory, EnvironmentRegistry

ENVIRONMENTS = EnvironmentRegistry() #! hidden
PROD = ENVIRONMENTS.new("prod") #! hidden


class Boto3Client(Datasource, DatasourceFactory):
    scheduling_strategies = ()

    def __init__(self, service_name: str, region_name: str):
        self._client = boto3.client(service_name, region_name=region_name)

    @classmethod
    def new(cls, service: str) -> "Boto3Client":  # (1)
        return cls(service, "eu-central-1")

    def __getattr__(self, name: str):  # (2)
        return getattr(self._client, name)


@check(
    name="EC2 Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def ec2_check(
    ec2: Annotated[Boto3Client, FromFactory("ec2")],  # (3)
):
    response = ec2.describe_instances()
    instances = response["Reservations"]
    yield ok(f"Found {len(instances)} reservations")


app = Watchpost(
    checks=[ec2_check],
    execution_environment=PROD,
)
app.register_datasource_factory(Boto3Client)  # (4)
```

1. The class implements both `Datasource` and `DatasourceFactory`.
2. Delegate all attribute access to the underlying boto3 client.
3. When the factory type is omitted from `FromFactory`, Watchpost infers it from the parameter type.
4. Register the class as a factory since checks use `FromFactory`.

## Scheduling Strategies on Datasources

Datasources can declare scheduling strategies that affect all checks using them:

```python title="Illustrative example"
from watchpost import Datasource
from watchpost.scheduling_strategy import MustRunInTargetEnvironmentStrategy


class KubernetesClientDatasource(Datasource):
    scheduling_strategies = (
        MustRunInTargetEnvironmentStrategy(),  # (1)
    )

    def __init__(self):
        # Uses in-cluster config, only works inside the cluster
        ...
```

1. This strategy ensures checks using this datasource only run when the execution environment matches the target environment.

When a check uses multiple datasources, strategies from all datasources are combined. The "strictest" decision wins. See [Scheduling Strategies](../advanced/scheduling-strategies.md) for details.

### Strategy Inheritance from Factories

If a datasource sets `scheduling_strategies` to `None`, it inherits strategies from its factory:

```python title="Illustrative example"
from watchpost import Datasource, DatasourceFactory
from watchpost.scheduling_strategy import MustRunInGivenExecutionEnvironmentStrategy
from watchpost import Environment #! hidden
MONITORING = Environment("monitoring") #! hidden


class ApiDatasource(Datasource):
    scheduling_strategies = None  # (1)

    def __init__(self, base_url: str):
        self.base_url = base_url


class ApiFactory(DatasourceFactory):
    scheduling_strategies = (
        MustRunInGivenExecutionEnvironmentStrategy(MONITORING),  # (2)
    )

    @classmethod
    def new(cls, service: str) -> ApiDatasource:
        return ApiDatasource(f"https://{service}.api.example.com")
```

1. `None` means "inherit from factory if applicable."
2. All datasources created by this factory will have this strategy.

## Checkmk Integration

- **UNKNOWN state**: When `DatasourceUnavailable` is raised, the check results in an UNKNOWN service state in Checkmk, indicating the check couldn't determine the actual status.
- **Error details**: The exception message appears in the service details, helping operators diagnose connection issues.

## Next Steps

- Learn about [Checks](checks.md) and all their configuration options
- Understand [Results](results.md) and how to construct them
- Explore [Scheduling Strategies](../advanced/scheduling-strategies.md) for advanced execution control
