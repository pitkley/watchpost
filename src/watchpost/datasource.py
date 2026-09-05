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
Datasource primitives and factory helpers.

This module defines the `Datasource` base class, the `DatasourceFactory`
protocol for constructing datasources, a `FromFactory` marker to declare
factory-created dependencies in check parameters, and a `DatasourceUnavailable`
exception to signal that a datasource is not available or otherwise unusable.

Notes:
    Datasources and factories can declare `scheduling_strategies` that Watchpost
    uses to decide where a check may run.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from types import EllipsisType
from typing import Any, ClassVar, Protocol, overload

from .scheduling_strategy import SchedulingStrategy


class DatasourceUnavailable(Exception):
    """
    Raised when a datasource cannot be created, accessed, or used.

    Watchpost raises this error when a check requests data, but the underlying
    system is not reachable, misconfigured, or otherwise unavailable.
    """


class Datasource(ABC):
    """
    Base class for datasources that provide input to checks.

    Subclass this to integrate systems your checks read from (APIs, files,
    services, etc.). Datasources can influence where and when checks are run by
    exposing scheduling strategies.
    """

    scheduling_strategies: tuple[SchedulingStrategy, ...] | EllipsisType | None = ...
    """
    Optional scheduling strategies that constrain where a check may run.

    - Ellipsis (...) means "unspecified". If a datasource created by a factory
      leaves this as Ellipsis, Watchpost will fall back to the factory's
      own `scheduling_strategies` if any.
    - A tuple provides explicit strategies for this datasource and takes
      precedence over any factory strategies.
    - None or an empty tuple means "no constraints".
    """


class DatasourceFactory(Protocol):
    """
    Protocol for factories that construct datasources for checks.

    A factory centralizes configuration and creation logic for a datasource.
    Watchpost can use factory-level `scheduling_strategies` when the created
    datasource does not define its own.
    """

    new: ClassVar[Callable[..., Datasource]]
    """
    Function that creates and returns a datasource instance.

    Watchpost forwards the positional and keyword arguments declared
    via `FromFactory(..., *args, **kwargs)` to this callable.
    """

    scheduling_strategies: tuple[SchedulingStrategy, ...] | EllipsisType | None = ...
    """
    Optional scheduling strategies applied to datasources created by this
    factory.

    A tuple sets explicit constraints; None or an empty tuple means
    "no constraints".
    """


FactoryCacheKey = tuple[
    type[DatasourceFactory] | None, tuple[Any, ...], frozenset[tuple[str, Any]]
]


class FromFactory:
    """
    Marker used with `typing.Annotated` to request a datasource from a factory.

    Use in a check parameter annotation as:

        Annotated[MyDatasource, FromFactory(MyFactory, "service")]

    You may omit the factory type when the parameter type implements the
    factory protocol:

        Annotated[MyDatasourceWithFactory, FromFactory("service")]

    Parameters are forwarded to the factory's `new` callable. Watchpost caches
    the constructed datasource per factory type and ordered argument values.
    Arguments must be hashable; keyword ordering does not affect identity.
    """

    @overload
    def __init__(
        self,
        factory: type[DatasourceFactory],
        *args: Any,
        **kwargs: Any,
    ): ...

    @overload
    def __init__(
        self,
        *args: Any,
        **kwargs: Any,
    ): ...

    def __init__(
        self,
        factory: type[DatasourceFactory] | Any = None,
        *args: Any,
        **kwargs: Any,
    ):
        """
        Initialize a `FromFactory` marker.

        Parameters:
            factory:
                Optional factory type used to create the datasource. If omitted,
                Watchpost infers the factory from the annotated parameter's
                type. If the first positional argument is not a type, it is
                treated as a factory argument and the factory is inferred.
            args:
                Positional arguments forwarded to the factory `new` callable.
            kwargs:
                Keyword arguments forwarded to the factory `new` callable.
        """
        self.factory_type: type[DatasourceFactory] | None
        if factory is None or isinstance(factory, type):
            self.factory_type = factory
            self.args = args
        else:
            self.factory_type = None
            self.args = (factory, *args)

        self.kwargs = kwargs

    def cache_key(
        self,
        type_key: type[DatasourceFactory] | None,
    ) -> FactoryCacheKey:
        """
        Generate a stable cache key for this factory invocation.

        Parameters:
            type_key:
                Fallback factory type to use when this instance did not specify
                a factory explicitly (i.e., it will be inferred from the
                annotated parameter type).

        Returns:
            A tuple that identifies the factory type and the provided arguments,
            suitable for use as a cache key.
        """
        try:
            key = (
                self.factory_type or type_key,
                self.args,
                frozenset(self.kwargs.items()),
            )
            hash(key)
        except TypeError as error:
            raise ValueError("Datasource factory arguments must be hashable") from error
        return key
