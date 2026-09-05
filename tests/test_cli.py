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
from click.testing import CliRunner

from watchpost import CheckResult, Environment, Watchpost, check
from watchpost.cli import _cli
from watchpost.scheduling_strategy import (
    MustRunAgainstGivenTargetEnvironmentStrategy,
)


@pytest.mark.parametrize("bad_scheduling", [False, True])
@pytest.mark.parametrize("bad_hostname", [False, True])
def test_verify_exit_status(
    monkeypatch: pytest.MonkeyPatch,
    bad_scheduling: bool,
    bad_hostname: bool,
) -> None:
    target = Environment("target")

    @check(
        name="sample",
        environments=[target],
        service_labels={},
        cache_for=None,
        scheduling_strategies=[
            MustRunAgainstGivenTargetEnvironmentStrategy(Environment("other"))
        ]
        if bad_scheduling
        else [],
        hostname="invalid hostname" if bad_hostname else "valid-host",
    )
    def probe(environment: Environment) -> CheckResult:
        raise AssertionError(f"verification must not run checks in {environment}")

    app = Watchpost(
        checks=[probe],
        execution_environment=target,
        hostname_coerce_into_valid_hostname=False,
    )
    monkeypatch.setattr(_cli, "find_app", lambda _name: app)
    try:
        result = CliRunner().invoke(_cli.cli, ["verify-check-configuration"])
        if bad_scheduling or bad_hostname:
            assert result.exit_code == 1, result.output
            assert "Check configurations verified." not in result.output
            assert "Check Configuration Verification" in result.output
        else:
            assert result.exit_code == 0, result.output
            assert "Check configurations verified." in result.output
    finally:
        app.shutdown()
