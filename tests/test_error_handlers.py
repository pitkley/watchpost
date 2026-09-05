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

"""Tests for error handler functionality."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from watchpost.app import Watchpost
from watchpost.check import (
    Check,
    ErrorHandler,
    check,
    expand_by_hostname,
    expand_by_name_suffix,
)
from watchpost.datasource import Datasource
from watchpost.environment import Environment
from watchpost.result import CheckState, ExecutionResult, Metric


class TestDatasource(Datasource):
    """Test datasource for testing."""


@pytest.fixture
def mock_watchpost() -> MagicMock:
    """Mock Watchpost with standard settings."""
    watchpost = MagicMock(spec=Watchpost)
    watchpost.hostname_strategy = None
    watchpost.hostname_fallback_to_default_hostname_generation = True
    watchpost.hostname_coerce_into_valid_hostname = True
    return watchpost


@pytest.fixture(autouse=True)
def patch_current_app(mock_watchpost: MagicMock) -> Generator[None]:
    """Automatically patch current_app for all tests."""
    with patch("watchpost.check.current_app", mock_watchpost):
        yield


@pytest.fixture
def test_environment() -> Environment:
    """Standard test environment."""
    return Environment("test_env")


@pytest.fixture
def mock_execution_result(test_environment: Environment) -> ExecutionResult:
    """Standard ExecutionResult for testing."""
    return ExecutionResult(
        piggyback_host="test-host",
        service_name="test-service",
        service_labels={"env": "test"},
        environment_name=test_environment.name,
        check_state=CheckState.CRIT,
        summary="Test error summary",
        details="Test error details",
        metrics=None,
        check_definition=None,
    )


@pytest.fixture
def mock_check(test_environment: Environment) -> Check:
    """Standard Check for testing."""

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    return Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
    )


# Unit Tests for expand_by_hostname


def test_expand_by_hostname_single_hostname(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
) -> None:
    """Test that expand_by_hostname works with a single hostname."""
    handler = expand_by_hostname(["explicit-host"])

    results = handler(mock_check, test_environment, [mock_execution_result])

    assert len(results) == 1
    assert results[0].piggyback_host == "explicit-host"
    # Other fields should be preserved
    assert results[0].service_name == mock_execution_result.service_name
    assert results[0].service_labels == mock_execution_result.service_labels
    assert results[0].check_state == mock_execution_result.check_state
    assert results[0].summary == mock_execution_result.summary
    assert results[0].details == mock_execution_result.details


def test_expand_by_hostname_multiple_hostnames(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test that expand_by_hostname creates cartesian product with multiple hostnames."""
    handler = expand_by_hostname(["host1", "host2"])

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        # Start with 2 input results
        input_results = [mock_execution_result, mock_execution_result]
        results = handler(mock_check, test_environment, input_results)

        # Should get 4 results (2 input x 2 hostnames)
        assert len(results) == 4

        # Collect unique hostnames
        hostnames = {r.piggyback_host for r in results}
        assert hostnames == {"host1", "host2"}

        # Each input result should be duplicated for each hostname
        host1_results = [r for r in results if r.piggyback_host == "host1"]
        host2_results = [r for r in results if r.piggyback_host == "host2"]
        assert len(host1_results) == 2
        assert len(host2_results) == 2
    finally:
        setattr(watchpost.check, "current_app", original_app)


def test_expand_by_hostname_preserves_all_fields(
    mock_check: Check,
    test_environment: Environment,
) -> None:
    """Test that expand_by_hostname preserves all non-hostname fields."""
    # Fill in all fields
    result = ExecutionResult(
        piggyback_host="original-host",
        service_name="test-service",
        service_labels={"key": "value", "env": "prod"},
        environment_name=test_environment.name,
        check_state=CheckState.WARN,
        summary="Test summary",
        details="Test details with\nmultiple lines",
        metrics=[Metric("metric1", 123.45)],
        check_definition=None,
    )

    handler = expand_by_hostname(["new-host"])

    results = handler(mock_check, test_environment, [result])

    assert len(results) == 1
    transformed = results[0]

    # Hostname should change
    assert transformed.piggyback_host == "new-host"

    # All other fields should be preserved
    assert transformed.service_name == result.service_name
    assert transformed.service_labels == result.service_labels
    assert transformed.environment_name == result.environment_name
    assert transformed.check_state == result.check_state
    assert transformed.summary == result.summary
    assert transformed.details == result.details
    assert transformed.metrics == result.metrics
    assert transformed.check_definition == result.check_definition


def test_expand_by_hostname_empty_list(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test that expand_by_hostname with empty list returns empty list."""
    handler = expand_by_hostname([])

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        results = handler(mock_check, test_environment, [mock_execution_result])
        # With empty hostname list, cartesian product yields empty list
        assert len(results) == 0
    finally:
        setattr(watchpost.check, "current_app", original_app)


# Unit Tests for expand_by_name_suffix


def test_expand_by_name_suffix_single_suffix(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
) -> None:
    """Test that expand_by_name_suffix works with a single suffix."""
    handler = expand_by_name_suffix([":suffix"])

    results = handler(mock_check, test_environment, [mock_execution_result])

    assert len(results) == 1
    assert results[0].service_name == "test-service:suffix"
    # Other fields preserved
    assert results[0].piggyback_host == mock_execution_result.piggyback_host
    assert results[0].check_state == mock_execution_result.check_state


def test_expand_by_name_suffix_multiple_suffixes(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
) -> None:
    """Test that expand_by_name_suffix creates cartesian product with multiple suffixes."""
    handler = expand_by_name_suffix([":a", ":b"])

    # Start with 2 input results
    input_results = [mock_execution_result, mock_execution_result]
    results = handler(mock_check, test_environment, input_results)

    # Should get 4 results (2 input x 2 suffixes)
    assert len(results) == 4

    # Check all suffix combinations present
    service_names = {r.service_name for r in results}
    assert service_names == {"test-service:a", "test-service:b"}

    # Each suffix appears twice (once for each input result)
    suffix_a_results = [r for r in results if r.service_name == "test-service:a"]
    suffix_b_results = [r for r in results if r.service_name == "test-service:b"]
    assert len(suffix_a_results) == 2
    assert len(suffix_b_results) == 2


def test_expand_by_name_suffix_preserves_all_fields(
    mock_check: Check,
    test_environment: Environment,
) -> None:
    """Test that expand_by_name_suffix preserves all non-service_name fields."""
    result = ExecutionResult(
        piggyback_host="test-host",
        service_name="original-service",
        service_labels={"key": "value"},
        environment_name=test_environment.name,
        check_state=CheckState.CRIT,
        summary="Summary",
        details="Details",
        metrics=None,
        check_definition=None,
    )

    handler = expand_by_name_suffix([":new"])

    results = handler(mock_check, test_environment, [result])

    assert len(results) == 1
    transformed = results[0]

    # Service name should change
    assert transformed.service_name == "original-service:new"

    # All other fields preserved
    assert transformed.piggyback_host == result.piggyback_host
    assert transformed.service_labels == result.service_labels
    assert transformed.environment_name == result.environment_name
    assert transformed.check_state == result.check_state
    assert transformed.summary == result.summary
    assert transformed.details == result.details
    assert transformed.metrics == result.metrics
    assert transformed.check_definition == result.check_definition


def test_expand_by_name_suffix_empty_list(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
) -> None:
    """Test that expand_by_name_suffix with empty list returns empty list."""
    handler = expand_by_name_suffix([])

    results = handler(mock_check, test_environment, [mock_execution_result])

    # With empty suffix list, cartesian product yields empty list
    assert len(results) == 0


# Integration Tests for Handler Composition


def test_composition_hostname_then_suffix(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test that hostname → suffix composition creates correct cartesian product."""
    hostname_handler = expand_by_hostname(["host1", "host2"])
    suffix_handler = expand_by_name_suffix([":a", ":b"])

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        # Apply hostname handler first (1 result → 2 results)
        hostname_results = hostname_handler(
            mock_check, test_environment, [mock_execution_result]
        )
        assert len(hostname_results) == 2

        # Apply suffix handler to hostname results (2 results → 4 results)
        final_results = suffix_handler(mock_check, test_environment, hostname_results)
        assert len(final_results) == 4

        # Verify all combinations present
        expected = {
            ("host1", "test-service:a"),
            ("host1", "test-service:b"),
            ("host2", "test-service:a"),
            ("host2", "test-service:b"),
        }
        actual = {(r.piggyback_host, r.service_name) for r in final_results}
        assert actual == expected
    finally:
        setattr(watchpost.check, "current_app", original_app)


def test_composition_suffix_then_hostname(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test that suffix → hostname composition produces same result as hostname → suffix."""
    hostname_handler = expand_by_hostname(["host1", "host2"])
    suffix_handler = expand_by_name_suffix([":a", ":b"])

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        # Apply in reverse order
        suffix_results = suffix_handler(
            mock_check, test_environment, [mock_execution_result]
        )
        final_results = hostname_handler(mock_check, test_environment, suffix_results)

        # Should get same 4 combinations
        assert len(final_results) == 4
        expected = {
            ("host1", "test-service:a"),
            ("host1", "test-service:b"),
            ("host2", "test-service:a"),
            ("host2", "test-service:b"),
        }
        actual = {(r.piggyback_host, r.service_name) for r in final_results}
        assert actual == expected
    finally:
        setattr(watchpost.check, "current_app", original_app)


def test_composition_multiple_handlers(
    mock_check: Check,
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test composition of 3+ handlers."""

    def custom_handler(
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
    ) -> list[ExecutionResult]:
        """Custom handler that adds a marker to details."""
        _ = check, environment
        transformed = []
        for result in results:
            new_result = ExecutionResult(
                piggyback_host=result.piggyback_host,
                service_name=result.service_name,
                service_labels=result.service_labels,
                environment_name=result.environment_name,
                check_state=result.check_state,
                summary=result.summary,
                details=f"{result.details}\n[custom marker]",
                metrics=result.metrics,
                check_definition=result.check_definition,
            )
            transformed.append(new_result)
        return transformed

    hostname_handler = expand_by_hostname(["host1"])
    suffix_handler = expand_by_name_suffix([":suffix"])

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        # Apply all three handlers in sequence
        results1 = hostname_handler(
            mock_check, test_environment, [mock_execution_result]
        )
        results2 = suffix_handler(mock_check, test_environment, results1)
        final_results = custom_handler(mock_check, test_environment, results2)

        # Should have 1 result with all transformations applied
        assert len(final_results) == 1
        result = final_results[0]
        assert result.piggyback_host == "host1"
        assert result.service_name == "test-service:suffix"
        assert result.details is not None
        assert "[custom marker]" in result.details
    finally:
        setattr(watchpost.check, "current_app", original_app)


# Tests for Check.apply_error_handlers


def test_apply_error_handlers_no_handlers(
    test_environment: Environment, mock_execution_result: ExecutionResult
) -> None:
    """Test that apply_error_handlers returns input unchanged when no handlers."""

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=None,
    )

    results = mock_check.apply_error_handlers(test_environment, mock_execution_result)

    assert len(results) == 1
    assert results[0].piggyback_host == mock_execution_result.piggyback_host
    assert results[0].service_name == mock_execution_result.service_name


def test_apply_error_handlers_empty_list(
    test_environment: Environment, mock_execution_result: ExecutionResult
) -> None:
    """Test that apply_error_handlers returns input unchanged with empty handlers list."""

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[],
    )

    results = mock_check.apply_error_handlers(test_environment, mock_execution_result)

    assert len(results) == 1
    assert results[0].piggyback_host == mock_execution_result.piggyback_host
    assert results[0].service_name == mock_execution_result.service_name


def test_apply_error_handlers_single_handler(
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test that apply_error_handlers correctly applies a single handler."""

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[expand_by_hostname(["new-host"])],
    )

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        results = mock_check.apply_error_handlers(
            test_environment, mock_execution_result
        )

        assert len(results) == 1
        assert results[0].piggyback_host == "new-host"
        assert results[0].service_name == mock_execution_result.service_name
    finally:
        setattr(watchpost.check, "current_app", original_app)


def test_apply_error_handlers_multiple_handlers(
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test that apply_error_handlers chains multiple handlers correctly."""

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[
            expand_by_hostname(["host1", "host2"]),
            expand_by_name_suffix([":a", ":b"]),
        ],
    )

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        results = mock_check.apply_error_handlers(
            test_environment, mock_execution_result
        )

        # Should have 4 results (2 hostnames x 2 suffixes)
        assert len(results) == 4

        # Verify all combinations
        expected = {
            ("host1", "test-service:a"),
            ("host1", "test-service:b"),
            ("host2", "test-service:a"),
            ("host2", "test-service:b"),
        }
        actual = {(r.piggyback_host, r.service_name) for r in results}
        assert actual == expected
    finally:
        setattr(watchpost.check, "current_app", original_app)


# Tests for @check Decorator Integration


def test_check_decorator_with_error_handlers(
    test_environment: Environment, mock_watchpost: MagicMock
) -> None:
    """Test that error_handlers parameter works in @check decorator."""

    @check(
        name="test_service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[expand_by_hostname(["host1", "host2"])],
    )
    def test_check_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    # Verify check was created with error_handlers
    assert test_check_func.error_handlers is not None
    assert len(test_check_func.error_handlers) == 1
    assert callable(test_check_func.error_handlers[0])

    # Verify handler works
    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        result = ExecutionResult(
            piggyback_host="original",
            service_name="test_service",
            service_labels={},
            environment_name=test_environment.name,
            check_state=CheckState.CRIT,
            summary="Error",
            details="Details",
            metrics=None,
            check_definition=None,
        )

        results = test_check_func.apply_error_handlers(test_environment, result)
        assert len(results) == 2
        assert {r.piggyback_host for r in results} == {"host1", "host2"}
    finally:
        setattr(watchpost.check, "current_app", original_app)


def test_check_decorator_without_error_handlers(test_environment: Environment) -> None:
    """Test that @check without error_handlers parameter works (backward compatibility)."""

    @check(
        name="test_service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
    )
    def test_check_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    # error_handlers should be None or empty list
    assert (
        test_check_func.error_handlers is None or test_check_func.error_handlers == []
    )


def test_check_decorator_empty_error_handlers(test_environment: Environment) -> None:
    """Test that @check with explicit empty error_handlers list works."""

    @check(
        name="test_service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[],
    )
    def test_check_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    # Should have empty list
    assert test_check_func.error_handlers == []


# Tests for Custom Error Handlers


def test_custom_error_handler(
    test_environment: Environment, mock_execution_result: ExecutionResult
) -> None:
    """Test that custom error handler implementations work correctly."""

    def custom_handler(
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
    ) -> list[ExecutionResult]:
        """Custom handler that modifies check state and adds details."""
        _ = check, environment
        transformed = []
        for result in results:
            new_result = ExecutionResult(
                piggyback_host=result.piggyback_host,
                service_name=result.service_name,
                service_labels=result.service_labels,
                environment_name=result.environment_name,
                check_state=CheckState.UNKNOWN,  # Change state
                summary=result.summary,
                details=f"{result.details}\n[custom error handler applied]",
                metrics=result.metrics,
                check_definition=result.check_definition,
            )
            transformed.append(new_result)
        return transformed

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[custom_handler],
    )

    results = mock_check.apply_error_handlers(test_environment, mock_execution_result)

    assert len(results) == 1
    result = results[0]
    assert result.check_state == CheckState.UNKNOWN
    assert result.details is not None
    assert "[custom error handler applied]" in result.details
    # Other fields preserved
    assert result.piggyback_host == mock_execution_result.piggyback_host
    assert result.service_name == mock_execution_result.service_name


def test_custom_error_handler_modifies_state(test_environment: Environment) -> None:
    """Test that custom handler can change check state."""

    def state_changing_handler(
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
    ) -> list[ExecutionResult]:
        """Change CRIT to UNKNOWN."""
        _ = check, environment
        transformed = []
        for result in results:
            new_state = (
                CheckState.UNKNOWN
                if result.check_state == CheckState.CRIT
                else result.check_state
            )
            new_result = ExecutionResult(
                piggyback_host=result.piggyback_host,
                service_name=result.service_name,
                service_labels=result.service_labels,
                environment_name=result.environment_name,
                check_state=new_state,
                summary=result.summary,
                details=result.details,
                metrics=result.metrics,
                check_definition=result.check_definition,
            )
            transformed.append(new_result)
        return transformed

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[state_changing_handler],
    )

    mock_execution_result = ExecutionResult(
        piggyback_host="test",
        service_name="test",
        service_labels={},
        environment_name=test_environment.name,
        check_state=CheckState.CRIT,  # Start with CRIT
        summary="Error",
        details="Details",
        metrics=None,
        check_definition=None,
    )

    results = mock_check.apply_error_handlers(test_environment, mock_execution_result)

    assert len(results) == 1
    assert results[0].check_state == CheckState.UNKNOWN


# Edge Cases and Error Conditions


def test_handler_preserves_environment_name(
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test that environment_name is correct in all results after handler expansion."""

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[
            expand_by_hostname(["host1", "host2"]),
            expand_by_name_suffix([":a", ":b"]),
        ],
    )

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        results = mock_check.apply_error_handlers(
            test_environment, mock_execution_result
        )

        # All results should have the correct environment name
        for result in results:
            assert result.environment_name == test_environment.name
    finally:
        setattr(watchpost.check, "current_app", original_app)


def test_expand_by_hostname_with_different_strategies(
    test_environment: Environment,
) -> None:
    """Explicit error-handler hostnames take precedence over check strategies."""

    @check(
        name="test_service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        hostname="check-{service_name}",  # Template strategy
        error_handlers=[expand_by_hostname(["host1"])],
    )
    def test_check_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    result = ExecutionResult(
        piggyback_host="original",
        service_name="test_service",
        service_labels={},
        environment_name=test_environment.name,
        check_state=CheckState.CRIT,
        summary="Error",
        details="Details",
        metrics=None,
        check_definition=None,
    )

    results = test_check_func.apply_error_handlers(test_environment, result)

    # The explicit host must not be overwritten by the check-level template.
    assert len(results) == 1
    assert results[0].piggyback_host == "host1"


# Performance and Scale Tests


def test_many_hostnames_many_suffixes(
    test_environment: Environment,
    mock_execution_result: ExecutionResult,
    mock_watchpost: MagicMock,
) -> None:
    """Test performance with many hostnames and suffixes."""

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    # 5 input results, 10 hostnames, 10 suffixes = 500 output results
    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[
            expand_by_hostname([f"host{i}" for i in range(10)]),
            expand_by_name_suffix([f":suffix{i}" for i in range(10)]),
        ],
    )

    import watchpost.check

    original_app = getattr(watchpost.check, "current_app", None)
    watchpost.check.current_app = mock_watchpost

    try:
        # Start with 5 input results
        input_results = [mock_execution_result] * 5

        # Apply hostname handler (5 x 10 = 50 results)
        assert mock_check.error_handlers is not None
        hostname_handler = mock_check.error_handlers[0]
        hostname_results = hostname_handler(mock_check, test_environment, input_results)
        assert len(hostname_results) == 50

        # Apply suffix handler (50 x 10 = 500 results)
        suffix_handler = mock_check.error_handlers[1]
        final_results = suffix_handler(mock_check, test_environment, hostname_results)
        assert len(final_results) == 500

        # Verify structure
        assert len({r.piggyback_host for r in final_results}) == 10  # 10 unique hosts
        assert len({r.service_name for r in final_results}) == 10  # 10 unique suffixes
    finally:
        setattr(watchpost.check, "current_app", original_app)


def test_deep_handler_chain(
    test_environment: Environment, mock_execution_result: ExecutionResult
) -> None:
    """Test performance with long handler chains."""

    def identity_handler(name: str) -> ErrorHandler:
        """Create a handler that just passes through results."""

        def handler(
            check: Check,
            environment: Environment,
            results: list[ExecutionResult],
        ) -> list[ExecutionResult]:
            _ = check, environment
            """Identity handler that adds a small marker."""
            transformed = []
            for result in results:
                new_result = ExecutionResult(
                    piggyback_host=result.piggyback_host,
                    service_name=result.service_name,
                    service_labels=result.service_labels,
                    environment_name=result.environment_name,
                    check_state=result.check_state,
                    summary=result.summary,
                    details=f"{result.details}[{name}]",
                    metrics=result.metrics,
                    check_definition=result.check_definition,
                )
                transformed.append(new_result)
            return transformed

        return handler

    def dummy_func(test_datasource: TestDatasource) -> None:
        _ = test_datasource
        return None

    # Chain of 10 identity handlers
    mock_check = Check(
        check_function=dummy_func,
        service_name="test-service",
        service_labels={},
        environments=[test_environment],
        cache_for=None,
        error_handlers=[identity_handler(f"h{i}") for i in range(10)],
    )

    results = mock_check.apply_error_handlers(test_environment, mock_execution_result)

    # Should have 1 result (no expansion)
    assert len(results) == 1

    # All handlers should have been applied (markers accumulated)
    result = results[0]
    for i in range(10):
        assert result.details is not None
        assert f"[h{i}]" in result.details
