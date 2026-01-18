# Getting Started

This guide walks you through creating your first Watchpost application and running a simple check. You'll also learn how to integrate your Watchpost application with Checkmk.

This guide uses the [uv package manager](https://docs.astral.sh/uv/) for commands and examples.
You can use pip or another tool if you prefer.
If you need to install uv, see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

## Installation

Install Watchpost in your project:

```bash
pip install 'watchpost[cli]'
```

Or with uv:

```bash
uv add 'watchpost[cli]'
```

We recommend installing Watchpost with the `cli` extra. It adds a `watchpost` command that makes it easy to list and run checks during development.

## Project Setup

Let's build a complete example that monitors whether example.com is reachable.

### 1. Initialize a new Python project

```bash
uv init --app --package ./my-watchpost
cd my-watchpost
```

You can omit `--package` if you prefer a single-file project. Using a package layout is recommended because it keeps your application organized as it grows.

### 2. Add Watchpost to your project

```console
$ uv add 'watchpost[cli]'
Using CPython 3.13.5
Creating virtual environment at: .venv
Resolved 12 packages in 266ms
      Built my-watchpost @ file:///tmp/my-watchpost
Prepared 6 packages in 166ms
Installed 11 packages in 25ms
 + anyio==4.12.1
 + click==8.3.1
 + idna==3.11
 + markdown-it-py==4.0.0
 + mdurl==0.1.2
 + my-watchpost==0.1.0 (from file:///tmp/my-watchpost)
 + pygments==2.19.2
 + rich==14.2.0
 + starlette==0.47.3
 + timelength==3.0.2
 + watchpost==0.1.0
```

### 3. Create environments

Edit `my-watchpost/src/my_watchpost/__init__.py` to create a minimal application:

```python
from watchpost import EnvironmentRegistry, Watchpost

ENVIRONMENTS = EnvironmentRegistry()
PRODUCTION = ENVIRONMENTS.new("production")

app = Watchpost(
    checks=[],
    execution_environment=PRODUCTION,
)
```

The `EnvironmentRegistry` provides a centralized place to define all environments. Each environment has a name (like `"production"`) that identifies what the check is monitoring.

The `execution_environment` tells Watchpost where this application instance is running.

### 4. Verify the app starts

```console
$ uv run watchpost --app my_watchpost:app list-checks  # (1)
$ uv run watchpost --app my_watchpost:app run-checks
            Check Execution Results
┏━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ State ┃ Environment ┃ Service Name ┃ Summary ┃
┡━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━┩
└───────┴─────────────┴──────────────┴─────────┘
```

1. There is no output because the application does not define any checks yet.

### 5. Add an HTTP client dependency

We'll use httpx and its `AsyncClient` to demonstrate async checks, but you can use any HTTP client (async or sync) for your own checks.

```console
$ uv add httpx
Resolved 17 packages in 658ms
      Built my-watchpost @ file:///tmp/my-watchpost
Prepared 1 package in 11ms
Uninstalled 1 package in 8ms
Installed 5 packages in 28ms
 + certifi==2025.8.3
 + h11==0.16.0
 + httpcore==1.0.9
 + httpx==0.28.1
 ~ my-watchpost==0.1.0 (from file:///tmp/my-watchpost)
```

### 6. Create a datasource

Edit `my-watchpost/src/my_watchpost/__init__.py` to add a datasource that provides HTTP clients:

```python hl_lines="1-2 4 7-13"
from contextlib import asynccontextmanager

import httpx

from watchpost import Datasource, EnvironmentRegistry, Watchpost

class HttpxClientFactory(Datasource):  # (1)
    scheduling_strategies = ()

    @asynccontextmanager
    async def client(self):  # (2)
        async with httpx.AsyncClient() as client:
            yield client

ENVIRONMENTS = EnvironmentRegistry()
PRODUCTION = ENVIRONMENTS.new("production")

app = Watchpost(
    checks=[],
    execution_environment=PRODUCTION,
)
app.register_datasource(HttpxClientFactory)  # (3)
```

1. Datasources inherit from `Datasource` base class. The `scheduling_strategies` tuple can contain strategies that control where this datasource can run (we'll leave it empty for now).
2. This async context manager provides a configured HTTP client to checks. Using a datasource instead of creating the client directly inside checks makes your code more testable and reusable.
3. Register the datasource with the application so Watchpost can inject it into checks.

### 7. Create a check

Now add a check that verifies example.com returns HTTP 200:

```py title="Illustrative example" hl_lines="6 15-28 33"
from contextlib import asynccontextmanager

import httpx

from watchpost import (
    check,
    crit,
    Datasource,
    EnvironmentRegistry,
    ok,
    Watchpost,
)

# ... (HttpxClientFactory class here)
@check(  # (1)
    name="example.com HTTP status",
    service_labels={},
    environments=[PRODUCTION],
    cache_for="5m",
)
async def example_com_http_status(
    client_factory: HttpxClientFactory,  # (2)
):
    async with client_factory.client() as client:
        response = await client.get("https://www.example.com")

    if response.status_code != 200:
        return crit(  # (3)
            "example.com returned an error",
            details=(
                f"Expected status: 200\n"
                f"Actual status: {response.status_code}\n"
                f"Response: {response.text}"
            ),
        )

    return ok("example.com is up")  # (4)

# ... (ENVIRONMENTS and PRODUCTION here)

app = Watchpost(
    checks=[
        example_com_http_status,  # (5)
    ],
    execution_environment=PRODUCTION,
)
app.register_datasource(HttpxClientFactory)
```

1. The `@check` decorator defines a monitoring check with:
    - `name`: The service name that appears in Checkmk
    - `service_labels`: Optional labels attached to the Checkmk service
    - `environments`: List of target environments this check monitors
    - `cache_for`: How long to cache results before running the check again

2. To use a datasource in a check, add a parameter annotated with the datasource type. Watchpost injects the instance automatically when the check runs.

3. If the check fails, return `crit(...)` (critical state). The details will be shown in the Checkmk service to help troubleshooting. You can also return `warn(...)` for warnings or `unknown(...)` for unknown states.

4. If everything is fine, return `ok(...)` (OK state).

5. Register the check with the application.

## Running the Check

Run your check using the `watchpost` CLI:

```console
$ uv run watchpost --app my_watchpost:app list-checks
my_watchpost.example_com_http_status(client_factory: my_watchpost.HttpxClientFactory)

$ uv run watchpost --app my_watchpost:app run-checks
                       Check Execution Results
┏━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ State ┃ Environment ┃ Service Name            ┃ Summary           ┃
┡━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│  OK   │ production  │ example.com HTTP status │ example.com is up │
└───────┴─────────────┴─────────────────────────┴───────────────────┘
```

The output shows:

- **State**: The check result state (OK, WARN, CRIT, or UNKNOWN)
- **Environment**: The target environment being monitored
- **Service Name**: The name from the `@check` decorator
- **Summary**: The message from `ok()`, `warn()`, `crit()`, or `unknown()`

The CLI also supports filtering:

```bash
# Run only checks whose name starts with "example"
watchpost --app my_watchpost:app run-checks --filter-prefix example

# Run only checks whose name contains "HTTP"
watchpost --app my_watchpost:app run-checks --filter-contains HTTP
```

## Running as HTTP Server

The Checkmk integration retrieves check results via HTTP. Watchpost is a valid ASGI application that you can run with any ASGI server.

### Using uvicorn

Install uvicorn:

```bash
pip install uvicorn
# or
uv add --dev uvicorn
```

Run your application:

```console
$ uvicorn my_watchpost:app
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Access the root endpoint to see Checkmk-compatible output:

```bash
curl http://127.0.0.1:8000/
```

The response is in [Checkmk piggyback format](https://docs.checkmk.com/latest/en/piggyback.html):

```
<<<<production-example.com HTTP status>>>>
<<<local:sep(0)>>>
0 example.com HTTP status - example.com is up
<<<<>>>>
```

### HTTP Endpoints

Watchpost provides several endpoints:

- **`/`**: Main endpoint that streams Checkmk agent output (piggyback format)
- **`/healthcheck`**: Returns HTTP 204 for load balancer health checks
- **`/executor/statistics`**: JSON statistics about check execution
- **`/executor/errored`**: JSON details of failed check executions

## Checkmk Integration

To integrate your Watchpost application with Checkmk, you need to configure Checkmk to fetch check results from the HTTP endpoint.

The integration details depend on your Checkmk setup. See the [Checkmk Integration guide](deployment/checkmk-integration.md) for detailed instructions on:

- Installing the Watchpost special agent for Checkmk
- Configuring Checkmk to poll your Watchpost application
- Service discovery and labeling
- Troubleshooting common issues

## Next Steps

You've now built your first Watchpost application and check! Here's what to explore next:

**Fundamentals:**

- [Environments](fundamentals/environments.md) - Learn about execution vs. target environments
- [Datasources](fundamentals/datasources.md) - Understand dependency injection and factory patterns
- [Checks](fundamentals/checks.md) - Explore all check configuration options
- [Results](fundamentals/results.md) - Learn about metrics, multiple results, and the builder pattern

**Advanced Features:**

- [Scheduling Strategies](advanced/scheduling-strategies.md) - Control where and when checks run
- [Caching](advanced/caching.md) - Configure caching backends and strategies
- [Hostname Resolution](advanced/hostname-resolution.md) - Customize how hostnames are determined

**Patterns and Recipes:**

- [Cookbook](cookbook/project-organization.md) - Real-world patterns for organizing your checks
