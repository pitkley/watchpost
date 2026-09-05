# Copyright 2025 TAKKT Industrial & Packaging GmbH
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

"""Per-process execution state, cache policy, and owned-resource lifecycle.

All per-pair polling work completes inside a context and lock before returning;
no generator holds either across a yield. Scheduling plans contain metadata,
while each poll evaluates a fresh decision.
"""

from __future__ import annotations

import threading
import traceback
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, assert_never

from .cache import InMemoryStorage, Storage
from .check import Check, CheckCache
from .datasource import DatasourceUnavailable
from .environment import Environment
from .executor import CheckExecutor
from .hostname import resolve_hostname
from .result import CheckState, ExecutionResult
from .scheduling_strategy import SchedulingDecision

if TYPE_CHECKING:
    from ._planning import _InstantiableDatasource
    from .app import Watchpost


@dataclass(frozen=True)
class _PollOutcome:
    decision: SchedulingDecision
    results: list[ExecutionResult] | None


class _CheckRuntime:
    def __init__(
        self,
        *,
        executor: CheckExecutor[list[ExecutionResult]] | None,
        max_workers: int | None,
        check_cache_storage: Storage | None,
    ) -> None:
        self.executor = (
            executor if executor is not None else CheckExecutor(max_workers=max_workers)
        )
        self._owned_executor = self.executor if executor is None else None
        self.cache = CheckCache(storage=check_cache_storage or InMemoryStorage())
        self._poll_lock = threading.RLock()
        self._poll_locks: dict[tuple[str, str], threading.RLock] = {}

    def shutdown(self, wait: bool = True) -> None:
        if self._owned_executor is not None:
            self._owned_executor.shutdown(wait=wait, cancel_futures=True)

    def _run_check(
        self,
        app: Watchpost,
        check: Check,
        environment: Environment,
        instantiable_datasources: dict[str, _InstantiableDatasource],
        *,
        custom_executor: CheckExecutor[list[ExecutionResult]] | None = None,
        use_cache: bool = True,
        scheduling_decision: SchedulingDecision | None = None,
        datasource_error: Exception | None = None,
    ) -> list[ExecutionResult] | None:
        """
        Execute a single check for one environment and return its results.

        This method resolves the piggyback host, evaluates scheduling, manages
        the cache, submits work to the executor, and normalizes error cases into
        `ExecutionResult` objects.

        Parameters:
            check:
                The `Check` to execute.
            environment:
                The environment the check targets.
            instantiable_datasources:
                Mapping of parameter names to datasource wrappers for this check.
            custom_executor:
                Optional executor to use instead of the application executor.
            use_cache:
                Whether to use and update the per-check cache.

        Returns:
            A list of `ExecutionResult` objects, or `None` if the check is not
            scheduled (`DONT_SCHEDULE`).
        """
        executor = custom_executor or self.executor

        piggyback_host = resolve_hostname(
            watchpost=app,
            environment=environment,
            check=check,
            result=None,
            fallback_to_default_hostname_generation=app.hostname_fallback_to_default_hostname_generation,
            coerce_into_valid_hostname=app.hostname_coerce_into_valid_hostname,
        )

        if scheduling_decision is None:
            scheduling_decision = app._resolve_check_scheduling_decision(
                check, environment
            )

        if use_cache:
            check_results_cache_entry = self.cache.get_check_results_cache_entry(
                check=check,
                environment=environment,
                return_expired=True,
            )
        else:
            check_results_cache_entry = None

        match scheduling_decision:
            case SchedulingDecision.SCHEDULE:
                # Fall through to the logic below.
                pass
            case SchedulingDecision.SKIP:
                if not check_results_cache_entry:
                    return check.apply_error_handlers(
                        environment,
                        ExecutionResult(
                            piggyback_host=piggyback_host,
                            service_name=check.service_name,
                            service_labels=check.service_labels,
                            environment_name=environment.name,
                            check_state=CheckState.UNKNOWN,
                            summary="Check is temporarily unschedulable and no prior results are available",
                            check_definition=check.invocation_information,
                        ),
                    )
                return check_results_cache_entry.value
            case SchedulingDecision.DONT_SCHEDULE:
                return None
            case _:
                assert_never(scheduling_decision)  # type: ignore[type-assertion-failure]

        executor_key = (check.identity, environment.name)
        should_update_cache = (
            check.cache_for is None
            or check_results_cache_entry is None
            or check_results_cache_entry.is_expired()
        )
        can_reuse_results = (
            check_results_cache_entry is not None
            and not check_results_cache_entry.is_expired()
        )

        check_has_errored = True
        try:
            if should_update_cache or not can_reuse_results:
                if datasource_error is not None:
                    raise datasource_error
                datasources = {
                    name: datasource.instance()
                    for name, datasource in instantiable_datasources.items()
                }
                executor.submit(
                    key=executor_key,
                    func=check.run_async if check.is_async else check.run_sync,
                    resubmit=False,
                    watchpost=app,
                    environment=environment,
                    datasources=datasources,
                )

            if can_reuse_results:
                return check_results_cache_entry.value  # type: ignore[union-attr]

            maybe_execution_results = executor.result(key=executor_key)
            check_has_errored = False

            # If the check is still running asynchronously but we did have a set
            # of results cached, we do want to fall back to this cache while it
            # is still available. This ensures that checks that are marked
            # `cache_for=None` that do have a cached result in a persistent
            # cache (if used) are not ignored. It also makes sure that any check
            # that has a `cache_for` specified does not return "check is running
            # asynchronously" in the short time period where the cache has
            # expired and the check was just submitted.
            if not maybe_execution_results and check_results_cache_entry:
                return check_results_cache_entry.value
        except DatasourceUnavailable as e:
            additional_details = f"\n\n{e!s}\n" + "".join(traceback.format_exception(e))
            if check_results_cache_entry and check_results_cache_entry.value:
                return [
                    replace(result, details=(result.details or "") + additional_details)
                    for result in check_results_cache_entry.value
                ]

            maybe_execution_results = check.apply_error_handlers(
                environment,
                ExecutionResult(
                    piggyback_host=piggyback_host,
                    service_name=check.service_name,
                    service_labels=check.service_labels,
                    environment_name=environment.name,
                    check_state=CheckState.UNKNOWN,
                    summary=str(e),
                    details=additional_details,
                    check_definition=check.invocation_information,
                ),
            )
        except Exception as e:
            maybe_execution_results = check.apply_error_handlers(
                environment,
                ExecutionResult(
                    piggyback_host=piggyback_host,
                    service_name=check.service_name,
                    service_labels=check.service_labels,
                    environment_name=environment.name,
                    check_state=CheckState.CRIT,
                    summary=str(e),
                    details="".join(traceback.format_exception(e)),
                    check_definition=check.invocation_information,
                ),
            )

        if not maybe_execution_results:
            return check.apply_error_handlers(
                environment,
                ExecutionResult(
                    piggyback_host=piggyback_host,
                    service_name=check.service_name,
                    service_labels=check.service_labels,
                    environment_name=environment.name,
                    check_state=CheckState.UNKNOWN,
                    summary="Check is running asynchronously and first results are not available yet",
                    check_definition=check.invocation_information,
                ),
            )

        if use_cache:
            self.cache.store_check_results(
                check=check,
                environment=environment,
                results=maybe_execution_results,
                override_cache_for=timedelta(0) if check_has_errored else None,
            )

        return maybe_execution_results

    def _poll_check(
        self,
        app: Watchpost,
        check: Check,
        environment: Environment,
        *,
        custom_executor: CheckExecutor[list[ExecutionResult]] | None = None,
        use_cache: bool = True,
    ) -> _PollOutcome:
        """Evaluate one pair, completing the transaction before returning or yielding."""
        with app.app_context():
            key = (check.identity, environment.name)
            with self._poll_lock:
                lock = self._poll_locks.setdefault(key, threading.RLock())
            with lock:
                plan = app._planner.resolve_plan(check)
                decision = plan.schedule(app.execution_environment, environment)
                results = self._run_check(
                    app=app,
                    check=check,
                    environment=environment,
                    instantiable_datasources=plan.datasources,
                    custom_executor=custom_executor,
                    use_cache=use_cache,
                    scheduling_decision=decision,
                    datasource_error=plan.datasource_error,
                )
        return _PollOutcome(decision, results)
