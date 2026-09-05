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

from __future__ import annotations

import asyncio
from concurrent.futures import wait
from datetime import UTC, datetime, timedelta

import pytest

from watchpost.app import Watchpost
from watchpost.cache import CacheEntry, CacheKey, InMemoryStorage
from watchpost.check import CheckResult, check
from watchpost.environment import Environment
from watchpost.executor import CheckExecutor
from watchpost.result import CheckState, ExecutionResult, ok

from .utils import decode_checkmk_output, with_event


def _collect_output(app: Watchpost) -> bytes:
    return b"".join(app.run_checks())


def test_run_checks_returns_placeholder_until_result_is_ready():
    # Arrange: environment and a check function that waits on an Event
    env = Environment("env-nonblocking")
    watchpost_env = Environment("watchpost-env")
    with (
        CheckExecutor(max_workers=1) as executor,
        with_event() as event,
    ):

        @check(
            name="nonblocking-service",
            service_labels={"test": "true"},
            environments=[env],
            cache_for=None,  # rerun only after the previous result is picked up
        )
        def my_check() -> object:
            event.wait()
            return ok("All good")

        app = Watchpost(
            checks=[my_check],
            execution_environment=watchpost_env,
            executor=executor,
            version="test",
        )

        # Act 1: First run while the event is not set
        output1 = _collect_output(app)
        results1 = decode_checkmk_output(output1)

        # Assert 1: We should see the placeholder UNKNOWN for our service
        service_results1 = [
            r for r in results1 if r["service_name"] == "nonblocking-service"
        ]
        assert len(service_results1) == 1
        assert service_results1[0]["environment"] == env.name
        assert service_results1[0]["check_state"] == "UNKNOWN"
        assert (
            service_results1[0]["summary"]
            == "Check is running asynchronously and first results are not available yet"
        )

        # The synthetic 'Watchpost: executed checks' result should also be present
        assert any(r["service_name"] == "Watchpost: executed checks" for r in results1)

        # Act 2: Second run without setting the event yet should still yield UNKNOWN
        output2 = _collect_output(app)
        results2 = decode_checkmk_output(output2)
        service_results2 = [
            r for r in results2 if r["service_name"] == "nonblocking-service"
        ]
        assert len(service_results2) == 1
        assert service_results2[0]["check_state"] == "UNKNOWN"
        assert any(r["service_name"] == "Watchpost: executed checks" for r in results2)


def test_run_checks_returns_final_result_after_event_is_set():
    # Arrange: environment and a check function that waits on an Event
    env = Environment("env-nonblocking")
    watchpost_env = Environment("watchpost-env")

    with (
        CheckExecutor(max_workers=1) as executor,
        with_event() as event,
    ):

        @check(
            name="nonblocking-service",
            service_labels={"test": "true"},
            environments=[env],
            cache_for=None,  # rerun only after the previous result is picked up
        )
        def my_check() -> object:
            event.wait()
            return ok("All good")

        app = Watchpost(
            checks=[my_check],
            execution_environment=watchpost_env,
            executor=executor,
            version="test",
        )

        # Act 1: First run while the event is not set -> expect UNKNOWN placeholder
        output1 = _collect_output(app)
        results1 = decode_checkmk_output(output1)
        service_results1 = [
            r for r in results1 if r["service_name"] == "nonblocking-service"
        ]
        assert len(service_results1) == 1
        assert service_results1[0]["check_state"] == "UNKNOWN"

        # Signal the check can complete and wait for the first submitted future to finish
        event.set()
        key = (my_check.name, env.name)
        key_state = executor._state.get(key)
        assert key_state, "future should be present for failing check"
        assert key_state.active_futures, "future should be present for failing check"
        wait(executor._state[key].active_futures, return_when="ALL_COMPLETED")

        # Act 2: Second run -> expect OK from finished result
        output2 = _collect_output(app)
        results2 = decode_checkmk_output(output2)

        service_results2 = [
            r for r in results2 if r["service_name"] == "nonblocking-service"
        ]
        assert len(service_results2) == 1
        assert service_results2[0]["environment"] == env.name
        assert service_results2[0]["check_state"] == "OK"
        assert service_results2[0]["summary"] == "All good"

        # The synthetic 'Watchpost: executed checks' result should also be present
        assert any(r["service_name"] == "Watchpost: executed checks" for r in results2)


def test_executor_errored_integration_nonblocking():
    env = Environment("env-nonblocking")
    watchpost_env = Environment("watchpost-env")

    with (
        CheckExecutor(max_workers=1) as executor,
        with_event() as event,
    ):

        @check(
            name="failing-service",
            service_labels={"test": "true"},
            environments=[env],
            cache_for="1m",
        )
        def failing_check() -> object:
            event.wait()
            raise ValueError("boom")

        app = Watchpost(
            checks=[failing_check],
            execution_environment=watchpost_env,
            executor=executor,
            version="test",
        )

        # First run: ensures submission and returns placeholder UNKNOWN
        output1 = b"".join(app.run_checks())
        results1 = decode_checkmk_output(output1)
        sr1 = [r for r in results1 if r["service_name"] == "failing-service"]
        assert len(sr1) == 1 and sr1[0]["check_state"] == "UNKNOWN"

        # Let the check complete with an error and wait for its future
        key = (failing_check.name, env.name)
        event.set()
        key_state = executor._state.get(key)
        assert key_state, "future should be present for failing check"
        assert key_state.active_futures, "future should be present for failing check"
        wait(executor._state[key].active_futures, return_when="ALL_COMPLETED")

        # Before pickup: errored() should report the error with a key string
        errs = executor.errored()
        assert len(errs) == 1
        err_key, err_msg = next(iter(errs.items()))
        assert failing_check.name in err_key
        assert env.name in err_key
        assert err_msg == "boom"

        # Next run should attempt to pick up the errored future and create a CRIT result
        output2 = b"".join(app.run_checks())
        results2 = decode_checkmk_output(output2)
        sr2 = [r for r in results2 if r["service_name"] == "failing-service"]
        assert len(sr2) == 1
        assert sr2[0]["check_state"] == "CRIT"
        assert sr2[0]["summary"] == "boom"
        assert "ValueError: boom" in sr2[0]["details"]

        # After pickup, errored() must be cleared
        assert executor.errored() == {}


def _prepare_expired_cache_entry(
    storage,
    key: str,
    value: list[ExecutionResult],
) -> None:
    entry = CacheEntry[list[ExecutionResult]](
        cache_key=CacheKey(key=key, package="watchpost"),
        value=value,
        added_at=datetime.now(tz=UTC) - timedelta(minutes=5),
        ttl=timedelta(seconds=1),
    )
    storage.store(entry)


def test_async_uses_expired_cached_results_when_available_with_cache_for():
    # Arrange
    env = Environment("env-nonblocking")
    watchpost_env = Environment("watchpost-env")

    storage = InMemoryStorage()

    with (
        CheckExecutor(max_workers=1) as executor,
        with_event() as event,
    ):

        @check(
            name="nonblocking-service",
            service_labels={"test": "true"},
            environments=[env],
            cache_for="1s",  # cached entries are normally short-lived
        )
        def my_check() -> object:
            event.wait()
            return ok("Live result")

        # Pre-populate an expired cached result for this check/environment key
        cache_key = f"{my_check.name}:{env.name}"
        cached_results = [
            ExecutionResult(
                piggyback_host="",
                service_name="nonblocking-service",
                service_labels={"test": "true"},
                environment_name=env.name,
                check_state=CheckState.OK,
                summary="Cached OK",
            )
        ]
        _prepare_expired_cache_entry(storage, cache_key, cached_results)

        app = Watchpost(
            checks=[my_check],
            execution_environment=watchpost_env,
            executor=executor,
            version="test",
            check_cache_storage=storage,
        )

        # Act: First run while event is not set
        output = b"".join(app.run_checks())
        results = decode_checkmk_output(output)

        # Assert: we should receive the cached result (OK, summary "Cached OK")
        service_results = [
            r for r in results if r["service_name"] == "nonblocking-service"
        ]
        assert len(service_results) == 1
        assert service_results[0]["environment"] == env.name
        assert service_results[0]["check_state"] == "OK"
        assert service_results[0]["summary"] == "Cached OK"


def test_async_uses_expired_cached_results_when_available_with_cache_for_none():
    # Arrange
    env = Environment("env-nonblocking")
    watchpost_env = Environment("watchpost-env")

    storage = InMemoryStorage()

    with (
        CheckExecutor(max_workers=1) as executor,
        with_event() as event,
    ):

        @check(
            name="nonblocking-service",
            service_labels={"test": "true"},
            environments=[env],
            cache_for=None,  # honor persistent fallback while sharing pending work
        )
        def my_check() -> object:
            event.wait()
            return ok("Live result")

        # Pre-populate an expired cached result for this check/environment key
        cache_key = f"{my_check.name}:{env.name}"
        cached_results = [
            ExecutionResult(
                piggyback_host="",
                service_name="nonblocking-service",
                service_labels={"test": "true"},
                environment_name=env.name,
                check_state=CheckState.OK,
                summary="Cached OK",
            )
        ]
        _prepare_expired_cache_entry(storage, cache_key, cached_results)

        app = Watchpost(
            checks=[my_check],
            execution_environment=watchpost_env,
            executor=executor,
            version="test",
            check_cache_storage=storage,
        )

        # Act: First run while event is not set
        output = b"".join(app.run_checks())
        results = decode_checkmk_output(output)

        # Assert: we should receive the cached result (OK, summary "Cached OK")
        service_results = [
            r for r in results if r["service_name"] == "nonblocking-service"
        ]
        assert len(service_results) == 1
        assert service_results[0]["environment"] == env.name
        assert service_results[0]["check_state"] == "OK"
        assert service_results[0]["summary"] == "Cached OK"


def test_raising_check_does_not_flap():
    env = Environment("env-nonblocking")
    watchpost_env = Environment("watchpost-env")
    storage = InMemoryStorage()

    with (
        CheckExecutor(max_workers=1) as executor,
        with_event() as event,
    ):

        @check(
            name="failing-service",
            service_labels={"test": "true"},
            environments=[env],
            cache_for="1m",
        )
        def failing_check() -> CheckResult:
            event.wait()
            raise ValueError("failing service")

        app = Watchpost(
            checks=[failing_check],
            execution_environment=watchpost_env,
            executor=executor,
            version="test",
            check_cache_storage=storage,
        )

        # 1. Request output, result should be unknown while check runs
        output1 = b"".join(app.run_checks())
        results1 = decode_checkmk_output(output1)
        sr1 = [r for r in results1 if r["service_name"] == "failing-service"]
        assert len(sr1) == 1 and sr1[0]["check_state"] == "UNKNOWN"

        # 2. Set event and wait for execution to finish
        key = (failing_check.name, env.name)
        event.set()
        key_state = executor._state.get(key)
        assert key_state, "future should be present for failing check"
        assert key_state.active_futures, "future should be present for failing check"
        wait(executor._state[key].active_futures, return_when="ALL_COMPLETED")
        event.clear()

        # 3. Request output, result should be crit. The check should not be
        #    resubmitted, because it is defined to cache the result.
        output2 = b"".join(app.run_checks())
        results2 = decode_checkmk_output(output2)
        sr2 = [r for r in results2 if r["service_name"] == "failing-service"]
        assert len(sr2) == 1 and sr2[0]["check_state"] == "CRIT"
        assert executor._state.get(key) is None

        # 4. Request output again, result should be crit because cache is reused
        #    and check should have been resubmitted.
        output3 = b"".join(app.run_checks())
        results3 = decode_checkmk_output(output3)
        sr3 = [r for r in results3 if r["service_name"] == "failing-service"]
        assert len(sr3) == 1 and sr3[0]["check_state"] == "CRIT"
        assert executor._state.get(key) is not None

        # 5. Request output again, result should be unknown because cache should
        #    only be used once and the check was not allowed to complete (still
        #    waiting for event).
        output4 = b"".join(app.run_checks())
        results4 = decode_checkmk_output(output4)
        sr4 = [r for r in results4 if r["service_name"] == "failing-service"]
        assert len(sr4) == 1 and sr4[0]["check_state"] == "UNKNOWN"
        assert executor._state.get(key) is not None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_uncached_polls_share_one_execution_until_pickup(asynchronous):
    env = Environment("bounded")
    calls = []
    with CheckExecutor(max_workers=1) as executor, with_event() as release:

        def sync_check():
            calls.append(1)
            release.wait(5)
            return ok("done")

        async def async_check():
            calls.append(1)
            await asyncio.to_thread(release.wait, 5)
            return ok("done")

        definition = check(
            name="bounded", service_labels={}, environments=[env], cache_for=None
        )(async_check if asynchronous else sync_check)
        app = Watchpost(
            checks=[definition], execution_environment=env, executor=executor
        )
        for _ in range(30):
            results = list(app.run_check(definition))
            assert results[0].check_state == CheckState.UNKNOWN
            assert executor.statistics().total == 1
        key = (definition.name, env.name)
        pending = executor._state[key].active_futures[0]
        release.set()
        pending.result(5)
        picked_up = list(app.run_check(definition))
        assert picked_up[0].summary == "done"
        assert executor.statistics().total == 0
        assert len(calls) == 1
        # The next poll may complete immediately, but must start one fresh job.
        list(app.run_check(definition))
        executor.executor.shutdown(wait=True)
        if asynchronous:
            state = executor._state.get(key)
            if state:
                state.active_futures[0].result(5)
        assert len(calls) == 2
