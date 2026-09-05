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

from watchpost import CheckResult, Datasource, Environment, Watchpost, check
from watchpost.scheduling_strategy import (
    InvalidCheckConfiguration,
    MustRunAgainstGivenTargetEnvironmentStrategy,
    MustRunInGivenExecutionEnvironmentStrategy,
    MustRunInTargetEnvironmentStrategy,
    SchedulingStrategy,
)

Monitoring = Environment("Monitoring")
Production = Environment("Production")


@pytest.mark.parametrize("explicit_targets", [False, True])
@pytest.mark.parametrize("execution", [Monitoring, Production, None])
def test_current_equals_target_uses_declared_environments(
    explicit_targets: bool,
    execution: Environment | None,
) -> None:
    strategies: list[SchedulingStrategy] = [MustRunInTargetEnvironmentStrategy()]
    if execution is not None:
        strategies.append(MustRunInGivenExecutionEnvironmentStrategy(execution))
    if explicit_targets:
        strategies.append(
            MustRunAgainstGivenTargetEnvironmentStrategy(Monitoring, Production)
        )

    class Source(Datasource):
        scheduling_strategies = tuple(strategies)

    @check(
        name="production", environments=[Production], service_labels={}, cache_for=None
    )
    def production(_source: Source) -> CheckResult:
        raise AssertionError("validation must not execute a check")

    app = Watchpost(checks=[production], execution_environment=Monitoring)
    app.register_datasource(Source)
    try:
        if execution == Monitoring:
            with pytest.raises(ExceptionGroup) as error:
                app.verify_check_scheduling()
            assert isinstance(error.value, ExceptionGroup)
            cause = error.value.exceptions[0]
            assert isinstance(cause, InvalidCheckConfiguration)
            assert "Current=Target" in cause.reason
        else:
            app.verify_check_scheduling()
    finally:
        app.shutdown()
