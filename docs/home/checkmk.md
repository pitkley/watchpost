# Checkmk integration

Checkmk polls a Watchpost application over HTTP. A **source host** collects the
response, and Checkmk assigns its piggyback sections to **target hosts**. The
Watchpost plugin turns those sections into services, labels, and metrics.

The supplied Docker image and integration tests use **Checkmk Raw 2.4.0p30**.
The complete collection, discovery, and monitoring flow below has been verified
with that version. Other Checkmk versions require separate compatibility testing.

## 1. Run a Watchpost application

You can use the [quickstart application](quickstart.md), replacing
`integration_example:app` below with `my_watchpost:app`. For a deterministic
integration test without an external service dependency, create a project:

```sh
uv init --app --python 3.13 my-watchpost-integration
cd my-watchpost-integration
uv add 'watchpost[cli]' uvicorn
```

Save this as `integration_example.py`:

```python
from watchpost import (
    CheckResult,
    EnvironmentRegistry,
    Metric,
    Thresholds,
    Watchpost,
    check,
    ok,
)
from watchpost.result import Boundaries

environments = EnvironmentRegistry()
production = environments.new(
    "production", hostname="watchpost-example-production"
)


@check(
    name="Example readiness",
    service_labels={"team": "platform"},
    environments=[production],
    cache_for="5m",
)
def readiness() -> CheckResult:
    return ok(
        "HTTP collection reached Watchpost",
        metrics=[Metric("requests", 7, Thresholds(10, 20), Boundaries(0, 100))],
    )


app = Watchpost(checks=[readiness], execution_environment=production)
```

Verify the configuration, list its target hosts, and start the HTTP server:

```sh
uv run watchpost --app integration_example:app verify-check-configuration
uv run watchpost --app integration_example:app get-check-hostnames
uv run uvicorn integration_example:app --host 0.0.0.0 --port 8000
```

The example's target is `watchpost-example-production`. The quickstart's default
target is `example.com-http-status-production`. Keep the server running while
completing the remaining steps. This local setup listens on the host's network
interfaces so the Checkmk container can connect; use the
[deployment guide](deployment.md) when choosing production access and process
settings.

## 2. Install the Checkmk integration

Choose either the supplied Docker image or an existing Checkmk site. Watchpost's
Python runtime runs separately from Checkmk; Checkmk needs the plugin and the
HTTP collection scripts.

### Docker

In another terminal, obtain a Watchpost repository checkout and build its image:

```sh
git clone https://github.com/pitkley/watchpost.git
cd watchpost
docker build --platform linux/amd64 \
  -f checkmk-integration/Dockerfile.checkmk \
  -t watchpost-checkmk:2.4.0p30 checkmk-integration

docker run -d --name watchpost-checkmk \
  --platform linux/amd64 \
  --add-host host.docker.internal:host-gateway \
  -v watchpost-checkmk:/omd/sites \
  --tmpfs /opt/omd/sites/cmk/tmp:uid=1000,gid=1000 \
  -p 127.0.0.1:8080:5000 \
  watchpost-checkmk:2.4.0p30

docker logs watchpost-checkmk
```

The pinned image supports AMD64; ARM64 machines need Docker's AMD64 emulation.
Wait for the log to report that the container has started. On first startup it
also reports the generated password for `cmkadmin`. Sign in at
[http://localhost:8080/cmk/check_mk/](http://localhost:8080/cmk/check_mk/).
The named volume retains the site's configuration and monitoring data across
container replacement. See the [Checkmk container documentation](https://docs.checkmk.com/2.4.0/en/introduction_docker.html)
for further site setup options.

Open a site-user shell for the following verification commands:

```sh
docker exec -it watchpost-checkmk su - cmk
```

### Existing site

As the Checkmk site user, run these commands from a Watchpost repository checkout
on the Checkmk server. The server also needs `curl` available in its command path.

```sh
mkdir -p "$HOME/local/lib/python3/cmk_addons/plugins/watchpost/agent_based"
mkdir -p "$HOME/local/bin"
install -m 644 checkmk-integration/watchpost-plugin/agent_based/watchpost.py \
  "$HOME/local/lib/python3/cmk_addons/plugins/watchpost/agent_based/watchpost.py"
install -m 755 checkmk-integration/agent-integration/watchpost_collect \
  checkmk-integration/agent-integration/watchpost_check "$HOME/local/bin/"
```

This uses Checkmk's [site-local API v2 plugin directory](https://docs.checkmk.com/2.4.0/en/devel_check_plugins.html).
For all subsequent commands, use your site's login shell. Replace
`host.docker.internal:8000` with the hostname and port at which this server can
reach Watchpost; use `https` instead of `http` when appropriate.

## 3. Verify collection and configure the source host

From the site-user shell, verify plugin registration and HTTP access:

```sh
cmk -L | awk '$1 == "watchpost"'
watchpost_check http host.docker.internal:8000
watchpost_collect http host.docker.internal:8000
```

The first command must list `watchpost`. The health probe succeeds silently when
`/healthcheck` returns HTTP 204. The collector prints agent sections, including
`<<<check_mk>>>`, `<<<watchpost>>>`, and piggyback host markers. An initial poll
may show a check as running; a later poll retrieves its completed result.

In **Setup > Hosts**, create `watchpost-source` with these settings:

| Setting | Value |
| --- | --- |
| IP address family | No IP |
| Checkmk agent / API integrations | API integrations if configured, else Checkmk agent |
| SNMP | No SNMP |
| Piggyback | Never use piggyback data |

The source host's piggyback setting controls incoming data; it can still supply
data for target hosts.

Create a rule under **Setup > Agents > Other integrations > Individual program
call instead of agent access**. Limit its host condition to `watchpost-source`
and set the command to:

```text
/usr/local/bin/watchpost_collect http host.docker.internal:8000
```

The rule above uses the image's absolute script path. For an existing site,
substitute the absolute path of the installed script, such as
`/omd/sites/<site>/local/bin/watchpost_collect`, and the Watchpost server's
reachable hostname and port. Verify the rule with `cmk -D watchpost-source`:
its agent type should show the configured program. See Checkmk's
[datasource program guide](https://docs.checkmk.com/latest/en/datasource_programs.html)
for this rule and its error handling.

## 4. Create target hosts and discover services

Create a host for each name returned by `get-check-hostnames`, matching case and
spelling exactly. For this guide, create `watchpost-example-production` with:

| Setting | Value |
| --- | --- |
| IP address family | No IP |
| Checkmk agent / API integrations | No API integrations, no Checkmk agent |
| SNMP | No SNMP |
| Piggyback | Always use and expect piggyback data |

Checkmk Raw requires these target hosts to exist before it can monitor them.
Checks using `NoPiggybackHostStrategy()` belong to the source host and require
no separate target host; `get-check-hostnames` omits them.
Result-level hostname overrides may produce additional hosts beyond the CLI's
static list; inspect `cmk-piggyback list piggybacked` after collection to find
those. Checkmk's [piggyback guide](https://docs.checkmk.com/2.4.0/en/piggyback.html)
explains host matching and missing or outdated data.

In Setup, discover and accept services on the source first, then on the target,
and activate the changes. The source gets `Watchpost: executed checks`; this
guide's target gets `Example readiness`. The equivalent site-user verification
sequence is:

```sh
cmk -vI --detect-plugins=watchpost watchpost-source
cmk --debug -v watchpost-source
cmk-piggyback list piggybacked
cmk -vI --detect-plugins=watchpost watchpost-example-production
cmk --debug -v watchpost-example-production
cmk -R
```

Use `--detect-plugins=watchpost` only to narrow discovery here. Filtering target
execution with that option excludes Checkmk's piggyback source-summary
processing and can produce a misleading missing-data warning.

The target should report **OK**, summary `HTTP collection reached Watchpost`,
and a `requests` metric with value `7`. The Check_MK service should report that it
successfully processed piggyback data from `watchpost-source`. The verified
performance-data representation is `requests=7;10;20;0;100`.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Collector cannot connect | Run it as the site user; verify the scheme, hostname, port, server binding, and network access. |
| No Watchpost services | Confirm `cmk -L` lists the plugin and `cmk -D watchpost-source` shows the collection program. |
| Target has no data | Collect the source first; inspect `cmk-piggyback list piggybacked` and compare the target name exactly. |
| Unexpected target names | Compare `get-check-hostnames` with `cmk-piggyback list orphans`; check result hostname overrides. |
| Piggyback data becomes stale | Check source collection and polling intervals; review Checkmk's **Processing of piggybacked host data** rule. |
| Duplicate Watchpost service | Give each result on a target host a distinct service name or name suffix. The plugin reports UNKNOWN for collisions. |

The health endpoint tests liveness, not successful check execution. Refer to
[deployment and runtime behavior](deployment.md) for polling, caching, executor
limits, diagnostics, and shutdown.
