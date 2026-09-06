# Copyright 2026 Pit Kleyersburg
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

"""Executor return types checked by mypy/ty; run directly to verify submission."""

from concurrent.futures import Future
from typing import assert_type

from watchpost.executor import CheckExecutor


def combine(value: int, *, offset: int) -> int:
    return value + offset


async def async_combine(value: int, *, offset: int) -> int:
    return value + offset


def main() -> None:
    with CheckExecutor[int]() as executor:
        sync = assert_type(executor.submit("sync", combine, 3, offset=4), Future[int])
        async_ = assert_type(
            executor.submit("async", async_combine, 5, offset=6), Future[int]
        )
        repeated = assert_type(
            executor.submit("sync", combine, 7, offset=8, resubmit=True), Future[int]
        )
        assert sync.result(timeout=5) == 7
        assert async_.result(timeout=5) == 11
        assert repeated.result(timeout=5) == 15


if __name__ == "__main__":
    main()
