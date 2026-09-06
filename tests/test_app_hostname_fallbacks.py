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

from concurrent.futures import Future
from typing import Any, override

from watchpost.app import Watchpost
from watchpost.check import check
from watchpost.datasource import DatasourceUnavailable
from watchpost.environment import Environment
from watchpost.executor import CheckExecutor
from watchpost.result import ok
from watchpost.scheduling_strategy import SchedulingDecision, SchedulingStrategy


class FakeExecutor[T](CheckExecutor[T]):
    """
    Minimal fake executor to drive Watchpost._run_check fallback branches deterministically.

    - submit(...) is a no-op
    - result(key) returns or raises preconfigured behavior
    """

    def __init__(self, behavior: Any):
        self._behavior = behavior

    @override
    def submit(self, *args: Any, **kwargs: Any) -> Future[T]:
        # No-op: these tests drive result() directly without executing checks.
        _ = args, kwargs
        return Future()

    def result(self, key: Any):
        _ = key
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return self._behavior


class AlwaysSkipStrategy(SchedulingStrategy):
    @override
    def schedule(self, check, current_execution_environment, target_environment):
        return SchedulingDecision.SKIP


def _mk_check_with_static_hostname(env: Environment):
    @check(
        name="svc",
        service_labels={},
        environments=[env],
        cache_for=None,
        hostname="check-host",
    )
    def my_check():
        # Will not actually be called in these tests
        return ok("unused")

    return my_check


def test_hostname_on_skip_without_prior_results_is_resolved():
    env = Environment("prod")
    my_check = _mk_check_with_static_hostname(env)

    app = Watchpost(
        checks=[my_check],
        execution_environment=Environment("exec-env"),
        executor=FakeExecutor(behavior=None),  # not used for SKIP without cache
        default_scheduling_strategies=[AlwaysSkipStrategy()],
        hostname_fallback_to_default_hostname_generation=False,
        hostname_coerce_into_valid_hostname=False,
    )

    with app.app_context():
        results = app._run_check(
            check=my_check,
            environment=env,
            instantiable_datasources={},
        )  # type: ignore[arg-type]
    assert results is not None
    assert len(results) == 1
    assert results[0].piggyback_host == "check-host"


def test_hostname_on_datasource_unavailable_without_cache_is_resolved():
    env = Environment("prod")
    my_check = _mk_check_with_static_hostname(env)

    app = Watchpost(
        checks=[my_check],
        execution_environment=Environment("exec-env"),
        executor=FakeExecutor(behavior=DatasourceUnavailable("temporary outage")),
        hostname_fallback_to_default_hostname_generation=False,
        hostname_coerce_into_valid_hostname=False,
    )

    with app.app_context():
        results = app._run_check(
            check=my_check,
            environment=env,
            instantiable_datasources={},
        )  # type: ignore[arg-type]
    assert results is not None
    assert len(results) == 1
    assert results[0].piggyback_host == "check-host"


def test_hostname_on_generic_exception_is_resolved():
    env = Environment("prod")
    my_check = _mk_check_with_static_hostname(env)

    app = Watchpost(
        checks=[my_check],
        execution_environment=Environment("exec-env"),
        executor=FakeExecutor(behavior=RuntimeError("boom")),
        hostname_fallback_to_default_hostname_generation=False,
        hostname_coerce_into_valid_hostname=False,
    )

    with app.app_context():
        results = app._run_check(
            check=my_check,
            environment=env,
            instantiable_datasources={},
        )  # type: ignore[arg-type]
    assert results is not None
    assert len(results) == 1
    assert results[0].piggyback_host == "check-host"


def test_hostname_on_async_first_run_is_resolved():
    env = Environment("prod")
    my_check = _mk_check_with_static_hostname(env)

    app = Watchpost(
        checks=[my_check],
        execution_environment=Environment("exec-env"),
        executor=FakeExecutor(behavior=None),  # result() returns None => async path
        hostname_fallback_to_default_hostname_generation=False,
        hostname_coerce_into_valid_hostname=False,
    )

    with app.app_context():
        results = app._run_check(
            check=my_check,
            environment=env,
            instantiable_datasources={},
        )  # type: ignore[arg-type]
    assert results is not None
    assert len(results) == 1
    assert results[0].piggyback_host == "check-host"
