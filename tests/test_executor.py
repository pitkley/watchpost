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

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Barrier, Event

import pytest

from watchpost.executor import CheckExecutor

from .utils import with_event


def job(*args, **kwargs):
    return {
        "args": args,
        "kwargs": kwargs,
    }


def waiting_job(*args, event: Event, **kwargs):
    event.wait()
    return {
        "args": args,
        "kwargs": kwargs,
    }


def test_empty_statistics():
    executor = CheckExecutor(max_workers=1)
    assert asdict(executor.statistics()) == {
        "total": 0,
        "completed": 0,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 0,
    }


def test_cant_get_result_of_unknown_job():
    executor = CheckExecutor(max_workers=1)

    with pytest.raises(KeyError):
        executor.result("job")


def test_submitting_and_getting_a_job():
    executor = CheckExecutor(max_workers=1)

    future = executor.submit("key", job, "arg", kwarg="kwarg")
    future.result()  # Wait for the job to complete

    assert future.done() is True
    assert asdict(executor.statistics()) == {
        "total": 1,
        "completed": 1,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 1,
    }

    assert executor.result("key") == {
        "args": ("arg",),
        "kwargs": {"kwarg": "kwarg"},
    }
    assert asdict(executor.statistics()) == {
        "total": 0,
        "completed": 0,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 0,
    }


def test_throwing_job():
    executor = CheckExecutor(max_workers=1)

    future = executor.submit("key", lambda: 1 / 0)

    # Wait for the job to complete
    with pytest.raises(ZeroDivisionError):
        future.result()

    assert future.done() is True
    assert asdict(executor.statistics()) == {
        "total": 1,
        "completed": 0,
        "errored": 1,
        "running": 0,
        "awaiting_pickup": 1,
    }

    with pytest.raises(ZeroDivisionError):
        executor.result("key")

    assert asdict(executor.statistics()) == {
        "total": 0,
        "completed": 0,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 0,
    }


def test_waiting_job():
    executor = CheckExecutor(max_workers=1)

    event = Event()
    future = executor.submit("key", waiting_job, event=event)
    assert future.done() is False
    assert asdict(executor.statistics()) == {
        "total": 1,
        "completed": 0,
        "errored": 0,
        "running": 1,
        "awaiting_pickup": 0,
    }
    assert executor.result("key") is None

    event.set()
    future.result()  # Wait for the job to complete
    assert asdict(executor.statistics()) == {
        "total": 1,
        "completed": 1,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 1,
    }

    assert executor.result("key") == {
        "args": (),
        "kwargs": {},
    }
    assert asdict(executor.statistics()) == {
        "total": 0,
        "completed": 0,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 0,
    }


def test_resubmit_disabled():
    executor = CheckExecutor(max_workers=1)

    future1 = executor.submit("key", job, "arg1", kwarg="kwarg1")
    future2 = executor.submit("key", job, "arg2", kwarg="kwarg2", resubmit=False)

    assert future1 is future2
    future1.result()  # Wait for the job to complete

    assert future1.done() is True
    assert asdict(executor.statistics()) == {
        "total": 1,
        "completed": 1,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 1,
    }

    assert executor.result("key") == {
        "args": ("arg1",),
        "kwargs": {"kwarg": "kwarg1"},
    }


def test_resubmit_enabled():
    executor = CheckExecutor(max_workers=1)

    future1 = executor.submit("key", job, "arg1", kwarg="kwarg1")
    future2 = executor.submit("key", job, "arg2", kwarg="kwarg2", resubmit=True)

    assert future1 is not future2
    # Wait for the jobs to complete
    future1.result()
    future2.result()

    assert future1.done() is True
    assert asdict(executor.statistics()) == {
        "total": 2,
        "completed": 2,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 2,
    }

    assert executor.result("key") == {
        "args": ("arg1",),
        "kwargs": {"kwarg": "kwarg1"},
    }
    assert asdict(executor.statistics()) == {
        "total": 1,
        "completed": 1,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 1,
    }
    assert executor.result("key") == {
        "args": ("arg2",),
        "kwargs": {"kwarg": "kwarg2"},
    }
    assert asdict(executor.statistics()) == {
        "total": 0,
        "completed": 0,
        "errored": 0,
        "running": 0,
        "awaiting_pickup": 0,
    }


def test_resubmit_results():
    executor = CheckExecutor(max_workers=2)

    with (
        with_event() as event1,
        with_event() as event2,
    ):
        assert event1 is not event2

        future1 = executor.submit("key", waiting_job, "future1", event=event1)
        future2 = executor.submit(
            "key", waiting_job, "future2", event=event2, resubmit=True
        )
        assert future1 is not future2

        # With no futures finished, there should be no result yet.
        assert executor.result("key") is None
        assert asdict(executor.statistics()) == {
            "total": 2,
            "completed": 0,
            "errored": 0,
            "running": 2,
            "awaiting_pickup": 0,
        }

        # Finishing future2 (which was submitted later) should return the results of
        # that future
        event2.set()
        future2.result()
        assert future2.done() is True
        assert asdict(executor.statistics()) == {
            "total": 2,
            "completed": 1,
            "errored": 0,
            "running": 1,
            "awaiting_pickup": 1,
        }
        assert executor.result("key") == {
            "args": ("future2",),
            "kwargs": {},
        }
        assert asdict(executor.statistics()) == {
            "total": 1,
            "completed": 0,
            "errored": 0,
            "running": 1,
            "awaiting_pickup": 0,
        }

        # Finishing future1 (which was submitted earlier) should now return the
        # results of that future
        event1.set()
        future1.result()
        assert future1.done() is True
        assert asdict(executor.statistics()) == {
            "total": 1,
            "completed": 1,
            "errored": 0,
            "running": 0,
            "awaiting_pickup": 1,
        }
        assert executor.result("key") == {
            "args": ("future1",),
            "kwargs": {},
        }
        assert asdict(executor.statistics()) == {
            "total": 0,
            "completed": 0,
            "errored": 0,
            "running": 0,
            "awaiting_pickup": 0,
        }


def test_errored_initially_empty():
    executor = CheckExecutor(max_workers=1)
    assert executor.errored() == {}


def _raise_value_error():
    raise ValueError("boom")


def test_errored_reports_and_clears_after_pickup():
    executor = CheckExecutor(max_workers=1)

    future = executor.submit("key-err", _raise_value_error)

    # Wait for the job to complete with an error
    with pytest.raises(ValueError, match="boom"):
        future.result()

    # Before picking up the result, errored() should report the failure
    errs = executor.errored()
    assert errs == {"key-err": "boom"}

    # Picking up the result should raise and clear errored tracking
    with pytest.raises(ValueError, match="boom"):
        executor.result("key-err")

    assert executor.errored() == {}


def test_concurrent_submissions_share_one_future(monkeypatch):
    gate = Barrier(16)
    with CheckExecutor(max_workers=1) as executor:
        original_submit = executor.executor.submit

        def slow_submit(*args, **kwargs):
            # Widen the check/submit race while callers synchronize at the gate.
            time.sleep(0.02)
            return original_submit(*args, **kwargs)

        monkeypatch.setattr(executor.executor, "submit", slow_submit)

        def submit():
            gate.wait(timeout=5)
            return executor.submit("same", lambda: 42)

        with ThreadPoolExecutor(16) as callers:
            futures = list(callers.map(lambda _: submit(), range(16)))
        assert all(future is futures[0] for future in futures)
        assert futures[0].result(5) == 42
        assert executor.result("same") == 42
        assert executor.statistics().total == 0


def test_concurrent_async_loop_startup_returns_ready_loop(monkeypatch):
    import asyncio

    gate = Barrier(8)
    original_new_loop = asyncio.new_event_loop

    def slow_new_loop():
        time.sleep(0.02)
        return original_new_loop()

    monkeypatch.setattr(asyncio, "new_event_loop", slow_new_loop)
    with CheckExecutor() as executor:

        def get_loop():
            gate.wait(timeout=5)
            return executor.asyncio_loop

        with ThreadPoolExecutor(8) as callers:
            loops = list(callers.map(lambda _: get_loop(), range(8)))
        assert loops[0] is not None
        assert all(loop is loops[0] for loop in loops)


def test_pickup_and_diagnostics_observe_completion_before_callback():
    from concurrent.futures import Future

    from watchpost.executor import _KeyState

    executor = CheckExecutor()
    future = Future()
    executor._state["done"] = _KeyState(active_futures=[future])
    future.set_result(42)
    assert executor.statistics().completed == 1
    assert executor.result("done") == 42
    # A late callback must not restore a consumed future.
    executor._done_callback("done", future)
    assert executor.statistics().total == 0
