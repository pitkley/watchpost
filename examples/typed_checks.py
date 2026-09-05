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

"""Supported consumer signatures, checked by CI's mypy and ty example checks."""

from collections.abc import Generator

from watchpost import CheckResult, Datasource, Environment, Watchpost, check, ok

TARGET = Environment("example")


class FirstSource(Datasource):
    scheduling_strategies = ()
    value = "first"


class SecondSource(Datasource):
    scheduling_strategies = ()
    value = "second"


@check(name="zero arguments", service_labels={}, environments=[TARGET], cache_for=None)
def zero_arguments() -> CheckResult:
    return ok("zero")


@check(name="environment", service_labels={}, environments=[TARGET], cache_for=None)
def environment_only(environment: Environment) -> CheckResult:
    return ok(environment.name)


@check(name="sources", service_labels={}, environments=[TARGET], cache_for=None)
def mixed_sources(first: FirstSource, *, second: SecondSource) -> CheckResult:
    return ok(first.value + second.value)


@check(name="async", service_labels={}, environments=[TARGET], cache_for=None)
async def async_environment(environment: Environment) -> CheckResult:
    return ok(environment.name)


@check(name="generated", service_labels={}, environments=[TARGET], cache_for=None)
def generated() -> Generator[CheckResult]:
    yield ok("generated")


@check(name="list", service_labels={}, environments=[TARGET], cache_for=None)
def multiple() -> list[CheckResult]:
    return [ok("list")]


app = Watchpost(
    checks=[
        zero_arguments,
        environment_only,
        mixed_sources,
        async_environment,
        generated,
        multiple,
    ],
    execution_environment=TARGET,
)
app.register_datasource(FirstSource)
app.register_datasource(SecondSource)
