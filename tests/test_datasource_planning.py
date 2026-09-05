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

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Annotated, Any

import pytest

from watchpost import (
    Datasource,
    DatasourceFactory,
    DatasourceUnavailable,
    Environment,
    FromFactory,
    Watchpost,
    check,
    ok,
)
from watchpost._planning import _InstantiableDatasource
from watchpost.check import Check
from watchpost.executor import BlockingCheckExecutor
from watchpost.globals import current_app
from watchpost.result import CheckResult, ExecutionResult
from watchpost.scheduling_strategy import (
    MustRunInGivenExecutionEnvironmentStrategy,
    SchedulingDecision,
    SchedulingStrategy,
)


@pytest.mark.parametrize("factory", [False, True])
def test_concurrent_resolution_publishes_one_shared_wrapper(
    monkeypatch: pytest.MonkeyPatch, factory: bool
) -> None:
    class Source(Datasource, DatasourceFactory):
        scheduling_strategies = ()

        @classmethod
        def new(cls) -> Datasource:
            return cls()

    candidates_created = Barrier(2)
    method_name = "from_factory" if factory else "from_datasource"
    original = getattr(_InstantiableDatasource, method_name)

    def create_candidate(*args: Any, **kwargs: Any) -> _InstantiableDatasource:
        candidate = original(*args, **kwargs)
        candidates_created.wait(timeout=5)
        return candidate

    monkeypatch.setattr(
        _InstantiableDatasource, method_name, staticmethod(create_candidate)
    )
    app = Watchpost(checks=[], execution_environment=Environment("test"))
    try:
        if factory:
            app.register_datasource_factory(Source)
        else:
            app.register_datasource(Source)
        with ThreadPoolExecutor(2) as callers:
            futures = [
                callers.submit(app._resolve_instantiable_datasource, Source)
                for _ in range(2)
            ]
            first, second = [future.result(timeout=5) for future in futures]
        assert first is second
        assert first.instance() is second.instance()
    finally:
        app.shutdown()


@pytest.mark.parametrize("factory", [False, True])
def test_concurrent_check_pairs_share_one_datasource_instance(factory: bool) -> None:
    constructed: list[Datasource] = []
    callers_ready = Barrier(8)

    class Source(Datasource, DatasourceFactory):
        scheduling_strategies = ()

        def __init__(self) -> None:
            constructed.append(self)
            # Give all callers an opportunity to observe an unfinished constructor.
            time.sleep(0.05)

        @classmethod
        def new(cls) -> Datasource:
            return cls()

    environments = [Environment("first"), Environment("second")]

    def check_source(ds: Source) -> CheckResult:
        return ok(str(id(ds)))

    if factory:
        check_source.__annotations__["ds"] = Annotated[Source, FromFactory(Source)]
    definitions = [
        check(
            name=f"shared-{index}",
            service_labels={},
            environments=[environments[index % 2]],
            cache_for=None,
        )(check_source)
        for index in range(8)
    ]
    with BlockingCheckExecutor(max_workers=8) as executor:
        app = Watchpost(
            checks=definitions, execution_environment=environments[0], executor=executor
        )
        if factory:
            app.register_datasource_factory(Source)
        else:
            app.register_datasource(Source)

        def poll(definition: Check) -> list[ExecutionResult]:
            callers_ready.wait(timeout=5)
            return list(app.run_check(definition))

        with ThreadPoolExecutor(8) as callers:
            results = list(callers.map(poll, definitions))
    assert len(constructed) == 1
    assert [[result.summary for result in output] for output in results] == [
        [str(id(constructed[0]))]
    ] * 8


@pytest.mark.parametrize("factory", [False, True])
def test_unrelated_datasources_can_construct_concurrently(factory: bool) -> None:
    constructors_ready = Barrier(2)
    env = Environment("test")

    class Source(Datasource):
        scheduling_strategies = ()

        def __init__(self, value: str) -> None:
            constructors_ready.wait(timeout=5)
            self.value = value

    class First(Source):
        pass

    class Second(Source):
        pass

    class Factory(DatasourceFactory):
        scheduling_strategies = ()
        new = Source

    def first(ds: First) -> CheckResult:
        return ok(ds.value)

    def second(ds: Second) -> CheckResult:
        return ok(ds.value)

    if factory:
        first.__annotations__["ds"] = Annotated[Source, FromFactory(Factory, "first")]
        second.__annotations__["ds"] = Annotated[Source, FromFactory(Factory, "second")]
    definitions = [
        check(
            name=function.__name__,
            service_labels={},
            environments=[env],
            cache_for=None,
        )(function)
        for function in (first, second)
    ]
    with BlockingCheckExecutor(max_workers=2) as executor:
        app = Watchpost(
            checks=definitions, execution_environment=env, executor=executor
        )
        if factory:
            app.register_datasource_factory(Factory)
        else:
            app.register_datasource(First, value="first")
            app.register_datasource(Second, value="second")
        with ThreadPoolExecutor(2) as callers:
            results = list(
                callers.map(
                    lambda definition: list(app.run_check(definition)), definitions
                )
            )
    assert [[result.summary for result in output] for output in results] == [
        ["first"],
        ["second"],
    ]


@pytest.mark.parametrize("verify_first", [False, True])
def test_recovered_factory_is_scheduled_using_actual_datasource_constraints(
    verify_first: bool,
) -> None:
    production, monitoring = Environment("production"), Environment("monitoring")
    attempts = 0

    class Restricted(Datasource):
        scheduling_strategies = (
            MustRunInGivenExecutionEnvironmentStrategy(production),
        )

    class Factory(DatasourceFactory):
        scheduling_strategies = (
            MustRunInGivenExecutionEnvironmentStrategy(monitoring),
        )

        @staticmethod
        def new() -> Datasource:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise DatasourceUnavailable("temporary startup failure")
            return Restricted()

    @check(
        name="restricted", service_labels={}, environments=[production], cache_for=None
    )
    def restricted(
        first: Annotated[Restricted, FromFactory(Factory)],
        second: Annotated[Restricted, FromFactory(Factory)],
    ) -> CheckResult:
        raise AssertionError(f"Must not execute from monitoring: {first}, {second}")

    with BlockingCheckExecutor() as executor:
        app = Watchpost(
            checks=[restricted], execution_environment=monitoring, executor=executor
        )
        app.register_datasource_factory(Factory)
        if verify_first:
            app.verify_check_scheduling()
        else:
            results = list(app.run_check(restricted))
            assert len(results) == 1
            assert results[0].check_state.name == "UNKNOWN"
            assert results[0].summary == "temporary startup failure"
        assert attempts == 1
        assert list(app.run_check(restricted)) == []
        assert attempts == 2
        assert list(app.run_check(restricted)) == []
        assert attempts == 2


def test_strategy_inspection_uses_the_scheduled_plan_snapshot() -> None:
    environment = Environment("test")
    attempts = 0
    observed: list[list[SchedulingStrategy]] = []

    class InspectPlan(SchedulingStrategy):
        def schedule(
            self,
            check: Check,
            current_execution_environment: Environment,
            target_environment: Environment,
        ) -> SchedulingDecision:
            assert current_execution_environment is environment
            assert target_environment is environment
            observed.append(current_app._resolve_scheduling_strategies(check))
            return SchedulingDecision.SCHEDULE

    class Source(Datasource):
        scheduling_strategies = ()

    class Factory(DatasourceFactory):
        scheduling_strategies = (
            MustRunInGivenExecutionEnvironmentStrategy(environment),
        )

        @staticmethod
        def new() -> Datasource:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise DatasourceUnavailable("temporary startup failure")
            return Source()

    @check(
        name="snapshot", service_labels={}, environments=[environment], cache_for=None
    )
    def definition(ds: Annotated[Source, FromFactory(Factory)]) -> CheckResult:
        return ok(str(ds))

    app = Watchpost(
        checks=[definition],
        execution_environment=environment,
        default_scheduling_strategies=[InspectPlan()],
    )
    app.register_datasource_factory(Factory)
    try:
        provisional = app._planner.resolve_plan(definition)
        recovered = app._planner.resolve_plan(definition)
        assert provisional.datasource_error is not None
        assert recovered.datasource_error is None
        with app.app_context():
            provisional.schedule(environment, environment)
            recovered.schedule(environment, environment)
        assert observed[0] is provisional.strategies
        assert observed[1] is recovered.strategies
        assert app._planner._active_plan.get() is None
        assert attempts == 2
    finally:
        app.shutdown()
