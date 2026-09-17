# Copyright 2026 Pit Kleyersburg <pitkley@googlemail.com>
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

import asyncio
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import override

import pytest

from watchpost import CheckResult, Environment, Watchpost, check, ok
from watchpost.cache import CacheEntry, CacheKey, InMemoryStorage
from watchpost.check import Check, CheckCache
from watchpost.executor import BlockingCheckExecutor, CheckExecutor
from watchpost.result import CheckState, ExecutionResult
from watchpost.scheduling_strategy import SchedulingDecision, SchedulingStrategy

from .utils import decode_checkmk_output, with_event


def _finish(
    executor: CheckExecutor,
    definition: Check,
    environment: Environment,
    release: Event,
) -> None:
    future = executor._state[(definition.identity, environment.name)].active_futures[0]
    release.set()
    try:
        _, unfinished = wait([future], timeout=5)
        assert not unfinished
    finally:
        release.clear()


@pytest.mark.parametrize("asynchronous", [False, True], ids=["thread", "async"])
@pytest.mark.parametrize("cache_for", [None, timedelta(0)], ids=["none", "zero"])
@pytest.mark.parametrize("seed", [None, "expired", "fresh"])
def test_uncached_results_survive_refresh_without_overlap(
    asynchronous, cache_for, seed, monkeypatch
):
    environment = Environment("uncached")
    storage = InMemoryStorage()
    calls = []
    with CheckExecutor(max_workers=4) as executor, with_event() as release:

        def result() -> CheckResult:
            if len(calls) == 2:
                raise ValueError("second execution failed")
            return ok(f"execution {len(calls)}")

        def sync_check() -> CheckResult:
            calls.append(1)
            assert release.wait(5)
            return result()

        async def async_check() -> CheckResult:
            calls.append(1)
            assert await asyncio.to_thread(release.wait, 5)
            return result()

        definition = check(
            name="uncached",
            service_labels={},
            environments=[environment],
            cache_for=cache_for,
        )(async_check if asynchronous else sync_check)
        if seed:
            storage.store(
                CacheEntry(
                    cache_key=CacheKey(
                        CheckCache._generate_check_cache_key(definition, environment),
                        "watchpost",
                    ),
                    value=[
                        ExecutionResult(
                            piggyback_host="",
                            service_name="uncached",
                            service_labels={},
                            environment_name=environment.name,
                            check_state=CheckState.OK,
                            summary="previous process",
                        )
                    ],
                    added_at=datetime.now(UTC) - timedelta(minutes=2),
                    ttl=timedelta(seconds=1)
                    if seed == "expired"
                    else timedelta(hours=1),
                )
            )

        def unexpected_write(_entry) -> None:
            pytest.fail("An uncached execution must not write to configured storage")

        monkeypatch.setattr(storage, "store", unexpected_write)
        app = Watchpost(
            checks=[definition],
            execution_environment=environment,
            executor=executor,
            check_cache_storage=storage,
        )

        def poll() -> tuple[str, str]:
            results = decode_checkmk_output(b"".join(app.run_checks()))
            (service,) = [r for r in results if r["service_name"] == "uncached"]
            return service["check_state"], service["summary"]

        initial = (
            ("OK", "previous process")
            if seed
            else (
                "UNKNOWN",
                "Check is running asynchronously and first results are not available yet",
            )
        )
        for _ in range(3):
            assert poll() == initial
            assert executor.statistics().total == 1

        # Exercise success, failure, and recovery, including many simultaneous
        # refresh polls. A retained result must never prevent fresh result pickup.
        for count, expected in enumerate(
            [
                ("OK", "execution 1"),
                ("CRIT", "second execution failed"),
                ("OK", "execution 3"),
            ],
            1,
        ):
            _finish(executor, definition, environment, release)
            assert poll() == expected
            assert len(calls) == count
            assert executor.statistics().total == 0
            with ThreadPoolExecutor(max_workers=8) as callers:
                polls = [callers.submit(poll) for _ in range(16)]
                assert [pending.result(5) for pending in polls] == [expected] * 16
            assert executor.statistics().total == 1


def test_uncached_fallback_is_isolated_by_check_environment_and_application():
    environments = [Environment("one"), Environment("two")]
    storage = InMemoryStorage()
    with CheckExecutor(max_workers=4) as executor, with_event() as release:

        def make_check(name: str) -> Check:
            @check(
                name=name, service_labels={}, environments=environments, cache_for=None
            )
            def definition(environment: Environment) -> CheckResult:
                assert release.wait(5)
                return ok(f"{name}/{environment.name}")

            return definition

        checks = [make_check("first"), make_check("second")]
        app = Watchpost(
            checks=[*checks],
            execution_environment=environments[0],
            executor=executor,
            check_cache_storage=storage,
        )

        def poll(watchpost: Watchpost) -> list[ExecutionResult]:
            return [
                result
                for definition in checks
                for result in watchpost.run_check(definition)
            ]

        assert all(result.check_state == CheckState.UNKNOWN for result in poll(app))
        futures = [state.active_futures[0] for state in executor._state.values()]
        release.set()
        _, unfinished = wait(futures, timeout=5)
        assert not unfinished
        release.clear()
        expected = [
            f"{definition.service_name}/{env.name}"
            for definition in checks
            for env in environments
        ]
        for _ in range(3):
            assert [result.summary for result in poll(app)] == expected
        assert executor.statistics().total == 4
        assert not storage.cache

        with CheckExecutor(max_workers=4) as restarted_executor:
            # Use the same definitions and storage, but a new application/runtime.
            # Release both executors before their context managers join workers.
            restarted = Watchpost(
                checks=[*checks],
                execution_environment=environments[0],
                executor=restarted_executor,
                check_cache_storage=storage,
            )
            try:
                assert all(
                    result.check_state == CheckState.UNKNOWN
                    for result in poll(restarted)
                )
            finally:
                release.set()


def test_use_cache_false_bypasses_and_does_not_replace_uncached_fallback():
    environment = Environment("uncached")
    calls = []
    with CheckExecutor(max_workers=1) as executor, with_event() as release:

        @check(
            name="uncached",
            service_labels={},
            environments=[environment],
            cache_for=None,
        )
        def definition() -> CheckResult:
            calls.append(1)
            assert release.wait(5)
            return ok(f"execution {len(calls)}")

        app = Watchpost(
            checks=[definition], execution_environment=environment, executor=executor
        )

        def poll(*, use_cache: bool = True) -> ExecutionResult:
            (result,) = app.run_check(definition, use_cache=use_cache)
            return result

        assert poll().check_state == CheckState.UNKNOWN
        _finish(executor, definition, environment, release)
        assert poll().summary == "execution 1"
        assert poll(use_cache=False).check_state == CheckState.UNKNOWN
        _finish(executor, definition, environment, release)
        assert poll(use_cache=False).summary == "execution 2"
        assert poll().summary == "execution 1"
        _finish(executor, definition, environment, release)
        assert poll().summary == "execution 3"


def test_uncached_fallback_respects_scheduling_decisions():
    environment = Environment("uncached")
    calls = []

    class SwitchStrategy(SchedulingStrategy):
        decision = SchedulingDecision.SKIP

        @override
        def schedule(self, check, current_execution_environment, target_environment):
            return self.decision

    strategy = SwitchStrategy()

    @check(
        name="uncached",
        service_labels={},
        environments=[environment],
        cache_for=None,
        scheduling_strategies=[strategy],
    )
    def definition() -> CheckResult:
        calls.append(1)
        return ok(f"execution {len(calls)}")

    with BlockingCheckExecutor() as executor:
        app = Watchpost(
            checks=[definition], execution_environment=environment, executor=executor
        )
        assert [result.check_state for result in app.run_check(definition)] == [
            CheckState.UNKNOWN
        ]
        assert not calls
        strategy.decision = SchedulingDecision.SCHEDULE
        assert [result.summary for result in app.run_check(definition)] == [
            "execution 1"
        ]
        strategy.decision = SchedulingDecision.SKIP
        for _ in range(3):
            assert [result.summary for result in app.run_check(definition)] == [
                "execution 1"
            ]
        strategy.decision = SchedulingDecision.DONT_SCHEDULE
        assert list(app.run_check(definition)) == []
        assert len(calls) == 1
        strategy.decision = SchedulingDecision.SCHEDULE
        assert [result.summary for result in app.run_check(definition)] == [
            "execution 2"
        ]
