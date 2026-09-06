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
Utility helpers for Watchpost.
"""

import inspect
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from timelength import TimeLength

import watchpost


@dataclass
class InvocationInformation:
    """
    Call-site information for an invocation.

    Holds the relative path of the module and the line number where the
    invocation occurred.
    """

    relative_path: str
    line_number: int

    def __hash__(self) -> int:
        return hash(
            (
                self.relative_path,
                self.line_number,
            )
        )


def get_invocation_information() -> InvocationInformation | None:
    """
    Return call-site information for the caller's caller.

    Inspects the call stack to locate the module and line number of the function
    that invoked the caller. The module path is made relative to the project
    root inferred from the watchpost package. If any information cannot be
    determined, returns None.

    Returns:
        InvocationInformation with the relative path and line number, or None if
        it cannot be determined.
    """

    # Get the frame of the function that called the function that called this
    current_frame = inspect.currentframe()
    if not current_frame or not current_frame.f_back or not current_frame.f_back.f_back:
        return None
    relevant_frame = current_frame.f_back.f_back

    relevant_module = inspect.getmodule(relevant_frame)
    if not relevant_module:
        return None

    if not watchpost.__file__ or not relevant_module.__file__:
        return None

    # FIXME: determining the root_directory will need rework. The Watchpost
    #        package might not be relative to the user's project root at all,
    #        always resulting in an absolute path. We should either just go for
    #        the absolute path here regardless, or allow the user to provide a
    #        root directory to calculate the relative path from.
    root_directory = Path(watchpost.__file__).parent.parent.parent

    relevant_module_path = Path(relevant_module.__file__)

    try:
        relative_path = relevant_module_path.relative_to(root_directory)
    except ValueError:
        return None

    return InvocationInformation(
        relative_path=str(relative_path),
        line_number=relevant_frame.f_lineno,
    )


def normalize_to_timedelta(value: timedelta | str | None) -> timedelta | None:
    """
    Normalize a value to a datetime.timedelta.

    - If value is None, return None.
    - If value is already a timedelta, return it unchanged.
    - If value is a string, parse it with timelength (e.g., "5m", "2h30m") and
      return the corresponding timedelta.

    Parameters:
        value:
            A timedelta, a string time expression, or None.

    Returns:
        A timedelta corresponding to the input, or None.
    """

    if value is None:
        return None
    if isinstance(value, timedelta):
        return value

    return timedelta(seconds=TimeLength(value).result.seconds)
