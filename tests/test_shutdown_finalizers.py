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
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from watchpost.executor import CheckExecutor


def test_cancelling_async_check_waits_for_awaited_finalizer():
    started, finalized = Event(), Event()

    async def work():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            # Closing async clients and transactions commonly requires an await.
            # A second cancellation must not interrupt this cleanup.
            await asyncio.sleep(0.05)
            finalized.set()

    executor = CheckExecutor()
    future = executor.submit("async-cleanup", work)
    try:
        assert started.wait(5)
        with ThreadPoolExecutor(1) as caller:
            shutdown = caller.submit(executor.shutdown, wait=True, cancel_futures=True)
            shutdown.result(timeout=5)
        assert future.cancelled()
        assert finalized.is_set()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
