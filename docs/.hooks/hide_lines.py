# Copyright (c) 2021 Olli Paakkunainen
#
# Licensed under the MIT Apache License.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Retrieved on 2026-01-19 from:
#   https://github.com/ollipa/chainmock/blob/ebc425da3193a1740376693fafed4559da2cc75b/docs/hooks/hide_lines.py
#
# SPDX-License-Identifier: MIT

from typing import Any

import mkdocs.plugins
from pymdownx import highlight


@mkdocs.plugins.event_priority(0)
def on_startup(command: str, dirty: bool) -> None:
    _ = command, dirty
    original = highlight.Highlight.highlight

    def patched(self: Any, src: str, *args: Any, **kwargs: Any) -> Any:
        src = "".join(
            line
            for line in src.splitlines(keepends=True)
            if not line.strip().endswith("#! hidden")
        )
        return original(self, src, *args, **kwargs)

    highlight.Highlight.highlight = patched  # ty: ignore[invalid-assignment]
