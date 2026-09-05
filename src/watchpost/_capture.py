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

"""Context-local text output capture shared by thread and async checks."""

from __future__ import annotations

import io
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, TextIO, cast

_output = ContextVar[tuple[io.StringIO, io.StringIO] | None](
    "watchpost_output", default=None
)
_lock = threading.RLock()
_active = 0
_streams: tuple[_RoutedStream, _RoutedStream] | None = None


class _RoutedStream:
    def __init__(self, fallback: TextIO, index: int):
        self.fallback = fallback
        self.index = index

    @property
    def target(self) -> TextIO:
        captured = _output.get()
        return captured[self.index] if captured is not None else self.fallback

    def write(self, value: str) -> int:
        return self.target.write(value)

    def flush(self) -> None:
        self.target.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.target, name)


@contextmanager
def capture_output() -> Iterator[tuple[io.StringIO, io.StringIO]]:
    """Route text writes to this context without serializing check execution.

    Other threads/tasks continue writing to the original streams. The shared
    routers are removed when the last capture exits. Code that writes directly
    to saved streams or file descriptors is outside this text-capture contract.
    """
    global _active, _streams
    captured = (io.StringIO(), io.StringIO())
    with _lock:
        if _active == 0:
            _streams = (_RoutedStream(sys.stdout, 0), _RoutedStream(sys.stderr, 1))
            sys.stdout, sys.stderr = cast(tuple[TextIO, TextIO], _streams)
        _active += 1
    token = _output.set(captured)
    try:
        yield captured
    finally:
        _output.reset(token)
        with _lock:
            _active -= 1
            if _active == 0:
                assert _streams is not None
                if sys.stdout is _streams[0]:
                    sys.stdout = _streams[0].fallback
                if sys.stderr is _streams[1]:
                    sys.stderr = _streams[1].fallback
                _streams = None
