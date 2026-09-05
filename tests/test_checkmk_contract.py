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

"""Fast wire-contract tests; the Docker smoke test uses the real Checkmk API.

The lightweight API doubles here keep the Python 3.13 library suite independent
of Checkmk's bundled Python and installation layout.
"""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from watchpost import CheckResult, Environment, Watchpost, check, ok, warn
from watchpost.executor import BlockingCheckExecutor
from watchpost.hostname import NoPiggybackHostStrategy
from watchpost.result import Boundaries, CheckState, ExecutionResult, Metric, Thresholds


@dataclass
class ApiResult:
    state: CheckState
    summary: str
    details: str | None = None


@dataclass
class ApiMetric:
    name: str
    value: float
    levels: tuple | None = None
    boundaries: tuple | None = None


@dataclass
class ApiLabel:
    key: str
    value: str


@dataclass
class ApiService:
    item: str
    labels: list


@pytest.fixture
def plugin(monkeypatch):
    for name in (
        "cmk",
        "cmk.agent_based",
        "cmk.agent_based.v2",
        "cmk.utils",
        "cmk.utils.log",
    ):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    api = sys.modules["cmk.agent_based.v2"]
    for name, value in dict(
        AgentSection=SimpleNamespace,
        CheckPlugin=SimpleNamespace,
        CheckResult=list,
        IgnoreResultsError=LookupError,
        Metric=ApiMetric,
        Result=ApiResult,
        Service=ApiService,
        ServiceLabel=ApiLabel,
        State=CheckState,
        StringTable=list,
    ).items():
        setattr(api, name, value)
    setattr(
        sys.modules["cmk.utils.log"],
        "console",
        SimpleNamespace(error=lambda _message: None),
    )
    path = (
        Path(__file__).resolve().parents[1]
        / "checkmk-integration/watchpost-plugin/agent_based/watchpost.py"
    )
    spec = importlib.util.spec_from_file_location("watchpost_contract_plugin", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def sections(plugin, results):
    output = b"".join(
        chunk for result in results for chunk in result.generate_checkmk_output()
    )
    return sections_from_output(plugin, output)


def sections_from_output(plugin, output):
    hosts = {}
    hostname = ""
    in_watchpost = False
    for line in output.decode().splitlines():
        if line.startswith("<<<<") and line.endswith(">>>>"):
            hostname = line[4:-4]
            in_watchpost = False
        elif line.startswith("<<<"):
            assert line == "<<<watchpost>>>"
            in_watchpost = True
        elif line:
            assert in_watchpost
            hosts.setdefault(hostname, []).append([line])
    assert hostname == "", "Unclosed piggyback section"
    return {host: plugin.parse_function(rows) for host, rows in hosts.items()}


def result(state=CheckState.OK, host="host", name="service", summary="healthy"):
    return ExecutionResult(
        piggyback_host=host,
        service_name=name,
        service_labels={"owner": "operations"},
        environment_name="prod",
        check_state=state,
        summary=summary,
        details="déjà vu\n診断",
        metrics=[
            Metric("requests/count", 42, Thresholds(50, 75), Boundaries(None, 100))
        ],
    )


@pytest.mark.parametrize("state", list(CheckState))
def test_generated_output_round_trips_through_plugin(plugin, state):
    section = sections(plugin, [result(state, summary=" first\nsecond ✓ ")])["host"]
    services = list(plugin.discovery_function(section))
    assert services == [ApiService("service", [ApiLabel("owner", "operations")])]
    outputs = list(plugin.check_function("service", section))
    assert outputs == [
        ApiMetric("requests_count", 42, (50, 75), (None, 100)),
        ApiResult(state, "first second ✓", "déjà vu\n診断"),
    ]


def test_duplicate_services_report_configuration_error_without_hiding_failure(plugin):
    section = sections(plugin, [result(), result(CheckState.CRIT, summary="outage")])[
        "host"
    ]
    assert list(plugin.discovery_function(section)) == [ApiService("service", [])]
    outputs = list(plugin.check_function("service", section))
    assert len(outputs) == 1
    assert outputs[0].state == CheckState.UNKNOWN
    assert outputs[0].summary == "Duplicate Watchpost service: service"
    assert "CRIT: outage" in outputs[0].details


def test_same_service_on_different_hosts_is_independent(plugin):
    hosts = sections(
        plugin, [result(host="first"), result(CheckState.CRIT, host="second")]
    )
    assert (
        list(plugin.check_function("service", hosts["first"]))[-1].state
        == CheckState.OK
    )
    assert (
        list(plugin.check_function("service", hosts["second"]))[-1].state
        == CheckState.CRIT
    )


@pytest.mark.parametrize("source_first", [False, True])
def test_source_host_checks_and_synthetic_output_stay_outside_piggyback_hosts(
    plugin, source_first
):
    environment = Environment("production")

    @check(
        name="service",
        environments=[environment],
        service_labels={},
        cache_for=None,
        hostname=NoPiggybackHostStrategy(),
    )
    def source_check() -> CheckResult:
        return warn("source-host result")

    @check(
        name="service",
        environments=[environment],
        service_labels={},
        cache_for=None,
        hostname="target-host",
    )
    def target_check() -> CheckResult:
        return ok("piggyback result")

    checks = (
        [source_check, target_check] if source_first else [target_check, source_check]
    )
    with BlockingCheckExecutor() as executor:
        app = Watchpost(
            checks=checks, execution_environment=environment, executor=executor
        )
        try:
            hosts = sections_from_output(
                plugin, b"".join(app.run_checks(act_as_agent=False))
            )
        finally:
            app.shutdown()

    assert set(hosts) == {"", "target-host"}
    assert list(plugin.discovery_function(hosts[""])) == [
        ApiService("service", []),
        ApiService("Watchpost: executed checks", []),
    ]
    assert list(plugin.check_function("service", hosts[""])) == [
        ApiResult(CheckState.WARN, "source-host result")
    ]
    assert list(plugin.discovery_function(hosts["target-host"])) == [
        ApiService("service", [])
    ]
    assert list(plugin.check_function("service", hosts["target-host"])) == [
        ApiResult(CheckState.OK, "piggyback result")
    ]
    synthetic = list(plugin.check_function("Watchpost: executed checks", hosts[""]))
    assert len(synthetic) == 1
    assert synthetic[0].state == CheckState.OK


def test_missing_service_and_optional_metrics(plugin):
    generated = result()
    generated.metrics = None
    section = sections(plugin, [generated])["host"]
    assert list(plugin.check_function("service", section)) == [
        ApiResult(CheckState.OK, "healthy", "déjà vu\n診断")
    ]
    with pytest.raises(LookupError, match="not found"):
        list(plugin.check_function("absent", section))
