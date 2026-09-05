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


"""Internal datasource registration and check scheduling metadata resolution."""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from types import EllipsisType
from typing import Annotated, Any, TypeVar, cast, get_args, get_origin

from .check import Check
from .datasource import Datasource, DatasourceFactory, FactoryCacheKey, FromFactory
from .environment import Environment
from .scheduling_strategy import SchedulingDecision, SchedulingStrategy

logger = logging.getLogger(__name__)

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
        self._instance_lock = threading.RLock()

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
        strategies, _ = self.resolve_scheduling_strategies()
        return strategies

    def resolve_scheduling_strategies(
        self,
    ) -> tuple[tuple[SchedulingStrategy, ...] | EllipsisType | None, Exception | None]:
        """Read one consistent strategy snapshot, retaining failed factory probes.

        A failed probe only supplies provisional factory strategies. The caller
        must retry resolution on a later poll and must not retry construction
        during execution of the provisional plan.
        """
        with self._instance_lock:
            if self.factory_type:
                try:
                    strategies = self.instance().scheduling_strategies
                except Exception as error:
                    return self.factory_type.scheduling_strategies, error
                if strategies not in (None, Ellipsis):
                    return strategies, None
                return self.factory_type.scheduling_strategies, None

            if self._instance is not None:
                strategies = self._instance.scheduling_strategies
                if strategies not in (None, Ellipsis):
                    return strategies, None
            assert self.datasource_type is not None
            return self.datasource_type.scheduling_strategies, None

    @classmethod
    def from_datasource(
        cls,
        datasource_type: type[D],
        **kwargs: Any,
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
        with self._instance_lock:
            if self._instance is None:
                if self.factory_type:
                    self._instance = self.factory_type.new(
                        *self.args,
                        **self.kwargs,
                    )
                else:
                    assert self.datasource_type is not None
                    self._instance = self.datasource_type(**self.kwargs)

            return self._instance


@dataclass(frozen=True)
class _ResolvedCheckPlan:
    """Resolved dependencies and strategies, without a cached scheduling decision."""

    check: Check
    datasources: dict[str, _InstantiableDatasource]
    strategies: list[SchedulingStrategy]
    datasource_error: Exception | None
    _active_plan: ContextVar[_ResolvedCheckPlan | None]

    def schedule(
        self,
        execution_environment: Environment,
        target_environment: Environment,
    ) -> SchedulingDecision:
        """Evaluate every strategy against the current execution and target pair."""
        # Strategies such as DetectImpossibleCombinationStrategy inspect the
        # planner again. They must see this snapshot even if another poll has
        # meanwhile resolved a previously unavailable factory successfully.
        token = self._active_plan.set(self)
        try:
            final_decision = SchedulingDecision.SCHEDULE
            for strategy in self.strategies:
                decision = strategy.schedule(
                    check=self.check,
                    current_execution_environment=execution_environment,
                    target_environment=target_environment,
                )
                if decision > final_decision:
                    final_decision = decision

            return final_decision
        finally:
            self._active_plan.reset(token)


class _CheckPlanner:
    """Own datasource registrations and reusable metadata for each check."""

    def __init__(self, default_scheduling_strategies: list[SchedulingStrategy]) -> None:
        self.default_scheduling_strategies = default_scheduling_strategies
        self._datasource_definitions: dict[
            type[Datasource] | type[DatasourceFactory], dict[str, Any]
        ] = {}
        self._datasource_factories: set[type] = set()

        self._instantiable_datasources: dict[
            type[Datasource] | type[DatasourceFactory] | FactoryCacheKey,
            _InstantiableDatasource,
        ] = {}

        self._resolved_instantiable_datasources: dict[
            Check,
            dict[str, _InstantiableDatasource],
        ] = {}
        self._resolved_strategies: dict[Check, list[SchedulingStrategy]] = {}
        self._resolved_plans: dict[Check, _ResolvedCheckPlan] = {}
        self._metadata_lock = threading.RLock()
        self._resolution_locks: dict[Check, threading.RLock] = {}
        self._active_plan: ContextVar[_ResolvedCheckPlan | None] = ContextVar(
            "watchpost_check_plan", default=None
        )

    def resolve_plan(self, check: Check) -> _ResolvedCheckPlan:
        """Reuse complete plans and retry factory probes in provisional plans.

        Only metadata publication uses the global lock. Factory code runs under
        the check's resolution lock and the individual datasource's instance lock,
        so unrelated checks and datasources can be resolved concurrently.
        """
        with self._metadata_lock:
            lock = self._resolution_locks.setdefault(check, threading.RLock())
        with lock:
            plan = self._resolved_plans.get(check)
            if plan is not None and plan.datasource_error is None:
                return plan

            datasources = self._resolve_datasources(check)
            strategies = list(check.scheduling_strategies or ())
            datasource_error: Exception | None = None
            resolutions = {
                datasource: datasource.resolve_scheduling_strategies()
                for datasource in dict.fromkeys(datasources.values())
            }
            for datasource in datasources.values():
                datasource_strategies, error = resolutions[datasource]
                if (
                    datasource_strategies is not None
                    and datasource_strategies is not Ellipsis
                ):
                    strategies.extend(datasource_strategies)
                if datasource_error is None:
                    datasource_error = error
            strategies.extend(self.default_scheduling_strategies)
            plan = _ResolvedCheckPlan(
                check=check,
                datasources=datasources,
                strategies=strategies,
                datasource_error=datasource_error,
                _active_plan=self._active_plan,
            )
            self._resolved_strategies[check] = strategies
            self._resolved_plans[check] = plan
            return plan

    def register_datasource(
        self,
        datasource_type: type[_D],
        **kwargs: Any,
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
        with self._metadata_lock:
            self._datasource_definitions[datasource_type] = kwargs

    def register_datasource_factory(self, factory_type: type[_DF]) -> None:
        """
        Register a `DatasourceFactory` that can produce datasources.

        Parameters:
            factory_type:
                The factory type to register.
        """
        with self._metadata_lock:
            self._datasource_factories.add(factory_type)

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
        with self._metadata_lock:
            instantiable_datasource = self._instantiable_datasources.get(
                datasource_type
            )
            datasource_kwargs = self._datasource_definitions.get(datasource_type)
        if instantiable_datasource is not None:
            return instantiable_datasource

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

        with self._metadata_lock:
            return self._instantiable_datasources.setdefault(
                datasource_type, instantiable_datasource
            )

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
        factory_type = from_factory.factory_type or type_key
        with self._metadata_lock:
            instantiable_datasource = self._instantiable_datasources.get(
                factory_cache_key
            )
            factory_registered = factory_type in self._datasource_factories
        if instantiable_datasource is not None:
            return instantiable_datasource

        if factory_registered:
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

            with self._metadata_lock:
                return self._instantiable_datasources.setdefault(
                    factory_cache_key, instantiable_datasource
                )

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
        with self._metadata_lock:
            resolved_instantiable_datasources = (
                self._resolved_instantiable_datasources.get(check)
            )
        if resolved_instantiable_datasources is not None:
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

        with self._metadata_lock:
            return self._resolved_instantiable_datasources.setdefault(
                check, instantiable_datasources
            )

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
        active_plan = self._active_plan.get()
        if active_plan is not None and active_plan.check is check:
            return active_plan.strategies
        return self.resolve_plan(check).strategies
