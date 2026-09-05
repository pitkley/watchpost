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

import asyncio

import pytest

from watchpost import (
    CheckResult,
    Environment,
    Watchpost,
    check,
    current_app,
)
from watchpost.executor import BlockingCheckExecutor
from watchpost.result import CheckState


@pytest.mark.parametrize("fail", [False, True])
def test_nested_context_restores_application(fail: bool) -> None:
    a = Watchpost(checks=[], execution_environment=Environment("A"))
    b = Watchpost(checks=[], execution_environment=Environment("B"))
    try:
        with a.app_context():
            assert current_app.execution_environment.name == "A"
            try:
                with b.app_context() as active:
                    assert active is b
                    assert current_app.execution_environment.name == "B"
                    with a.app_context():
                        assert current_app.execution_environment.name == "A"
                    assert current_app.execution_environment.name == "B"
                    if fail:
                        raise LookupError("inside context")
            except LookupError:
                assert fail
            assert current_app.execution_environment.name == "A"
        with pytest.raises(RuntimeError):
            _ = current_app.execution_environment
    finally:
        a.shutdown()
        b.shutdown()


def test_async_contexts_remain_independent() -> None:
    a = Watchpost(checks=[], execution_environment=Environment("A"))
    b = Watchpost(checks=[], execution_environment=Environment("B"))

    async def run() -> None:
        ready = asyncio.Event()

        async def child() -> None:
            with b.app_context():
                ready.set()
                await asyncio.sleep(0)
                assert current_app.execution_environment.name == "B"

        with a.app_context():
            task = asyncio.create_task(child())
            await ready.wait()
            assert current_app.execution_environment.name == "A"
            await task
            assert current_app.execution_environment.name == "A"

    try:
        asyncio.run(run())
    finally:
        a.shutdown()
        b.shutdown()


def test_stream_yields_without_leaking_app_context() -> None:
    environment = Environment("test")

    @check(name="test", environments=[environment], service_labels={}, cache_for=None)
    def probe(environment: Environment) -> CheckResult:
        assert current_app.execution_environment is environment
        return CheckResult(CheckState.OK, summary="context probe succeeded")

    executor = BlockingCheckExecutor()
    app = Watchpost(
        checks=[probe], execution_environment=environment, executor=executor
    )
    try:
        for _chunk in app.run_checks():
            with pytest.raises(RuntimeError):
                _ = current_app.execution_environment
        results = list(app.run_check(probe))
        assert results[0].summary == "context probe succeeded"
    finally:
        executor.shutdown()
