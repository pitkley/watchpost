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
Execution environments and registry.

This module defines `Environment`, representing where a check runs or targets,
and `EnvironmentRegistry`, a simple container to manage named environments.

Notes:
    Environments can carry hostname configuration used during result output.
"""

from collections.abc import Hashable

from .hostname import HostnameInput, to_strategy


class Environment:
    """
    Represents a logical environment in which checks run.

    An environment typically maps to how or where checks are executed (for
    example: dev, stage, prod, monitoring). It can carry a hostname resolution
    strategy and arbitrary metadata that checks and data sources may use to
    alter behavior.
    """

    def __init__(
        self,
        name: str,
        *,
        hostname: HostnameInput | None = None,
        **metadata: Hashable,
    ):
        """
        Create a new environment definition.

        Parameters:
            name:
                The name of the environment (e.g., "dev", "stage", "prod",
                "monitoring").
            hostname:
                Input used to determine the hostname when running checks in this
                environment. Accepts the same forms as `HostnameInput` such as a
                template string, a callable, or a strategy instance. If omitted,
                the application may fall back to its default hostname
                generation.
            metadata:
                Arbitrary, hashable metadata attached to the environment. Checks
                and data sources may consult these values to change behavior.

        Notes:
            Hostname handling integrates with the hostname resolution system in
            `watchpost.hostname`. The chosen strategy ultimately influences the
            Checkmk host a service is associated with.
        """
        self.name = name
        self.hostname_strategy = to_strategy(hostname)
        self.metadata = metadata

    def __eq__(self, other: object) -> bool:
        """
        Compare environments by name, hostname strategy, and metadata.

        Returns:
            True if all identifying attributes are equal; otherwise False.
        """
        if isinstance(other, Environment):
            return (
                self.name == other.name
                and self.hostname_strategy == other.hostname_strategy
                and self.metadata == other.metadata
            )
        return False

    def __hash__(self) -> int:
        """
        Compute a stable hash based on name, hostname strategy, and metadata.

        Returns:
            An integer suitable for using `Environment` instances as dict keys
            or set members.
        """
        return hash(
            (
                self.name,
                self.hostname_strategy,
                frozenset(self.metadata.items()),
            )
        )


class EnvironmentRegistry:
    """
    Simple in-memory registry of `Environment` objects.

    The registry provides dictionary-like access and helpers to create and
    manage environments used by a Watchpost application.
    """

    def __init__(self) -> None:
        """
        Initialize an empty registry.
        """
        self._environments: dict[str, Environment] = {}

    def __getitem__(self, name: str) -> Environment:
        """
        Return the environment registered under the given name.

        Parameters:
            name:
                The name of the environment to look up.

        Returns:
            The matching `Environment` instance.

        Raises:
            KeyError:
                If no environment with the given name exists.
        """
        return self._environments[name]

    def __contains__(self, name: str) -> bool:
        """
        Indicate whether an environment with the given name is registered.

        Parameters:
            name:
                The environment name to check.

        Returns:
            True if the name is present; otherwise False.
        """
        return name in self._environments

    def __iter__(self):  # type: ignore[no-untyped-def]
        """
        Iterate over registered environments in insertion order.

        Returns:
            An iterator yielding `Environment` instances.
        """
        return iter(self._environments.values())

    def __len__(self) -> int:
        """
        Return the number of registered environments.
        """
        return len(self._environments)

    def get(self, name: str, default: Environment | None = None) -> Environment | None:
        """
        Return the environment for the given name, or a default if missing.

        Parameters:
            name:
                The environment name to look up.
            default:
                Value to return when the name is not present.

        Returns:
            The matching `Environment`, or the provided default.
        """
        return self._environments.get(name, default)

    def new(
        self,
        name: str,
        *,
        hostname: HostnameInput | None = None,
        **metadata: Hashable,
    ) -> Environment:
        """
        Create a new environment, add it to the registry, and return it.

        Parameters:
            name:
                The name of the environment to create.
            hostname:
                Input used to determine the hostname for this environment. See
                `Environment.__init__` for supported forms.
            metadata:
                Arbitrary, hashable metadata to attach to the environment.

        Returns:
            The created and registered `Environment` instance.
        """
        environment = Environment(
            name,
            hostname=hostname,
            **metadata,
        )
        self.add(environment)
        return environment

    def add(self, environment: Environment) -> None:
        """
        Add or replace an environment by its name.

        Parameters:
            environment:
                The environment to register. If another environment with the
                same name exists, it will be overwritten.
        """
        self._environments[environment.name] = environment
