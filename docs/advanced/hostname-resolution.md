# Hostname Resolution

Hostnames determine which Checkmk host receives your check results. Watchpost uses the Checkmk piggyback format, where each result is tagged with a hostname that Checkmk uses to associate the service with a host.

## Why Hostnames Matter

In Checkmk's piggyback format, every service must be associated with a host. The hostname you specify determines:

- Which Checkmk host displays the service
- How alerts are routed
- Which host groups and folders the service belongs to

Without proper hostname configuration, your services may end up on unexpected hosts or fail to appear at all.

## The Hostname Hierarchy

Watchpost resolves hostnames in this order, using the first match:

1. **Result-level**: `CheckResult.hostname` or `alternative_hostname` parameter
2. **Check-level**: `@check(hostname=...)` decorator parameter
3. **Environment-level**: `Environment(hostname=...)` constructor parameter
4. **Application-level**: `Watchpost(hostname=...)` constructor parameter
5. **Default fallback**: `"{service_name}-{environment.name}"` (if enabled)

```python title="Illustrative example"
from watchpost import Watchpost, Environment, EnvironmentRegistry, check, ok

ENVIRONMENTS = EnvironmentRegistry()
PROD = ENVIRONMENTS.new(
    "prod",
    hostname="prod-services",  # (3) Environment-level
)

@check(
    name="API Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    hostname="api-host",  # (2) Check-level
)
def api_health():
    return ok(
        "API healthy",
        alternative_hostname="api-primary",  # (1) Result-level (wins!)
    )

app = Watchpost(
    checks=[api_health],
    execution_environment=PROD,
    hostname="default-host",  # (4) App-level
)
```

1. Result-level wins: service appears on `api-primary`.
2. Would be used if result didn't specify a hostname.
3. Would be used if check didn't specify a hostname.
4. Would be used if environment didn't specify a hostname.

## Hostname Strategies

Watchpost accepts several types of hostname inputs:

### Static Strings

The simplest form - a literal hostname string:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="API Health",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    hostname="api-services",  # (1)
)
def api_health():
    return ok("OK")
```

1. All results from this check go to the host `api-services`.

### Template Strings

Use placeholders to build hostnames dynamically:

```python title="Illustrative example"
from watchpost import check, ok, EnvironmentRegistry

ENVIRONMENTS = EnvironmentRegistry()
PROD = ENVIRONMENTS.new("prod")
STAGING = ENVIRONMENTS.new("staging")

@check(
    name="API Health",
    service_labels={},
    environments=[PROD, STAGING],
    cache_for="5m",
    hostname="{service_name}-{environment.name}",  # (1)
)
def api_health():
    return ok("OK")
```

1. Produces `API Health-prod` for PROD and `API Health-staging` for STAGING.

Available template variables:

| Variable | Description |
|----------|-------------|
| `{service_name}` | The check's service name |
| `{environment.name}` | The target environment's name |
| `{check.*}` | Any attribute of the Check object |

### Callable Strategies

For complex logic, pass a function that receives a `HostnameContext`:

```python title="Illustrative example"
from watchpost import check, ok, EnvironmentRegistry
from watchpost.hostname import HostnameContext

ENVIRONMENTS = EnvironmentRegistry()
PROD = ENVIRONMENTS.new("prod")
STAGING = ENVIRONMENTS.new("staging")

def environment_hostname(ctx: HostnameContext) -> str | None:
    """Route to different hosts based on environment."""
    hostname_map = {
        "prod": "production-services",
        "staging": "staging-services",
    }
    return hostname_map.get(ctx.environment.name)

@check(
    name="API Health",
    service_labels={},
    environments=[PROD, STAGING],
    cache_for="5m",
    hostname=environment_hostname,  # (1)
)
def api_health():
    return ok("OK")
```

1. Function is called for each result to determine hostname.

The `HostnameContext` provides:

```python title="HostnameContext fields" { "validate": false }
@dataclass
class HostnameContext:
    check: Check              # The check being executed
    environment: Environment  # Target environment
    service_name: str         # Check's service name
    service_labels: dict      # Check's service labels
    result: CheckResult | None  # The result (if available)
```

Returning `None` from a callable allows fallback to the next level in the hierarchy.

### HostnameStrategy Classes

For reusable strategies, implement the `HostnameStrategy` protocol:

```python title="Illustrative example"
from watchpost.hostname import HostnameStrategy, HostnameContext

class LabelBasedHostname(HostnameStrategy):
    """Use a service label as the hostname."""

    def __init__(self, label_key: str, default: str | None = None):
        self.label_key = label_key
        self.default = default

    def resolve(self, ctx: HostnameContext) -> str | None:
        return ctx.service_labels.get(self.label_key, self.default)
```

Built-in strategy classes:

| Class | Description |
|-------|-------------|
| `StaticHostnameStrategy` | Always returns a fixed hostname |
| `TemplateStrategy` | Formats a template string with context |
| `FunctionStrategy` | Wraps a callable |
| `CompositeStrategy` | Tries multiple strategies in order |
| `CoercingStrategy` | Wraps another strategy and coerces to RFC1123 |
| `NoPiggybackHostStrategy` | Explicitly disables piggyback (results go to local host) |

### Disabling Piggyback

To have results appear on the host where Watchpost runs (no piggyback), use `NoPiggybackHostStrategy`:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.hostname import NoPiggybackHostStrategy
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Local Check",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    hostname=NoPiggybackHostStrategy(),  # (1)
)
def local_check():
    return ok("OK")
```

1. Results appear on the Checkmk host where the agent runs, not a piggyback host.

## Result-Level Hostname Override

For checks that produce multiple results targeting different hosts, use `alternative_hostname` on each result:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Multi-Host Status",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
    hostname="default-host",  # (1)
)
def multi_host_status():
    hosts = ["web-01", "web-02", "web-03"]

    for host in hosts:
        yield ok(
            f"Host {host} healthy",
            name_suffix=f" - {host}",
            alternative_hostname=host,  # (2)
        )
```

1. Fallback hostname if result doesn't specify one.
2. Each result goes to its own Checkmk host.

## RFC1123 Compliance

Hostnames must conform to RFC1123:

- Only ASCII letters (`a-z`, `A-Z`), digits (`0-9`), hyphens (`-`), and dots (`.`)
- Labels (parts between dots) must be 1-63 characters
- Labels must start and end with an alphanumeric character
- Total length max 253 characters

### Automatic Coercion

By default, Watchpost coerces invalid hostnames to be RFC1123-compliant:

```python title="Illustrative example"
from watchpost.hostname import coerce_to_rfc1123

# Invalid characters replaced with hyphens
coerce_to_rfc1123("My Service / Prod")  # Returns: "my-service-prod"

# Unicode normalized to ASCII
coerce_to_rfc1123("service-caf\u00e9")  # Returns: "service-cafe"

# Leading/trailing hyphens stripped from labels
coerce_to_rfc1123("-my-service-")  # Returns: "my-service"
```

### Disabling Coercion

If you want strict validation (fail on invalid hostnames), disable coercion:

```python title="Illustrative example" { "validate": false }
from watchpost import Watchpost

app = Watchpost(
    checks=[...],
    execution_environment=PROD,
    hostname_coerce_into_valid_hostname=False,  # (1)
)
```

1. Raises `HostnameResolutionError` instead of coercing invalid hostnames.

## Disabling Default Fallback

By default, if no hostname is resolved, Watchpost generates `{service_name}-{environment.name}`. To disable this:

```python title="Illustrative example" { "validate": false }
from watchpost import Watchpost

app = Watchpost(
    checks=[...],
    execution_environment=PROD,
    hostname_fallback_to_default_hostname_generation=False,  # (1)
)
```

1. Raises `HostnameResolutionError` if no strategy produces a hostname.

## Common Patterns

### Per-Environment Hostnames

Route all checks for an environment to a single host:

```python title="Illustrative example"
from watchpost import EnvironmentRegistry

ENVIRONMENTS = EnvironmentRegistry()
DEV = ENVIRONMENTS.new("dev", hostname="dev-services")
STAGING = ENVIRONMENTS.new("staging", hostname="staging-services")
PROD = ENVIRONMENTS.new("prod", hostname="prod-services")
```

### Service-Based Hostnames

Route based on service labels:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost.hostname import HostnameContext
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

def service_hostname(ctx: HostnameContext) -> str | None:
    """Use the 'component' label as hostname, with fallback."""
    return ctx.service_labels.get("component", "misc-services")

@check(
    name="Database Pool",
    service_labels={"component": "database"},
    environments=[PROD],
    cache_for="5m",
    hostname=service_hostname,
)
def db_pool():
    return ok("Pool healthy")  # Goes to "database" host
```

### Result-Driven Hostnames

For multi-tenant or multi-region checks:

```python title="Illustrative example"
from watchpost import check, ok
from watchpost import Environment #! hidden
PROD = Environment("prod") #! hidden

@check(
    name="Customer API",
    service_labels={},
    environments=[PROD],
    cache_for="5m",
)
def customer_api():
    customers = ["acme", "globex", "initech"]

    for customer in customers:
        status = check_customer_api(customer)
        yield ok(
            f"API healthy",
            name_suffix=f" - {customer}",
            alternative_hostname=f"{customer}-services",  # (1)
        )
```

1. Each customer's result goes to their own Checkmk host.

## CLI Commands

### get-check-hostnames

List all resolved hostnames for your checks:

```console
$ watchpost --app myapp:app get-check-hostnames
Check: API Health
  Environment: prod → api-services
  Environment: staging → api-staging

Check: Database Pool
  Environment: prod → database
```

Useful for verifying hostname configuration before deployment.

## Checkmk Integration

### Piggyback Format

Watchpost generates output in Checkmk's piggyback format:

```
<<<<hostname>>>>
<<<local>>>
0 "Service Name" - Service is OK
<<<<>>>>
```

The `<<<<hostname>>>>` line tells Checkmk which host should receive the following service data.

### Host Requirements

For piggyback data to be processed:

1. The target host must exist in Checkmk (or auto-creation must be enabled)
2. The Checkmk agent processing the output must have piggyback enabled
3. The host must be in a folder that accepts piggyback data

### Troubleshooting

**Service not appearing:**

- Verify the hostname exists in Checkmk
- Check that hostname is RFC1123-compliant
- Run `get-check-hostnames` to see resolved hostnames
- Check Checkmk's piggyback data in `var/check_mk/piggyback/`

**Service on wrong host:**

- Check the hostname hierarchy (result → check → environment → app → default)
- Use explicit hostnames at the appropriate level
- Review callable strategies for logic errors

## Next Steps

- Learn about [Caching](caching.md) for result persistence
- Explore [Error Handlers](error-handlers.md) for handling failures in multi-host checks
- See [Scheduling Strategies](scheduling-strategies.md) for execution control
