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

from watchpost import (
    CheckResult,
    Environment,
    check,
    current_app,
    ok,
)

from .. import ENVIRONMENT_TEST


@check(
    name="discovered dummy",
    service_labels={"foo": "bar"},
    environments=[ENVIRONMENT_TEST],
    cache_for=None,
)
def dummy_check_function(environment: Environment) -> CheckResult:
    print("This is a running check.")
    print(f"Current app: {current_app}")
    print(f"Environment: {environment}")
    return ok("This is a check result.")
