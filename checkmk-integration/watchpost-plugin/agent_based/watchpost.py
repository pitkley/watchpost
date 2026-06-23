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

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, TypedDict

from cmk.agent_based.v2 import (
    AgentSection,
    CheckPlugin,
    CheckResult,
    IgnoreResultsError,
    Metric,
    Result,
    Service,
    ServiceLabel,
    State,
    StringTable,
)
from cmk.utils.log import console


def parse_b64_json(string_table: StringTable) -> Iterator[dict[str, Any]]:
    for b64_encoded_json, *_ in string_table:
        yield json.loads(base64.b64decode(b64_encoded_json))


def sanitize_summary(summary: str) -> str:
    return summary.replace("\n", " ").strip()


class Check(TypedDict):
    service_name: str
    service_labels: dict[str, str]
    environment: str
    check_state: State
    summary: str
    details: str | None
    metrics: list[Metric]


@dataclass
class Section:
    checks: list[Check]


def parse_metrics(metrics: list[dict] | None) -> list[Metric]:
    if not metrics:
        return []

    result = []
    for metric in metrics:
        if metric.get("name") is None or metric.get("value") is None:
            console.error("[Watchpost plugin] Metric name or value is missing")
            continue

        levels = None
        if "levels" in metric:
            levels = (metric["levels"]["warning"], metric["levels"]["critical"])

        boundaries = None
        if "boundaries" in metric:
            boundaries = (
                metric["boundaries"]["lower"],
                metric["boundaries"]["upper"],
            )

        result.append(
            Metric(
                name=metric["name"],
                value=metric["value"],
                levels=levels,
                boundaries=boundaries,
            )
        )
    return result


def parse_function(string_table: StringTable) -> Section | None:
    def transform(check: dict[str, Any]) -> Check:
        check["check_state"] = State[check["check_state"]]
        check["metrics"] = parse_metrics(check.get("metrics"))
        return check

    checks = [transform(check) for check in parse_b64_json(string_table)]
    return Section(checks=checks)


def discovery_function(section: Section) -> Iterator[Service]:
    for check in section.checks:
        yield Service(
            item=check["service_name"],
            labels=[
                ServiceLabel(key, value)
                for key, value in check["service_labels"].items()
            ],
        )


def check_function(item: str, section: Section) -> CheckResult:
    check: Check | None = next(
        (check for check in section.checks if check["service_name"] == item),
        None,
    )
    if check is None:
        raise IgnoreResultsError("section for check not found")

    if check["metrics"]:
        yield from check["metrics"]

    yield Result(
        state=check["check_state"],
        summary=sanitize_summary(check["summary"]),
        details=check["details"],
    )


agent_section_watchpost = AgentSection(
    name="watchpost",
    parse_function=parse_function,
)
check_plugin_watchpost = CheckPlugin(
    name="watchpost",
    service_name="%s",
    discovery_function=discovery_function,
    check_function=check_function,
)
