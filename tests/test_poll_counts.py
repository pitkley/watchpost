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

from typing import override

from watchpost import CheckResult, Environment, Watchpost, check, ok
from watchpost.check import Check
from watchpost.executor import BlockingCheckExecutor
from watchpost.scheduling_strategy import SchedulingDecision, SchedulingStrategy

from .utils import decode_checkmk_output


class Decisions(SchedulingStrategy):
    def __init__(self, decisions: dict[str, SchedulingDecision]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    @override
    def schedule(
        self,
        check: Check,
        current_execution_environment: Environment,
        target_environment: Environment,
    ) -> SchedulingDecision:
        self.calls.append(target_environment.name)
        return self.decisions[target_environment.name]


def test_count_tracks_actual_targets_cache_hits_and_skips() -> None:
    monitoring, production, staging = [
        Environment(name) for name in ("monitoring", "production", "staging")
    ]
    strategy = Decisions(
        {
            "monitoring": SchedulingDecision.DONT_SCHEDULE,
            "production": SchedulingDecision.SCHEDULE,
            "staging": SchedulingDecision.SKIP,
        }
    )
    executions: list[str] = []

    @check(
        name="probe",
        environments=[monitoring, production, staging],
        service_labels={},
        cache_for="1h",
        scheduling_strategies=[strategy],
    )
    def probe(environment: Environment) -> list[CheckResult]:
        executions.append(environment.name)
        return [ok("first", name_suffix="first"), ok("second", name_suffix="second")]

    executor = BlockingCheckExecutor()
    app = Watchpost(checks=[probe], execution_environment=monitoring, executor=executor)
    try:
        app.verify_check_scheduling()
        strategy.calls.clear()
        for _poll in range(2):
            output = decode_checkmk_output(b"".join(app.run_checks()))
            summary = output[-1]
            assert summary["summary"] == "1 check/environment pairs eligible to run"
            assert (
                summary["details"]
                == f"Eligible check/environment pairs:\n- {probe.name} [production]"
            )
        assert strategy.calls == ["monitoring", "production", "staging"] * 2
        assert executions == ["production"]
        strategy.decisions["production"] = SchedulingDecision.SKIP
        output = decode_checkmk_output(b"".join(app.run_checks()))
        assert output[-1]["summary"] == "0 check/environment pairs eligible to run"
        assert (
            len(
                [
                    result
                    for result in output
                    if result["summary"] in {"first", "second"}
                ]
            )
            == 2
        )
    finally:
        executor.shutdown()


def test_interleaved_polls_count_independently_without_reevaluation() -> None:
    targets = [Environment("first"), Environment("second")]
    schedule = SchedulingDecision.SCHEDULE
    skip = SchedulingDecision.SKIP
    decisions = [schedule, skip, skip, schedule]

    class Alternating(SchedulingStrategy):
        @override
        def schedule(
            self,
            check: Check,
            current_execution_environment: Environment,
            target_environment: Environment,
        ) -> SchedulingDecision:
            return decisions.pop(0)

    @check(
        name="probe",
        environments=targets,
        service_labels={},
        cache_for=None,
        scheduling_strategies=[Alternating()],
    )
    def probe() -> CheckResult:
        return ok("done")

    executor = BlockingCheckExecutor()
    app = Watchpost(checks=[probe], execution_environment=targets[0], executor=executor)
    try:
        app.verify_check_scheduling()
        decisions[:] = [schedule, skip, skip, schedule]
        first = app.run_checks(act_as_agent=False)
        initial_chunk = next(first)
        second_output = decode_checkmk_output(
            b"".join(app.run_checks(act_as_agent=False))
        )
        first_output = decode_checkmk_output(initial_chunk + b"".join(first))
        assert (
            second_output[-1]["summary"] == "0 check/environment pairs eligible to run"
        )
        assert (
            first_output[-1]["summary"] == "2 check/environment pairs eligible to run"
        )
        assert decisions == []
    finally:
        executor.shutdown()
