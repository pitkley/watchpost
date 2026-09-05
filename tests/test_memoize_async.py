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
import math
from datetime import timedelta
from pathlib import Path

import pytest

from watchpost.cache import Cache, DiskStorage, InMemoryStorage


@pytest.fixture(params=["memory", "disk"])
def cache(request: pytest.FixtureRequest, tmp_path: Path) -> Cache:
    return Cache(
        DiskStorage(str(tmp_path)) if request.param == "disk" else InMemoryStorage()
    )


def test_async_memoize_caches_values_and_binds_defaults(cache: Cache) -> None:
    calls = 0

    @cache.memoize(key="async-{a}-{b}")
    async def compute(a: int = 2, *, b: int = 3) -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return a + b

    async def run() -> None:
        assert await compute() == 5
        assert await compute(2, b=3) == 5
        assert await compute(a=2) == 5
        assert calls == 1
        assert await compute(4) == 7
        assert calls == 2

    asyncio.run(run())


def test_sync_memoize_binds_defaults(cache: Cache) -> None:
    calls = 0

    @cache.memoize(key="sync-{a}-{b}")
    def compute(a: int = 2, *, b: int = 3) -> int:
        nonlocal calls
        calls += 1
        return a + b

    assert compute() == compute(a=2, b=3) == compute(2) == 5
    assert calls == 1


def test_async_errors_and_cancellation_are_not_cached(cache: Cache) -> None:
    calls = 0

    @cache.memoize(key=0)
    async def compute() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("failed")
        if calls == 2:
            raise asyncio.CancelledError
        return calls

    async def run() -> None:
        with pytest.raises(ValueError, match="failed"):
            await compute()
        with pytest.raises(asyncio.CancelledError):
            await compute()
        assert await compute() == 3
        assert await compute() == 3

    asyncio.run(run())


def test_async_memoize_honors_expiry_and_key_generator(cache: Cache) -> None:
    calls = 0

    @cache.memoize(key_generator=lambda a: a, ttl=timedelta(seconds=-1))
    async def compute(a: int) -> int:
        nonlocal calls
        calls += 1
        return a + calls

    async def run() -> None:
        assert await compute(1) == 2
        assert await compute(1) == 3

    asyncio.run(run())


def test_fixed_and_generated_keys_accept_uninspectable_builtins(cache: Cache) -> None:
    fixed = cache.memoize(key="builtin", package="test")(math.log)
    assert fixed(2) == math.log(2)
    assert fixed(3) == math.log(2)
    generated = cache.memoize(key_generator=lambda value: value, package="test")(
        math.log
    )
    assert generated(2) == math.log(2)
    assert generated(3) == math.log(3)
