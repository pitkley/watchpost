# Copyright 2025 TAKKT Industrial & Packaging GmbH
# Copyright 2026 Pit Kleyersburg <pitkley@googlemail.com>
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
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from watchpost import Environment, Watchpost, current_app, ok
from watchpost.check import Check


def make_check(func):
    return Check(func, "capture", {}, [Environment("test")], None)


def test_overlapping_thread_checks_capture_only_their_own_output():
    original = sys.stdout, sys.stderr
    entered, finish = Event(), Event()
    app = Watchpost(checks=[], execution_environment=Environment("test"))

    def first():
        assert entered.wait(5)
        print("first-out")  # noqa: T201
        print("first-err", file=sys.stderr)  # noqa: T201
        return ok("first")

    def second():
        entered.set()
        assert finish.wait(5)
        print("second-out")  # noqa: T201
        return ok("second")

    def run(func):
        return make_check(func).run_sync(
            watchpost=app, environment=app.execution_environment, datasources={}
        )

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(run, first)
        b = pool.submit(run, second)
        try:
            result_a = a.result(5)[0]
        finally:
            finish.set()
        result_b = b.result(5)[0]
    assert "first-out" in result_a.details
    assert "first-err" in result_a.details
    assert "second" not in result_a.details
    assert "second-out" in result_b.details
    assert "first" not in result_b.details
    assert (sys.stdout, sys.stderr) == original


@pytest.mark.parametrize("fail", [False, True])
def test_overlapping_async_checks_restore_streams_even_on_failure(fail):
    original = sys.stdout, sys.stderr
    app = Watchpost(checks=[], execution_environment=Environment("test"))

    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def first():
            await entered.wait()
            print("first")  # noqa: T201
            if fail:
                raise ValueError("failed")
            return ok("first")

        async def second():
            entered.set()
            await finish.wait()
            print("second")  # noqa: T201
            return ok("second")

        async def execute(func):
            return await make_check(func).run_async(
                watchpost=app, environment=app.execution_environment, datasources={}
            )

        a, b = asyncio.create_task(execute(first)), asyncio.create_task(execute(second))
        try:
            if fail:
                with pytest.raises(ValueError, match="failed"):
                    await a
            else:
                assert "first" in (await a)[0].details
        finally:
            finish.set()
        result_b = (await b)[0]
        assert "second" in result_b.details
        assert "first" not in result_b.details

    asyncio.run(run())
    assert (sys.stdout, sys.stderr) == original


def test_generator_body_has_context_and_capture():
    app = Watchpost(checks=[], execution_environment=Environment("generator"))

    def generated():
        print("generated output")  # noqa: T201
        yield ok(current_app.execution_environment.name)

    result = make_check(generated).run_sync(
        watchpost=app, environment=app.execution_environment, datasources={}
    )[0]
    assert result.summary == "generator"
    assert "generated output" in result.details


def test_generator_failure_restores_context_and_streams():
    original = sys.stdout, sys.stderr
    app = Watchpost(checks=[], execution_environment=Environment("generator"))

    def generated():
        yield ok("first")
        raise ValueError("iteration failed")

    with pytest.raises(ValueError, match="iteration failed"):
        make_check(generated).run_sync(
            watchpost=app, environment=app.execution_environment, datasources={}
        )
    assert (sys.stdout, sys.stderr) == original
    assert not current_app
