# Deployment and runtime behavior

Run Watchpost as a long-lived ASGI application and have Checkmk poll it over HTTP.
The [Checkmk integration guide](checkmk.md) covers collection and service discovery.
The application requires Python 3.13 or newer.

## Start and validate an application

From the application project, install the CLI extra and an ASGI server, then
validate the configuration before starting the server:

```shell
uv add 'watchpost[cli]' uvicorn
uv run watchpost --app my_watchpost:app verify-check-configuration
uv run watchpost --app my_watchpost:app run-checks
uv run uvicorn my_watchpost:app --host 0.0.0.0 --port 8000 --workers 1
```

Verification returns a nonzero exit status for scheduling or hostname errors.
`run-checks` waits for checks and disables result caching by default; use `--cache`
to exercise caching. HTTP uses the application's background executor. Configure
checks, datasources, and strategies before serving requests; validation and
resolved configuration are cached for the lifetime of the application.

Use one ASGI worker as a starting point. Every process owns its own datasource
instances, executor, event loop, and pending-work registry. Multiple workers or
replicas can execute the same check simultaneously. A shared disk or Redis cache
shares results, but does not provide a distributed scheduling lock. Partition
checks or arrange collection so each check has one responsible application
instance when duplicate execution is undesirable.

The HTTP endpoints have no built-in authentication. Restrict access to the
Checkmk site through network policy or an authenticated reverse proxy. Use HTTPS
when collection crosses an untrusted network.

## Polling, scheduling, and caching

A request to `/` drives execution and streams Checkmk agent output. Watchpost has
no independent periodic scheduler: `cache_for="5m"` makes results reusable for
five minutes; it does not cause a check to run every five minutes without polls.
Configure the collection interval in Checkmk to suit the desired freshness.

For each check and its declared target environment:

| Situation | Poll behavior |
| --- | --- |
| `SCHEDULE` with a fresh cached result | Return the cached result. |
| `SCHEDULE` without a reusable result | Submit work if none is pending, then pick up completed results. |
| Work is still running | Return an available prior result, otherwise UNKNOWN until a later poll retrieves completion. |
| `SKIP` | Reuse an available prior result; otherwise return UNKNOWN. |
| `DONT_SCHEDULE` | Emit no result for that pair from this instance. |

The first HTTP poll commonly returns UNKNOWN because execution has started but
has not finished. A later poll collects the result. An expired cache entry may
be returned once while refreshing; stale results are not retained indefinitely.

Both cached and uncached checks have at most one outstanding execution per
check/environment pair through the application API. Once a completed uncached
result has been picked up, the next poll may start another execution.
`max_workers` limits synchronous worker threads; async checks run as tasks on the
executor's event loop. For a fixed application, pending work is bounded by the
number of check/environment pairs. Direct executor callers that explicitly use
`resubmit=True` opt into overlapping work.

The default cache is in memory and disappears at process exit. Set
`Watchpost(check_cache_storage=...)` to a `DiskStorage` or `RedisStorage` instance
for persistence. Use a separate disk directory or Redis namespace for unrelated
applications. Disk entries use pickle and the cache directory must be writable
only by trusted processes. Disk writes replace complete files atomically;
missing or corrupt entries are treated as misses. Short filesystem locks coordinate
publication and expiry across threads and processes, preventing expiry from
deleting a concurrent replacement. Use a filesystem that supports advisory file
locking; persistent lock files and shard directories are retained. Persistent storage does not
restore pending jobs after a restart.

Generated checks are identified by their qualified function name and service
name. Supply distinct `@check(id="...")` values when those names would collide
for the same target. Explicit IDs also provide stable keys when code is moved.
Changing an identity causes a cache miss. The current versioned keys do not reuse
older name-only cache entries.

`Cache.memoize()` supports synchronous and `async def` functions. Async functions
cache their awaited values, and formatted keys include omitted argument defaults.
Exceptions and cancellation are not cached. Storage operations remain synchronous,
and simultaneous misses may each execute the memoized function.

## Timeouts and shutdown

Set timeouts on external I/O in every check. Use synchronous client calls inside
`def` checks, and await async clients inside `async def` checks. Blocking I/O in an
async check blocks the event loop shared by all async checks. Client connect/read
timeouts bound those operations; use `asyncio.timeout()` when an async check also
needs a deadline for the whole operation.

Watchpost does not impose a check deadline or forcibly stop running Python
threads. A blocked check retains its pending slot instead of creating another job
on every poll. Ensure synchronous operations finish within a time compatible
with the ASGI server's shutdown grace period.

ASGI lifespan validates configuration on startup and closes Watchpost's own
executor on shutdown, including after failed startup. Shutdown cancels async
checks and queued thread jobs, waits for running synchronous checks and async
cleanup, stops the event loop, and releases executor resources. Use context
managers inside checks to close HTTP clients and other per-call resources.
Datasources have no automatic lifecycle/close hook; application-owned long-lived
resources need their own lifecycle management.

For standalone use, call `app.shutdown()` in `finally`. An executor passed via
`Watchpost(executor=...)` or assigned to `app.executor` belongs to the caller and
must be shut down explicitly. If you replace an application-created executor,
`app.shutdown()` still closes that original executor.
Direct executor users can drain work with `shutdown(wait=True)` or request
cancellation with `shutdown(wait=True, cancel_futures=True)`.

## Diagnostics and service identity

| Endpoint or service | Meaning |
| --- | --- |
| `/healthcheck` | HTTP 204 means the app can respond; it does not prove checks or their dependencies are healthy. |
| `/executor/statistics` | Snapshot of executor counters and pending work in this process. |
| `/executor/errored` | Executor error diagnostics; inspect check output for failures handled as monitoring results. |
| `Watchpost: executed checks` | Number of check/environment pairs whose actual decision was `SCHEDULE` in this poll. Includes cache hits and already-running work; excludes skipped pairs. |

The synthetic service name is retained for existing Checkmk installations. Its
count describes eligibility, not completed invocations or the number of emitted
services. Its details list each eligible check and actual target environment.

Datasource construction failures affect their check's output. An unavailable
datasource produces UNKNOWN or an available prior result with failure details;
other exceptions produce CRIT. Error handlers can expand these into the same
hosts and service suffixes normally emitted by the check. Failed factory probes
are retried on later polls; execution uses the recovered datasource’s scheduling
restrictions before running the check. Shared datasource construction is serialized
per instance, while unrelated datasources can initialize concurrently.

Each `(hostname, service_name)` must be unique across collected results. Duplicate
identities become one UNKNOWN service with conflict details in Checkmk. Give
checks distinct service names, result suffixes, or hosts. `get-check-hostnames`
shows configuration-derived names; it cannot predict per-result dynamic hosts or
service suffixes. Ensure those hosts exist in Checkmk and run discovery after
collection.
