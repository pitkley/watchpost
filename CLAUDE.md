# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Watchpost is a Python framework for writing monitoring checks as code and integrating them with Checkmk. It provides a decorator-based API for defining checks, manages execution across multiple environments, handles datasource dependencies, and generates Checkmk-compatible output.

## Development Commands

### Setup

```bash
# Install uv (package manager)
# Install dependencies and setup hooks
uv run lefthook install
```

### Testing

```bash
# Run all tests
uv run pytest

# Run tests excluding Docker-dependent tests
uv run pytest -m "not docker"

# Run a specific test file
uv run pytest tests/test_app.py

# Run a specific test function
uv run pytest tests/test_app.py::test_function_name
```

### Linting and Type Checking

```bash
# Run all linting checks
uv run ruff check .
uv run ruff format --diff  # Check formatting without modifying
uv run ruff format         # Auto-format code
uv run ty check            # Type checking with ty
uv run mypy examples/ src/ # Type checking with mypy
```

### Running Examples

```bash
# Run the basic example via its entrypoint
cd examples/basic && uv run example

# Run via watchpost CLI
cd examples/basic && uv run watchpost --app basic:app run-checks
```

### Documentation

```bash
# Build documentation locally
uv run --all-extras --all-groups mkdocs build --strict

# Serve documentation with live reload
uv run --all-extras --all-groups mkdocs serve
```

### License Management

```bash
# Check that all dependencies use allowed licenses
.tools/check-third-party-licenses.sh

# Regenerate THIRD_PARTY_LICENSES.md
.tools/show-third-party-licenses.sh > ./THIRD_PARTY_LICENSES.md
```

## Architecture

### Core Execution Flow

1. **Watchpost Application** (`src/watchpost/app.py`): The main ASGI application that:
   - Discovers checks from decorated functions or Python modules
   - Manages datasource registration and instantiation via `_InstantiableDatasource` wrappers
   - Resolves scheduling strategies from checks, datasources, and application defaults
   - Coordinates check execution through `CheckExecutor`
   - Generates Checkmk-compatible output (piggyback format)

2. **Check Decorator** (`src/watchpost/check.py`): The `@check` decorator defines monitoring checks with:
   - Service name and labels (maps to Checkmk services)
   - Target environments where the check runs
   - Cache duration (`cache_for`)
   - Optional hostname strategies and scheduling strategies
   - Support for both sync and async check functions

3. **Executor** (`src/watchpost/executor.py`): A key-aware, non-blocking execution engine:
   - Wraps `ThreadPoolExecutor` for synchronous checks
   - Runs async checks on a background asyncio event loop via `AsyncioLoopThread`
   - Deduplicates work per key (check name + environment)
   - Tracks statistics (running, completed, errored futures)
   - Used by both CLI (`BlockingCheckExecutor`) and HTTP modes

### Dependency Injection System

Watchpost uses type hints for dependency injection:

- **Datasources**: Check functions declare `Datasource` subclass parameters
- **Direct instantiation**: `def check(datasource: MyDatasource)`
- **Factory pattern**: `def check(ds: Annotated[MyDatasource, FromFactory(MyFactory, "arg")])`
- **Environment access**: Optional `environment: Environment` parameter

The application resolves these at startup in `_resolve_datasources()`, creating `_InstantiableDatasource` wrappers that defer actual instantiation until execution.

### Scheduling Strategy System

Scheduling strategies control **where** and **when** checks execute:

- **Strategies come from**:
  1. Check-level (`@check` decorator)
  2. Datasource-level (declared on `Datasource` or `DatasourceFactory`)
  3. Application defaults (e.g., `DetectImpossibleCombinationStrategy`)

- **Key strategies** (`src/watchpost/scheduling_strategy.py`):
  - `MustRunInGivenExecutionEnvironmentStrategy`: Pin execution to specific environments
  - `MustRunAgainstGivenTargetEnvironmentStrategy`: Restrict target environments
  - `MustRunInTargetEnvironmentStrategy`: Require execution == target (in-cluster checks)
  - `DetectImpossibleCombinationStrategy`: Validates that strategies don't conflict

- **Decisions** (`SchedulingDecision` enum):
  - `SCHEDULE`: Run the check now
  - `SKIP`: Temporarily skip and reuse cached results
  - `DONT_SCHEDULE`: Never run from this environment

The app evaluates all strategies and takes the "strictest" decision.

### Caching System

Multi-tiered caching in `src/watchpost/cache.py`:

- **Storage backends**: `InMemoryStorage`, `DiskStorage`, `RedisStorage`, `ChainedStorage`
- **CheckCache** (`src/watchpost/check.py`): Caches `ExecutionResult` lists per (check, environment)
- Cache keys use format `{check.name}:{environment.name}`
- TTL controlled by `cache_for` parameter in `@check` decorator
- Expired entries can be returned once for graceful degradation

### Hostname Resolution

Hostnames determine the Checkmk piggyback host for check results:

- **Hierarchy** (first match wins):
  1. Result-level (via `CheckResult.hostname`)
  2. Check-level (`@check(hostname=...)`)
  3. Environment-level (`Environment(hostname=...)`)
  4. Application-level (`Watchpost(hostname=...)`)
  5. Default fallback: `{service_name}-{environment.name}`

- **Strategies** (`src/watchpost/hostname.py`):
  - Template strings (e.g., `"{service_name}-{environment.name}"`)
  - Callables taking `(watchpost, check, environment, result)`
  - `HostnameStrategy` instances
  - RFC1123 compliance enforced (unless disabled via `hostname_coerce_into_valid_hostname`)

### Results System

Check functions can return (`src/watchpost/result.py`):

- **Single result**: `CheckResult` or `OngoingCheckResult` (builder)
- **Multiple results**: `list[CheckResult | OngoingCheckResult]`
- **Generator**: `yield CheckResult | OngoingCheckResult`

Helper functions: `ok()`, `warn()`, `crit()`, `unknown()`, `build_result()`

Results include:
- State (OK, WARN, CRIT, UNKNOWN)
- Summary and details
- Optional metrics with thresholds
- Optional `name_suffix` (creates multiple services from one check)

### HTTP/ASGI Interface

The Watchpost application is a Starlette ASGI app (`src/watchpost/http.py`):

- **`/`**: Streams Checkmk agent output (main integration point)
- **`/healthcheck`**: Simple health check endpoint
- **`/executor/statistics`**: JSON statistics about executor state
- **`/executor/errored`**: JSON of errored check executions

Run with any ASGI server (e.g., `uvicorn example:app`).

## Code Conventions

### Type Checking

- **Strict mypy** enabled for production code (not tests):
  - `disallow_untyped_calls`, `disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_untyped_decorators`
- Tests in `tests/` are excluded from mypy but should still use type hints where practical
- Use `ty check` for complementary type validation

### Licensing

- All Python files **must** include the Apache 2.0 license header (15 lines)
- Use `.tools/check-license-comments.sh` to verify
- Only specific licenses allowed for dependencies (see `pyproject.toml` `[tool.licensecheck]`)

### Import Organization

- Ruff auto-organizes imports (`I` rule)
- Standard library → third-party → local imports

### Error Handling

- **`DatasourceUnavailable`**: Raised when external systems are unreachable; triggers check UNKNOWN state
- **`InvalidCheckConfiguration`**: Raised during validation when checks can't be scheduled; aggregated into `ExceptionGroup`

### Testing

- Use `BlockingCheckExecutor` in tests for synchronous behavior
- Mark Docker-dependent tests with `@pytest.mark.docker`
- Helper utilities in `tests/utils.py`

## Key Patterns

### Defining a Check

```python
from watchpost import check, ok, warn, Environment

@check(
    name="My Service Name",
    service_labels={"component": "api"},
    environments=[PROD, STAGE],
    cache_for="5m",  # or timedelta(minutes=5)
)
async def my_check(datasource: MyDatasource) -> CheckResult:
    status = await datasource.get_status()
    if status.healthy:
        return ok("Service is healthy")
    return warn("Service degraded", details=f"Status: {status}")
```

### Creating a Datasource

```python
from watchpost import Datasource

class MyDatasource(Datasource):
    scheduling_strategies = ()  # or tuple of strategies

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    async def get_status(self):
        # Implementation
        pass
```

### Registering with Application

```python
from watchpost import Watchpost

app = Watchpost(
    checks=[my_check],  # or a module for auto-discovery
    execution_environment=PROD,
)

app.register_datasource(MyDatasource, endpoint="https://api.example.com")
```

### Using Factories

```python
class MyFactory(DatasourceFactory):
    scheduling_strategies = ()

    @classmethod
    def new(cls, service_name: str) -> Datasource:
        return MyDatasource(endpoint=f"https://{service_name}.example.com")

# In check:
from typing import Annotated
from watchpost import FromFactory

@check(...)
def my_check(
    ds: Annotated[MyDatasource, FromFactory(MyFactory, "auth")]
):
    ...
```

## Special Files

- **`src/watchpost/globals.py`**: Context variable (`_cv`) for accessing `current_app` during execution
- **`src/watchpost/vendored/`**: Vendored dependencies (currently `local_proxy.py`)
- **`checkmk-integration/`**: Checkmk plugin and Docker image for custom Checkmk builds
- **`.tools/`**: Shell scripts for license checking and validation
