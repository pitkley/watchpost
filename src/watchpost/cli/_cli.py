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

from collections.abc import Iterable

import click  # type: ignore
from rich.console import Console
from rich.live import Live
from rich.table import Table

from watchpost.executor import BlockingCheckExecutor, CheckExecutor
from watchpost.hostname import resolve_hostname
from watchpost.scheduling_strategy import InvalidCheckConfiguration

from ..app import Watchpost
from ..result import CheckState, ExecutionResult
from .loader import find_app

STATE_STYLES = {
    CheckState.OK: "bold green",
    CheckState.WARN: "bold yellow",
    CheckState.CRIT: "bold red",
    CheckState.UNKNOWN: "bold magenta",
}


def _get_check_hostnames(
    app: Watchpost,
    *,
    collect_errors: bool = False,
) -> set[str]:
    hostnames = set()
    errors = []
    for check in app.checks:
        for environment in check.environments:
            try:
                hostname = resolve_hostname(
                    watchpost=app,
                    check=check,
                    environment=environment,
                    result=None,
                    fallback_to_default_hostname_generation=app.hostname_fallback_to_default_hostname_generation,
                    coerce_into_valid_hostname=app.hostname_coerce_into_valid_hostname,
                )
                hostnames.add(hostname)
            except Exception as e:
                if collect_errors:
                    errors.append(
                        InvalidCheckConfiguration(
                            check, "Hostname resolution failed", e
                        )
                    )
                else:
                    raise e

    if errors:
        raise ExceptionGroup("Failed to resolve hostnames", errors)

    return hostnames


def display_results_table(results: Iterable[ExecutionResult]) -> None:
    """Displays a list of ExecutionResults in a rich Table."""

    console = Console()
    table = Table(title="Check Execution Results")
    table.add_column("State", justify="center", no_wrap=True)
    table.add_column("Environment", style="cyan")
    table.add_column("Service Name", style="bold")
    table.add_column("Summary", style="default", overflow="fold")

    with Live(table, console=console, vertical_overflow="visible"):
        for result in results:
            state_style = STATE_STYLES.get(result.check_state, "default")

            summary = result.summary
            if result.details:
                summary = f"{summary.strip()}\n\n{result.details}"

            table.add_row(
                f"[{state_style}]{result.check_state.name}[/]",
                result.environment_name,
                result.service_name,
                summary.strip(),
            )
            table.add_section()


@click.group()  # type: ignore[misc]
@click.option(
    "--app",
    help="The Watchpost application to load, in 'module:variable' format.",
)  # type: ignore[misc]
@click.pass_context  # type: ignore[misc]
def cli(
    ctx: click.Context,
    app: str | None = None,
) -> None:
    ctx.obj = find_app(app)


@cli.command()  # type: ignore[misc]
@click.pass_obj  # type: ignore[misc]
def list_checks(app: Watchpost) -> None:
    for check in sorted(app.checks, key=lambda check: check.name):
        click.echo(f"{check.name}{check.signature}")


@cli.command()  # type: ignore[misc]
@click.pass_obj  # type: ignore[misc]
def verify_check_configuration(app: Watchpost) -> None:
    console = Console()

    table = Table(title="Check Configuration Verification")
    table.add_column("Check Name")
    table.add_column("Error")

    try:
        app.verify_check_scheduling()
        console.print("Check configurations verified.")
    except ExceptionGroup as exception_group:
        for exception in exception_group.exceptions:
            if not isinstance(exception, InvalidCheckConfiguration):
                table.add_row(
                    "<unknown>",
                    str(exception),
                )
            else:
                table.add_row(
                    exception.check.name,
                    exception.reason,
                )
            table.add_section()

    try:
        _get_check_hostnames(app, collect_errors=True)
    except ExceptionGroup as exception_group:
        for exception in exception_group.exceptions:
            if isinstance(exception, InvalidCheckConfiguration):
                table.add_row(
                    exception.check.name,
                    str(exception.cause),
                )
            else:
                table.add_row(
                    "<unknown>",
                    str(exception),
                )
            table.add_section()

    if table.rows:
        console.print(table)


@cli.command()  # type: ignore[misc]
@click.option(
    "--asynchronous-check-execution/--synchronous-check-execution",
    is_flag=True,
    default=False,
    help=(
        "Whether to run checks asynchronously or synchronously (default). If "
        "you run them asynchronously, almost all checks will return in the "
        "`UNKNOWN` state informing you they are running asynchronously."
    ),
)  # type: ignore[misc]
@click.option(
    "--cache/--no-cache",
    is_flag=True,
    default=False,
    help=(
        "Whether to make use of check-caching at all, both for storing and for "
        "retrieving. Usually when you run on the CLI you don't want any caches "
        "to be active, which is the default. If you specifically want to test "
        "and see cached results, provide `--cache`."
    ),
)  # type: ignore[misc]
@click.option(
    "--filter-prefix",
    default=None,
    help="Filter which checks to run by prefix against their name (as shown by list-checks)",
)  # type: ignore[misc]
@click.option(
    "--filter-contains",
    default=None,
    help="Filter which checks to run by substring against their name (as shown by list-checks)",
)  # type: ignore[misc]
@click.pass_obj  # type: ignore[misc]
def run_checks(
    app: Watchpost,
    asynchronous_check_execution: bool = False,
    cache: bool = False,
    filter_prefix: str | None = None,
    filter_contains: str | None = None,
) -> None:
    custom_executor: CheckExecutor[list[ExecutionResult]] | None = None
    if not asynchronous_check_execution:
        custom_executor = BlockingCheckExecutor()

    def _run() -> Iterable[ExecutionResult]:
        for check in app.checks:
            if filter_prefix and not check.name.startswith(filter_prefix):
                continue
            if filter_contains and filter_contains not in check.name:
                continue

            yield from app.run_check(
                check,
                custom_executor=custom_executor,
                use_cache=cache,
            )

    try:
        display_results_table(_run())
    finally:
        if custom_executor is not None:
            custom_executor.shutdown(wait=True, cancel_futures=True)
        app.shutdown()


@cli.command()  # type: ignore[misc]
@click.pass_obj  # type: ignore[misc]
def get_check_hostnames(app: Watchpost) -> None:
    for hostname in sorted(_get_check_hostnames(app)):
        click.echo(hostname)


def main() -> None:
    cli(auto_envvar_prefix="WATCHPOST")
