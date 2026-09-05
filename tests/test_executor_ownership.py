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

from watchpost import Environment, Watchpost
from watchpost.executor import CheckExecutor
from watchpost.result import ExecutionResult


def test_replacing_executor_preserves_actual_resource_ownership() -> None:
    app = Watchpost(checks=[], execution_environment=Environment("test"))
    owned = app.executor
    loop = owned.asyncio_loop
    supplied: CheckExecutor[list[ExecutionResult]] = CheckExecutor()
    try:
        app.executor = supplied
        app.shutdown()
        assert loop.is_closed()
        with pytest.raises(RuntimeError, match="shut down"):
            owned.submit("closed", lambda: [])
        assert supplied.submit("open", lambda: []).result(timeout=2) == []
    finally:
        owned.shutdown(wait=True, cancel_futures=True)
        supplied.shutdown(wait=True, cancel_futures=True)


def test_replacing_supplied_executor_closes_neither_caller_resource() -> None:
    first: CheckExecutor[list[ExecutionResult]] = CheckExecutor()
    second: CheckExecutor[list[ExecutionResult]] = CheckExecutor()
    app = Watchpost(
        checks=[], execution_environment=Environment("test"), executor=first
    )
    try:
        app.executor = second
        app.shutdown()
        assert first.submit("first", lambda: []).result(timeout=2) == []
        assert second.submit("second", lambda: []).result(timeout=2) == []
    finally:
        first.shutdown(wait=True, cancel_futures=True)
        second.shutdown(wait=True, cancel_futures=True)
