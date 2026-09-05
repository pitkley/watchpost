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

import pytest

from watchpost import Environment, Watchpost, ok
from watchpost.check import Check
from watchpost.hostname import (
    CoercingStrategy,
    HostnameContext,
    HostnameResolutionError,
    NoPiggybackHostStrategy,
    TemplateStrategy,
    coerce_to_rfc1123,
    is_rfc1123_hostname,
    resolve_hostname,
    to_strategy,
)


def resolve(inputs):
    env = Environment("prod", hostname=inputs[3])
    app = Watchpost(checks=[], execution_environment=env, hostname=inputs[4])
    definition = Check(
        lambda: ok("result"),
        "service",
        {},
        [env],
        None,
        hostname_strategy=to_strategy(inputs[2]),
    )
    return resolve_hostname(
        watchpost=app,
        check=definition,
        environment=env,
        result=ok("result", alternative_hostname=inputs[1]),
        explicit_hostname=inputs[0],
    )


@pytest.mark.parametrize("winner", range(6))
def test_hostname_precedence_falls_through_all_levels(winner):
    inputs = [lambda _: None for _ in range(5)]
    for index in range(winner, 5):
        inputs[index] = f"level-{index}"
    assert resolve(inputs) == (f"level-{winner}" if winner < 5 else "service-prod")


@pytest.mark.parametrize("winner", range(5))
def test_no_piggyback_sentinel_stops_resolution(winner):
    inputs = [lambda _: None for _ in range(5)]
    inputs[winner] = NoPiggybackHostStrategy()
    for index in range(winner + 1, 5):
        inputs[index] = "later-host"
    assert resolve(inputs) == ""


def test_hostname_templates_preserve_nested_context_objects():
    env = Environment("prod")
    definition = Check(lambda: ok("result"), "service", {}, [env], None)
    ctx = HostnameContext.new(check=definition, environment=env, result=ok("healthy"))
    assert (
        TemplateStrategy(
            "{check.service_name}-{environment.name}-{result.summary}"
        ).resolve(ctx)
        == "service-prod-healthy"
    )


@pytest.mark.parametrize(
    "raw", ["a" * 62 + "-b", ".".join(["a" * 62 + "-b"] * 10), "a" * 254]
)
def test_coercion_remains_valid_at_length_boundaries(raw):
    hostname = coerce_to_rfc1123(raw)
    assert is_rfc1123_hostname(hostname)
    assert all(not label.endswith("-") for label in hostname.split("."))


def test_strategy_errors_identify_the_resolution_level():
    def broken(_):
        raise ValueError("broken")

    with pytest.raises(HostnameResolutionError, match="explicit level"):
        resolve([broken, None, None, None, None])


def test_coercion_wrapper_preserves_no_piggyback():
    assert (
        resolve(
            [
                CoercingStrategy(NoPiggybackHostStrategy()),
                None,
                "later-host",
                None,
                None,
            ]
        )
        == ""
    )
