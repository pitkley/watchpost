#!/usr/bin/env python3
# Copyright 2026 Pit Kleyersburg
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

"""Generate with Watchpost's Python; verify with Checkmk's bundled Python."""

import argparse
import importlib
from pathlib import Path
from typing import Any, cast


def generate_fixture(path: Path) -> None:
    # The producer requires Python 3.13; do not import it in Checkmk's runtime.
    from watchpost.result import (
        Boundaries,
        CheckState,
        ExecutionResult,
        Metric,
        Thresholds,
    )

    results = [
        ExecutionResult(
            piggyback_host="contract-production",
            service_name="Contract OK",
            service_labels={"team": "platform", "environment": "production"},
            environment_name="production",
            check_state=CheckState.OK,
            summary="  Healthy\nwith detail  ",
            details="Details line one\nDetails line two",
            metrics=[
                Metric(
                    "request rate",
                    1.5,
                    levels=Thresholds(5, 10),
                    boundaries=Boundaries(0, None),
                ),
            ],
        ),
        ExecutionResult(
            piggyback_host="",
            service_name="Contract OK",
            service_labels={"scope": "source"},
            environment_name="source",
            check_state=CheckState.WARN,
            summary="Same service name on the source host",
        ),
        ExecutionResult(
            piggyback_host="contract-production",
            service_name="Contract WARN",
            service_labels={},
            environment_name="production",
            check_state=CheckState.WARN,
            summary="Warning: Grüß Gott",
        ),
        ExecutionResult(
            piggyback_host="contract-production",
            service_name="Contract CRIT",
            service_labels={},
            environment_name="production",
            check_state=CheckState.CRIT,
            summary="Failure",
            metrics=[Metric("errors", 2, boundaries=Boundaries(None, 100))],
        ),
        ExecutionResult(
            piggyback_host="contract-production",
            service_name="Contract UNKNOWN",
            service_labels={},
            environment_name="production",
            check_state=CheckState.UNKNOWN,
            summary="Waiting",
        ),
        ExecutionResult(
            piggyback_host="contract-staging",
            service_name="Contract OK",
            service_labels={"environment": "staging"},
            environment_name="staging",
            check_state=CheckState.CRIT,
            summary="Same service name on another host",
        ),
    ]
    for state in (CheckState.OK, CheckState.CRIT):
        results.append(
            ExecutionResult(
                piggyback_host="contract-production",
                service_name="Contract duplicate",
                service_labels={"conflicting": state.name},
                environment_name="production",
                check_state=state,
                summary=f"Duplicate {state.name}",
                metrics=[Metric("ambiguous", state.value)],
            )
        )

    path.write_bytes(
        b"".join(
            chunk for result in results for chunk in result.generate_checkmk_output()
        )
    )


def read_sections(path: Path) -> dict[str, list[list[str]]]:
    """Demultiplex framing; an empty hostname identifies source-host results."""
    sections: dict[str, list[list[str]]] = {}
    hostname = ""
    in_watchpost = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("<<<<") and line.endswith(">>>>"):
            hostname = line[4:-4]
            in_watchpost = False
        elif line.startswith("<<<"):
            assert line == "<<<watchpost>>>", line
            in_watchpost = True
        elif line:
            assert in_watchpost, line
            sections.setdefault(hostname, []).append([line])
    assert hostname == "", "Unclosed piggyback section"
    return sections


def check_fixture(path: Path) -> None:
    # Import the installed plugin, not the checkout, to verify Docker packaging.
    api = cast(Any, importlib.import_module("cmk.agent_based.v2"))
    plugin = cast(
        Any, importlib.import_module("cmk.plugins.watchpost.agent_based.watchpost")
    )
    assert isinstance(plugin.agent_section_watchpost, api.AgentSection)
    assert isinstance(plugin.check_plugin_watchpost, api.CheckPlugin)

    sections = read_sections(path)
    assert set(sections) == {"", "contract-production", "contract-staging"}
    production = plugin.parse_function(sections["contract-production"])
    services = list(plugin.discovery_function(production))
    assert all(isinstance(service, api.Service) for service in services)
    assert len(services) == 5, services
    by_name = {service.item: service for service in services}
    assert set(by_name) == {
        "Contract OK",
        "Contract WARN",
        "Contract CRIT",
        "Contract UNKNOWN",
        "Contract duplicate",
    }
    assert {label.name: label.value for label in by_name["Contract OK"].labels} == {
        "team": "platform",
        "environment": "production",
    }
    assert not by_name["Contract duplicate"].labels

    expected = {
        "Contract OK": (api.State.OK, "Healthy with detail"),
        "Contract WARN": (api.State.WARN, "Warning: Grüß Gott"),
        "Contract CRIT": (api.State.CRIT, "Failure"),
        "Contract UNKNOWN": (api.State.UNKNOWN, "Waiting"),
        "Contract duplicate": (
            api.State.UNKNOWN,
            "Duplicate Watchpost service: Contract duplicate",
        ),
    }
    for item, (state, summary) in expected.items():
        output = list(plugin.check_function(item, production))
        results = [value for value in output if isinstance(value, api.Result)]
        metrics = [value for value in output if isinstance(value, api.Metric)]
        assert len(results) == 1, (item, output)
        assert (results[0].state, results[0].summary) == (state, summary)
        if item == "Contract OK":
            assert results[0].details == "Details line one\nDetails line two"
            assert len(metrics) == 1
            assert (metrics[0].name, metrics[0].value) == ("request_rate", 1.5)
            assert metrics[0].levels == (5, 10)
            assert metrics[0].boundaries == (0, None)
        elif item == "Contract CRIT":
            assert len(metrics) == 1
            assert (metrics[0].name, metrics[0].value) == ("errors", 2)
            assert metrics[0].levels == (None, None)
            assert metrics[0].boundaries == (None, 100)
        else:
            assert not metrics, (item, metrics)

    staging = plugin.parse_function(sections["contract-staging"])
    staging_services = list(plugin.discovery_function(staging))
    assert len(staging_services) == 1
    assert staging_services[0].item == "Contract OK"
    result = list(plugin.check_function("Contract OK", staging))
    assert len(result) == 1
    assert result[0].state == api.State.CRIT
    assert result[0].summary == "Same service name on another host"

    source = plugin.parse_function(sections[""])
    source_services = list(plugin.discovery_function(source))
    assert len(source_services) == 1
    assert source_services[0].item == "Contract OK"
    assert {label.name: label.value for label in source_services[0].labels} == {
        "scope": "source",
    }
    source_results = list(plugin.check_function("Contract OK", source))
    assert len(source_results) == 1
    assert source_results[0].state == api.State.WARN
    assert source_results[0].summary == "Same service name on the source host"

    try:
        list(plugin.check_function("Missing service", production))
    except api.IgnoreResultsError:
        pass
    else:
        raise AssertionError("Missing services must raise IgnoreResultsError")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "check"))
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    if args.action == "generate":
        generate_fixture(args.fixture)
    else:
        check_fixture(args.fixture)


if __name__ == "__main__":
    main()
