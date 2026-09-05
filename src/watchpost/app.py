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

import asyncio
import sys
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from types import ModuleType
from typing import (
    Any,
    TypeVar,
)

from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

from . import http
from ._planning import _CheckPlanner, _InstantiableDatasource
from ._runtime import _CheckRuntime, _PollOutcome
from .cache import Storage
from .check import Check
from .datasource import (
    Datasource,
    DatasourceFactory,
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

_D = TypeVar("_D", bound=Datasource)
_DF = TypeVar("_DF", bound=DatasourceFactory)


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

        self._runtime = _CheckRuntime(
            executor=executor,
            max_workers=max_workers,
            check_cache_storage=check_cache_storage,
        )
        self._planner = _CheckPlanner(
            default_scheduling_strategies or [DetectImpossibleCombinationStrategy()]
        )
        # Retain these private aliases for existing integrations and diagnostics.
        self._check_cache = self._runtime.cache
        self._datasource_definitions = self._planner._datasource_definitions
        self._datasource_factories = self._planner._datasource_factories
        self._instantiable_datasources = self._planner._instantiable_datasources
        self._resolved_instantiable_datasources = (
            self._planner._resolved_instantiable_datasources
        )
        self._resolved_strategies = self._planner._resolved_strategies

        self._starlette = Starlette(
            routes=http.routes,
            lifespan=self._lifespan,
        )

        self._check_scheduling_verified = False
        self._check_hostname_generation_verified = False

    @property
    def executor(self) -> CheckExecutor[list[ExecutionResult]]:
        """The executor used by this application."""
        return self._runtime.executor

    @executor.setter
    def executor(self, executor: CheckExecutor[list[ExecutionResult]]) -> None:
        self._runtime.executor = executor

    @property
    def default_scheduling_strategies(self) -> list[SchedulingStrategy]:
        """Default strategies included when resolving each check's plan."""
        return self._planner.default_scheduling_strategies

    @default_scheduling_strategies.setter
    def default_scheduling_strategies(
        self, strategies: list[SchedulingStrategy]
    ) -> None:
        self._planner.default_scheduling_strategies = strategies

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
        try:
            self.verify_check_scheduling()
            self.verify_hostname_generation()
            yield
        finally:
            await asyncio.to_thread(self.shutdown)

    def shutdown(self, wait: bool = True) -> None:
        """Release an internally created executor; supplied executors are caller-owned.

        Cancel async/queued checks and wait for running synchronous checks when
        ``wait=True``. Call this after standalone use; ASGI lifespan does so
        automatically, including when startup validation fails.
        """
        self._runtime.shutdown(wait=wait)

    @contextmanager
    def app_context(self) -> Generator[Watchpost]:
        """
        Provide a context where the current Watchpost instance is active.

        This sets the global context variable so helper utilities can access the
        current application instance during check execution. Nested contexts activate
        their own application and restore the previous one on exit.

        Returns:
            The current `Watchpost` instance via a context manager.
        """
        token = _cv.set(self)
        try:
            yield self
        finally:
            _cv.reset(token)

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
        self._planner.register_datasource(datasource_type, **kwargs)

    def register_datasource_factory(self, factory_type: type[_DF]) -> None:
        """
        Register a `DatasourceFactory` that can produce datasources.

        Parameters:
            factory_type:
                The factory type to register.
        """
        self._planner.register_datasource_factory(factory_type)

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

    def _generate_synthetic_result_outputs(
        self,
        eligible_pairs: list[tuple[Check, Environment]],
    ) -> Generator[bytes]:
        """Describe actual SCHEDULE decisions from this poll, including cache hits."""
        details = "Eligible check/environment pairs:"
        if eligible_pairs:
            details += "\n- " + "\n- ".join(
                f"{check.name} [{environment.name}]"
                for check, environment in eligible_pairs
            )
        result = ExecutionResult(
            piggyback_host="",
            # Preserve this service identity for existing Checkmk installations.
            service_name="Watchpost: executed checks",
            service_labels={},
            environment_name=self.execution_environment.name,
            check_state=CheckState.OK,
            summary=f"{len(eligible_pairs)} check/environment pairs eligible to run",
            details=details,
        )
        yield from result.generate_checkmk_output()

    def _resolve_instantiable_datasource(
        self,
        datasource_type: type[_D] | type[_DF],
    ) -> _InstantiableDatasource:
        return self._planner._resolve_instantiable_datasource(datasource_type)

    def _resolve_instantiable_datasource_from_factory(
        self,
        type_key: type[_DF],
        from_factory: FromFactory,
    ) -> _InstantiableDatasource:
        return self._planner._resolve_instantiable_datasource_from_factory(
            type_key, from_factory
        )

    def _resolve_datasources(self, check: Check) -> dict[str, _InstantiableDatasource]:
        return self._planner._resolve_datasources(check)

    def _resolve_scheduling_strategies(self, check: Check) -> list[SchedulingStrategy]:
        return self._planner._resolve_scheduling_strategies(check)

    def _resolve_check_scheduling_decision(
        self,
        check: Check,
        environment: Environment,
    ) -> SchedulingDecision:
        return self._planner.resolve_plan(check).schedule(
            self.execution_environment, environment
        )

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
        identities: set[tuple[str, str]] = set()
        with self.app_context():
            for check in self.checks:
                try:
                    datasources = self._resolve_datasources(check)
                    for target_environment in check.environments:
                        identity = (check.identity, target_environment.name)
                        if identity in identities:
                            exceptions.append(
                                InvalidCheckConfiguration(
                                    check,
                                    "Duplicate check identity for target environment; assign distinct @check(id=...) values",
                                )
                            )
                        identities.add(identity)
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
        scheduling_decision: SchedulingDecision | None = None,
    ) -> list[ExecutionResult] | None:
        return self._runtime._run_check(
            self,
            check,
            environment,
            instantiable_datasources,
            custom_executor=custom_executor,
            use_cache=use_cache,
            scheduling_decision=scheduling_decision,
        )

    def _poll_check(
        self,
        check: Check,
        environment: Environment,
        *,
        custom_executor: CheckExecutor[list[ExecutionResult]] | None = None,
        use_cache: bool = True,
    ) -> _PollOutcome:
        return self._runtime._poll_check(
            self,
            check,
            environment,
            custom_executor=custom_executor,
            use_cache=use_cache,
        )

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
        for environment in check.environments:
            outcome = self._poll_check(
                check,
                environment,
                custom_executor=custom_executor,
                use_cache=use_cache,
            )
            if outcome.results:
                yield from outcome.results

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
        if act_as_agent:
            yield from self._generate_checkmk_agent_output()

        eligible_pairs = []
        for check in self.checks:
            for environment in check.environments:
                outcome = self._poll_check(check, environment)
                if outcome.decision == SchedulingDecision.SCHEDULE:
                    eligible_pairs.append((check, environment))
                for result in outcome.results or []:
                    yield from result.generate_checkmk_output()

        yield from self._generate_synthetic_result_outputs(eligible_pairs)

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
