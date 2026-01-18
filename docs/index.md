# Watchpost &ndash; code-driven monitoring checks for Checkmk

Watchpost is a small framework for writing monitoring checks as Python code and integrating them with [Checkmk](https://checkmk.com/).
It helps you configure checks through a simple function decorator, handles running checks across and against multiple environments, and supports you in gathering data from external systems.

<sup><i>
Affiliation: This project has no official affiliation with Checkmk GmbH or any of its affiliates.
"Checkmk" is a trademark of Checkmk GmbH.
</i></sup>

## Example

Install Watchpost in your project:

```shell
pip install 'git+https://github.com/pitkley/watchpost[cli]'
```

You can now write a basic Watchpost application like this:

```python {linenums="1" hl_lines="10-15 24-27 29 34"}
import urllib.error
import urllib.request

from watchpost import EnvironmentRegistry, Watchpost, check, crit, ok

ENVIRONMENTS = EnvironmentRegistry()
PRODUCTION = ENVIRONMENTS.new("production")


@check(  # (1)
    name="example.com HTTP status",
    service_labels={},
    environments=[PRODUCTION],
    cache_for="5m",
)
async def example_com_http_status():
    try:
        with urllib.request.urlopen("https://www.example.com") as response:
            status_code = response.status
    except urllib.error.HTTPError as e:
        status_code = e.code

    if status_code != 200:
        return crit(  # (2)
            "example.com returned an error",
            details=f"Expected status: 200\nActual status: {status_code}\n",
        )

    return ok("example.com is up")  # (3)


app = Watchpost(
    checks=[
        example_com_http_status,  # (4)
    ],
    execution_environment=PRODUCTION,
)
```

1. Use the `@check` decorator to define your check:

    * A human-friendly name that will appear as the service name in Checkmk.
    * Optional service labels to attach to the Checkmk service.
    * The environments this check targets.
    * A cache duration that controls how long a result is kept before the check runs again.

2. If the check fails, return `crit(...)`. The details will be shown in the Checkmk service to help troubleshooting.
3. If everything is fine, return `ok(...)`.
4. Register the check with the application.

Assuming this is saved as `example.py`, you can run it locally as such using the `watchpost` CLI:

```console
$ watchpost --app example:app run-checks
                       Check Execution Results
┏━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ State ┃ Environment ┃ Service Name            ┃ Summary           ┃
┡━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│  OK   │ production  │ example.com HTTP status │ example.com is up │
└───────┴─────────────┴─────────────────────────┴───────────────────┘
```

The Checkmk integration makes use of HTTP to retrieve the check results from the Watchpost application.
To support this, Watchpost is a valid ASGI web application which you can run with any ASGI server, for example [uvicorn](https://www.uvicorn.org/):

```console
$ pip install uvicorn
$ uvicorn example:app
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

## Capabilities at a glance

* Checks and results
    * `@check` decorator, multiple result modes (single, multiple, yielded, builder)
    * Result helpers: `ok`, `warn`, `crit`, `unknown`, metrics, thresholds
* Environments and scheduling
    * Target vs. execution environments, pluggable scheduling strategies with validation
* Datasources
    * Simple base class (`Datasource`) and factory pattern to share configuration
* Execution and streaming
    * Key‑aware executor, error aggregation, Checkmk output generation
* Caching
    * In‑memory, disk, and optional Redis backends; memoization helper
* ASGI / HTTP
    * Starlette app; routes: `/`, `/healthcheck`, `/executor/statistics`, `/executor/errored`

## Next steps

* Try the [quickstart guide](home/quickstart.md).
* See the [full API reference](reference/api.md).
