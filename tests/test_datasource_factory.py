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

import logging
from typing import Annotated, cast

import pytest

from tests.utils import decode_checkmk_output
from watchpost.app import Watchpost, _InstantiableDatasource
from watchpost.check import Check, check
from watchpost.datasource import Datasource, DatasourceFactory, FromFactory
from watchpost.environment import Environment
from watchpost.executor import BlockingCheckExecutor
from watchpost.result import CheckResult, ok
from watchpost.scheduling_strategy import (
    MustRunInGivenExecutionEnvironmentStrategy,
    SchedulingDecision,
)

# Define test environment
TEST_ENVIRONMENT = Environment("test-env")


# Define test datasources and factories
class TestDatasource(Datasource):
    __test__ = False


class ConfigurableTestDatasource(Datasource):
    def __init__(self, config_value: str):
        self.config_value = config_value


class TestFactory(DatasourceFactory):
    __test__ = False

    @staticmethod
    def new(service: str) -> Datasource:
        return ConfigurableTestDatasource(f"factory-created-{service}")


class ParameterizedTestFactory(DatasourceFactory):
    prefix = "default-prefix"

    @classmethod
    def new(cls, service: str, region: str = "default-region") -> Datasource:
        return ConfigurableTestDatasource(f"{cls.prefix}-{service}-{region}")


class DatasourceWithFactory(Datasource, DatasourceFactory):
    def __init__(self, value: str):
        self.value = value

    @classmethod
    def new(cls, value: str) -> DatasourceWithFactory:
        return cls(value)


class DatasourceWithFactoryNoArgs(Datasource, DatasourceFactory):
    def __init__(self):
        self.value = "static"

    @classmethod
    def new(cls) -> DatasourceWithFactoryNoArgs:
        return cls()


def test_register_datasource_factory() -> None:
    """Test that a datasource factory can be registered with an Watchpost instance."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register a factory
    app.register_datasource_factory(TestFactory)

    # Verify the factory was registered
    assert TestFactory in app._datasource_factories


def test_resolve_datasource_from_factory() -> None:
    """Test that a datasource can be resolved from a factory."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register a factory
    app.register_datasource_factory(TestFactory)

    # Create a FromFactory instance
    from_factory = FromFactory(TestFactory, "test-service")

    # Resolve the datasource
    instantiable_datasource = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource,
        from_factory,
    )
    assert isinstance(instantiable_datasource, _InstantiableDatasource)
    datasource = instantiable_datasource.instance()

    # Verify the datasource was resolved correctly
    assert isinstance(datasource, ConfigurableTestDatasource)
    assert datasource.config_value == "factory-created-test-service"


def test_resolve_datasource_from_factory_caching() -> None:
    """Test that resolved datasources are cached."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register a factory
    app.register_datasource_factory(TestFactory)

    # Create a FromFactory instance
    from_factory = FromFactory(TestFactory, "test-service")

    # Resolve the datasource twice
    datasource1 = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource, from_factory
    )
    datasource2 = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource, from_factory
    )

    # Verify that the same instance was returned both times
    assert datasource1 is datasource2


def test_resolve_datasource_from_factory_with_different_args() -> None:
    """Test that different args produce different datasources."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register a factory
    app.register_datasource_factory(TestFactory)

    # Create two FromFactory instances with different args
    from_factory1 = FromFactory(TestFactory, "service1")
    from_factory2 = FromFactory(TestFactory, "service2")

    # Resolve the datasources
    instantiable_datasource1 = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource, from_factory1
    )
    datasource1: ConfigurableTestDatasource = cast(
        ConfigurableTestDatasource,
        instantiable_datasource1.instance(),
    )
    instantiable_datasource2 = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource, from_factory2
    )
    datasource2: ConfigurableTestDatasource = cast(
        ConfigurableTestDatasource,
        instantiable_datasource2.instance(),
    )

    # Verify that different instances were returned
    assert datasource1 is not datasource2
    assert datasource1.config_value == "factory-created-service1"
    assert datasource2.config_value == "factory-created-service2"


def test_resolve_datasource_from_factory_with_kwargs() -> None:
    """Test that a datasource can be resolved from a factory with kwargs."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register a factory
    app.register_datasource_factory(ParameterizedTestFactory)

    # Create a FromFactory instance with kwargs
    from_factory = FromFactory(
        ParameterizedTestFactory, "test-service", region="us-west-2"
    )

    # Resolve the datasource
    instantiable_datasource = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource,
        from_factory,
    )
    assert isinstance(instantiable_datasource, _InstantiableDatasource)
    datasource = instantiable_datasource.instance()

    # Verify the datasource was resolved correctly
    assert isinstance(datasource, ConfigurableTestDatasource)
    assert datasource.config_value == "default-prefix-test-service-us-west-2"


def test_factory_not_registered() -> None:
    """Test that an error is raised when trying to resolve a datasource from an unregistered factory."""
    # Create an Watchpost instance
    app = Watchpost(
        checks=[],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Create a FromFactory instance
    from_factory = FromFactory(TestFactory, "test-service")

    # Attempt to resolve the datasource
    with pytest.raises(ValueError, match="No datasource factory for"):
        app._resolve_instantiable_datasource_from_factory(
            ConfigurableTestDatasource, from_factory
        )


def test_check_with_factory_datasource() -> None:
    """Test that a check can use a datasource from a factory."""

    # Create a check function that uses a factory datasource
    @check(
        name="test-service",
        service_labels={"env": "test"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def test_check(
        datasource: Annotated[
            ConfigurableTestDatasource,
            FromFactory(TestFactory, "test-service"),
        ],
    ) -> CheckResult:
        return ok(f"Datasource config: {datasource.config_value}")

    # Create an Watchpost instance with the check
    app = Watchpost(
        checks=[test_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register the factory
    app.register_datasource_factory(TestFactory)

    instantiable_datasource = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource,
        FromFactory(TestFactory, "test-service"),
    )
    assert isinstance(instantiable_datasource, _InstantiableDatasource)
    datasource = instantiable_datasource.instance()
    assert isinstance(datasource, ConfigurableTestDatasource)

    # Run the check
    results = test_check.run_sync(
        watchpost=app,
        datasources={
            "datasource": datasource,
        },
        environment=TEST_ENVIRONMENT,
    )

    # Verify the results
    assert len(results) == 1
    assert results[0].summary == "Datasource config: factory-created-test-service"


def test_check_with_multiple_factory_datasources() -> None:
    """Test that a check can use multiple datasources from factories."""

    # Create a check function that uses multiple factory datasources
    @check(
        name="test-service",
        service_labels={"env": "test"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def test_check(
        datasource1: Annotated[
            ConfigurableTestDatasource, FromFactory(TestFactory, "service1")
        ],
        datasource2: Annotated[
            ConfigurableTestDatasource, FromFactory(TestFactory, "service2")
        ],
    ) -> CheckResult:
        return ok(
            f"Datasource1: {datasource1.config_value}, Datasource2: {datasource2.config_value}"
        )

    # Create an Watchpost instance with the check
    app = Watchpost(
        checks=[test_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register the factory
    app.register_datasource_factory(TestFactory)

    # Run the check
    instantiable_datasource1 = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource,
        FromFactory(TestFactory, "service1"),
    )
    instantiable_datasource2 = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource,
        FromFactory(TestFactory, "service2"),
    )

    datasource1 = instantiable_datasource1.instance()
    assert isinstance(datasource1, ConfigurableTestDatasource)
    datasource2 = instantiable_datasource2.instance()
    assert isinstance(datasource2, ConfigurableTestDatasource)

    results = test_check.run_sync(
        watchpost=app,
        datasources={
            "datasource1": datasource1,
            "datasource2": datasource2,
        },
        environment=TEST_ENVIRONMENT,
    )

    # Verify the results
    assert len(results) == 1
    assert (
        results[0].summary
        == "Datasource1: factory-created-service1, Datasource2: factory-created-service2"
    )


def test_check_with_mixed_datasources() -> None:
    """Test that a check can use both regular datasources and factory datasources."""

    # Create a check function that uses both types of datasources
    @check(
        name="test-service",
        service_labels={"env": "test"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def test_check(
        regular: TestDatasource,
        factory: Annotated[
            ConfigurableTestDatasource, FromFactory(TestFactory, "test-service")
        ],
    ) -> CheckResult:
        return ok(f"Regular: {type(regular).__name__}, Factory: {factory.config_value}")

    # Create an Watchpost instance with the check
    app = Watchpost(
        checks=[test_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register the datasource and factory
    app.register_datasource(TestDatasource)
    app.register_datasource_factory(TestFactory)

    # Run the check
    instantiable_datasource = app._resolve_instantiable_datasource_from_factory(
        ConfigurableTestDatasource,
        FromFactory(TestFactory, "test-service"),
    )

    datasource = instantiable_datasource.instance()
    assert isinstance(datasource, ConfigurableTestDatasource)

    results = test_check.run_sync(
        watchpost=app,
        datasources={
            "regular": TestDatasource(),
            "factory": datasource,
        },
        environment=TEST_ENVIRONMENT,
    )

    # Verify the results
    assert len(results) == 1
    assert (
        results[0].summary
        == "Regular: TestDatasource, Factory: factory-created-test-service"
    )


def test_watchpost_resolve_datasources_with_factory() -> None:
    """Test that Watchpost._resolve_datasources correctly handles factory datasources."""

    # Create a check function that uses a factory datasource
    def check_func(
        datasource: Annotated[
            ConfigurableTestDatasource, FromFactory(TestFactory, "test-service")
        ],
    ) -> CheckResult:
        return ok(f"Datasource config: {datasource.config_value}")

    # Create a Check object
    check_obj = Check(
        check_function=check_func,
        service_name="test-service",
        service_labels={"env": "test"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )

    # Create an Watchpost instance
    app = Watchpost(
        checks=[check_obj],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register the factory
    app.register_datasource_factory(TestFactory)

    # Resolve the datasources
    instantiable_datasources = app._resolve_datasources(check_obj)

    # Verify the datasources
    assert "datasource" in instantiable_datasources
    assert isinstance(instantiable_datasources["datasource"], _InstantiableDatasource)
    datasource = instantiable_datasources["datasource"].instance()
    assert isinstance(datasource, ConfigurableTestDatasource)
    assert datasource.config_value == "factory-created-test-service"


def test_watchpost_run_checks_with_factory() -> None:
    """Test that Watchpost.run_checks correctly handles factory datasources."""

    # Create a check function that uses a factory datasource
    @check(
        name="test-service",
        service_labels={"env": "test"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def test_check(
        datasource: Annotated[
            ConfigurableTestDatasource, FromFactory(TestFactory, "test-service")
        ],
    ) -> CheckResult:
        return ok(f"Datasource config: {datasource.config_value}")

    # Create an Watchpost instance with the check
    app = Watchpost(
        checks=[test_check],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Register the factory
    app.register_datasource_factory(TestFactory)

    # Run the checks
    output = list(app.run_checks())

    # Verify that output was generated
    assert len(output) > 0
    assert isinstance(output[0], bytes)

    decoded_output = decode_checkmk_output(b"".join(output))
    for item in decoded_output:
        item.pop("check_definition", None)

    assert sorted(decoded_output, key=lambda result: result["service_name"]) == sorted(
        [
            {
                "check_state": "OK",
                "details": None,
                "environment": "test-env",
                "metrics": [],
                "service_labels": {"env": "test"},
                "service_name": "test-service",
                "summary": "Datasource config: factory-created-test-service",
            },
            {
                "check_state": "OK",
                "details": "Eligible check/environment pairs:\n- tests.test_datasource_factory.test_watchpost_run_checks_with_factory.<locals>.test_check [test-env]",
                "environment": "test-env",
                "metrics": [],
                "service_labels": {},
                "service_name": "Watchpost: executed checks",
                "summary": "1 check/environment pairs eligible to run",
            },
        ],
        key=lambda result: result["service_name"],
    )


def test_fromfactory_cache_key() -> None:
    """Test that FromFactory.cache_key is correctly generated."""
    # Create two FromFactory instances with the same args
    from_factory1 = FromFactory(TestFactory, "test-service")
    from_factory2 = FromFactory(TestFactory, "test-service")

    # Verify that they have the same cache key
    assert from_factory1.cache_key(TestFactory) == from_factory2.cache_key(TestFactory)

    # Create two FromFactory instances with different args
    from_factory3 = FromFactory(TestFactory, "service1")
    from_factory4 = FromFactory(TestFactory, "service2")

    # Verify that they have different cache keys
    assert from_factory3.cache_key(TestFactory) != from_factory4.cache_key(TestFactory)

    # Create two FromFactory instances with the same args but different kwargs
    from_factory5 = FromFactory(
        ParameterizedTestFactory, "test-service", region="us-west-1"
    )
    from_factory6 = FromFactory(
        ParameterizedTestFactory, "test-service", region="us-west-2"
    )

    # Verify that they have different cache keys
    assert from_factory5.cache_key(TestFactory) != from_factory6.cache_key(TestFactory)


def test_annotated_with_non_fromfactory() -> None:
    """Test that using Annotated with something other than FromFactory raises a ValueError."""

    # Create a check function that uses Annotated with something other than FromFactory
    def check_func(
        _datasource: Annotated[ConfigurableTestDatasource, "not-a-fromfactory"],
    ) -> CheckResult:
        return ok("Test passed")

    # Create a Check object
    check_obj = Check(
        check_function=check_func,
        service_name="test-service",
        service_labels={"env": "test"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )

    # Create an Watchpost instance
    app = Watchpost(
        checks=[check_obj],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )

    # Attempt to resolve the datasources, which should raise a ValueError
    with pytest.raises(ValueError, match="Unsupported annotation"):
        app._resolve_datasources(check_obj)


A = Environment("A")
B = Environment("B")


class OwnStrategyDatasource(Datasource):
    scheduling_strategies = (MustRunInGivenExecutionEnvironmentStrategy(A),)


class FactoryWithDifferentStrategy(DatasourceFactory):
    # Factory provides a conflicting execution environment (B), but the
    # datasource created by the factory defines its own strategy (A), which
    # must take precedence.
    scheduling_strategies = (MustRunInGivenExecutionEnvironmentStrategy(B),)

    @staticmethod
    def new(*_args, **_kwargs) -> Datasource:
        return OwnStrategyDatasource()


def test_factory_datasource_prefers_own_strategies_over_factory() -> None:
    @check(
        name="factory-prefers-ds-own",
        service_labels={"test": "true"},
        environments=[A],
        cache_for=None,
    )
    def my_check(_ds: Annotated[Datasource, FromFactory(FactoryWithDifferentStrategy)]):
        return ok("unused")

    app = Watchpost(
        checks=[my_check],
        execution_environment=A,
        executor=BlockingCheckExecutor(),
    )
    app.register_datasource_factory(FactoryWithDifferentStrategy)

    with app.app_context():
        # The decision should be based on the datasource's own strategy (A),
        # not the factory's (B).
        decision = app._resolve_check_scheduling_decision(my_check, A)
        assert decision == SchedulingDecision.SCHEDULE

        # Verify that the resolved strategies include the A constraint and not B.
        strategies = app._resolve_scheduling_strategies(my_check)
        a_present = False
        b_present = False
        for s in strategies:
            if isinstance(s, MustRunInGivenExecutionEnvironmentStrategy):
                if (
                    A in s.supported_execution_environments
                    and len(s.supported_execution_environments) == 1
                ):
                    a_present = True
                if (
                    B in s.supported_execution_environments
                    and len(s.supported_execution_environments) == 1
                ):
                    b_present = True
        assert a_present
        assert not b_present


class EllipsisDatasource(Datasource):
    # No explicit strategies -> Ellipsis from base class
    pass


class FactoryWithStrategies(DatasourceFactory):
    scheduling_strategies = (MustRunInGivenExecutionEnvironmentStrategy(B),)

    @staticmethod
    def new(*_args, **_kwargs) -> Datasource:
        return EllipsisDatasource()


def test_factory_strategies_applied_when_datasource_has_no_strategies() -> None:
    @check(
        name="factory-strategies-applied",
        service_labels={"test": "true"},
        environments=[B],
        cache_for=None,
    )
    def my_check(_ds: Annotated[Datasource, FromFactory(FactoryWithStrategies)]):
        return ok("unused")

    # Executing from B should be allowed due to factory strategies
    app_b = Watchpost(
        checks=[my_check],
        execution_environment=B,
        executor=BlockingCheckExecutor(),
    )
    app_b.register_datasource_factory(FactoryWithStrategies)

    with app_b.app_context():
        decision_b = app_b._resolve_check_scheduling_decision(my_check, B)
        assert decision_b == SchedulingDecision.SCHEDULE

    # Executing from A should not be allowed due to factory strategies pinning to B
    app_a = Watchpost(
        checks=[my_check],
        execution_environment=A,
        executor=BlockingCheckExecutor(),
    )
    app_a.register_datasource_factory(FactoryWithStrategies)

    with app_a.app_context():
        decision_a = app_a._resolve_check_scheduling_decision(my_check, B)
        assert decision_a == SchedulingDecision.DONT_SCHEDULE


ENV = Environment("env")


class NoStrategyDatasource(Datasource):
    # No explicit strategies -> Ellipsis from base class
    pass


class FactoryWithoutStrategies(DatasourceFactory):
    # Explicitly define no strategies
    scheduling_strategies = ()

    @staticmethod
    def new(*_args, **_kwargs) -> Datasource:
        return NoStrategyDatasource()


def test_factory_and_datasource_without_strategies_logs_no_warning(caplog) -> None:
    @check(
        name="warning-when-no-strategies",
        service_labels={"test": "true"},
        environments=[ENV],
        cache_for=None,
    )
    def my_check(_ds: Annotated[Datasource, FromFactory(FactoryWithoutStrategies)]):
        return ok("unused")

    app = Watchpost(
        checks=[my_check],
        execution_environment=ENV,
        executor=BlockingCheckExecutor(),
    )
    app.register_datasource_factory(FactoryWithoutStrategies)

    # Capture warnings emitted during datasource resolution
    caplog.set_level(logging.WARNING)

    with app.app_context():
        # Trigger resolution of strategies which resolves the datasource as well
        _ = app._resolve_scheduling_strategies(my_check)

    warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
    assert all(
        "The factory-created datasource has no scheduling strategies defined"
        not in rec.message
        for rec in warnings
    )


def test_datasource_with_factory():
    @check(
        name="datasource-with-factory-with-type",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def datasource_with_factory_with_type(
        ds: Annotated[
            DatasourceWithFactory,
            FromFactory(DatasourceWithFactory, "some-value-with-type"),
        ],
    ):
        return ok(ds.value)

    @check(
        name="datasource-with-factory-without-type",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def datasource_with_factory_without_type(
        ds: Annotated[DatasourceWithFactory, FromFactory("some-value-without-type")],
    ):
        return ok(ds.value)

    @check(
        name="datasource-with-factory-with-both",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def datasource_with_factory_with_both(
        ds1: Annotated[
            DatasourceWithFactory,
            FromFactory("some-value-without-both"),
        ],
        ds2: Annotated[
            DatasourceWithFactory,
            FromFactory(DatasourceWithFactory, "some-value-without-both"),
        ],
    ):
        return ok(f"{ds1 is ds2=}: {ds1.value} {ds2.value}")

    app = Watchpost(
        checks=[
            datasource_with_factory_without_type,
            datasource_with_factory_with_type,
            datasource_with_factory_with_both,
        ],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )
    app.register_datasource_factory(DatasourceWithFactory)

    checkmk_output = decode_checkmk_output(b"".join(app.run_checks()))
    for item in checkmk_output:
        item.pop("check_definition", None)

    assert len(checkmk_output) == 4
    assert sorted(checkmk_output, key=lambda result: result["service_name"]) == sorted(
        [
            {
                "service_name": "datasource-with-factory-without-type",
                "service_labels": {"test": "true"},
                "environment": "test-env",
                "check_state": "OK",
                "summary": "some-value-without-type",
                "metrics": [],
                "details": None,
            },
            {
                "service_name": "datasource-with-factory-with-type",
                "service_labels": {"test": "true"},
                "environment": "test-env",
                "check_state": "OK",
                "summary": "some-value-with-type",
                "metrics": [],
                "details": None,
            },
            {
                "service_name": "datasource-with-factory-with-both",
                "service_labels": {"test": "true"},
                "environment": "test-env",
                "check_state": "OK",
                "summary": "ds1 is ds2=True: some-value-without-both some-value-without-both",
                "metrics": [],
                "details": None,
            },
            {
                "service_name": "Watchpost: executed checks",
                "service_labels": {},
                "environment": "test-env",
                "check_state": "OK",
                "summary": "3 check/environment pairs eligible to run",
                "metrics": [],
                "details": "Eligible check/environment pairs:\n- tests.test_datasource_factory.test_datasource_with_factory.<locals>.datasource_with_factory_without_type [test-env]\n- tests.test_datasource_factory.test_datasource_with_factory.<locals>.datasource_with_factory_with_type [test-env]\n- tests.test_datasource_factory.test_datasource_with_factory.<locals>.datasource_with_factory_with_both [test-env]",
            },
        ],
        key=lambda result: result["service_name"],
    )


def test_datasource_with_factory_no_args():
    @check(
        name="datasource-by-factory",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def datasource_by_factory(ds: DatasourceWithFactoryNoArgs):
        return ok(str(id(ds)))

    @check(
        name="datasource-annotated-no-args",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def datasource_annotated_no_args(
        ds: Annotated[DatasourceWithFactoryNoArgs, FromFactory()],
    ):
        return ok(str(id(ds)))

    @check(
        name="datasource-annotated-type-arg",
        service_labels={"test": "true"},
        environments=[TEST_ENVIRONMENT],
        cache_for=None,
    )
    def datasource_annotated_type_arg(
        ds: Annotated[
            DatasourceWithFactoryNoArgs, FromFactory(DatasourceWithFactoryNoArgs)
        ],
    ):
        return ok(str(id(ds)))

    app = Watchpost(
        checks=[
            datasource_by_factory,
            datasource_annotated_no_args,
            datasource_annotated_type_arg,
        ],
        execution_environment=TEST_ENVIRONMENT,
        executor=BlockingCheckExecutor(),
    )
    app.register_datasource_factory(DatasourceWithFactoryNoArgs)

    checkmk_output = decode_checkmk_output(b"".join(app.run_checks()))
    for item in checkmk_output:
        item.pop("check_definition", None)

    assert len(checkmk_output) == 4

    # We want to verify that the datasource injected into each check is the
    # exact same object, since the invocations above are, in the end, equivalent.
    datasource_id = checkmk_output[0]["summary"]

    assert sorted(checkmk_output, key=lambda result: result["service_name"]) == sorted(
        [
            {
                "service_name": "Watchpost: executed checks",
                "service_labels": {},
                "environment": "test-env",
                "check_state": "OK",
                "summary": "3 check/environment pairs eligible to run",
                "metrics": [],
                "details": "Eligible check/environment pairs:\n- tests.test_datasource_factory.test_datasource_with_factory_no_args.<locals>.datasource_by_factory [test-env]\n- tests.test_datasource_factory.test_datasource_with_factory_no_args.<locals>.datasource_annotated_no_args [test-env]\n- tests.test_datasource_factory.test_datasource_with_factory_no_args.<locals>.datasource_annotated_type_arg [test-env]",
            },
            {
                "service_name": "datasource-annotated-no-args",
                "service_labels": {"test": "true"},
                "environment": "test-env",
                "check_state": "OK",
                "summary": datasource_id,
                "metrics": [],
                "details": None,
            },
            {
                "service_name": "datasource-annotated-type-arg",
                "service_labels": {"test": "true"},
                "environment": "test-env",
                "check_state": "OK",
                "summary": datasource_id,
                "metrics": [],
                "details": None,
            },
            {
                "service_name": "datasource-by-factory",
                "service_labels": {"test": "true"},
                "environment": "test-env",
                "check_state": "OK",
                "summary": datasource_id,
                "metrics": [],
                "details": None,
            },
        ],
        key=lambda result: result["service_name"],
    )


def test_factory_keys_preserve_order_repetitions_and_keyword_values():
    assert FromFactory(TestFactory, "a", "b").cache_key(None) != FromFactory(
        TestFactory, "b", "a"
    ).cache_key(None)
    assert FromFactory(TestFactory, "a").cache_key(None) != FromFactory(
        TestFactory, "a", "a"
    ).cache_key(None)
    assert FromFactory(TestFactory, a=1, b=2).cache_key(None) == FromFactory(
        TestFactory, b=2, a=1
    ).cache_key(None)

    class Collision:
        def __hash__(self):
            return 42

    first, second = Collision(), Collision()
    assert FromFactory(TestFactory, first).cache_key(None) != FromFactory(
        TestFactory, second
    ).cache_key(None)
    assert FromFactory(TestFactory, value=first).cache_key(None) != FromFactory(
        TestFactory, value=second
    ).cache_key(None)


def test_reordered_factory_arguments_create_distinct_datasources():
    class OrderedDatasource(Datasource):
        def __init__(self, values):
            self.values = values

    class OrderedFactory(DatasourceFactory):
        scheduling_strategies = ()

        @classmethod
        def new(cls, *values):
            return OrderedDatasource(values)

    app = Watchpost(checks=[], execution_environment=Environment("test"))
    app.register_datasource_factory(OrderedFactory)
    first = app._resolve_instantiable_datasource_from_factory(
        OrderedFactory, FromFactory(OrderedFactory, "primary", "backup")
    ).instance()
    second = app._resolve_instantiable_datasource_from_factory(
        OrderedFactory, FromFactory(OrderedFactory, "backup", "primary")
    ).instance()
    assert first is not second
    assert isinstance(first, OrderedDatasource)
    assert isinstance(second, OrderedDatasource)
    assert first.values == ("primary", "backup")
    assert second.values == ("backup", "primary")


@pytest.mark.parametrize(
    "marker", [FromFactory(TestFactory, []), FromFactory(TestFactory, value={})]
)
def test_factory_keys_explain_unhashable_arguments(marker):
    with pytest.raises(ValueError, match="must be hashable"):
        marker.cache_key(None)
