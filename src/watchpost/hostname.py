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
Hostname resolution utilities and strategies.

Provides a small strategy protocol to compute the target hostname for a check
result, helpers to validate or coerce hostnames to RFC1123, and the
orchestration function `resolve_hostname` that applies precedence across result,
check, environment, and the Watchpost application.

Notes:
    Hostnames must follow RFC1123: ASCII letters, digits, and hyphens in
    dot-separated labels, each label 1-63 characters, total length up to 253
    characters. Labels must start and end with an alphanumeric character.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol, override

if TYPE_CHECKING:
    from .app import Watchpost
    from .check import Check
    from .environment import Environment
    from .result import CheckResult


@dataclass(frozen=True)
class HostnameContext:
    """
    Carries context available when resolving a hostname for a check execution.

    This object is passed to hostname strategies so they can decide on a
    hostname based on the check, environment, service information, and an
    optional result.
    """

    check: Check
    """
    The current check being executed.
    """

    environment: Environment
    """
    The current environment of the execution.
    """

    service_name: str
    """
    The service name as defined by the check (`@check`). Checkmk will use this
    name to identify the service.
    """

    service_labels: dict[str, Any]
    """
    Labels attached to the service. Useful for templating or custom strategies.
    """

    result: CheckResult | None = None
    """
    The optional result that the check returned. If present, it may include a
    hostname override on the result which takes precedence during resolution.
    """

    @classmethod
    def new(
        cls,
        *,
        check: Check,
        environment: Environment,
        result: CheckResult | None = None,
        service_name: str | None = None,
        service_labels: dict[str, Any] | None = None,
    ) -> HostnameContext:
        """
        Create a HostnameContext with sensible defaults from the given inputs.

        Parameters:
            check:
                The check for which a hostname is being resolved.
            environment:
                The environment in which the check runs.
            result:
                Optional result produced by the check. When given, used to
                support per-result hostname overrides.
            service_name:
                Optional service name. Defaults to the check's service name.
            service_labels:
                Optional service labels. Defaults to the check's service labels.

        Returns:
            A new HostnameContext initialized with the provided values and
            defaults.
        """
        return cls(
            check=check,
            environment=environment,
            result=result,
            service_name=service_name or check.service_name,
            service_labels=service_labels or check.service_labels,
        )


class _NoPiggybackHostType(Enum):
    NO_PIGGYBACK_HOST = object()


NO_PIGGYBACK_HOST = _NoPiggybackHostType.NO_PIGGYBACK_HOST
NoPiggybackHost = Literal[_NoPiggybackHostType.NO_PIGGYBACK_HOST]


class HostnameStrategy(Protocol):
    """
    Strategy protocol for resolving a hostname from a `HostnameContext`.

    Implementations return a hostname string or `None` if they cannot decide.
    Returning `None` allows composition where later strategies may provide a
    hostname.
    """

    def resolve(self, ctx: HostnameContext) -> str | NoPiggybackHost | None: ...


class StaticHostnameStrategy(HostnameStrategy):
    """
    Always returns the given hostname literal.

    Parameters:
        hostname:
            The hostname to return for any context.
    """

    def __init__(self, hostname: str):
        self.hostname = hostname

    @override
    def resolve(self, ctx: HostnameContext) -> str | NoPiggybackHost | None:
        return self.hostname


class FunctionStrategy(HostnameStrategy):
    """
    Calls a function with the `HostnameContext` to compute a hostname.

    Returning `None` indicates no decision and allows fallthrough to later
    strategies.

    Parameters:
        fn:
            A callable that receives a `HostnameContext` and returns a hostname
            or `None`.
    """

    def __init__(self, fn: Callable[[HostnameContext], str | NoPiggybackHost | None]):
        self.fn = fn

    @override
    def resolve(self, ctx: HostnameContext) -> str | NoPiggybackHost | None:
        return self.fn(ctx)


class TemplateStrategy(HostnameStrategy):
    """
    Formats a string template using fields from the `HostnameContext`.

    The template is processed with `str.format(**asdict(ctx))`. You can access
    context fields, including nested attributes on objects such as
    `{environment.name}` or `{check.service_name}`.

    Parameters:
        template:
            A format string for `str.format` using keys from the context.
    """

    def __init__(self, template: str):
        self.template = template

    @override
    def resolve(self, ctx: HostnameContext) -> str | NoPiggybackHost | None:
        return self.template.format(**asdict(ctx))


class CompositeStrategy(HostnameStrategy):
    """
    Tries multiple strategies in order and returns the first non-empty result.

    Parameters:
        strategies:
            One or more strategies to evaluate in order.
    """

    def __init__(self, *strategies: HostnameStrategy):
        self.strategies = strategies

    @override
    def resolve(self, ctx: HostnameContext) -> str | NoPiggybackHost | None:
        for s in self.strategies:
            val = s.resolve(ctx)
            if val:
                return val
        return None


class NoPiggybackHostStrategy(HostnameStrategy):
    """
    Explicitly disables the setting of a piggyback hostname. Whatever results a
    check has created will be associated with the host the Watchpost application
    ran on.

    Compared to simply providing an empty string, using this strategy will never
    result in either a fallback hostname being generated or a
    `HostnameResolutionError` to be raised.
    """

    @override
    def resolve(self, ctx: HostnameContext) -> str | NoPiggybackHost | None:
        return NO_PIGGYBACK_HOST


LABEL_MAX = 63
HOSTNAME_MAX = 253


def is_rfc1123_hostname(value: str) -> bool:
    """
    Determine whether the given value is a valid RFC1123 hostname.

    Parameters:
        value:
            The hostname candidate to validate.

    Returns:
        True if the value conforms to RFC1123; otherwise, False.

    Notes:
        Enforces ASCII letters, digits, and hyphens in dot-separated labels;
        each label is 1-63 characters and must start and end with an
        alphanumeric character. The full hostname must not exceed 253
        characters.
    """
    if not value or len(value) > HOSTNAME_MAX:
        return False

    # Only ASCII letters, digits, hyphens and dots overall
    for ch in value:
        o = ord(ch)
        if not (97 <= o <= 122 or 48 <= o <= 57 or ch in {".", "-"} or 65 <= o <= 90):
            return False

    # Check labels
    parts = value.split(".")
    for label in parts:
        if not (1 <= len(label) <= LABEL_MAX):
            return False
        # Must start/end with alnum
        if not (label[0].isalnum() and label[-1].isalnum()):
            return False
        # Only alnum or hyphen in label
        for ch in label:
            if not (ch.isalnum() or ch == "-"):
                return False
    return True


_invalid_char_re = re.compile(r"[^a-z0-9.-]+")  # after lowercasing
_multi_dot_re = re.compile(r"\.+")
_multi_dash_re = re.compile(r"-+")


def coerce_to_rfc1123(value: str | NoPiggybackHost) -> str:
    """
    Normalize and coerce an arbitrary string into an RFC1123-compatible
    hostname.

    The process lowercases the input, removes unsupported characters, replaces
    invalid characters with hyphens, normalizes repeated separators, trims
    leading/trailing hyphens from labels, and truncates labels and the overall
    hostname to RFC1123 limits. If the value cannot be coerced into a valid
    hostname, a `ValueError` is raised.

    Parameters:
        value:
            The input string to coerce into a hostname.

    Returns:
        A string that complies with RFC1123 hostname rules.

    Raises:
        ValueError:
            If the input is empty or cannot be transformed into a valid
            hostname.
    """
    if value is NO_PIGGYBACK_HOST:
        return ""
    if not value:
        raise ValueError("Cannot coerce empty hostname")

    # Normalize Unicode to ASCII (drop unsupported)
    norm = unicodedata.normalize("NFKD", value)
    s = norm.encode("ascii", "ignore").decode("ascii").lower()

    # Replace invalid chars, normalize repeated separators
    s = _invalid_char_re.sub("-", s)
    s = _multi_dot_re.sub(".", s)
    s = _multi_dash_re.sub("-", s)

    # Clean labels
    labels: list[str] = []
    for raw in s.split("."):
        label = raw.strip("-")  # cannot start/end with hyphen
        if not label:
            continue
        if len(label) > LABEL_MAX:
            label = label[:LABEL_MAX]
        labels.append(label)

    if not labels:
        raise ValueError(f"Cannot coerce hostname {value!r} to RFC1123")

    # Truncate overall length at label boundaries
    out: list[str] = []
    total = 0
    for i, label in enumerate(labels):
        extra = len(label) + (1 if i > 0 else 0)
        if total + extra > HOSTNAME_MAX:
            break
        out.append(label)
        total += extra

    if not out:
        raise ValueError(f"Cannot coerce hostname {value!r} to RFC1123")

    return ".".join(out)


class CoercingStrategy(HostnameStrategy):
    """
    Wraps another strategy and coerces its result to RFC1123 if present.

    If the inner strategy returns `None`, no coercion happens and `None` is
    returned. If a non-empty hostname is returned, it is passed through
    `coerce_to_rfc1123`.

    Parameters:
        inner:
            The inner strategy whose output should be coerced.
    """

    def __init__(self, inner: HostnameStrategy):
        self.inner = inner

    @override
    def resolve(self, ctx: HostnameContext) -> str | NoPiggybackHost | None:
        val = self.inner.resolve(ctx)
        return None if val is None else coerce_to_rfc1123(val)


HostnameInput = str | Callable[[HostnameContext], str | None] | HostnameStrategy


class HostnameResolutionError(Exception):
    """
    Raised when a hostname cannot be resolved or validated.

    This error wraps exceptions thrown by strategies and signals failure to
    produce a usable hostname for a check/environment combination.
    """


def to_strategy(value: HostnameInput | None) -> HostnameStrategy | None:
    """
    Convert user-facing hostname input into a `HostnameStrategy`.

    Accepts one of the following inputs:

    - A string interpreted as a template for `TemplateStrategy`.
    - A callable taking `HostnameContext` and returning a hostname or `None`.
    - An object implementing the `HostnameStrategy` protocol.

    Parameters:
        value:
            The value to convert into a strategy, or `None` to indicate no
            strategy.

    Returns:
        A `HostnameStrategy` instance or `None` if the input was `None`.

    Raises:
        TypeError:
            If the input type is not supported.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return TemplateStrategy(value)
    if hasattr(value, "resolve"):
        # Assume it matches the protocol
        return value
    if callable(value):
        return FunctionStrategy(value)

    raise TypeError(
        f"Unsupported hostname input type: {type(value)!r}. Expected str, callable, or HostnameStrategy."
    )


def resolve_hostname(
    *,
    watchpost: Watchpost,
    check: Check,
    environment: Environment,
    result: CheckResult | None,
    fallback_to_default_hostname_generation: bool = True,
    coerce_into_valid_hostname: bool = True,
) -> str:
    """
    Resolve the final hostname for a check execution.

    Precedence:

    1. Per-result override on the `CheckResult` (if present).
    2. Check-level strategy on the `Check`.
    3. Environment-level strategy on the `Environment`.
    4. Watchpost-level strategy on the application.
    5. Optional fallback to "{service_name}-{environment.name}" if enabled.

    The resolved hostname must conform to RFC1123. If it does not and
    `coerce_into_valid_hostname` is True, the hostname is coerced using
    `coerce_to_rfc1123`. Otherwise, a `HostnameResolutionError` is raised.

    Parameters:
        watchpost:
            The Watchpost application providing a default strategy.
        check:
            The check for which a hostname is being resolved.
        environment:
            The environment in which the check runs.
        result:
            Optional result that may contain a per-result hostname override.
        fallback_to_default_hostname_generation:
            Whether to fall back to "{service_name}-{environment.name}" when no
            strategy produced a hostname.
        coerce_into_valid_hostname:
            Whether to coerce a non-compliant hostname into RFC1123 format.

    Returns:
        The resolved hostname.

    Raises:
        HostnameResolutionError:
            If no hostname can be resolved, a strategy fails, or a non-compliant
            hostname cannot be coerced.
    """
    ctx = HostnameContext.new(
        check=check,
        environment=environment,
        result=result,
    )

    # Determine candidate in precedence order
    candidate: str | NoPiggybackHost | None = None

    # 1) Per-result override
    if result and result.hostname:
        if strategy := to_strategy(result.hostname):
            candidate = strategy.resolve(ctx)
    else:
        # 2) Check-level strategy
        if check.hostname_strategy:
            try:
                val = check.hostname_strategy.resolve(ctx)
                if val is NO_PIGGYBACK_HOST or (isinstance(val, str) and val):
                    candidate = val
            except Exception as e:
                raise HostnameResolutionError(
                    f"Hostname strategy failed at check level for {check.service_name}/{environment.name}: {e}"
                ) from e

        # 3) Environment-level strategy
        if candidate is None and environment.hostname_strategy:
            try:
                val = environment.hostname_strategy.resolve(ctx)
                if val is NO_PIGGYBACK_HOST or (isinstance(val, str) and val):
                    candidate = val
            except Exception as e:
                raise HostnameResolutionError(
                    f"Hostname strategy failed at environment level for {check.service_name}/{environment.name}: {e}"
                ) from e

        # 4) Watchpost-level strategy
        if candidate is None and watchpost.hostname_strategy:
            try:
                val = watchpost.hostname_strategy.resolve(ctx)
                if val is NO_PIGGYBACK_HOST or (isinstance(val, str) and val):
                    candidate = val
            except Exception as e:
                raise HostnameResolutionError(
                    f"Hostname strategy failed at watchpost level for {check.service_name}/{environment.name}: {e}"
                ) from e

    if candidate is NO_PIGGYBACK_HOST:
        # If it was explicitly requested to disable piggybacking, return an
        # empty string to associate the check output with the host Watchpost is
        # running on.
        return ""

    if candidate is None and fallback_to_default_hostname_generation:
        candidate = f"{check.service_name}-{environment.name}"

    if candidate is None:
        raise HostnameResolutionError(
            f"No hostname could be resolved for check={check.service_name} env={environment.name}"
        )

    if is_rfc1123_hostname(candidate):
        return candidate

    if not coerce_into_valid_hostname:
        raise HostnameResolutionError(
            f"Resolved hostname is not RFC1123-compatible: {candidate!r} for {check.service_name}/{environment.name}"
        )

    try:
        coerced_candidate = coerce_to_rfc1123(candidate)
        return coerced_candidate
    except ValueError as e:
        raise HostnameResolutionError(
            f"Cannot coerce hostname {candidate!r} to RFC1123: {e}"
        ) from e
