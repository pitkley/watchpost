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

"""
Threaded check executor for Watchpost.

Provides a non-blocking, key-aware execution engine that de-duplicates work per
key and can run both synchronous and asynchronous check functions. It exposes
lightweight statistics used by the HTTP endpoints and tests.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections import deque
from collections.abc import Awaitable, Callable, Hashable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast, override

if TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger(f"{__package__}.{__name__}")


class AsyncioLoopThread(threading.Thread):
    """
    Run an asyncio event loop in a dedicated background thread.

    The thread creates its own event loop and runs it forever until stopped.
    `CheckExecutor` uses this to execute coroutine functions without blocking
    the worker threads in the thread pool.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_started = threading.Event()

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        self.loop_started.set()
        self.loop.run_forever()

    def stop(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)


@dataclass
class _KeyState[T]:
    """
    Internal state for a single execution key.

    Tracks currently running futures and those that are finished and awaiting
    pickup via `result()`.
    """

    active_futures: list[Future[T]] = field(default_factory=list)
    """
    Futures currently submitted for this key.
    """

    finished_futures: deque[Future[T]] = field(default_factory=deque)
    """
    Completed futures waiting for their results to be retrieved.
    """


class CheckExecutor[T]:
    """
    Execute checks concurrently while avoiding duplicate work per key.

    This executor wraps a `ThreadPoolExecutor` and adds key-aware submission:
    if a job for a key is already running, later submissions with the same key
    return the existing future unless `resubmit=True` is passed. The executor
    can also run coroutine functions by scheduling them on a single background
    asyncio event loop.
    """

    @dataclass
    class Statistics:
        """
        Summary statistics of the executor state.

        These values feed monitoring endpoints and tests to provide visibility
        into how many jobs are running, finished, or awaiting pickup.
        """

        total: int
        """
        Number of active futures across all keys (running + awaiting pickup).
        """
        completed: int
        """
        Number of successfully completed futures awaiting pickup.
        """
        errored: int
        """
        Number of futures that completed with an exception and await pickup.
        """
        running: int
        """
        Number of futures currently executing (not yet completed).
        """
        awaiting_pickup: int
        """
        Total number of finished futures (completed + errored) that have not
        yet been retrieved via `result()`.
        """

    def __init__(
        self,
        max_workers: int | None = None,
    ):
        self._lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self._state: dict[Hashable, _KeyState[T]] = {}
        self._asyncio_loop_thread: AsyncioLoopThread | None = None

    @property
    def asyncio_loop(self) -> asyncio.AbstractEventLoop:
        """
        Return the background asyncio event loop, starting it on first access.

        Returns:
            The event loop used to run coroutine functions submitted to this
            executor.
        """
        with self._lock:
            if not self._asyncio_loop_thread:
                self._asyncio_loop_thread = AsyncioLoopThread(daemon=True)
                self._asyncio_loop_thread.start()
                self._asyncio_loop_thread.loop_started.wait()

            return cast(asyncio.AbstractEventLoop, self._asyncio_loop_thread.loop)

    def __enter__(self) -> CheckExecutor[T]:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.shutdown(wait=True)

    def shutdown(self, wait: bool = False) -> None:
        """
        Shut down the executor and stop the background event loop.

        Parameters:
            wait:
                If true, waits for all running futures to finish before
                returning. This is passed through to the underlying
                `ThreadPoolExecutor.shutdown()`.
        """
        if self._asyncio_loop_thread:
            self._asyncio_loop_thread.stop()
        self.executor.shutdown(wait=wait)

    def submit[**P](  # type: ignore[valid-type]
        self,
        key: Hashable,
        func: Callable[P, T | Awaitable[T]],
        *args: P.args,
        resubmit: bool = False,
        **kwargs: P.kwargs,
    ) -> Future:
        """
        Submit a function to run for a key, deduplicating concurrent work.

        If another job with the same key is already running and `resubmit` is
        false, this returns the existing future instead of starting a new one.
        Coroutine functions are scheduled on the background asyncio loop.

        Parameters:
            key:
                The deduplication key. Only one active job per key is started
                unless `resubmit=True` is given.
            func:
                The callable to execute. May be synchronous or a coroutine
                function.
            *args:
                Positional arguments passed to the callable.
            resubmit:
                When true, always schedules a new job even if one with the same
                key is already running.
            **kwargs:
                Keyword arguments passed to the callable.

        Returns:
            A Future representing the running or already existing job.
        """
        with self._lock:
            key_state = self._state.setdefault(key, _KeyState())

            if not resubmit and key_state.active_futures:
                # One or more jobs for this key are already running. We don't want
                # to start another one, so we return the first existing future.
                return key_state.active_futures[0]

            logger.debug("Submitting future for key %s", key)
            if inspect.iscoroutinefunction(func):
                future = asyncio.run_coroutine_threadsafe(
                    func(*args, **kwargs),  # type: ignore[invalid-argument-type]
                    self.asyncio_loop,
                )
            else:
                future = self.executor.submit(func, *args, **kwargs)
            key_state.active_futures.append(future)
            future.add_done_callback(lambda future: self._done_callback(key, future))
            return future

    def _done_callback(self, key: Hashable, _future: Future[T]) -> None:
        with self._lock:
            if key_state := self._state.get(key):
                self._collect_finished(key_state)

    @staticmethod
    def _collect_finished(key_state: _KeyState[T]) -> None:
        # A Future becomes done before its callbacks run. Refresh from the
        # futures themselves so result()/statistics() never lag completion.
        for future in key_state.active_futures:
            if future.done() and future not in key_state.finished_futures:
                key_state.finished_futures.append(future)

    def result(self, key: Hashable) -> T | None:
        """
        Retrieve the next finished result for a key.

        If no job for the key exists, raises a KeyError. If jobs exist but none
        has completed yet, returns None. When the last active future for a key
        is consumed, the internal state for the key is cleaned up.

        Parameters:
            key:
                The key used when submitting the job(s).

        Returns:
            The completed result value or None if the job is still running.

        Raises:
            KeyError:
                If no job for the given key has been submitted.
        """
        with self._lock:
            key_state = self._state.get(key)
            if key_state:
                self._collect_finished(key_state)

            if not key_state or not key_state.finished_futures:
                if not key_state or not key_state.active_futures:
                    # No future for the key has been submitted at all.
                    raise KeyError(key) from None

                # No future for the key has finished yet, returning no result.
                return None

            finished_future = key_state.finished_futures.popleft()
            try:
                key_state.active_futures.remove(finished_future)
            except ValueError:
                pass

            if not key_state.active_futures:
                assert len(key_state.finished_futures) == 0
                del self._state[key]

            return finished_future.result()

    def statistics(self) -> CheckExecutor.Statistics:
        """
        Compute executor statistics.

        Returns:
            A `CheckExecutor.Statistics` instance summarizing total, completed,
            errored, running, and awaiting pickup futures.
        """
        with self._lock:
            total = 0
            completed = 0
            errored = 0

            for key_state in self._state.values():
                self._collect_finished(key_state)
                total += len(key_state.active_futures)
                for finished_future in key_state.finished_futures:
                    assert finished_future.done()
                    if finished_future.exception():
                        errored += 1
                    else:
                        completed += 1

            awaiting_pickup = completed + errored
            running = total - awaiting_pickup

            return CheckExecutor.Statistics(
                total=total,
                completed=completed,
                errored=errored,
                running=running,
                awaiting_pickup=awaiting_pickup,
            )

    def errored(self) -> dict[str, str]:
        """
        Return a mapping of keys to stringified exceptions for errored jobs.

        Returns:
            A dict mapping the string form of each key to the corresponding
            exception message of futures that completed with an error and have
            not yet been picked up via `result()`.
        """
        with self._lock:
            errors = {}
            for key, key_state in self._state.items():
                self._collect_finished(key_state)
                for future in key_state.finished_futures:
                    if (exception := future.exception()) is not None:
                        errors[str(key)] = str(exception)

            return errors


class BlockingCheckExecutor[T](CheckExecutor[T]):
    """
    Execute checks while blocking until results are available.

    This variant waits for all futures of a key to complete when `result()` is
    called. It is useful for tests and simple CLI usage where non-blocking
    behavior is not required.

    Notes:
        Prefer the default `CheckExecutor` for production use. This class is
        provided for special cases (such as the CLI or in unit-tests) where
        synchronous behavior is desired.
    """

    def __init__(
        self,
        max_workers: int | None = 1,
    ):
        super().__init__(max_workers)

    @override
    def result(
        self,
        key: Hashable,
    ) -> T | None:
        """
        Wait for all active futures of the key and then return the next result.

        Parameters:
            key:
                The key used when submitting the job(s).

        Returns:
            The completed result value, or None if no finished results are
            queued after waiting.
        """
        with self._lock:
            futures = list(self._state[key].active_futures)
        wait(futures, return_when="ALL_COMPLETED")
        return super().result(key)
