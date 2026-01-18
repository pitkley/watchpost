# Watchpost Documentation Plan

This document outlines the planned structure and content for the Watchpost documentation. It is intended to guide future documentation work, whether done manually or in conversation with an AI assistant.

## Design Principles

Based on project requirements, this documentation should:

1. **Hybrid structure with examples** - Short tutorials interleaved with reference material, heavy use of annotated code examples
2. **Experienced audience** - Assumes familiarity with both Python and Checkmk; focuses on Watchpost-specific features rather than general monitoring concepts
3. **Progressive integration of advanced features** - Advanced features introduced naturally as documentation progresses, not hidden in a separate section
4. **CLI integrated early** - CLI commands introduced alongside the features they exercise (e.g., `run-checks` with checks, `verify-check-configuration` with scheduling)
5. **Checkmk integration woven throughout** - Explain how each feature maps to Checkmk as it's introduced (service labels, piggyback format, performance data, etc.)

---

## Navigation Structure

```
Introduction/
├── Overview (index.md)
├── Concepts
└── Getting Started

Fundamentals/
├── Environments
├── Datasources
├── Checks
└── Results

Advanced/
├── Scheduling Strategies
├── Hostname Resolution
├── Caching
└── Error Handlers

Cookbook/
├── Project Organization
├── Datasource Patterns
├── Check Patterns
├── Caching Strategies
└── Error Handling Patterns

Deployment/
├── CLI Reference
├── HTTP Endpoints
└── Checkmk Integration

API Reference/ (auto-generated)
```

---

## Page-by-Page Content Plan

### Introduction

#### 1. Overview (`index.md`)

**Status:** Exists, may need updates to align with new structure

**Purpose:** Landing page that explains what Watchpost is and why you'd use it.

**Content:**
- One-paragraph description of Watchpost
- Key capabilities as bullet points
- A minimal but complete "hello world" example showing a check, datasource, and running it
- Links to Getting Started for next steps

**Checkmk integration:** Brief mention that output is Checkmk-compatible (piggyback format)

**Notes:** Keep this page short. The goal is to quickly communicate "what is this" and get people to the next page.

---

#### 2. Concepts (`concepts.md`)

**Status:** NEW

**Purpose:** Establish the mental model before diving into code.

**Content:**

1. **Execution vs Target Environments**
   - Execution environment: where the Watchpost app runs
   - Target environment: what the check is monitoring
   - Why these can differ (e.g., central monitoring server checking multiple clusters)
   - Simple diagram or ASCII art showing the relationship

2. **Check Lifecycle**
   - Registration: decorating functions with `@check`
   - Scheduling: deciding whether to run based on strategies
   - Execution: running the check (sync or async)
   - Output: generating Checkmk-compatible results

3. **Dependency Injection Model**
   - Checks declare datasource dependencies via type hints
   - Watchpost instantiates and injects datasources at execution time
   - Brief preview of `FromFactory` for more complex cases

4. **Mapping to Checkmk**
   - Check name → Checkmk service name
   - service_labels → Checkmk labels
   - Hostname → Checkmk piggyback host
   - Result state → Checkmk state (OK/WARN/CRIT/UNKNOWN)
   - Metrics → Checkmk performance data

**Length:** ~2-3 screens. Enough to establish vocabulary, not a deep dive.

---

#### 3. Getting Started (`getting-started.md`)

**Status:** Exists as `home/quickstart.md`, may need updates

**Purpose:** Get the reader from zero to running their first check.

**Content:**

1. **Installation**
   ```bash
   pip install watchpost
   # or
   uv add watchpost
   ```

2. **Project Setup**
   - Minimal file structure
   - Create environments
   - Create a simple datasource
   - Create a check

3. **Running the Check**
   - Via CLI: `watchpost --app myapp:app run-checks`
   - Output explanation (Checkmk format)

4. **Running as HTTP Server**
   - Via uvicorn: `uvicorn myapp:app`
   - Accessing the root endpoint

5. **Next Steps**
   - Point to Fundamentals section

**Example code:** Complete, copy-pasteable example with environment, datasource, check, and app registration.

**CLI integration:** Introduce `run-checks` command here.

---

### Fundamentals

#### 4. Environments (`environments.md`)

**Status:** NEW

**Purpose:** Explain how to define and work with environments.

**Content:**

1. **The Environment Class**
   - Creating environments with name and optional parameters
   - Environment metadata
   - Environment-level hostname

2. **EnvironmentRegistry**
   - Registering environments
   - Accessing environments by name
   - Why use a registry (consistency, typo prevention)

3. **Execution Environment vs Target Environment**
   - Deeper explanation with examples
   - The `execution_environment` parameter on `Watchpost`
   - How checks specify their `environments` (targets)

4. **Practical Patterns**
   - Multi-environment setups (dev/staging/prod)
   - In-cluster monitoring (execution == target)
   - Central monitoring (one execution, many targets)

**Example code:**
```python
from watchpost import Environment, EnvironmentRegistry

registry = EnvironmentRegistry()

DEV = registry.register(Environment(name="dev", hostname="dev-services"))
STAGING = registry.register(Environment(name="staging", hostname="staging-services"))
PROD = registry.register(Environment(name="prod", hostname="prod-services"))
```

**Checkmk integration:** Explain how environment hostname becomes piggyback host.

---

#### 5. Datasources (`datasources.md`)

**Status:** NEW

**Purpose:** Explain how to create datasources and how dependency injection works.

**Content:**

1. **The Datasource Base Class**
   - Creating a datasource by subclassing
   - Synchronous vs asynchronous methods
   - Async context managers (`__aenter__`/`__aexit__`)

2. **Registering Datasources**
   - `app.register_datasource(MyDatasource, **kwargs)`
   - How constructor arguments are passed

3. **Dependency Injection**
   - Type hints on check parameters
   - How Watchpost resolves dependencies
   - Optional `environment: Environment` parameter

4. **DatasourceUnavailable Exception**
   - When to raise it
   - How it affects check results (UNKNOWN state)
   - Graceful handling of external system failures

5. **Datasource Factories**
   - The `DatasourceFactory` protocol
   - `FromFactory` annotation
   - Use case: same datasource type with different configurations

6. **Scheduling Strategies on Datasources**
   - The `scheduling_strategies` class attribute
   - When strategies on datasources make sense
   - Preview of scheduling strategies section

**Example code:** Multiple examples showing:
- Basic datasource with `register_datasource`
- Async context manager datasource
- Factory pattern with `FromFactory`

**Checkmk integration:** Explain that unavailable datasources result in UNKNOWN state.

---

#### 6. Checks (`checks.md`)

**Status:** NEW

**Purpose:** Deep dive into the `@check` decorator and check functions.

**Content:**

1. **The @check Decorator**
   - Required parameter: `name`
   - Full parameter reference with explanations

2. **Check Parameters Reference**
   - `name` - The service name (→ Checkmk service)
   - `environments` - Target environments list
   - `service_labels` - Labels dict (→ Checkmk labels)
   - `cache_for` - Cache duration (string like "5m" or timedelta)
   - `hostname` - Hostname override (string, callable, or strategy)
   - `scheduling_strategies` - Tuple of strategies
   - `error_handlers` - Tuple of error handlers

3. **Sync vs Async Checks**
   - Both patterns with examples
   - When to use which
   - How Watchpost handles each

4. **Check Function Signatures**
   - Datasource parameters (type-hint injected)
   - Optional `environment: Environment` parameter
   - Return types (CheckResult, list, generator)

5. **Check Discovery**
   - Explicit registration: `Watchpost(checks=[check1, check2])`
   - Module discovery: `Watchpost(checks=mymodule)`
   - Package scanning with predicates
   - Filtering and deduplication

6. **Service Labels**
   - How labels map to Checkmk
   - Common labeling patterns

**CLI integration:**
- `run-checks` - Execute checks
- `--filter-prefix` and `--filter-contains` for filtering
- `list-checks` - List all available checks

**Example code:**
```python
@check(
    name="API Health",
    environments=[PROD, STAGING],
    service_labels={"component": "api", "tier": "backend"},
    cache_for="5m",
)
async def api_health_check(api: ApiDatasource) -> CheckResult:
    ...
```

**Checkmk integration:** Service name, labels, and how multiple environments create multiple services.

---

#### 7. Results (`results.md`)

**Status:** NEW

**Purpose:** Explain how to construct and return check results.

**Content:**

1. **CheckState Enum**
   - OK, WARN, CRIT, UNKNOWN
   - Numeric values (0-3)
   - Mapping to Checkmk states

2. **Helper Functions**
   - `ok(summary, *, details=None, metrics=None)`
   - `warn(...)`, `crit(...)`, `unknown(...)`
   - When to use each

3. **CheckResult Dataclass**
   - All fields explained
   - `state`, `summary`, `details`
   - `metrics`, `hostname`, `name_suffix`

4. **OngoingCheckResult Builder**
   - Builder pattern for complex results
   - `.state()`, `.summary()`, `.details()`, `.metric()`, `.hostname()`
   - `.build()` to finalize

5. **Returning Multiple Results**
   - Return a list
   - Return a generator (yield)
   - Use cases for multiple results

6. **name_suffix for Multiple Services**
   - How name_suffix creates service variants
   - Pattern: one check → multiple Checkmk services
   - Example: per-endpoint health checks

7. **Metrics and Thresholds**
   - `Metric` class: name, value, unit, thresholds, boundaries
   - `Thresholds` class: warn and crit levels
   - How metrics appear in Checkmk (performance data)

**Example code:**
```python
# Simple result
return ok("All systems operational")

# Result with metrics
return ok(
    "Response time normal",
    metrics=[
        Metric(
            name="response_time",
            value=150,
            unit="ms",
            thresholds=Thresholds(warn=200, crit=500),
        )
    ],
)

# Multiple results with name_suffix
for endpoint, status in endpoints.items():
    yield ok(f"{endpoint} healthy", name_suffix=endpoint)
```

**Checkmk integration:** Detailed explanation of how results map to Checkmk output format.

---

### Advanced

#### 8. Scheduling Strategies (`scheduling-strategies.md`)

**Status:** NEW

**Purpose:** Explain how to control where and when checks run.

**Content:**

1. **The Scheduling Problem**
   - Why you need to control check execution
   - Example scenarios: in-cluster checks, region-specific checks

2. **SchedulingDecision Enum**
   - `SCHEDULE` - Run the check
   - `SKIP` - Don't run now, use cache
   - `DONT_SCHEDULE` - Never run from this context

3. **Strategy Sources**
   - Check-level (in `@check` decorator)
   - Datasource-level (on `Datasource` class)
   - App-level defaults

4. **Built-in Strategies**

   **MustRunInGivenExecutionEnvironmentStrategy**
   - Pin execution to specific environments
   - Use case: checks that must run from a specific location

   **MustRunAgainstGivenTargetEnvironmentStrategy**
   - Restrict which targets a check can monitor
   - Use case: environment-specific checks

   **MustRunInTargetEnvironmentStrategy**
   - Require execution environment == target environment
   - Use case: in-cluster Kubernetes checks

5. **Strategy Composition**
   - Multiple strategies evaluated together
   - "Strictest wins" behavior
   - Combining strategies

6. **Conflict Detection**
   - `DetectImpossibleCombinationStrategy`
   - `InvalidCheckConfiguration` exception
   - How validation works at startup

7. **Custom Strategies**
   - Implementing the strategy protocol
   - When to create custom strategies

**CLI integration:**
- `verify-check-configuration` - Validate all checks can be scheduled
- Output explanation and interpreting errors

**Example code:** Examples for each built-in strategy with realistic use cases.

---

#### 9. Hostname Resolution (`hostname-resolution.md`)

**Status:** NEW

**Purpose:** Explain the hostname system and how to customize it.

**Content:**

1. **Why Hostnames Matter**
   - Checkmk piggyback format requires hostnames
   - Hostname determines which host shows the service

2. **The Hostname Hierarchy**
   - Result-level (`CheckResult.hostname`)
   - Check-level (`@check(hostname=...)`)
   - Environment-level (`Environment(hostname=...)`)
   - App-level (`Watchpost(hostname=...)`)
   - Default fallback pattern

3. **Hostname Strategies**

   **Template Strings**
   - Available variables: `service_name`, `environment`, etc.
   - Example: `"{service_name}-{environment.name}"`

   **Callable Strategies**
   - Function signature: `(watchpost, check, environment, result) -> str`
   - Use case: dynamic hostname based on result content

   **HostnameStrategy Protocol**
   - Implementing custom strategy classes

4. **RFC1123 Compliance**
   - Valid hostname requirements
   - Automatic coercion (lowercase, replace invalid chars)
   - `hostname_coerce_into_valid_hostname` option

5. **Common Patterns**
   - Per-environment hostnames
   - Per-service hostnames
   - Result-driven hostnames (e.g., per-customer)

**CLI integration:**
- `get-check-hostnames` - List all resolved hostnames

**Checkmk integration:** How hostnames create piggyback sections in output.

---

#### 10. Caching (`caching.md`)

**Status:** NEW

**Purpose:** Explain the caching system and storage backends.

**Content:**

1. **Why Caching**
   - Reduce load on monitored systems
   - Handle temporary unavailability
   - The `cache_for` parameter recap

2. **How Caching Works**
   - Cache key: `{check.name}:{environment.name}`
   - TTL-based expiration
   - Graceful degradation (return expired once)

3. **Storage Backends**

   **InMemoryStorage** (default)
   - When to use: single-instance deployments
   - Limitations: lost on restart, not shared

   **DiskStorage**
   - Configuration and file location
   - When to use: persistence across restarts

   **RedisStorage**
   - Configuration with Redis connection
   - When to use: multi-instance deployments

   **ChainedStorage**
   - Combining backends (e.g., in-memory + disk)
   - Read/write behavior

4. **Configuring the Cache**
   - Setting storage backend on the app
   - Per-check `cache_for` values

5. **Cache Behavior Edge Cases**
   - What happens when cache expires
   - What happens when check fails but cache exists
   - Interaction with scheduling strategies

**Example code:** Configuration examples for each backend.

---

#### 11. Error Handlers (`error-handlers.md`)

**Status:** NEW

**Purpose:** Explain how to transform error results.

**Content:**

1. **The Problem**
   - When a check fails to execute (exception, unavailable datasource)
   - Default behavior: single UNKNOWN result
   - Why you might want different behavior

2. **ErrorHandler Protocol**
   - Signature and expected behavior
   - When error handlers are invoked

3. **Built-in Error Handlers**

   **expand_by_hostname()**
   - Expands error to all hostnames the check would produce
   - Use case: multi-host checks that fail entirely

   **expand_by_name_suffix()**
   - Expands error to all name_suffix values
   - Use case: multi-service checks that fail entirely

4. **Chaining Error Handlers**
   - Multiple handlers in sequence
   - Order of application

5. **Custom Error Handlers**
   - Implementing the protocol
   - Access to check context

6. **Practical Patterns**
   - "Alert on all or none" pattern
   - Graceful degradation patterns

**Example code:**
```python
@check(
    name="Multi-Host Check",
    error_handlers=(expand_by_hostname(["host-a", "host-b", "host-c"]),),
)
def multi_host_check(ds: MyDatasource):
    # If this fails, UNKNOWN appears for all three hosts
    ...
```

---

### Cookbook

The cookbook provides practical recipes for common patterns and real-world scenarios. Each recipe is self-contained with complete, copy-pasteable code examples.

**Source file:** `docs-cookbook-recipes.md` contains detailed, generalized recipes extracted from a real-world Watchpost deployment. Use this file as the primary source when implementing the cookbook pages.

#### 12. Project Organization (`cookbook/project-organization.md`)

**Status:** NEW

**Purpose:** How to structure a Watchpost project as it grows.

**Recipes from `docs-cookbook-recipes.md`:**
- Recipe #1: Project Organization (domain-driven file structure)

**Content:**
1. **Domain-Driven File Organization**
   - Group checks by service/domain, not by function type
   - Example directory structure
   - Where to put environments, datasources, shared utilities

2. **Centralized Registries**
   - Single `environments.py` for all environment definitions
   - Single `datasources.py` or organized by domain
   - Benefits: consistency, discoverability, typo prevention

3. **Module Discovery**
   - Using `Watchpost(checks=[module])` for automatic discovery
   - Package scanning patterns

---

#### 13. Datasource Patterns (`cookbook/datasource-patterns.md`)

**Status:** NEW

**Purpose:** Common patterns for building datasources.

**Recipes from `docs-cookbook-recipes.md`:**
- Recipe #2: Dual Datasource-Factory Pattern
- Recipe #8: Callable Credentials
- Recipe #9: Internal Datasource Caching (OAuth Tokens)
- Recipe #11: Custom HTTP Authentication Classes
- Recipe #15: Context Manager Datasources for HTTP Clients

**Content:**
1. **Dual Datasource + Factory**
   - Single class implements both protocols
   - `new()` method loads from environment variables
   - When to use vs. separate factory class

2. **Context Manager for HTTP Clients**
   - Sync and async variants
   - Proper resource cleanup
   - Connection pooling considerations

3. **Internal Caching (OAuth/Tokens)**
   - Managing short-lived credentials
   - Token refresh with automatic caching
   - TTL calculation from token expiry

4. **Custom Authentication Classes**
   - `httpx.Auth` subclasses for non-standard auth
   - Bearer tokens, custom headers, service accounts
   - Reusable across datasources

5. **Callable Credentials**
   - Lazy evaluation at registration time
   - Supporting credential rotation
   - Factory `new()` accepting callables

---

#### 14. Check Patterns (`cookbook/check-patterns.md`)

**Status:** NEW

**Purpose:** Common patterns for writing checks.

**Recipes from `docs-cookbook-recipes.md`:**
- Recipe #3: Generator Checks for Multiple Services
- Recipe #4: Result Builder for Multi-Validation Checks
- Recipe #5: Environment-Specific Check Configuration
- Recipe #6: Dynamic Hostname Resolution
- Recipe #13: Data-Driven Check Configuration
- Recipe #14: Deadline/Expiration Tracking Pattern
- Recipe #16: Container/Process Inventory Checks

**Content:**
1. **Generator Checks**
   - Yielding multiple results with `name_suffix`
   - Creating one Checkmk service per item
   - When generators vs. lists

2. **Result Builder for Multiple Validations**
   - `build_result()` pattern
   - Accumulating warnings and criticals
   - Automatic state escalation

3. **Environment-Specific Configuration**
   - Dictionary mapping environments to config
   - Injecting `environment: Environment` parameter
   - Different thresholds per environment

4. **Data-Driven Configuration**
   - Module-level dictionaries for expected state
   - Dataclasses for structured config
   - Severity functions per item

5. **Dynamic Hostname Resolution**
   - Callable hostname strategies
   - Per-environment hostname mapping
   - Result-level `alternative_hostname` override

6. **Deadline/Expiration Tracking**
   - Generic pattern for tracking dates
   - Configurable warn/crit thresholds
   - Examples: certificates, licenses, renewals

7. **Inventory Checks**
   - Expected vs. discovered items
   - Alerting on missing or unexpected items
   - Summary results for "unknown" items

---

#### 15. Caching Strategies (`cookbook/caching-strategies.md`)

**Status:** NEW

**Purpose:** Advanced caching patterns beyond basic `cache_for`.

**Recipes from `docs-cookbook-recipes.md`:**
- Recipe #7: Layered Caching Strategy
- Recipe #10: Shared Runtime Cache for Helper Functions

**Content:**
1. **Layered Caching with ChainedStorage**
   - In-memory + Redis for speed and persistence
   - Configuration from environment variables
   - Behavior: read from first, write to all

2. **Runtime Cache for Helper Functions**
   - Shared `Cache` instance across checks
   - `@cache.memoize` decorator
   - Avoiding redundant API calls within execution

3. **Datasource-Level Caching**
   - OAuth token caching (cross-reference to datasource patterns)
   - TTL matching external expiry

---

#### 16. Error Handling Patterns (`cookbook/error-handling-patterns.md`)

**Status:** NEW

**Purpose:** Patterns for graceful error handling.

**Recipes from `docs-cookbook-recipes.md`:**
- Recipe #12: Graceful Degradation with DatasourceUnavailable

**Content:**
1. **DatasourceUnavailable for Temporary Failures**
   - Which exceptions to catch
   - Timeout vs. connection vs. server errors
   - Distinguishing "can't check" from "check failed"

2. **Validation with Specific Error Messages**
   - Catching different exception types
   - Providing actionable error details
   - Error messages that aid debugging

3. **Error Handlers for Multi-Result Checks**
   - Cross-reference to Error Handlers in Advanced section
   - When to use `expand_by_hostname()` vs. `expand_by_name_suffix()`

---

### Deployment

#### 17. CLI Reference (`cli-reference.md`)

**Status:** NEW

**Purpose:** Comprehensive reference for all CLI commands.

**Content:**

1. **General Usage**
   - `watchpost --app MODULE:APP COMMAND`
   - Environment variables (`WATCHPOST_*` prefix)

2. **Commands**

   **run-checks**
   - `--asynchronous-check-execution` / `--synchronous-check-execution`
   - `--cache` / `--no-cache`
   - `--filter-prefix PREFIX`
   - `--filter-contains SUBSTRING`

   **list-checks**
   - Output format
   - What information is shown

   **verify-check-configuration**
   - What it validates
   - Interpreting output
   - Exit codes

   **get-check-hostnames**
   - Output format
   - Use cases

3. **Integration with Checkmk Agent**
   - Running as a local check
   - Cron-based execution
   - Output format expectations

**Note:** This page can be relatively terse since CLI commands are also introduced in context throughout the docs.

---

#### 18. HTTP Endpoints (`http-endpoints.md`)

**Status:** NEW

**Purpose:** Reference for the ASGI/HTTP interface.

**Content:**

1. **Running as ASGI Application**
   - Uvicorn, Gunicorn, Hypercorn examples
   - Configuration options

2. **Endpoints**

   **`/` (Root)**
   - Streams Checkmk agent output
   - Response format (piggyback sections)
   - How Checkmk consumes this

   **`/healthcheck`**
   - Returns 204
   - Use for load balancer health checks

   **`/executor/statistics`**
   - JSON response with executor stats
   - Fields: running, completed, errored
   - Use for monitoring Watchpost itself

   **`/executor/errored`**
   - JSON response with error details
   - Debugging failed check executions

3. **Deployment Patterns**
   - Behind reverse proxy
   - Multiple workers
   - Caching considerations

---

#### 19. Checkmk Integration (`checkmk-integration.md`)

**Status:** NEW

**Purpose:** Dedicated page for Checkmk-specific integration details.

**Content:**

1. **Output Format**
   - Piggyback format explanation
   - Section structure
   - How Checkmk parses the output

2. **Agent Plugin**
   - The `checkmk-integration/` directory
   - Installing the special agent
   - Configuration in Checkmk

3. **Service Discovery**
   - How services appear in Checkmk
   - Labels and their uses
   - Hostname to host mapping

4. **Performance Data**
   - How Metrics become PNP data
   - Graphing in Checkmk
   - Threshold visualization

5. **Common Issues**
   - Host not found (hostname mapping)
   - Service not discovered (timing, rules)
   - Stale data (caching, scheduling)

---

### API Reference

**Status:** Exists, auto-generated with mkdocstrings

**Notes:**
- Keep the auto-generated reference
- Consider organizing into sections if it grows large
- Ensure docstrings in source are complete

---

## Implementation Notes

### Priorities

**High Priority (Core Path):**
1. Concepts
2. Getting Started (update existing)
3. Environments
4. Datasources
5. Checks
6. Results

**Medium Priority (Advanced Features):**
7. Scheduling Strategies
8. Hostname Resolution
9. Caching
10. Error Handlers

**Lower Priority (Practical Patterns):**
12. Cookbook: Project Organization
13. Cookbook: Datasource Patterns
14. Cookbook: Check Patterns
15. Cookbook: Caching Strategies
16. Cookbook: Error Handling Patterns

**Lowest Priority (Reference):**
17. CLI Reference
18. HTTP Endpoints
19. Checkmk Integration

### Cross-Cutting Concerns

Each page should include where relevant:
- **Code examples** - Complete, copy-pasteable, with annotations
- **CLI commands** - Related commands introduced in context
- **Checkmk mapping** - How the feature appears in Checkmk
- **Links** - To related pages and API reference

### Style Guidelines

- Use code blocks with syntax highlighting
- Include output examples where helpful
- Keep paragraphs short (3-5 sentences max)
- Use headers liberally for scannability
- Annotate examples explaining key lines

### Files to Create/Modify

**Create:**
- `docs/concepts.md`
- `docs/fundamentals/environments.md`
- `docs/fundamentals/datasources.md`
- `docs/fundamentals/checks.md`
- `docs/fundamentals/results.md`
- `docs/advanced/scheduling-strategies.md`
- `docs/advanced/hostname-resolution.md`
- `docs/advanced/caching.md`
- `docs/advanced/error-handlers.md`
- `docs/cookbook/project-organization.md`
- `docs/cookbook/datasource-patterns.md`
- `docs/cookbook/check-patterns.md`
- `docs/cookbook/caching-strategies.md`
- `docs/cookbook/error-handling-patterns.md`
- `docs/deployment/cli-reference.md`
- `docs/deployment/http-endpoints.md`
- `docs/deployment/checkmk-integration.md`

**Modify:**
- `docs/index.md` - Update to align with new structure
- `docs/home/quickstart.md` - Rename/move to `docs/getting-started.md`, update content
- `mkdocs.yml` - Update navigation structure

**Delete:**
- `docs/home/` directory (after moving quickstart)

### mkdocs.yml Navigation Update

```yaml
nav:
  - Introduction:
    - Overview: index.md
    - Concepts: concepts.md
    - Getting Started: getting-started.md
  - Fundamentals:
    - Environments: fundamentals/environments.md
    - Datasources: fundamentals/datasources.md
    - Checks: fundamentals/checks.md
    - Results: fundamentals/results.md
  - Advanced:
    - Scheduling Strategies: advanced/scheduling-strategies.md
    - Hostname Resolution: advanced/hostname-resolution.md
    - Caching: advanced/caching.md
    - Error Handlers: advanced/error-handlers.md
  - Cookbook:
    - Project Organization: cookbook/project-organization.md
    - Datasource Patterns: cookbook/datasource-patterns.md
    - Check Patterns: cookbook/check-patterns.md
    - Caching Strategies: cookbook/caching-strategies.md
    - Error Handling Patterns: cookbook/error-handling-patterns.md
  - Deployment:
    - CLI Reference: deployment/cli-reference.md
    - HTTP Endpoints: deployment/http-endpoints.md
    - Checkmk Integration: deployment/checkmk-integration.md
  - API Reference: reference/api.md
```

---

## Supporting Files

### `docs-cookbook-recipes.md`

This companion file contains **16 detailed, generalized recipes** extracted from a real-world Watchpost deployment. Each recipe includes:

- Problem statement
- Complete solution with code examples
- When to use / when not to use guidance

**Important:** This file is the primary source for implementing the Cookbook section. The recipes are already generalized and anonymized - copy the code examples directly.

---

## Open Questions

Document any questions that arise during implementation here:

1. Should the existing `basic` example be expanded or should new examples be created for the docs?
2. How much Checkmk-specific detail is appropriate vs. pointing to Checkmk docs?
