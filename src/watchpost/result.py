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
Result types and builders for Watchpost checks.
"""

from __future__ import annotations

import base64
import io
import json
import traceback
from collections.abc import Callable, Generator
from dataclasses import dataclass
from enum import Enum
from types import GeneratorType
from typing import TYPE_CHECKING, Any, assert_never, cast

from .utils import InvocationInformation

if TYPE_CHECKING:
    from .check import CheckFunctionResult
    from .hostname import HostnameInput

Details = str | dict | Exception


def normalize_details(details: Details | None) -> str | None:
    """
    Normalize and format the given details into a string.

    This function processes the input `details` parameter and normalizes it into
    a string representation based on its type. If the input is `None`, it
    returns `None`. For `Exception` objects, it extracts and formats the
    exception details. For dictionaries, it formats each key-value pair as a
    newline-separated string. Empty strings are normalized to `None`. Other
    string inputs are returned unchanged.

    Parameters:
        details:
            The input details to normalize. It can be of type `Details` (e.g.,
            exception, string, dictionary), or `None`.

    Returns:
        A formatted string representation of the input details, or `None` if the
        input is `None` or an empty string.
    """

    if details is None:
        return None

    if isinstance(details, Exception):
        return "".join(traceback.format_exception(details))

    if isinstance(details, dict):
        return "\n".join(f"{key}: {value}" for key, value in details.items())

    if isinstance(details, str) and details.strip() == "":
        return None

    return details


@dataclass
class Thresholds:
    """
    Represents threshold values with warning and critical levels.

    This maps to the Checkmk concept of thresholds exactly and thus directly
    relates to metrics, see the `Metric` class.
    """

    warning: int | float
    critical: int | float

    def to_json_compatible_dict(self) -> dict[str, int | float]:
        return {
            "warning": self.warning,
            "critical": self.critical,
        }


@dataclass
class Metric:
    """
    Represents a measurable metric with an optional threshold configuration.

    This maps to the Checkmk concept of metrics exactly.
    """

    name: str
    value: int | float
    levels: Thresholds | None = None
    boundaries: Thresholds | None = None

    def to_json_compatible_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.sanitized_name,
            "value": self.value,
        }
        if self.levels is not None:
            result["levels"] = self.levels.to_json_compatible_dict()
        if self.boundaries is not None:
            result["boundaries"] = self.boundaries.to_json_compatible_dict()
        return result

    @property
    def sanitized_name(self) -> str:
        return (
            self.name.replace(" ", "_")
            .replace(":", "_")
            .replace("/", "_")
            .replace("\\", "_")
        )


class CheckState(Enum):
    """
    Represents the state of a check.

    This maps to the Checkmk concept of states exactly.
    """

    OK = 0
    WARN = 1
    CRIT = 2
    UNKNOWN = 3

    def __lt__(self, other: CheckState) -> bool:
        return self.value < other.value

    @property
    def check_function(self) -> Callable[..., CheckResult]:
        """
        Determines the appropriate check function based on the current check
        state.

        This property evaluates the current `CheckState` of the instance and
        matches it to a corresponding check function (`ok`, `warn`, `crit`, or
        `unknown`).

        Returns:
            The function corresponding to the current state of the check.
        """

        match self:
            case CheckState.OK:
                return ok
            case CheckState.WARN:
                return warn
            case CheckState.CRIT:
                return crit
            case CheckState.UNKNOWN:
                return unknown
            case _:
                assert_never(self)  # type: ignore[type-assertion-failure]

    @property
    def state_marker(self) -> str:
        """
        Returns a Checkmk output compatible state marker (such as `(!!)` for
        CRIT) which Checkmk will automatically render as a visual status label.
        """

        match self:
            case CheckState.OK:
                return "(.)"
            case CheckState.WARN:
                return "(!)"
            case CheckState.CRIT:
                return "(!!)"
            case CheckState.UNKNOWN:
                return "(?)"
            case _:
                assert_never(self)  # type: ignore[type-assertion-failure]


@dataclass(init=False)
class CheckResult:
    """
    Represents the result of a check performed on a system or component.

    This class encapsulates the result of a check operation, providing details
    such as the state of the check, a summary, optional details, a name suffix,
    metrics, and hostname information. It is designed to store and convey the
    outcome of a check operation in a structured format.
    """

    check_state: CheckState
    """
    The state of the check, indicating whether it was successful, warning,
    critical, or unknown.
    """

    summary: str
    """
    A summary of the check result, indicating the outcome of the check.
    
    Checkmk will show this summary on pages like a host overview.
    """

    details: str | None = None
    """
    A detailed output of the check result, providing additional information
    beyond the summary.
    
    Checkmk will show this detailed output when you are viewing a specific
    service.
    """

    name_suffix: str | None = None
    """
    A suffix to add to the check name as defined in the `@check` decorator.
    
    This enables a single check function to return multiple results that create
    multiple services on the Checkmk side.
    """

    metrics: list[Metric] | None = None
    """
    An optional list of metrics to associate with the check.
    """

    hostname: HostnameInput | None = None
    """
    An optional hostname that overrides the hostname that would have been used
    otherwise.
    """

    def __init__(
        self,
        check_state: CheckState,
        summary: str,
        details: Details | None = None,
        name_suffix: str | None = None,
        metrics: list[Metric] | None = None,
        hostname: HostnameInput | None = None,
    ):
        self.check_state = check_state
        self.summary = summary
        self.details = normalize_details(details)
        self.name_suffix = name_suffix
        self.metrics = metrics
        self.hostname = hostname


def ok(
    summary: str,
    details: Details | None = None,
    name_suffix: str | None = None,
    metrics: list[Metric] | None = None,
    alternative_hostname: HostnameInput | None = None,
) -> CheckResult:
    """
    Generates a CheckResult object indicating an OK check state.

    This function creates and returns a CheckResult indicating the system or
    component is in an OK state. It allows for passing additional information
    such as a summary, details, name suffix, metrics, or an alternative
    hostname.

    Parameters:
        summary:
            A summary of the check result, indicating the outcome of the check.
            Checkmk will show this summary on pages like a host overview.
        details:
            A detailed output of the check result, providing additional
            information beyond the summary. Checkmk will show this detailed
            output when you are viewing a specific service.
        name_suffix:
            A suffix to add to the check name as defined in the `@check`
            decorator. This enables a single check function to return multiple
            results that create multiple services on the Checkmk side.
        metrics:
            An optional list of metrics to associate with the check.
        alternative_hostname:
            An optional hostname that overrides the hostname that would have
            been used otherwise.

    Returns:
        A `CheckResult` object representing the OK check result.
    """

    return CheckResult(
        check_state=CheckState.OK,
        summary=summary,
        details=details,
        name_suffix=name_suffix,
        metrics=metrics,
        hostname=alternative_hostname,
    )


def warn(
    summary: str,
    details: Details | None = None,
    name_suffix: str | None = None,
    metrics: list[Metric] | None = None,
    alternative_hostname: HostnameInput | None = None,
) -> CheckResult:
    """
    Generates a CheckResult object indicating a WARN check state.

    This function creates and returns a CheckResult indicating the system or
    component is in a WARN state. It allows for passing additional information
    such as a summary, details, name suffix, metrics, or an alternative
    hostname.

    Parameters:
        summary:
            A summary of the check result, indicating the outcome of the check.
            Checkmk will show this summary on pages like a host overview.
        details:
            A detailed output of the check result, providing additional
            information beyond the summary. Checkmk will show this detailed
            output when you are viewing a specific service.
        name_suffix:
            A suffix to add to the check name as defined in the `@check`
            decorator. This enables a single check function to return multiple
            results that create multiple services on the Checkmk side.
        metrics:
            An optional list of metrics to associate with the check.
        alternative_hostname:
            An optional hostname that overrides the hostname that would have
            been used otherwise.

    Returns:
        A `CheckResult` object representing the WARN check result.
    """

    return CheckResult(
        check_state=CheckState.WARN,
        summary=summary,
        details=details,
        name_suffix=name_suffix,
        metrics=metrics,
        hostname=alternative_hostname,
    )


def crit(
    summary: str,
    details: Details | None = None,
    name_suffix: str | None = None,
    metrics: list[Metric] | None = None,
    alternative_hostname: HostnameInput | None = None,
) -> CheckResult:
    """
    Generates a CheckResult object indicating a CRIT check state.

    This function creates and returns a CheckResult indicating the system or
    component is in a CRIT state. It allows for passing additional information
    such as a summary, details, name suffix, metrics, or an alternative
    hostname.

    Parameters:
        summary:
            A summary of the check result, indicating the outcome of the check.
            Checkmk will show this summary on pages like a host overview.
        details:
            A detailed output of the check result, providing additional
            information beyond the summary. Checkmk will show this detailed
            output when you are viewing a specific service.
        name_suffix:
            A suffix to add to the check name as defined in the `@check`
            decorator. This enables a single check function to return multiple
            results that create multiple services on the Checkmk side.
        metrics:
            An optional list of metrics to associate with the check.
        alternative_hostname:
            An optional hostname that overrides the hostname that would have
            been used otherwise.

    Returns:
        A `CheckResult` object representing the CRIT check result.
    """

    return CheckResult(
        check_state=CheckState.CRIT,
        summary=summary,
        details=details,
        name_suffix=name_suffix,
        metrics=metrics,
        hostname=alternative_hostname,
    )


def unknown(
    summary: str,
    details: Details | None = None,
    name_suffix: str | None = None,
    metrics: list[Metric] | None = None,
    alternative_hostname: HostnameInput | None = None,
) -> CheckResult:
    """
    Generates a CheckResult object indicating an UNKNOWN check state.

    This function creates and returns a CheckResult indicating the system or
    component is in an UNKNOWN state. It allows for passing additional
    information such as a summary, details, name suffix, metrics, or an
    alternative hostname.

    Parameters:
        summary:
            A summary of the check result, indicating the outcome of the check.
            Checkmk will show this summary on pages like a host overview.
        details:
            A detailed output of the check result, providing additional
            information beyond the summary. Checkmk will show this detailed
            output when you are viewing a specific service.
        name_suffix:
            A suffix to add to the check name as defined in the `@check`
            decorator. This enables a single check function to return multiple
            results that create multiple services on the Checkmk side.
        metrics:
            An optional list of metrics to associate with the check.
        alternative_hostname:
            An optional hostname that overrides the hostname that would have
            been used otherwise.

    Returns:
        A `CheckResult` object representing the UNKNOWN check result.
    """

    return CheckResult(
        check_state=CheckState.UNKNOWN,
        summary=summary,
        details=details,
        name_suffix=name_suffix,
        metrics=metrics,
        hostname=alternative_hostname,
    )


class OngoingCheckResult:
    """
    An "ongoing check result" represents a builder that allows you to build up a
    new check result by adding multiple OK/WARN/UNKNOWN/CRIT results, called
    "partials", which will eventually result in a regular check result that
    holds the worst check state provided by the individual results.

    Use of this builder greatly simplifies scenarios where your check function
    validates multiple aspects of a single system or component but only returns
    a single check result: by allowing you to provide the status of each aspect
    as you check it through simple function calls on the builder instead of
    having to manually combine the results into a single check result, you can
    reduce the complexity of your check function and improve its readability.

    Constructing this builder is recommended through the top-level
    `build_result` available in this module.
    """

    @dataclass
    class Partial:
        """
        The internal type representing a partial result of a check.
        """

        check_state: CheckState
        summary: str
        details: Details | None

        def __str__(self) -> str:
            if self.details:
                return f"{self.summary} {self.check_state.state_marker}:\n{normalize_details(self.details)}\n"
            return f"{self.summary}\n"

    def __init__(
        self,
        ok_summary: str,
        fail_summary: str,
        base_details: Details | None = None,
        name_suffix: str | None = None,
        metrics: list[Metric] | None = None,
        alternative_hostname: HostnameInput | None = None,
    ):
        """
        NOTE: please prefer using the `build_result` function available in this
        module instead of calling this constructor directly.
        """

        self.ok_summary = ok_summary
        self.fail_summary = fail_summary
        self.base_details = base_details
        self.name_suffix = name_suffix
        self.metrics = metrics
        self.alternative_hostname = alternative_hostname
        self.results: list[OngoingCheckResult.Partial] = []

    @property
    def check_state(self) -> CheckState:
        """
        The overall check state of the builder, calculated based on the worst
        check state of the individual results.
        """

        if not self.results:
            return CheckState.OK

        # CRIT is a special case in that it is the worst state, even though numerically
        # UNKNOWN is higher.
        if any(result.check_state == CheckState.CRIT for result in self.results):
            return CheckState.CRIT

        return max(result.check_state for result in self.results)

    def add_check_result(self, check_result: CheckResult) -> None:
        """
        Add a check result to the builder as a partial result.

        For most use cases you probably want to use the higher-level `ok`,
        `warn`, `crit`, and `unknown` functions instead.

        Parameters:
            check_result:
                The check result to add as a partial result.
        """

        self.results.append(
            OngoingCheckResult.Partial(
                check_state=check_result.check_state,
                summary=check_result.summary,
                details=check_result.details,
            )
        )

    def ok(self, summary: str, details: Details | None = None) -> None:
        """
        Add an OK result to the builder as a partial result.

        Parameters:
            summary:
                A summary of the check result, indicating the outcome of the
                check. Checkmk will show this summary on pages like a host
                overview.
            details:
                A detailed output of the check result, providing additional
                information beyond the summary. Checkmk will show this detailed
                output when you are viewing a specific service.
        """

        self.results.append(OngoingCheckResult.Partial(CheckState.OK, summary, details))

    def warn(self, summary: str, details: Details | None = None) -> None:
        """
        Add a WARN result to the builder as a partial result.

        Parameters:
            summary:
                A summary of the check result, indicating the outcome of the
                check. Checkmk will show this summary on pages like a host
                overview.
            details:
                A detailed output of the check result, providing additional
                information beyond the summary. Checkmk will show this detailed
                output when you are viewing a specific service.
        """

        self.results.append(
            OngoingCheckResult.Partial(CheckState.WARN, summary, details)
        )

    def crit(self, summary: str, details: Details | None = None) -> None:
        """
        Add a CRIT result to the builder as a partial result.

        Parameters:
            summary:
                A summary of the check result, indicating the outcome of the
                check. Checkmk will show this summary on pages like a host
                overview.
            details:
                A detailed output of the check result, providing additional
                information beyond the summary. Checkmk will show this detailed
                output when you are viewing a specific service.
        """

        self.results.append(
            OngoingCheckResult.Partial(CheckState.CRIT, summary, details)
        )

    def unknown(self, summary: str, details: Details | None = None) -> None:
        """
        Add an UNKNOWN result to the builder as a partial result.

        Parameters:
            summary:
                A summary of the check result, indicating the outcome of the
                check. Checkmk will show this summary on pages like a host
                overview.
            details:
                A detailed output of the check result, providing additional
                information beyond the summary. Checkmk will show this detailed
                output when you are viewing a specific service.
        """

        self.results.append(
            OngoingCheckResult.Partial(CheckState.UNKNOWN, summary, details)
        )

    def to_check_result(self) -> CheckResult:
        """
        Finalize this builder by turning it into a check result.

        The created result holds the cumulative data of all partial results,
        with the worst check state as determined by the check state of the
        individual results.
        """

        maximum_state = self.check_state

        details = None
        # If any details were provided, we create a compound string of them.
        # Otherwise, we'll keep the details None.
        if self.base_details is not None or len(self.results) > 0:
            details = normalize_details(self.base_details) if self.base_details else ""
            if self.results:
                result_details = "\n".join(str(result) for result in self.results)
                details = f"{details}\n\n{result_details}".strip()

        return maximum_state.check_function(
            summary=(
                self.ok_summary if maximum_state == CheckState.OK else self.fail_summary
            ),
            details=details,
            name_suffix=self.name_suffix,
            metrics=self.metrics,
            alternative_hostname=self.alternative_hostname,
        )


def build_result(
    ok_summary: str,
    fail_summary: str,
    base_details: Details | None = None,
    name_suffix: str | None = None,
    metrics: list[Metric] | None = None,
    alternative_hostname: HostnameInput | None = None,
) -> OngoingCheckResult:
    """
    Start building up a new check result that allows adding multiple
    OK/WARN/UNKNOWN/CRIT results, called "partials", eventually resulting in a
    regular check result that holds the worst check state provided by the
    individual results.

    Use of this builder greatly simplifies scenarios where your check function
    validates multiple aspects of a single system or component but only returns
    a single check result: by allowing you to provide the status of each aspect
    as you check it through simple function calls on the builder instead of
    having to manually combine the results into a single check result, you can
    reduce the complexity of your check function and improve its readability.

    Parameters:
        ok_summary:
            Overall check summary that will be shown if the final result is OK.
        fail_summary:
            Overall check summary that will be shown if the final result is
            WARN, UNKNOWN, or CRIT.
        base_details:
            Optional base details for the check result. They will always be
            included and then enriched by the details provided by partial results.
        name_suffix:
            Optional suffix for the check result name.
        metrics:
            Optional list of metrics associated with the check.
        alternative_hostname:
            Optional alternative hostname input for the check.

    Returns:
        An instance of OngoingCheckResult initialized with the provided
        parameters and ready to receive partial results.
    """

    return OngoingCheckResult(
        ok_summary=ok_summary,
        fail_summary=fail_summary,
        base_details=base_details,
        name_suffix=name_suffix,
        metrics=metrics,
        alternative_hostname=alternative_hostname,
    )


def normalize_check_function_result(
    check_function_result: CheckFunctionResult,
    stdout: io.StringIO,
    stderr: io.StringIO,
) -> list[CheckResult]:
    maybe_ongoing_check_results: list[CheckResult | OngoingCheckResult]
    if isinstance(check_function_result, GeneratorType):
        maybe_ongoing_check_results = list(check_function_result)
    elif isinstance(check_function_result, list):
        maybe_ongoing_check_results = check_function_result
    else:
        maybe_ongoing_check_results = [
            cast(
                CheckResult | OngoingCheckResult,
                check_function_result,
            )
        ]

    check_results: list[CheckResult] = [
        (result.to_check_result() if isinstance(result, OngoingCheckResult) else result)
        for result in maybe_ongoing_check_results
        if result is not None
    ]

    stdfd_details = ""
    if stdout.tell() > 0:
        stdfd_details += f"\n\n<STDOUT>\n{stdout.getvalue()}\n</STDOUT>"
    if stderr.tell() > 0:
        stdfd_details += f"\n\n<STDERR>\n{stderr.getvalue()}\n</STDERR>"
    stdfd_details = stdfd_details.strip()

    if stdfd_details:
        for check_result in check_results:
            if check_result.details is None:
                check_result.details = ""
            check_result.details += stdfd_details

    if len(check_results) == 0:
        return [
            CheckResult(
                check_state=CheckState.UNKNOWN,
                summary="CHECK CODE ERROR: no results returned",
                details=(
                    "The watchpost check function returned no results. Please verify "
                    "the code of this check if there is a circumstance in which it "
                    "does not return any results, and remediate this."
                ),
            )
        ]

    return check_results


@dataclass
class ExecutionResult:
    """
    This is an internal type that represents the final execution result of a
    check, containing everything required to turn it into Checkmk-compatible
    output.
    """

    piggyback_host: str
    service_name: str
    service_labels: dict[str, str]
    environment_name: str
    check_state: CheckState
    summary: str
    details: str | None = None
    metrics: list[Metric] | None = None
    check_definition: InvocationInformation | None = None

    def generate_checkmk_output(self) -> Generator[bytes]:
        yield b"<<<<"
        yield self.piggyback_host.encode("utf-8")
        yield b">>>>\n"
        yield b"<<<watchpost>>>\n"

        result: dict[str, Any] = {
            "service_name": self.service_name,
            "service_labels": self.service_labels,
            "environment": self.environment_name,
            "check_state": self.check_state.name,
            "summary": self.summary,
            "metrics": [
                metric.to_json_compatible_dict()
                for metric in (self.metrics if self.metrics else [])
            ],
        }

        details = self.details
        if self.check_definition:
            result["check_definition"] = {
                "relative_path": self.check_definition.relative_path,
                "line_number": self.check_definition.line_number,
            }
        result["details"] = details

        yield base64.b64encode(json.dumps(result).encode("utf-8"))
        yield b"\n"
        yield b"<<<<>>>>\n"
