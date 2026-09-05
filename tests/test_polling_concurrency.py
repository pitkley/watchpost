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

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from watchpost import Environment, Watchpost, check, ok
from watchpost.executor import BlockingCheckExecutor


@pytest.mark.parametrize("cache_for", [None, "1m"])
def test_concurrent_polls_do_not_report_result_pickup_as_check_failure(
    monkeypatch, cache_for
):
    env = Environment("concurrent-polls")
    start = Barrier(2)

    @check(
        name="concurrent-polls",
        service_labels={},
        environments=[env],
        cache_for=cache_for,
    )
    def successful():
        return ok("successful")

    with BlockingCheckExecutor() as executor:
        app = Watchpost(
            checks=[successful], execution_environment=env, executor=executor
        )
        app.verify_check_scheduling()
        original_result = executor.result

        def delayed_result(key):
            # Widen the gap between submission and pickup. Synchronization is
            # outside run_check so serializing poll orchestration stays valid.
            time.sleep(0.05)
            return original_result(key)

        monkeypatch.setattr(executor, "result", delayed_result)

        def poll():
            start.wait(timeout=5)
            return list(app.run_check(successful))

        with ThreadPoolExecutor(2) as callers:
            futures = [callers.submit(poll) for _ in range(2)]
            results = [future.result(timeout=5) for future in futures]

    assert [[r.check_state.name for r in poll] for poll in results] == [["OK"], ["OK"]]
    assert [[r.summary for r in poll] for poll in results] == [
        ["successful"],
        ["successful"],
    ]
