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

from datetime import UTC, datetime, timedelta
from typing import override
from unittest.mock import MagicMock, patch

import pytest

from watchpost.app import Watchpost
from watchpost.cache import CacheEntry, CacheKey, InMemoryStorage
from watchpost.check import Check, CheckCache, check
from watchpost.datasource import Datasource, DatasourceUnavailable
from watchpost.environment import Environment
from watchpost.executor import BlockingCheckExecutor
from watchpost.globals import current_app
from watchpost.result import CheckState, ExecutionResult, ok
from watchpost.scheduling_strategy import InvalidCheckConfiguration

from .utils import decode_checkmk_output

TEST_ENVIRONMENT = Environment("test-env")


class TestDatasource(Datasource):
    pass


def test_watchpost_initialization():
    """Test that an Watchpost object can be properly initialized."""
    # Create a mock check
    mock_check = MagicMock(spec=Check)

    # Initialize the Watchpost object
    app = Watchpost(
        checks=[mock_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Verify the Watchpost object was initialized correctly
    assert app.checks == [mock_check]


def test_app_context():
    """Test that the app_context method properly sets and resets the context variable."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Before entering the context, current_app should raise an error
    with pytest.raises(RuntimeError, match="Watchpost application is not available"):
        _ = current_app.__name__  # type: ignore[unresolved-attribute]

    # Within the context, current_app should be the app instance
    with app.app_context():
        assert current_app._get_current_object() is app  # type: ignore[unresolved-attribute]

    # After exiting the context, current_app should raise an error again
    with pytest.raises(RuntimeError, match="Watchpost application is not available"):
        _ = current_app.__name__  # type: ignore[unresolved-attribute]


def test_app_context_exception_handling():
    """Test that the app_context method properly handles exceptions."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Test that the context is properly reset even if an exception occurs
    try:
        with app.app_context():
            assert current_app._get_current_object() is app  # type: ignore[unresolved-attribute]
            raise ValueError("Test exception")
    except ValueError:
        pass

    # After the exception, current_app should raise an error
    with pytest.raises(RuntimeError, match="Watchpost application is not available"):
        _ = current_app.__name__  # type: ignore[unresolved-attribute]


def test_app_context_lookup_error():
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    with pytest.raises(LookupError, match="lookup error across yield"):
        with app.app_context():
            with app.app_context():
                raise LookupError("lookup error across yield")


def test_run_checks_once():
    """Test that the run_checks_once method runs all checks and outputs the results."""
    # Create a mock check that returns a known ExecutionResult
    mock_check = MagicMock(spec=Check)
    mock_check.name = "Test Check"
    mock_check.identity = "Test Check"
    mock_check.service_name = "Test Check"
    mock_check.service_labels = {}
    mock_check.environments = [TEST_ENVIRONMENT]
    mock_check.cache_for = None
    mock_check.is_async = False
    execution_result = ExecutionResult(
        piggyback_host="test-host",
        service_name="test-service",
        service_labels={"env": "test"},
        environment_name="test-env",
        check_state=CheckState.OK,
        summary="Test summary",
    )
    mock_check.run_sync.return_value = [execution_result]

    # Initialize the Watchpost object
    app = Watchpost(
        checks=[mock_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Mock sys.stdout.buffer.write to capture the output
    with patch("sys.stdout.buffer.write") as mock_write:
        # Run the checks
        app.run_checks_once()

        # Verify that the check was run
        mock_check.run_sync.assert_called_once()

        # Verify that sys.stdout.buffer.write was called with the expected data
        assert mock_write.call_count > 0

        # Collect all the data written to stdout
        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)

        # Decode the base64 data using the utility function
        json_data = decode_checkmk_output(all_data)[0]

        # Verify the decoded data contains the expected values
        assert json_data["service_name"] == "test-service"
        assert json_data["environment"] == "test-env"
        assert json_data["check_state"] == "OK"
        assert json_data["summary"] == "Test summary"


def test_run_checks_once_with_multiple_checks():
    """Test that the run_checks_once method runs multiple checks."""
    # Create two mock checks
    mock_check1 = MagicMock(spec=Check)
    mock_check1.name = "Test Check 1"
    mock_check1.identity = "Test Check 1"
    mock_check1.service_name = "Test Check 1"
    mock_check1.service_labels = {}
    mock_check1.environments = [TEST_ENVIRONMENT]
    mock_check1.cache_for = None
    mock_check1.is_async = False
    execution_result1 = ExecutionResult(
        piggyback_host="test-host-1",
        service_name="test-service-1",
        service_labels={"env": "test"},
        environment_name="test-env",
        check_state=CheckState.OK,
        summary="Test summary 1",
    )
    mock_check1.run_sync.return_value = [execution_result1]

    mock_check2 = MagicMock(spec=Check)
    mock_check2.name = "Test Check 2"
    mock_check2.identity = "Test Check 2"
    mock_check2.service_name = "Test Check 2"
    mock_check2.service_labels = {}
    mock_check2.environments = [TEST_ENVIRONMENT]
    mock_check2.cache_for = None
    mock_check2.is_async = False
    execution_result2 = ExecutionResult(
        piggyback_host="test-host-2",
        service_name="test-service-2",
        service_labels={"env": "test"},
        environment_name="test-env",
        check_state=CheckState.WARN,
        summary="Test summary 2",
    )
    mock_check2.run_sync.return_value = [execution_result2]

    # Initialize the Watchpost object with both checks
    app = Watchpost(
        checks=[mock_check1, mock_check2],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Mock sys.stdout.buffer.write to capture the output
    with patch("sys.stdout.buffer.write") as mock_write:
        # Run the checks
        app.run_checks_once()

        # Verify that both checks were run
        mock_check1.run_sync.assert_called_once()
        mock_check2.run_sync.assert_called_once()

        # Collect all the data written to stdout
        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)

        # Verify the basic structure
        assert b"test-host-1" in all_data
        assert b"test-host-2" in all_data

        # Decode the base64 data using the utility function
        json_data_list = decode_checkmk_output(all_data)

        # Verify we found three results (our two and the one default watchpost check)
        assert len(json_data_list) == 3

        # Verify the first result
        assert any(
            data["service_name"] == "test-service-1"
            and data["environment"] == "test-env"
            and data["check_state"] == "OK"
            and data["summary"] == "Test summary 1"
            for data in json_data_list
        )

        # Verify the second result
        assert any(
            data["service_name"] == "test-service-2"
            and data["environment"] == "test-env"
            and data["check_state"] == "WARN"
            and data["summary"] == "Test summary 2"
            for data in json_data_list
        )


def test_run_checks_once_with_real_check():
    """Test that the run_checks_once method works with a real Check object."""

    # Create a simple check function
    def check_func(test_datasource: TestDatasource):
        _ = test_datasource
        return ok("Test passed")

    # Create a real Check object
    check = Check(
        check_function=check_func,
        service_name="test-service",
        service_labels={"env": "test"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )

    # Initialize the Watchpost object
    app = Watchpost(
        checks=[check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )
    app.register_datasource(TestDatasource)

    # Mock sys.stdout.buffer.write to capture the output
    with patch("sys.stdout.buffer.write") as mock_write:
        # Run the checks
        app.run_checks_once()

        # Collect all the data written to stdout
        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)

        # Verify the basic structure
        expected_host = "test-service-test-env"
        assert expected_host.encode() in all_data

        # Decode the base64 data using the utility function
        json_data = decode_checkmk_output(all_data)[0]

        # Verify the decoded data contains the expected values
        assert json_data["service_name"] == "test-service"
        assert json_data["environment"] == "test-env"
        assert json_data["check_state"] == "OK"
        assert json_data["summary"] == "Test passed"


def test_ensure_current_app_is_set_in_check():
    @check(
        name="Current app is set in check",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def check_func(test_datasource: TestDatasource):
        _ = test_datasource

        from watchpost.globals import current_app

        return ok(repr(current_app))

    app = Watchpost(
        checks=[check_func],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )
    app.register_datasource(TestDatasource)

    with app.app_context():
        raw_output = b"".join(app.run_checks())
        decoded_output = decode_checkmk_output(raw_output)
        for item in decoded_output:
            item.pop("check_definition", None)

    assert sorted(decoded_output, key=lambda result: result["service_name"]) == sorted(
        [
            {
                "check_state": "OK",
                "details": None,
                "environment": "test-env",
                "metrics": [],
                "service_labels": {"test": "true"},
                "service_name": "Current app is set in check",
                "summary": repr(app),
            },
            {
                "check_state": "OK",
                "details": "Eligible check/environment pairs:\n- tests.test_app.test_ensure_current_app_is_set_in_check.<locals>.check_func [test-env]",
                "environment": "test-env",
                "metrics": [],
                "service_labels": {},
                "service_name": "Watchpost: executed checks",
                "summary": "1 check/environment pairs eligible to run",
            },
        ],
        key=lambda result: result["service_name"],
    )


def test_run_checks_skip_without_prior_results_returns_unknown():
    from watchpost.scheduling_strategy import SchedulingDecision, SchedulingStrategy

    class AlwaysSkipStrategy(SchedulingStrategy):
        @override
        def schedule(self, check, current_execution_environment, target_environment):
            return SchedulingDecision.SKIP

    @check(
        name="Skip without prior",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def my_check():
        raise AssertionError("Should not be executed when SKIP without prior results")

    app = Watchpost(
        checks=[my_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
        default_scheduling_strategies=[AlwaysSkipStrategy()],
    )

    with patch("sys.stdout.buffer.write") as mock_write:
        app.run_checks_once()
        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)
        results = decode_checkmk_output(all_data)
        result = next(r for r in results if r["service_name"] == "Skip without prior")
        assert result["check_state"] == "UNKNOWN"
        assert (
            result["summary"]
            == "Check is temporarily unschedulable and no prior results are available"
        )


def test_run_checks_skip_with_prior_results_reuses_cache():
    from watchpost.scheduling_strategy import SchedulingDecision, SchedulingStrategy

    class AlwaysSkipStrategy(SchedulingStrategy):
        @override
        def schedule(self, check, current_execution_environment, target_environment):
            return SchedulingDecision.SKIP

    @check(
        name="Skip with prior",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for="5s",
    )
    def my_check():
        raise AssertionError("Should not be executed when cache exists and SKIP")

    app = Watchpost(
        checks=[my_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
        default_scheduling_strategies=[AlwaysSkipStrategy()],
    )

    # Prime the cache with a prior OK result
    execution_result = ExecutionResult(
        piggyback_host="pre",
        service_name="Skip with prior",
        service_labels={"test": "true"},
        environment_name=TEST_ENVIRONMENT.name,
        check_state=CheckState.OK,
        summary="Cached result",
    )
    app._check_cache.store_check_results(my_check, TEST_ENVIRONMENT, [execution_result])

    with patch("sys.stdout.buffer.write") as mock_write:
        app.run_checks_once()
        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)
        results = decode_checkmk_output(all_data)
        result = next(r for r in results if r["service_name"] == "Skip with prior")
        assert result["check_state"] == "OK"
        assert result["summary"] == "Cached result"


def test_run_checks_reuses_cached_results_under_schedule():
    call_count = {"n": 0}

    @check(
        name="Cached schedule",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for="10s",
    )
    def my_check():
        call_count["n"] += 1
        return ok(f"Run {call_count['n']}")

    app = Watchpost(
        checks=[my_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # First run: executes the check and stores the result in cache
    with patch("sys.stdout.buffer.write") as mock_write1:
        app.run_checks_once()
        all_data1 = b"".join(
            call_args[0][0] for call_args in mock_write1.call_args_list
        )
        results1 = decode_checkmk_output(all_data1)
        res1 = next(r for r in results1 if r["service_name"] == "Cached schedule")
        assert res1["summary"] == "Run 1"
        assert call_count["n"] == 1

    # Second run: should reuse cached results, not execute the check again
    with patch("sys.stdout.buffer.write") as mock_write2:
        app.run_checks_once()
        all_data2 = b"".join(
            call_args[0][0] for call_args in mock_write2.call_args_list
        )
        results2 = decode_checkmk_output(all_data2)
        res2 = next(r for r in results2 if r["service_name"] == "Cached schedule")
        assert res2["summary"] == "Run 1"
        assert call_count["n"] == 1


def test_run_checks_dont_schedule_produces_no_results():
    from watchpost.scheduling_strategy import SchedulingDecision, SchedulingStrategy

    class AlwaysDontScheduleStrategy(SchedulingStrategy):
        @override
        def schedule(self, check, current_execution_environment, target_environment):
            return SchedulingDecision.DONT_SCHEDULE

    @check(
        name="Dont schedule",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def my_check():
        raise AssertionError("Should not be executed when DONT_SCHEDULE")

    app = Watchpost(
        checks=[my_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
        default_scheduling_strategies=[AlwaysDontScheduleStrategy()],
    )

    with patch("sys.stdout.buffer.write") as mock_write:
        app.run_checks_once()
        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)
        results = decode_checkmk_output(all_data)

        # There should only be the synthetic "Watchpost: executed checks" result
        assert len(results) == 1
        assert results[0]["service_name"] == "Watchpost: executed checks"
        # And definitely no result for our check
        assert not any(r["service_name"] == "Dont schedule" for r in results)


def test_datasource_unavailable_without_cache_returns_unknown():
    @check(
        name="DS Unavailable",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def failing_check():
        raise DatasourceUnavailable("temporary outage")

    app = Watchpost(
        checks=[failing_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    with patch("sys.stdout.buffer.write") as mock_write:
        app.run_checks_once()
        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)
        results = decode_checkmk_output(all_data)

    result = next(r for r in results if r["service_name"] == "DS Unavailable")
    assert result["check_state"] == "UNKNOWN"
    assert result["summary"] == "temporary outage"
    assert result["details"] is not None
    assert "temporary outage" in result["details"]
    assert "DatasourceUnavailable" in result["details"]
    # Synthetic result is present
    assert any(r["service_name"] == "Watchpost: executed checks" for r in results)


def test_datasource_unavailable_with_expired_cache_returns_enriched_cached_result():
    calls = {"n": 0}

    @check(
        name="DS Unavailable Cached",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=timedelta(microseconds=1),
    )
    def flaky_check():
        calls["n"] += 1
        if calls["n"] == 1:
            return ok("Cached ok")
        raise DatasourceUnavailable("backend down")

    app = Watchpost(
        checks=[flaky_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # First run: stores OK in cache
    with patch("sys.stdout.buffer.write") as mock_write1:
        app.run_checks_once()
        all_data1 = b"".join(
            call_args[0][0] for call_args in mock_write1.call_args_list
        )
        results1 = decode_checkmk_output(all_data1)
        res1 = next(r for r in results1 if r["service_name"] == "DS Unavailable Cached")
        assert res1["check_state"] == "OK"
        assert res1["summary"] == "Cached ok"

    # Second run: raises DatasourceUnavailable, should reuse cached result with enriched details
    with patch("sys.stdout.buffer.write") as mock_write2:
        app.run_checks_once()
        all_data2 = b"".join(
            call_args[0][0] for call_args in mock_write2.call_args_list
        )
        results2 = decode_checkmk_output(all_data2)
        res2 = next(r for r in results2 if r["service_name"] == "DS Unavailable Cached")

    assert res2["check_state"] == "OK"
    assert res2["summary"] == "Cached ok"
    # details should be added (was None on first run) and include the exception info
    assert res2["details"] is not None
    assert "backend down" in res2["details"]
    assert "DatasourceUnavailable" in res2["details"]


def _prepare_expired_cache_entry(
    storage: InMemoryStorage,
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


def test_cache_is_used_only_if_no_fresh_results_available():
    # Arrange: a blocking executor makes fresh results deterministically available
    env = Environment("blocking-env")
    watchpost_env = Environment("watchpost-env")

    storage = InMemoryStorage()

    @check(
        name="blocking-service",
        service_labels={"test": "true"},
        environments=[env],
        cache_for="1s",  # cache exists but is expired below
    )
    def my_check() -> object:
        return ok("Live result")

    # Pre-populate an expired cached result for this check/environment key
    cache_key = CheckCache._generate_check_cache_key(my_check, env)
    cached_results = [
        ExecutionResult(
            piggyback_host="",
            service_name="blocking-service",
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
        executor=BlockingCheckExecutor(),
        version="test",
        check_cache_storage=storage,
    )

    # Act: Run checks; with a blocking executor, a fresh result will be available
    output = b"".join(app.run_checks())
    results = decode_checkmk_output(output)

    # Assert: the fresh result must be used, not the expired cached one
    service_results = [r for r in results if r["service_name"] == "blocking-service"]
    assert len(service_results) == 1
    assert service_results[0]["environment"] == env.name
    assert service_results[0]["check_state"] == "OK"
    assert service_results[0]["summary"] == "Live result"  # not "Cached OK"


def test_verify_check_scheduling_reports_missing_required_kwargs() -> None:
    # Define a check function that declares a parameter which Watchpost cannot provide
    # (it's not an Environment nor a Datasource-typed argument)
    def my_check(foo: int):
        _ = foo
        return ok("fine")

    check = Check(
        check_function=my_check,
        service_name="svc-kwargs-mismatch",
        service_labels={},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )

    app = Watchpost(
        checks=[check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    with pytest.raises(ExceptionGroup) as eg:
        app.verify_check_scheduling(force=True)

    assert isinstance(eg.value, ExceptionGroup)
    assert eg.value.message == "One or more checks are not well-configured"
    # The group should contain exactly one InvalidCheckConfiguration
    assert len(eg.value.exceptions) == 1
    exc = eg.value.exceptions[0]
    assert isinstance(exc, InvalidCheckConfiguration)
    # Error message should explain required vs provided kwargs
    msg = str(exc)
    assert "Invalid check configuration:" in msg
    assert "Failed to resolve datasources: Unsupported parameter" in msg
    assert "foo: <class 'int'>" in msg
    # And reference the affected check for easier debugging
    assert "svc-kwargs-mismatch" in msg or check.service_name in msg


class MissingDatasource(Datasource):
    pass


def test_verify_check_scheduling_wraps_datasource_resolution_errors() -> None:
    # Define a datasource type that is NOT registered with the app

    # The check requires the unregistered datasource, which should cause
    # Watchpost._resolve_datasources to raise a ValueError.
    def needs_missing_ds(ds: MissingDatasource):
        _ = ds
        return ok("won't run")

    check = Check(
        check_function=needs_missing_ds,
        service_name="svc-missing-ds",
        service_labels={},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )

    app = Watchpost(
        checks=[check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    with pytest.raises(ExceptionGroup) as eg:
        app.verify_check_scheduling(force=True)

    assert isinstance(eg.value, ExceptionGroup)
    assert eg.value.message == "One or more checks are not well-configured"
    assert len(eg.value.exceptions) == 1
    exc = eg.value.exceptions[0]
    assert isinstance(exc, InvalidCheckConfiguration)
    # The reason should be wrapped with a helpful prefix and include the underlying message
    msg = str(exc)
    assert "Failed to resolve datasources:" in msg
    assert "No datasource definition for" in msg
    # And reference the affected check
    assert "svc-missing-ds" in msg or check.service_name in msg
