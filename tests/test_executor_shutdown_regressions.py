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
from concurrent.futures import CancelledError
from threading import Condition, Event, Thread, current_thread

import pytest

from watchpost.executor import BlockingCheckExecutor, CheckExecutor


@pytest.mark.parametrize("shield_cleanup", [False, True])
def test_shutdown_preserves_child_task_awaited_by_check_finalizer(shield_cleanup):
    started, sync_started, cleanup_started, finalized = (
        Event(),
        Event(),
        Event(),
        Event(),
    )

    async def work():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:

            async def cleanup():
                cleanup_started.set()
                await asyncio.sleep(0.05)
                finalized.set()

            task = asyncio.create_task(cleanup())
            await (asyncio.shield(task) if shield_cleanup else task)

    def synchronous_work():
        sync_started.set()
        assert cleanup_started.wait(5)

    executor = CheckExecutor(max_workers=1)
    future = executor.submit("async", work)
    executor.submit("sync", synchronous_work)
    try:
        assert started.wait(5)
        assert sync_started.wait(5)
        executor.shutdown(wait=True, cancel_futures=True)
        assert future.cancelled()
        assert cleanup_started.is_set()
        assert finalized.is_set()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def test_blocking_result_wakes_when_shutdown_cancels_queued_thread_work():
    running, release, waiting, finished = Event(), Event(), Event(), Event()

    def slow():
        running.set()
        assert release.wait(5)

    executor = BlockingCheckExecutor(max_workers=1)
    executor.submit("running", slow)
    assert running.wait(5)
    queued = executor.submit("queued", lambda: 42)
    errors = []

    def pick_up():
        waiting.set()
        try:
            executor.result("queued")
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()

    caller = Thread(target=pick_up, daemon=True)
    caller.start()
    try:
        assert waiting.wait(5)
        executor.shutdown(wait=False, cancel_futures=True)
        release.set()
        executor.shutdown(wait=True)
        assert queued.cancelled()
        assert finished.wait(5)
        assert len(errors) == 1
        assert isinstance(errors[0], CancelledError)
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)
        caller.join(timeout=1)


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_blocking_result_propagates_caller_interruption(
    monkeypatch: pytest.MonkeyPatch, interruption: type[BaseException]
) -> None:
    started, release = Event(), Event()

    def work() -> int:
        started.set()
        assert release.wait(5)
        return 42

    executor: BlockingCheckExecutor[int] = BlockingCheckExecutor()
    future = executor.submit("running", work)
    caller = current_thread()
    original_wait = Condition.wait

    def interrupted_wait(condition: Condition, timeout: float | None = None) -> bool:
        if current_thread() is caller:
            raise interruption()
        return original_wait(condition, timeout)

    try:
        assert started.wait(5)
        with monkeypatch.context() as patched:
            # Both Future.result() and Event.wait() block on a condition. Inject
            # an interrupt in the caller's wait without disturbing the worker.
            patched.setattr(Condition, "wait", interrupted_wait)
            with pytest.raises(interruption):
                executor.result("running")
        assert not future.done()
        release.set()
        assert executor.result("running") == 42
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_async_loop_installation_failure_closes_loop_and_allows_shutdown(monkeypatch):
    loop = asyncio.new_event_loop()

    def fail(_loop):
        raise OSError("cannot install loop")

    monkeypatch.setattr(asyncio, "new_event_loop", lambda: loop)
    monkeypatch.setattr(asyncio, "set_event_loop", fail)
    executor = CheckExecutor()
    try:
        with pytest.raises(RuntimeError, match="Could not start"):
            executor.asyncio_loop
        executor.shutdown(wait=False)
        assert executor._shutdown_thread is not None
        executor._shutdown_thread.join(timeout=5)
        assert not executor._shutdown_thread.is_alive()
        assert loop.is_closed()
        executor.shutdown(wait=True)
    finally:
        if not loop.is_closed():
            loop.close()


def test_shutdown_handles_async_work_cancelled_before_loop_can_start_it():
    loop_blocked, release_loop = Event(), Event()

    def block_loop():
        loop_blocked.set()
        assert release_loop.wait(5)

    async def work():
        await asyncio.sleep(0)

    executor = CheckExecutor()
    loop = executor.asyncio_loop
    loop.call_soon_threadsafe(block_loop)
    try:
        assert loop_blocked.wait(5)
        future = executor.submit("queued", work)
        assert future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        release_loop.set()
        assert executor._shutdown_thread is not None
        executor._shutdown_thread.join(timeout=5)
        assert not executor._shutdown_thread.is_alive()
        assert future.cancelled()
        assert loop.is_closed()
    finally:
        release_loop.set()
        executor.shutdown(wait=True, cancel_futures=True)
