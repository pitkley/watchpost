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

import runpy
from pathlib import Path

from watchpost import Watchpost
from watchpost.executor import BlockingCheckExecutor
from watchpost.result import CheckState


def test_typed_consumer_examples_execute() -> None:
    example = Path(__file__).resolve().parents[1] / "examples" / "typed_checks.py"
    app = runpy.run_path(str(example))["app"]
    assert isinstance(app, Watchpost)
    executor = BlockingCheckExecutor()
    try:
        app.verify_check_scheduling()
        results = [
            result
            for check in app.checks
            for result in app.run_check(check, custom_executor=executor)
        ]
        assert len(results) == 6
        assert all(result.check_state == CheckState.OK for result in results)
        assert {result.summary for result in results} == {
            "zero",
            "example",
            "firstsecond",
            "generated",
            "list",
        }
    finally:
        executor.shutdown()
        app.shutdown()
