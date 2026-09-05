# Copyright 2025 TAKKT Industrial & Packaging GmbH
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

"""
Check definitions, execution helpers, and caching.

This module provides the core pieces for defining and running monitoring checks:

- `Check`: a lightweight wrapper around a user-defined check function plus
  metadata such as the service name, labels, environments, and hostname
  strategy.
- `check`: a decorator to declare checks in a Pythonic way.
- `CheckCache`: a small helper that stores and retrieves executed check results.

Notes:

- A `Check` maps to one or more Checkmk services. The application turns results
  produced by a check into Checkmk-compatible output later. Service labels map
  to Checkmk service labels.
- Both synchronous and asynchronous check functions are supported.
- Hostname resolution (the Checkmk piggyback host) is delegated to the
  hostname strategy; see `watchpost.hostname` for details.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import typing
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from ._capture import capture_output
from .cache import Cache, CacheEntry, Storage
from .datasource import Datasource
from .environment import Environment
from .globals import current_app
from .hostname import HostnameInput, HostnameStrategy, resolve_hostname, to_strategy
from .result import (
    CheckResult,
    ExecutionResult,
    OngoingCheckResult,
    normalize_check_function_result,
)
from .scheduling_strategy import SchedulingStrategy
from .utils import (
    InvocationInformation,
    get_invocation_information,
    normalize_to_timedelta,
)

if TYPE_CHECKING:
    from .app import Watchpost

CheckFunctionResult = (
    CheckResult
    | OngoingCheckResult
    | list[CheckResult | OngoingCheckResult]
    | Generator[CheckResult | OngoingCheckResult]
)

_E = Environment
_D = TypeVar("_D", bound=Datasource)
_R = CheckFunctionResult | Awaitable[CheckFunctionResult]

CheckFunction = (
    Callable[[_D], _R]
    | Callable[[_D, _D], _R]
    | Callable[[_D, _D, _D], _R]
    | Callable[[_D, _D, _D, _D], _R]
    | Callable[[_D, _D, _D, _D, _D], _R]
    | Callable[[_D, _D, _D, _D, _D, _D], _R]
    | Callable[[_D, _D, _D, _D, _D, _D, _D], _R]
    | Callable[[_D, _D, _D, _D, _D, _D, _D, _D], _R]
    | Callable[[_D, _D, _D, _D, _D, _D, _D, _D, _D], _R]
    | Callable[[_E, _D], _R]
    | Callable[[_E, _D], _R]
    | Callable[[_E, _D, _D], _R]
    | Callable[[_E, _D, _D, _D], _R]
    | Callable[[_E, _D, _D, _D, _D], _R]
    | Callable[[_E, _D, _D, _D, _D, _D], _R]
    | Callable[[_E, _D, _D, _D, _D, _D, _D], _R]
    | Callable[[_E, _D, _D, _D, _D, _D, _D, _D], _R]
    | Callable[[_E, _D, _D, _D, _D, _D, _D, _D, _D], _R]
    | Callable[[_E, _D, _D, _D, _D, _D, _D, _D, _D, _D], _R]
)


class ErrorHandler(Protocol):
    """
    A strategy for transforming error results to match a check's service
    pattern.

    When a check function fails to execute successfully (e.g., raises an
    exception, datasource is unavailable, or scheduling prevents execution),
    Watchpost needs to create synthetic monitoring results. An `ErrorHandler`
    transforms the initial error result(s) to match the services that the check
    would normally create.

    For example, if a check normally produces results for multiple hostnames or
    with different name suffixes, an ErrorHandler can expand a single error
    result into multiple results with the appropriate hostname and service name
    variations.

    Multiple ErrorHandlers can be attached to a single check. They are applied
    sequentially, with each handler receiving the output of the previous one.
    This allows composition of different expansion strategies (e.g., first
    expand by hostname, then by name suffix).

    See `expand_by_hostname()` and `expand_by_name_suffix()` for built-in
    implementations that handle common expansion patterns.
    """

    def __call__(
        self,
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
    ) -> list[ExecutionResult]:
        """
        Transform error results to match the check's service pattern.

        This method is called when a check fails to execute successfully. The
        initial `error_results` list typically contains a single
        `ExecutionResult` representing the error condition (e.g., `CRIT` state
        with exception details).

        The handler should examine the check definition and environment to
        determine what services the check would normally produce, then transform
        the input results accordingly. For example, a handler might:

        - Duplicate results across multiple hostnames using `resolve_hostname()`
        - Apply name suffixes to create service name variations
        - Apply custom business logic based on check metadata

        Parameters:
            check:
                The check definition, providing access to service name, labels,
                hostname strategy, and other metadata that may inform the
                transformation.
            environment:
                The environment in which the check was supposed to run,
                providing context for hostname resolution and other
                environment-specific decisions.
            results:
                The current list of results to transform. Initially this is
                typically a single result, but when multiple handlers are
                chained, it may contain results from previous handlers.

        Returns:
            A list of `ExecutionResult` objects. For simple transformations,
            this may be the same length as error_results. For expansions (e.g.,
            multiplying across hostnames or name suffixes), this will be longer.
            The results should include all necessary variations to match what
            the check function would have produced during normal execution.

        Notes:
            - The handler should NOT modify the input `ExecutionResult` objects.
              Instead, create new objects with the transformed values.
            - All fields from the source result should be preserved unless
              intentionally modified (e.g., `piggyback_host`, `service_name`).
            - The check state (`CRIT`, `UNKNOWN`, etc.) should typically be
              preserved unless the handler has a specific reason to change it.
        """
        ...


def expand_by_hostname(hostnames: list[str]) -> ErrorHandler:
    def handler(
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
    ) -> list[ExecutionResult]:
        resolved_hostnames = [
            resolve_hostname(
                watchpost=current_app,
                environment=environment,
                check=check,
                result=None,
                fallback_to_default_hostname_generation=current_app.hostname_fallback_to_default_hostname_generation,
                coerce_into_valid_hostname=current_app.hostname_coerce_into_valid_hostname,
                explicit_hostname=hostname,
            )
            for hostname in hostnames
        ]

        return [
            ExecutionResult(
                piggyback_host=resolved_hostname,
                service_name=result.service_name,
                service_labels=result.service_labels,
                environment_name=environment.name,
                check_state=result.check_state,
                summary=result.summary,
                details=result.details,
                metrics=result.metrics,
                check_definition=result.check_definition,
            )
            for result in results
            for resolved_hostname in resolved_hostnames
        ]

    return handler


def expand_by_name_suffix(name_suffixes: list[str]) -> ErrorHandler:
    def handler(
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
    ) -> list[ExecutionResult]:
        _ = check, environment
        return [
            ExecutionResult(
                piggyback_host=result.piggyback_host,
                service_name=f"{result.service_name}{name_suffix}",
                service_labels=result.service_labels,
                environment_name=result.environment_name,
                check_state=result.check_state,
                summary=result.summary,
                details=result.details,
                check_definition=result.check_definition,
            )
            for result in results
            for name_suffix in name_suffixes
        ]

    return handler


@dataclass(frozen=True)
class Check:
    """
    Represents a monitoring check definition.

    A `Check` wraps a user-defined check function together with metadata such as
    the service name, labels, environments, caching, scheduling, and hostname
    strategy. A `Check` maps to one or more Checkmk services; each execution
    produces one or more results that are later rendered as Checkmk-compatible
    output.
    """

    check_function: CheckFunction
    """
    The user-provided function that implements the check. It may be synchronous
    or asynchronous and may accept one or more `Datasource` instances and
    optionally an `environment: Environment` parameter.
    """

    service_name: str
    """
    The base service name used for the Checkmk service created by this check.
    A result may append a `name_suffix` to this name to create multiple
    services.
    """

    service_labels: dict[str, Any]
    """
    Labels attached to the service. This maps to Checkmk service labels exactly.
    """

    environments: list[Environment]
    """
    The environments against which this check is executed.
    """

    cache_for: timedelta | None
    """
    Optional time-to-live for caching check results. If set, results are cached
    for this duration. Without caching, the next poll after result pickup starts
    another execution. Polls share outstanding work; a check/environment pair
    never overlaps executions through the application.
    """

    invocation_information: InvocationInformation | None = None
    """
    Source location information for the check definition (file and line).
    Used for diagnostics and included in output.
    """

    scheduling_strategies: list[SchedulingStrategy] | None = None
    """
    Optional scheduling strategies that influence when, where and how the check
    is run.
    """

    hostname_strategy: HostnameStrategy | None = None
    """
    Strategy to resolve the piggyback host (hostname) for results of this
    check. If not set here, environment- or app-level strategies may apply.
    """

    error_handlers: list[ErrorHandler] | None = None
    """
    Optional error handlers that are called to adapt the results when the check
    function raises an exception.
    """

    id: str | None = None
    """Optional stable identity for generated checks with otherwise identical names."""

    @property
    def identity(self) -> str:
        """Stable execution/cache identity, independent of object memory addresses.

        By default combines the qualified function name and service name. Supply
        ``id`` when generated definitions share both. Changing an ID invalidates
        that check's persistent result cache.
        """
        if self.id is not None:
            return "explicit:" + self.id
        return "auto:" + json.dumps([self.name, self.service_name])

    def __hash__(self) -> int:
        """
        Return a stable hash for the check based on its defining properties.
        """
        return hash(
            (
                self.check_function,
                self.service_name,
                tuple(self.service_labels.items()),
                self.cache_for,
                self.invocation_information,
            )
        )

    @property
    def name(self) -> str:
        """
        Returns the fully qualified name of the check function.

        This is the diagnostic and CLI filtering name; execution and cache
        keys use ``identity``.
        """

        return f"{self.check_function.__module__}.{self.check_function.__qualname__}"

    @property
    def signature(self) -> inspect.Signature:
        """
        Returns the cached `inspect.Signature` of the check function.
        """

        return self._check_function_signature  # type: ignore[attr-defined]

    @property
    def type_hints(self) -> dict[str, Any]:
        """
        Returns the resolved type hints for the check function's parameters.

        The mapping excludes the `return` annotation. If resolving forward
        references raises `NameError`, it falls back to a mapping derived from
        the function signature.
        """
        try:
            return {
                k: v
                for k, v in typing.get_type_hints(
                    self.check_function, include_extras=True
                ).items()
                if k != "return"
            }
        except NameError:
            return dict(self.signature.parameters)

    @property
    def is_async(self) -> bool:
        """
        Indicates whether the check function is asynchronous.

        Returns `True` if the function is a coroutine or a coroutine function,
        otherwise `False`.
        """
        return inspect.iscoroutine(self.check_function) or inspect.iscoroutinefunction(
            self.check_function
        )

    def __post_init__(self) -> None:
        """
        Initializes derived fields after dataclass construction.

        Caches the function signature to avoid repeated `inspect.signature`
        calls.
        """
        if self.id is not None and not self.id.strip():
            raise ValueError("Check id must not be empty")
        object.__setattr__(
            self,
            "_check_function_signature",
            inspect.signature(self.check_function),
        )

    def get_function_kwargs(
        self,
        *,
        environment: Environment,
        datasources: dict[str, Datasource],
    ) -> dict[str, Environment | Datasource]:
        """
        Build the keyword arguments to call the check function.

        Parameters:
            environment:
                The environment in which the check runs. Only included in the
                result if the check function declares an `environment`
                parameter.
            datasources:
                A mapping of datasource names to instantiated `Datasource`
                objects.

        Returns:
            A dictionary of keyword arguments to pass to the check function.
        """
        kwargs: dict[str, Environment | Datasource] = {
            **datasources,
        }

        if "environment" in self._check_function_signature.parameters:  # type: ignore[attr-defined]
            kwargs["environment"] = environment

        return kwargs

    def _normalize_and_materialize_results(
        self,
        *,
        watchpost: Watchpost,
        environment: Environment,
        initial_result: CheckFunctionResult,
        stdout: io.StringIO,
        stderr: io.StringIO,
    ) -> list[ExecutionResult]:
        """
        Normalize raw check function output and create `ExecutionResult`
        objects.

        This method handles all valid return forms of a check function (single
        result, list, or generator), merges captured `stdout`/`stderr` into
        result details, applies `name_suffix` to the `service_name`, resolves
        the piggyback host via the hostname strategy, and materializes the final
        `ExecutionResult` instances.

        Parameters:
            watchpost:
                The Watchpost application, used for context and hostname
                resolution settings.
            environment:
                The environment in which the check ran.
            initial_result:
                The raw object returned by the check function before
                normalization.
            stdout:
                Captured `stdout` stream during execution.
            stderr:
                Captured `stderr` stream during execution.

        Returns:
            A list of `ExecutionResult` instances ready for Checkmk output.
        """
        normalized_results = normalize_check_function_result(
            initial_result,
            stdout,
            stderr,
        )

        collected_results = []
        for result in normalized_results:
            updated_service_name = self.service_name
            if result.name_suffix:
                updated_service_name += result.name_suffix

            piggyback_host = resolve_hostname(
                watchpost=watchpost,
                check=self,
                environment=environment,
                result=result,
                fallback_to_default_hostname_generation=watchpost.hostname_fallback_to_default_hostname_generation,
                coerce_into_valid_hostname=watchpost.hostname_coerce_into_valid_hostname,
            )
            collected_results.append(
                ExecutionResult(
                    piggyback_host=piggyback_host,
                    service_name=updated_service_name,
                    service_labels=self.service_labels,
                    environment_name=environment.name,
                    check_state=result.check_state,
                    summary=result.summary,
                    details=result.details,
                    metrics=result.metrics,
                    check_definition=self.invocation_information,
                )
            )

        return collected_results

    def run_sync(
        self,
        *,
        watchpost: Watchpost,
        environment: Environment,
        datasources: dict[str, Datasource],
    ) -> list[ExecutionResult]:
        """
        Execute the check function synchronously and return `ExecutionResult`s.

        Captures `stdout`/`stderr` during execution, then normalizes and
        materializes results. Raises `TypeError` if the check function is
        asynchronous (use `run_async` instead).

        Parameters:
            watchpost:
                The Watchpost application providing execution context.
            environment:
                The environment in which the check should run.
            datasources:
                Instantiated `Datasource` objects passed as keyword arguments.

        Returns:
            A list of `ExecutionResult` instances created from the check output.

        Raises:
            TypeError:
                If the check is asynchronous. Use `run_async` instead.
        """
        if self.is_async:
            raise TypeError(
                "Check is not sync but async, call and await `run_async` instead"
            )

        kwargs = self.get_function_kwargs(
            environment=environment,
            datasources=datasources,
        )
        with capture_output() as (stdout, stderr), watchpost.app_context():
            initial_result = cast(
                CheckFunctionResult,
                self.check_function(**kwargs),  # type: ignore[call-arg]
            )
            return self._normalize_and_materialize_results(
                watchpost=watchpost,
                environment=environment,
                initial_result=initial_result,
                stdout=stdout,
                stderr=stderr,
            )

    async def run_async(
        self,
        *,
        watchpost: Watchpost,
        environment: Environment,
        datasources: dict[str, Datasource],
    ) -> list[ExecutionResult]:
        """
        Execute the check function asynchronously and return `ExecutionResult`s.

        Captures `stdout`/`stderr` during execution, then normalizes and
        materializes results. Raises `TypeError` if the check function is
        synchronous.

        Parameters:
            watchpost:
                The Watchpost application providing execution context.
            environment:
                The environment in which the check should run.
            datasources:
                Instantiated `Datasource` objects passed as keyword arguments.

        Returns:
            A list of `ExecutionResult` instances created from the check output.

        Raises:
            TypeError:
                If the check is synchronous. Use `run_sync` instead.
        """
        if not self.is_async:
            raise TypeError(
                "Check is not async but sync, call `run_sync` without await instead"
            )

        kwargs = self.get_function_kwargs(
            environment=environment,
            datasources=datasources,
        )
        with capture_output() as (stdout, stderr), watchpost.app_context():
            initial_result = await cast(
                Awaitable[CheckFunctionResult],
                self.check_function(**kwargs),  # type: ignore[call-arg]
            )
            return self._normalize_and_materialize_results(
                watchpost=watchpost,
                environment=environment,
                initial_result=initial_result,
                stdout=stdout,
                stderr=stderr,
            )

    def apply_error_handlers(
        self,
        environment: Environment,
        execution_result: ExecutionResult,
    ) -> list[ExecutionResult]:
        results = [execution_result]

        if not self.error_handlers:
            return results

        for error_handler in self.error_handlers:
            results = error_handler(self, environment, results)

        return results


def check(
    *,
    name: str,
    service_labels: dict[str, Any],
    environments: list[Environment],
    cache_for: timedelta | str | None,
    hostname: HostnameInput | None = None,
    scheduling_strategies: list[SchedulingStrategy] | None = None,
    error_handlers: list[ErrorHandler] | None = None,
    id: str | None = None,
) -> Callable[[CheckFunction], Check]:
    """
    Decorator to define a Watchpost check function.

    Use this decorator to declare a check and attach metadata such as the
    service name, labels, environments, caching, and an optional hostname
    strategy. The decorated function may return a single result or
    result-builder, a list of results or result-builders, or yield results or
    result-builders as a generator.

    This decorator supports both sync and async functions.

    Parameters:
        name:
            The Checkmk service name for this check.
        service_labels:
            Labels to attach to the service. This maps to Checkmk service labels
            exactly.
        environments:
            The environments in which this check will be executed.
        cache_for:
            Optional cache duration. Accepts a `timedelta` or a string supported
            by `normalize_to_timedelta`. If provided, results are cached for
            this duration.
        hostname:
            An optional hostname input or strategy that controls piggyback host
            resolution for results of this check.
        scheduling_strategies:
            Optional list of scheduling strategies to apply to this check.
        error_handlers:
            Optional list of error handlers that are called to adapt the results
            when the check function raises an exception.
        id:
            Stable identity override for generated checks sharing both qualified
            function and service names. Must be unique per target environment.

    Returns:
        A `Check` instance wrapping the decorated function and provided
        metadata.

    Notes:
        - A `CheckResult` may set a `name_suffix` to create multiple services
          from a single check function.
        - See `watchpost.hostname` for the available hostname strategy inputs.
    """
    check_definition = get_invocation_information()

    def decorator(func: CheckFunction) -> Check:
        return Check(
            check_function=func,
            service_name=name,
            service_labels=service_labels,
            environments=environments,
            cache_for=normalize_to_timedelta(cache_for),
            invocation_information=check_definition,
            scheduling_strategies=scheduling_strategies,
            hostname_strategy=to_strategy(hostname),
            error_handlers=error_handlers,
            id=id,
        )

    return decorator


class CheckCache:
    """
    Caches executed check results per check and environment.

    This class is a thin wrapper around `Cache` that uses a composite key based
    on the check definition and environment. Values are lists of
    `ExecutionResult` objects, stored with a TTL derived from the `Check`.
    """

    def __init__(self, storage: Storage):
        """
        Initialize the cache with a given storage backend.

        Parameters:
            storage:
                The storage backend used to persist cache entries.
        """
        self._cache = Cache(storage)

    @staticmethod
    def _hash_datasources(datasources: dict[str, Datasource]) -> str:
        """
        Returns a stable hash for a mapping of datasources.

        Useful to detect datasource changes in cache keys when needed.
        """
        return hashlib.sha256(
            str({k: repr(v) for k, v in sorted(datasources.items())}).encode()
        ).hexdigest()

    @staticmethod
    def _generate_check_cache_key(
        check: Check,
        environment: Environment,
    ) -> str:
        """
        Generate a stable cache key for a check/environment pair.

        Returns:
            A versioned key containing the check identity and environment.
            Legacy name-only entries are intentionally not reused.
        """
        return "v2:" + json.dumps([check.identity, environment.name])

    def get_check_results_cache_entry(
        self,
        check: Check,
        environment: Environment,
        return_expired: bool = False,
    ) -> CacheEntry[list[ExecutionResult]] | None:
        """
        Retrieve cached results for a given check and environment.

        Parameters:
            check:
                The check whose results should be retrieved.
            environment:
                The environment associated with the cached results.
            return_expired:
                Whether to return an entry even if it has expired. An expired
                entry is returned at most once.

        Returns:
            A `CacheEntry` containing a list of `ExecutionResult`, or `None` if
            no entry exists.
        """
        return self._cache.get(
            self._generate_check_cache_key(check, environment),
            return_expired=return_expired,
        )

    def store_check_results(
        self,
        check: Check,
        environment: Environment,
        results: list[ExecutionResult],
        override_cache_for: timedelta | None = None,
    ) -> None:
        """
        Store the results of a check execution.

        Parameters:
            check:
                The check that produced the results.
            environment:
                The environment in which the check ran.
            results:
                The list of `ExecutionResult` instances to cache.
            override_cache_for:
                Override the time to cache the result for.

        Notes:
            This is a no-op if `check.cache_for` is `None` (caching disabled).
        """
        if not check.cache_for:
            return

        self._cache.store(
            self._generate_check_cache_key(check, environment),
            results,
            ttl=override_cache_for
            if override_cache_for is not None
            else check.cache_for,
        )
