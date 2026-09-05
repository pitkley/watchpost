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
from watchpost.hostname import NoPiggybackHostStrategy


@pytest.mark.parametrize("include_piggyback_target", [False, True])
def test_get_check_hostnames_omits_source_host(
    monkeypatch: pytest.MonkeyPatch,
    include_piggyback_target: bool,
) -> None:
    source = Environment("source", hostname=NoPiggybackHostStrategy())
    environments = [source]
    if include_piggyback_target:
        environments.append(Environment("target", hostname="target-host"))

    @check(
        name="sample",
        environments=environments,
        service_labels={},
        cache_for=None,
    )
    def probe() -> CheckResult:
        raise AssertionError("hostname listing must not run checks")

    app = Watchpost(checks=[probe], execution_environment=source)
    monkeypatch.setattr(_cli, "find_app", lambda _name: app)
    try:
        result = CliRunner().invoke(_cli.cli, ["get-check-hostnames"])
        assert result.exit_code == 0, result.output
        assert result.output == ("target-host\n" if include_piggyback_target else "")
    finally:
        app.shutdown()
