# Runtime architecture

`Watchpost` is the public application façade. It accepts configuration, discovers
checks, validates startup, provides application contexts and the ASGI lifecycle,
and renders check and synthetic output. Its private collaborators separate three
kinds of state:

- `_CheckPlanner` in `src/watchpost/_planning.py` owns datasource registrations,
  factory wrappers, resolved parameter bindings, and ordered scheduling
  strategies. `_ResolvedCheckPlan` groups that metadata for a check. Plans reuse
  resolved metadata and evaluate a fresh scheduling decision for each target and
  poll. Datasource construction remains lazy except for the existing factory
  strategy inspection that may need an instance. Failed factory probes produce
  provisional plans: the current poll reports the failure, and later polls retry
  resolution before applying the recovered datasource’s scheduling restrictions.
- `_CheckRuntime` in `src/watchpost/_runtime.py` owns the executor, result cache,
  per-pair polling locks, and executor ownership policy. It applies the plan,
  submits or retrieves work, materializes failure results, publishes the cache,
  and releases the executor originally created by the application. Replacing the
  selected executor does not transfer ownership of the replacement. `_PollOutcome` returns the actual decision and
  results to the application, allowing request-local diagnostics.
- `Check` and `CheckExecutor` handle check invocation and work coordination.
  Check invocation establishes application context and captures output while
  materializing results, including generators. The executor manages threads,
  async tasks, result pickup, and shutdown.

A poll transaction holds its check/environment lock across submission, result
pickup, and cache publication. It releases both the lock and application context
before results are yielded to a streaming response. Keep this boundary intact:
ASGI streaming can resume a synchronous generator in a different copied context.
Different check/environment pairs can still run concurrently. Datasource wrappers
are published under a short metadata lock; each wrapper serializes its own
initialization. User constructors run outside the metadata lock, so unrelated
datasources can initialize concurrently. Per-check resolution locks protect plan
publication. Scheduling callbacks use the current plan’s context-local snapshot
even if another poll has since resolved a previously unavailable factory.

Scheduling plans do not cache `SCHEDULE`, `SKIP`, or `DONT_SCHEDULE` decisions.
Strategies may depend on current conditions. The synthetic service uses the
outcomes from that request, rather than calling strategies again or sharing an
accumulator across requests.

The application retains thin private resolution delegates for existing internal
callers, including scheduling validation. New resolution logic belongs in the
planner; execution/cache policy belongs in the runtime. Configure registrations
and strategies before serving requests: this refactor preserves the existing
metadata caching and validation lifecycle, without adding live reconfiguration.

The regression suite covers output isolation, concurrent polls, datasource
factories and failures, scheduling, hostname generation, streaming contexts,
resource shutdown, cache storage, and the Checkmk integration contract. Run the
commands in the repository's `AGENTS.md`, and see `.tools/README.md` for the smoke
test against an actual Checkmk installation.
