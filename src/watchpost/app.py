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

"""
Watchpost application and Starlette integration.

This module provides the `Watchpost` ASGI application that discovers and runs
checks, manages datasources, applies scheduling strategies, resolves hostnames,
coordinates execution via an internal executor, and exposes HTTP endpoints.

Notes:
    - The app streams Checkmk-compatible output from `/`.
    - Operational endpoints expose executor statistics and errors.
    - Global app context is available via `current_app` in `globals.py`.
"""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from types import EllipsisType, ModuleType
from typing import (
    Annotated,
    Any,
    TypeVar,
    assert_never,
    cast,
    get_args,
    get_origin,
)

from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

from . import http
from .cache import InMemoryStorage, Storage
from .check import Check, CheckCache
from .datasource import (
    Datasource,
    DatasourceFactory,
    DatasourceUnavailable,
    FromFactory,
)
from .discover_checks import discover_checks
from .environment import Environment
from .executor import CheckExecutor
from .globals import _cv
from .hostname import HostnameInput, resolve_hostname, to_strategy
from .result import CheckState, ExecutionResult
from .scheduling_strategy import (
    DetectImpossibleCombinationStrategy,
    InvalidCheckConfiguration,
    SchedulingDecision,
    SchedulingStrategy,
)

logger = logging.getLogger(f"{__package__}.{__name__}")

_D = TypeVar("_D", bound=Datasource)
_DF = TypeVar("_DF", bound=DatasourceFactory)


class _InstantiableDatasource[D: Datasource, DF: DatasourceFactory]:
    """
    Internal wrapper representing a datasource that can be instantiated later.

    The instance can come from either a concrete `Datasource` type or be
    produced by a `DatasourceFactory`. This wrapper defers instantiation until
    needed, exposes scheduling strategies, and caches a single instance per
    Watchpost process.

    Notes:
        When wrapping a factory, `scheduling_strategies` attempts to instantiate
        the datasource to honor instance-level strategies when possible. If
        instantiation fails (e.g., not runnable in the current environment), it
        falls back to the factory's declared strategies.
    """

    def __init__(
        self,
        *,
        datasource_type: type[D] | None,
        factory_type: type[DF] | None,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ):
        """
        Initialize the wrapper with either a datasource type or a factory.

        Parameters:
            datasource_type:
                Concrete `Datasource` type to instantiate later. Mutually
                exclusive with `factory_type`.
            factory_type:
                Factory type that knows how to create a concrete datasource.
            args:
                Positional arguments forwarded to the factory's constructor when
                using a factory.
            kwargs:
                Keyword arguments forwarded to the datasource constructor (when
                using `datasource_type`) or to the factory (when using
                `factory_type`).
        """
        self.datasource_type = datasource_type
        self.factory_type = factory_type
        self.args = args
        self.kwargs = kwargs
        self._instance: Datasource | None = None

    @property
    def scheduling_strategies(
        self,
    ) -> tuple[SchedulingStrategy, ...] | EllipsisType | None:
        """
        Return scheduling strategies declared by the datasource or its factory.

        If this wrapper already holds a datasource instance, and it exposes
        `scheduling_strategies`, that value is returned. When wrapping a
        factory, this method tries to instantiate the datasource to detect
        instance-level strategies; if instantiation fails, the factory's
        `scheduling_strategies` are used instead.

        Returns:
            A tuple of strategies, `Ellipsis` when unspecified, or `None` when
            no strategies are defined.
        """
        if self._instance is not None and (
            scheduling_strategies := getattr(
                self._instance,
                "scheduling_strategies",
                None,
            )
        ) not in (None, Ellipsis):
            return scheduling_strategies

        if self.factory_type:
            # We try to instantiate the instance to see if it has any specific
            # scheduling strategies. This is a step that can fail if the
            # datasource is not meant to be instantiated in the current
            # execution environment, but this might not be something we know at
            # this point.
            #
            # This can be considered a best-effort attempt to honor a
            # factory-created datasource's scheduling strategies.
            try:
                if (
                    scheduling_strategies := self.instance().scheduling_strategies
                ) not in (None, Ellipsis):
                    return scheduling_strategies
            except Exception:
                pass
            return self.factory_type.scheduling_strategies

        assert self.datasource_type is not None
        return self.datasource_type.scheduling_strategies

    @classmethod
    def from_datasource(
        cls,
        datasource_type: type[D],
        **kwargs: dict[str, Any],
    ) -> _InstantiableDatasource:
        """
        Create an instantiable wrapper for a concrete datasource type.

        Parameters:
            datasource_type:
                The concrete `Datasource` class to instantiate later.
            kwargs:
                Keyword arguments to pass to the datasource constructor.

        Returns:
            A wrapper that can lazily instantiate the datasource.
        """
        return cls(
            datasource_type=datasource_type,
            factory_type=None,
            args=(),
            kwargs=kwargs,
        )

    @classmethod
    def from_factory(
        cls,
        factory_type: type[DF],
        *args: Any,
        **kwargs: Any,
    ) -> _InstantiableDatasource:
        """
        Create an instantiable wrapper from a `DatasourceFactory`.

        Parameters:
            factory_type:
                The factory type used to construct the datasource.
            args:
                Positional arguments forwarded to the factory's constructor.
            kwargs:
                Keyword arguments forwarded to the factory's constructor.

        Returns:
            A wrapper that can lazily create the datasource via the factory.
        """
        return cls(
            datasource_type=None,
            factory_type=factory_type,
            args=args,
            kwargs=kwargs,
        )

    def instance(self) -> Datasource:
        """
        Create or return the cached datasource instance.

        Returns:
            The instantiated datasource. Subsequent calls return the same
            instance.
        """
        if not self._instance:
            if self.factory_type:
                self._instance = self.factory_type.new(
                    *self.args,
                    **self.kwargs,
                )
            else:
                assert self.datasource_type is not None
                self._instance = self.datasource_type(**self.kwargs)

        return self._instance


class Watchpost:
    """
    Main Watchpost application and ASGI app.

    Watchpost discovers and runs checks across environments, coordinates
    datasources, applies scheduling strategies, and generates Checkmk-compatible
    output. A `Watchpost` instance is also an ASGI application backed by
    Starlette and exposes operational endpoints.

    Notes:
        The instance manages a per-check result cache and a key-aware executor
        to run checks without blocking. It also verifies check configuration and
        hostname generation at application startup.
    """

    def __init__(
        self,
        *,
        checks: list[Check | ModuleType],
        execution_environment: Environment,
        version: str = "unknown",
        max_workers: int | None = None,
        executor: CheckExecutor[list[ExecutionResult]] | None = None,
        check_cache_storage: Storage | None = None,
        default_scheduling_strategies: list[SchedulingStrategy] | None = None,
        hostname: HostnameInput | None = None,
        hostname_fallback_to_default_hostname_generation: bool = True,
        hostname_coerce_into_valid_hostname: bool = True,
    ):
        """
        Initialize a Watchpost application.

        Parameters:
            checks:
                A list of `Check` objects or Python modules to scan for checks.
                Modules are discovered recursively.
            execution_environment:
                The environment in which this Watchpost instance runs checks.
            version:
                Version string included in the Checkmk agent header output.
            max_workers:
                Maximum number of worker threads for the internal executor. Used
                when no custom `executor` is provided.
            executor:
                Optional custom `CheckExecutor` to use instead of the default.
            check_cache_storage:
                Optional storage backend for the per-check result cache.
                Defaults to in-memory storage.
            default_scheduling_strategies:
                Default strategies applied to all checks in addition to those
                specified by checks and datasources. If not provided, the
                `DetectImpossibleCombinationStrategy` will be applied by
                default.
            hostname:
                Optional Watchpost-level hostname strategy or value used to
                resolve the piggyback host for results.
            hostname_fallback_to_default_hostname_generation:
                Whether to fall back to the default hostname generation
                "{service_name}-{environment.name}" when no strategy resolves a
                hostname.
            hostname_coerce_into_valid_hostname:
                Whether to coerce a non-compliant hostname into RFC1123 format
                during hostname resolution. If `False`, a non-compliant hostname
                will result in an error.
        """
        self.checks: list[Check] = []
        for check_or_module in checks:
            if isinstance(check_or_module, ModuleType):
                self.checks.extend(
                    discover_checks(
                        module=check_or_module,
                        recursive=True,
                        raise_on_import_error=True,
                    )
                )
            else:
                self.checks.append(check_or_module)

        self.execution_environment = execution_environment
        self.version = version

        self.hostname_strategy = to_strategy(hostname)
        self.hostname_fallback_to_default_hostname_generation = (
            hostname_fallback_to_default_hostname_generation
        )
        self.hostname_coerce_into_valid_hostname = hostname_coerce_into_valid_hostname

        if executor:
            self.executor = executor
        else:
            self.executor = CheckExecutor(max_workers=max_workers)

        if default_scheduling_strategies:
            self.default_scheduling_strategies = default_scheduling_strategies
        else:
            self.default_scheduling_strategies = [
                DetectImpossibleCombinationStrategy(),
            ]

        self._check_cache = CheckCache(
            storage=check_cache_storage or InMemoryStorage(),
        )

        self._datasource_definitions: dict[
            type[Datasource] | type[DatasourceFactory], dict[str, Any]
        ] = {}
        self._datasource_factories: set[type] = set()

        self._instantiable_datasources: dict[
            type[Datasource]
            | type[DatasourceFactory]
            | tuple[type[DatasourceFactory] | None, int, int],
            _InstantiableDatasource,
        ] = {}

        self._resolved_instantiable_datasources: dict[
            Check,
            dict[str, _InstantiableDatasource],
        ] = {}
        self._resolved_strategies: dict[Check, list[SchedulingStrategy]] = {}

        self._starlette = Starlette(
            routes=http.routes,
            lifespan=self._lifespan,
        )

        self._check_scheduling_verified = False
        self._check_hostname_generation_verified = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        ASGI entrypoint that delegates to the internal Starlette app.

        Parameters:
            scope:
                ASGI scope.
            receive:
                ASGI receive callable.
            send:
                ASGI send callable.
        """
        with self.app_context():
            return await self._starlette(scope, receive, send)

    @asynccontextmanager
    async def _lifespan(self, _app: Starlette) -> AsyncGenerator[None]:
        """
        Starlette lifespan hook that verifies configuration on startup.

        This ensures checks are schedulable and hostnames can be resolved before
        serving requests.
        """
        self.verify_check_scheduling()
        self.verify_hostname_generation()
        yield

    @contextmanager
    def app_context(self) -> Generator[Watchpost]:
        """
        Provide a context where the current Watchpost instance is active.

        This sets the global context variable so helper utilities can access the
        current application instance during check execution.

        Returns:
            The current `Watchpost` instance via a context manager.
        """
        try:
            _cv.get()
        except LookupError:
            # We only set the global context variable if it is not already set.
            _token = _cv.set(self)
            try:
                yield self
            finally:
                _cv.reset(_token)
        else:
            # Yielding here (not in the try block) ensures we only catch
            # `LookupError` from `_cv.get()`, not from code that runs during
            # yield. (A `try` falls through to an `else` if no exception was
            # caught.)
            yield self

    def register_datasource(
        self,
        datasource_type: type[_D],
        **kwargs: dict[str, Any],
    ) -> None:
        """
        Register a datasource type and its constructor arguments.

        Parameters:
            datasource_type:
                The `Datasource` subclass to register.
            kwargs:
                Keyword arguments to use when instantiating the datasource.
        """
        if datasource_type.scheduling_strategies is Ellipsis:
            logger.warning(
                "The provided datasource '%s' has no scheduling strategies defined. Please make sure to either define them or explicitly set scheduling_strategies=().",
                datasource_type.__name__,
            )
        self._datasource_definitions[datasource_type] = kwargs

    def register_datasource_factory(self, factory_type: type[_DF]) -> None:
        """
        Register a `DatasourceFactory` that can produce datasources.

        Parameters:
            factory_type:
                The factory type to register.
        """
        self._datasource_factories.add(factory_type)

    def _generate_checkmk_agent_output(self) -> Generator[bytes]:
        """
        Generate the Checkmk agent header for this Watchpost instance.

        Returns:
            A byte stream containing the Checkmk section header with version and
            static agent information.
        """
        yield b"<<<check_mk>>>\n"
        yield b"Version: watchpost-"
        yield self.version.encode("utf-8")
        yield b"\n"
        yield b"AgentOS: watchpost\n"

    def _generate_synthetic_result_outputs(self) -> Generator[bytes]:
        """
        Generate synthetic results that complement real check outputs.

        Returns:
            A byte stream representing additional sections, such as a summary of
            all discovered check functions.
        """

        def run_checks() -> ExecutionResult:
            applicable_checks = [
                check
                for check in self.checks
                if self._resolve_check_scheduling_decision(
                    check, self.execution_environment
                )
                == SchedulingDecision.SCHEDULE
            ]

            details = "Check functions:\n- "
            details += "\n- ".join(check.name for check in applicable_checks)

            return ExecutionResult(
                piggyback_host="",
                service_name="Watchpost: executed checks",
                service_labels={},
                environment_name=self.execution_environment.name,
                check_state=CheckState.OK,
                summary=f"Ran {len(applicable_checks)} checks",
                details=details,
            )

        execution_results = [
            run_checks(),
        ]

        for execution_result in execution_results:
            yield from execution_result.generate_checkmk_output()

    def _resolve_instantiable_datasource(
        self,
        datasource_type: type[_D] | type[_DF],
    ) -> _InstantiableDatasource:
        """
        Resolve a datasource or factory type into an `_InstantiableDatasource`.

        The result is cached per type for reuse across checks.

        Parameters:
            datasource_type:
                Either a concrete `Datasource` subclass or a `DatasourceFactory`
                type.

        Returns:
            An `_InstantiableDatasource` that can create the instance on demand.

        Raises:
            ValueError:
                If no matching datasource definition or factory is registered.
        """
        if instantiable_datasource := self._instantiable_datasources.get(
            datasource_type
        ):
            return instantiable_datasource

        datasource_kwargs = self._datasource_definitions.get(datasource_type)
        if datasource_kwargs is None:
            try:
                instantiable_datasource = (
                    self._resolve_instantiable_datasource_from_factory(
                        cast(type[_DF], datasource_type),
                        FromFactory(),
                    )
                )
            except ValueError as e:
                raise ValueError(
                    f"No datasource definition for {datasource_type}"
                ) from e
        else:
            instantiable_datasource = _InstantiableDatasource.from_datasource(
                cast(type[_D], datasource_type),
                **datasource_kwargs,
            )

        self._instantiable_datasources[datasource_type] = instantiable_datasource
        return instantiable_datasource

    def _resolve_instantiable_datasource_from_factory(
        self,
        type_key: type[_DF],
        from_factory: FromFactory,
    ) -> _InstantiableDatasource:
        """
        Resolve an instantiable datasource specified via
        `Annotated[..., FromFactory]`.

        Parameters:
            type_key:
                The annotated type to use as the cache key when a concrete
                factory is not specified on `from_factory`.
            from_factory:
                The `FromFactory` descriptor carrying the concrete factory and its
                arguments.

        Returns:
            An `_InstantiableDatasource` created from the given factory.

        Raises:
            ValueError:
                If the referenced factory type has not been registered.
        """
        factory_cache_key = from_factory.cache_key(type_key)
        if instantiable_datasource := self._instantiable_datasources.get(
            factory_cache_key
        ):
            return instantiable_datasource

        factory_type = from_factory.factory_type or type_key
        if factory_type in self._datasource_factories:
            instantiable_datasource = _InstantiableDatasource.from_factory(
                factory_type,
                *from_factory.args,
                **from_factory.kwargs,
            )

            if factory_type.scheduling_strategies is Ellipsis:
                logger.warning(
                    "The datasource-factory '%s' has no scheduling strategies defined. Please make sure that either your factory or the datasource created by your factory has them defined or explicitly set to scheduling_strategies=().",
                    factory_type,
                )

            self._instantiable_datasources[factory_cache_key] = instantiable_datasource
            return instantiable_datasource

        raise ValueError(
            f"No datasource factory for {factory_type}. "
            f"Make sure you have registered the factory using register_datasource_factory({factory_type.__name__}) "
            f"before running checks."
        )

    def _resolve_datasources(self, check: Check) -> dict[str, _InstantiableDatasource]:
        """
        Inspect a check's signature and map parameters to instantiable
        datasources.

        This supports two forms of annotations:

        - A concrete `Datasource` subclass.
        - `Annotated[DatasourceType, FromFactory(...)]` to specify a factory and
          its arguments.

        Parameters:
            check:
                The `Check` whose parameters should be resolved.

        Returns:
            A mapping of parameter names to `_InstantiableDatasource` wrappers.

        Raises:
            ValueError:
                If an unsupported annotation is encountered.
        """
        if (
            resolved_instantiable_datasources
            := self._resolved_instantiable_datasources.get(check)
        ):
            return resolved_instantiable_datasources

        instantiable_datasources = {}
        for name, parameter in check.type_hints.items():
            if get_origin(parameter) is Annotated:
                type_key, *args = get_args(parameter)
                annotation_class = args[0]

                if isinstance(annotation_class, FromFactory):
                    instantiable_datasources[name] = (
                        self._resolve_instantiable_datasource_from_factory(
                            type_key,
                            annotation_class,
                        )
                    )
                    continue

                raise ValueError(
                    f"Unsupported annotation {parameter}. "
                    f"When using Annotated, the second argument must be an instance of FromFactory. "
                    f"Example: Annotated[YourDatasourceType, FromFactory(YourFactoryType, 'arg1', arg2=value)]"
                )

            if isinstance(parameter, type) and issubclass(parameter, Datasource):
                instantiable_datasources[name] = self._resolve_instantiable_datasource(
                    parameter
                )
                continue

            if isinstance(parameter, type) and issubclass(parameter, Environment):
                continue

            raise ValueError(
                f"Unsupported parameter `{name}: {parameter}` in `{check.name}`.\n"
                "Only types derived from Datasource (or Environment) are "
                "supported. (If your type is derived from Datasource, make sure "
                "it is a regular class defined outside of a function.)"
            )

        self._resolved_instantiable_datasources[check] = instantiable_datasources
        return instantiable_datasources

    def _resolve_scheduling_strategies(self, check: Check) -> list[SchedulingStrategy]:
        """
        Build the list of scheduling strategies that apply to a check.

        The final list includes, in order:

        - Strategies declared on the check.
        - Strategies declared on each datasource used by the check.
        - The application's default strategies.

        Parameters:
            check:
                The `Check` for which to resolve strategies.

        Returns:
            An ordered list of `SchedulingStrategy` objects to evaluate.
        """
        if resolved_strategies := self._resolved_strategies.get(check):
            return resolved_strategies

        strategies = []

        if check.scheduling_strategies:
            strategies.extend(check.scheduling_strategies)

        for instantiable_datasource in self._resolve_datasources(check).values():
            if (
                instantiable_datasource.scheduling_strategies
                and instantiable_datasource.scheduling_strategies is not Ellipsis
            ):
                strategies.extend(instantiable_datasource.scheduling_strategies)

        strategies.extend(self.default_scheduling_strategies)

        self._resolved_strategies[check] = strategies
        return strategies

    def _resolve_check_scheduling_decision(
        self,
        check: Check,
        environment: Environment,
    ) -> SchedulingDecision:
        """
        Evaluate strategies to decide whether a check should run.

        Parameters:
            check:
                The check under consideration.
            environment:
                The target environment of the check execution.

        Returns:
            The final `SchedulingDecision` after evaluating all strategies.
        """
        strategies = self._resolve_scheduling_strategies(check)

        final_decision = SchedulingDecision.SCHEDULE
        for strategy in strategies:
            decision = strategy.schedule(
                check=check,
                current_execution_environment=self.execution_environment,
                target_environment=environment,
            )
            if decision > final_decision:
                final_decision = decision

        return final_decision

    def verify_check_scheduling(
        self,
        force: bool = False,
    ) -> None:
        """
        Validate that checks can be scheduled and invoked correctly.

        This verifies argument provisioning for each check and evaluates each
        strategy's configuration checks. It aggregates errors across all checks.

        Parameters:
            force:
                Run verification even if it has already completed successfully.

        Raises:
            ExceptionGroup:
                If one or more checks are misconfigured.
        """
        if self._check_scheduling_verified and not force:
            return

        exceptions = []
        with self.app_context():
            for check in self.checks:
                try:
                    datasources = self._resolve_datasources(check)
                    for target_environment in check.environments:
                        available_kwarg_keys = {
                            "environment",
                            *datasources.keys(),
                        }
                        expected_kwarg_keys = set(check.signature.parameters.keys())
                        if not available_kwarg_keys.issuperset(expected_kwarg_keys):
                            exceptions.append(
                                InvalidCheckConfiguration(
                                    check,
                                    (
                                        f"Check requires the following arguments: {', '.join(expected_kwarg_keys)}\n"
                                        f"Watchpost can only provide: {', '.join(available_kwarg_keys)}"
                                    ),
                                )
                            )

                        # We ignore the return value, we only care if .schedule
                        # throws an InvalidCheckConfiguration exception.
                        try:
                            self._resolve_check_scheduling_decision(
                                check,
                                target_environment,
                            )
                        except InvalidCheckConfiguration as e:
                            exceptions.append(e)
                except ValueError as e:
                    exceptions.append(
                        InvalidCheckConfiguration(
                            check,
                            f"Failed to resolve datasources: {e!s}",
                            e,
                        )
                    )

        if exceptions:
            raise ExceptionGroup(
                "One or more checks are not well-configured", exceptions
            )
        self._check_scheduling_verified = True

    def verify_hostname_generation(
        self,
        force: bool = False,
    ) -> None:
        """
        Validate that hostnames can be resolved for all checks and environments.

        Parameters:
            force:
                Run verification even if it has already completed successfully.

        Raises:
            ExceptionGroup:
                If hostname resolution fails for any check/environment.
        """
        if self._check_hostname_generation_verified and not force:
            return

        errors: list[InvalidCheckConfiguration] = []
        for check in self.checks:
            for environment in check.environments:
                try:
                    resolve_hostname(
                        watchpost=self,
                        check=check,
                        environment=environment,
                        result=None,
                        fallback_to_default_hostname_generation=self.hostname_fallback_to_default_hostname_generation,
                        coerce_into_valid_hostname=self.hostname_coerce_into_valid_hostname,
                    )
                except Exception as e:
                    errors.append(
                        InvalidCheckConfiguration(
                            check,
                            "Hostname resolution failed",
                            e,
                        )
                    )

        if errors:
            raise ExceptionGroup("Failed to resolve hostnames", errors)

        self._check_hostname_generation_verified = True

    def _run_check(
        self,
        check: Check,
        environment: Environment,
        instantiable_datasources: dict[str, _InstantiableDatasource],
        *,
        custom_executor: CheckExecutor[list[ExecutionResult]] | None = None,
        use_cache: bool = True,
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
            watchpost=self,
            environment=environment,
            check=check,
            result=None,
            fallback_to_default_hostname_generation=self.hostname_fallback_to_default_hostname_generation,
            coerce_into_valid_hostname=self.hostname_coerce_into_valid_hostname,
        )

        scheduling_decision = self._resolve_check_scheduling_decision(
            check,
            environment,
        )

        if use_cache:
            check_results_cache_entry = self._check_cache.get_check_results_cache_entry(
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

        executor_key = (check.name, environment.name)
        should_update_cache = (
            check.cache_for is None
            or check_results_cache_entry is None
            or check_results_cache_entry.is_expired()
        )
        can_reuse_results = (
            check_results_cache_entry is not None
            and not check_results_cache_entry.is_expired()
        )

        if should_update_cache or not can_reuse_results:
            datasources = {
                name: datasource.instance()
                for name, datasource in instantiable_datasources.items()
            }
            executor.submit(
                key=executor_key,
                func=check.run_async if check.is_async else check.run_sync,
                resubmit=False,
                watchpost=self,
                environment=environment,
                datasources=datasources,
            )

        if can_reuse_results:
            return check_results_cache_entry.value  # type: ignore[union-attr]

        check_has_errored = True
        try:
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
                for result in check_results_cache_entry.value:
                    if result.details:
                        result.details += additional_details
                    else:
                        result.details = additional_details
                return check_results_cache_entry.value

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
            self._check_cache.store_check_results(
                check=check,
                environment=environment,
                results=maybe_execution_results,
                override_cache_for=timedelta(0) if check_has_errored else None,
            )

        return maybe_execution_results

    def run_check(
        self,
        check: Check,
        *,
        custom_executor: CheckExecutor[list[ExecutionResult]] | None = None,
        use_cache: bool = True,
    ) -> Generator[ExecutionResult]:
        """
        Run a single check across all its target environments.

        Parameters:
            check:
                The `Check` to run.
            custom_executor:
                Optional executor used only for this call.
            use_cache:
                Whether to use and update the per-check cache.

        Yields:
            `ExecutionResult` objects produced by the check for each environment.
        """
        with self.app_context():
            instantiable_datasources = self._resolve_datasources(check)
            for environment in check.environments:
                execution_results = self._run_check(
                    check=check,
                    environment=environment,
                    instantiable_datasources=instantiable_datasources,
                    custom_executor=custom_executor,
                    use_cache=use_cache,
                )

                if not execution_results:
                    continue
                yield from execution_results

    def run_checks(self, act_as_agent: bool = True) -> Generator[bytes]:
        """
        Run all checks and produce a Checkmk-compatible output stream.

        This yields the agent header, the serialized results of all checks, and
        synthetic sections.

        Parameters:
            act_as_agent:
                If Watchpost should act as a full Checkmk agent, including the
                `<<<checkmk>>>` preamble. This should be true if Watchpost is
                queried by the Checkmk site directly (via HTTP), but can be set
                to false if you have the Checkmk agent invoke Watchpost for you,
                for example.

        Yields:
            Bytes in Checkmk agent format.
        """
        self.verify_check_scheduling()
        with self.app_context():
            if act_as_agent:
                yield from self._generate_checkmk_agent_output()

            for check in self.checks:
                for execution_result in self.run_check(check):
                    yield from execution_result.generate_checkmk_output()

            yield from self._generate_synthetic_result_outputs()

    def run_checks_once(self, act_as_agent: bool = True) -> None:
        """
        Run all the checks once and write the output stream to stdout.

        This is a convenience method primarily intended for CLI usage.

        Parameters:
            act_as_agent:
                If Watchpost should act as a full Checkmk agent, including the
                `<<<checkmk>>>` preamble. This should be true if Watchpost is
                queried by the Checkmk site directly (via HTTP), but can be set
                to false if you have the Checkmk agent invoke Watchpost for you,
                for example.
        """
        with self.app_context():
            for chunk in self.run_checks(act_as_agent=act_as_agent):
                sys.stdout.buffer.write(chunk)
