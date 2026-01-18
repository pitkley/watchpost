# Quickstart

This guide walks you through creating your first Watchpost application and running a simple check.
The guide also explains how you will integrate your Watchpost application with Checkmk.

This guide uses the [uv package manager](https://docs.astral.sh/uv/) for commands and examples.
You can use pip or another tool if you prefer.
If you need to install uv, see the [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

## Create a Watchpost application

1. Initialize a new Python project:

    ```
    uv init --app --package ./my-watchpost
    ```

    You can omit `--package` if you prefer a single-file project.
    Using a package layout is recommended because it keeps your application organized as it grows.

2. Add Watchpost to your project:

    ```console
    $ cd my-watchpost
    $ uv add 'git+https://github.com/pitkley/watchpost[cli]'
    Using CPython 3.13.5
    Creating virtual environment at: .venv
        Updated https://github.com/pitkley/watchpost (<some hash>)
    Resolved 13 packages in 266ms
          Built my-watchpost @ file:///tmp/my-watchpost
    Prepared 1 package in 13ms
    Installed 11 packages in 31ms
     + anyio==4.10.0
     + click==8.2.1
     + idna==3.10
     + markdown-it-py==4.0.0
     + mdurl==0.1.2
     + pygments==2.19.2
     + rich==14.1.0
     + sniffio==1.3.1
     + starlette==0.47.3
     + timelength==3.0.2
     + watchpost==0.1.0 (from git+https://github.com/pitkley/watchpost@<some hash>)
    ```

    We recommend installing Watchpost with the `cli` extra.
    It adds a `watchpost` command that makes it easy to list and run checks during development.

3. Edit `my-watchpost/src/my_watchpost/__init__.py` to create a minimal application:

    ```python
    from watchpost import EnvironmentRegistry, Watchpost

    ENVIRONMENTS = EnvironmentRegistry()
    PRODUCTION = ENVIRONMENTS.new("production")

    app = Watchpost(
        checks=[],
        execution_environment=PRODUCTION,
    )
    ```

4. Verify that the app starts and contains no checks yet

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

Now let’s add a first check that verifies whether <https://www.example.com> is reachable and returns a 200 OK status code.

1. Add a HTTP client dependency.

    We use httpx and its `AsyncClient` to demonstrate async checks, but you can use any HTTP client (async or sync) for your own checks.

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

2. Edit `my-watchpost/src/my_watchpost/__init__.py` to:

    1. Add a new datasource that provides the http client.
    2. Add a check that verifies the status code of <https://www.example.com>.
    3. Register the check with the application.
    4. Register the datasource with the application.

    ```python {linenums="1" hl_lines="11-17 20-25 27 33-40 42 47 51"}
    from contextlib import asynccontextmanager

    import httpx

    from watchpost import check, crit, ok, Datasource, EnvironmentRegistry, Watchpost

    ENVIRONMENTS = EnvironmentRegistry()
    PRODUCTION = ENVIRONMENTS.new("production")


    class HttpxClientFactory(Datasource):  # (1)
        scheduling_strategies = ()

        @asynccontextmanager
        async def client(self):
            async with httpx.AsyncClient() as client:
                yield client


    @check(  # (2)
        name="example.com HTTP status",
        service_labels={},
        environments=[PRODUCTION],
        cache_for="5m",
    )
    async def example_com_http_status(
        client_factory: HttpxClientFactory,  # (3)
    ):
        async with client_factory.client() as client:
            response = await client.get("https://www.example.com")

        if response.status_code != 200:
            return crit(  # (4)
                "example.com returned an error",
                details=(
                    f"Expected status: 200\n"
                    f"Actual status: {response.status_code}\n"
                    f"Response: {response.text}"
                ),
            )

        return ok("example.com is up")  # (5)


    app = Watchpost(
        checks=[
            example_com_http_status,  # (6)
        ],
        execution_environment=PRODUCTION,
    )
    app.register_datasource(HttpxClientFactory)  # (7)
    ```

    1. Define a datasource that constructs httpx clients.
       You may wonder why this is a separate class instead of creating the client directly inside the check. In real projects your datasources often encapsulate more context (for example, which environment the client can run in) or wrap an API with domain-specific helpers. Keeping that logic in a datasource makes your checks simpler and easier to test.

    2. Use the `@check` decorator to define your check:

       - A human-friendly name that will appear as the service name in Checkmk.
       - Optional service labels to attach to the Checkmk service.
       - The environments this check targets.
       - A cache duration that controls how long a result is kept before the check runs again.

    3. To use a datasource in a check, add a parameter annotated with the datasource type. Watchpost injects the instance automatically when the check runs.
    4. If the check fails, return `crit(...)`. The details will be shown in the Checkmk service to help troubleshooting.
    5. If everything is fine, return `ok(...)`.
    6. Register the check with the application.
    7. Register the datasource with the application.

3. List and run the check

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

You have now built your first Watchpost application and check. 🎉

## Checkmk integration

_TODO_

## Summary

This example shows how you can codify checks with Watchpost in a clean, testable way. If you can code it, you can check it.
