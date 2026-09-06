#!/bin/bash
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

set -euo pipefail

# Enumerate packages and inspect their metadata in the same clean environment.
# A separate licensecheck process in the caller's environment may otherwise
# combine local metadata with PyPI fallbacks and produce a different report.
exec uv run \
  --locked \
  --isolated \
  --all-extras \
  --all-groups \
  bash -o pipefail -c \
    'uv pip freeze | licensecheck --format markdown --skip-dependencies watchpost' \
  | grep -vF 'Size:' \
  ;
