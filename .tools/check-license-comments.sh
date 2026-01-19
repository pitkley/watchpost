#!/bin/bash
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

# Pattern for regular files
PATTERN_COPYRIGHT='^(//|#) Copyright [[:digit:]]+ .+'
PATTERN_SPDX='^(//|#) SPDX-License-Identifier: Apache-2.0'

# Pattern for vendored files
PATTERN_ANY_COPYRIGHT='^(//|#) Copyright'
PATTERN_VENDORED_SPDX='^(//|#) SPDX-License-Identifier: (Apache-2.0|BSD-2-Clause|BSD-3-Clause|MIT|MPL-2.0)'

ERRORS=0

# Check regular files
while read -r -d $'\0' file
do
  if [[ "$file" == src/watchpost/vendored/* ]]; then
    # Skip vendored files, they will be checked separately
    continue
  fi
  if [[ "$file" == docs/.hooks/hide_lines.py ]]; then
    # Skip vendored files, they will be checked separately
    continue
  fi

  if ! grep -qE "${PATTERN_COPYRIGHT}" "$file"; then
    echo "$file: missing/malformed copyright-notice"
    ERRORS=$((ERRORS + 1))
  fi
  if ! grep -qE "${PATTERN_SPDX}" "$file"; then
    echo "$file: missing/malformed SPDX license identifier"
    ERRORS=$((ERRORS + 1))
  fi
done < <(\
  git ls-files -z -- \
    '*.py' \
    '*.sh' \
)

# Check vendored files
while read -r -d $'\0' file
do
  if ! grep -qE "${PATTERN_ANY_COPYRIGHT}" "$file"; then
    echo "$file: missing copyright-notice"
    ERRORS=$((ERRORS + 1))
  fi
  if ! grep -qE "${PATTERN_VENDORED_SPDX}" "$file"; then
    echo "$file: missing/malformed SPDX license identifier (must be one of: Apache-2.0, BSD-2-Clause, BSD-3-Clause, MIT, MPL-2.0)"
    ERRORS=$((ERRORS + 1))
  fi
done < <(\
  git ls-files -z -- \
    'src/watchpost/vendored/*.py' \
    'src/watchpost/vendored/*.sh' \
    'docs/.hooks/hide_lines.py' \
)

if [[ "$ERRORS" -gt 0 ]]; then
  exit 1
fi
