#!/bin/bash
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

set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTRACT_DIRECTORY="$(mktemp -d "${TMPDIR:-/tmp}/watchpost-checkmk-contract.XXXXXX")"
CONTAINER_NAME="watchpost-contract-$(date +%s)-$$"
CONTAINER_CREATED=false

cleanup() {
  status=$?
  trap - EXIT
  if [ "$CONTAINER_CREATED" = true ]; then
    if [ "$status" -ne 0 ]; then
      docker logs "$CONTAINER_NAME" >&2 || true
      if [ -f "$CONTRACT_DIRECTORY/readiness.log" ]; then
        cat "$CONTRACT_DIRECTORY/readiness.log" >&2
      fi
    fi
    docker rm --force --volumes "$CONTAINER_NAME" >/dev/null || true
  fi
  rm -rf "$CONTRACT_DIRECTORY"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

cd "$REPOSITORY_ROOT"
uv run --isolated --no-dev --frozen python .tools/checkmk_contract_smoke.py \
  generate "$CONTRACT_DIRECTORY/agent-output.txt"
# The site user inside the container needs to read the generated fixture.
chmod 755 "$CONTRACT_DIRECTORY"
chmod 644 "$CONTRACT_DIRECTORY/agent-output.txt"

# Set WATCHPOST_CHECKMK_IMAGE to reuse an image built from the current checkout.
if [ -n "${WATCHPOST_CHECKMK_IMAGE:-}" ]; then
  CONTRACT_IMAGE="$WATCHPOST_CHECKMK_IMAGE"
  docker image inspect "$CONTRACT_IMAGE" >/dev/null
else
  CONTRACT_IMAGE="watchpost-checkmk-contract:local"
  docker build --platform linux/amd64 \
    --file checkmk-integration/Dockerfile.checkmk \
    --tag "$CONTRACT_IMAGE" checkmk-integration
fi

docker create --platform linux/amd64 \
  --name "$CONTAINER_NAME" \
  --env CMK_SITE_ID=cmk \
  --env CMK_PASSWORD=watchpost-disposable-contract-test \
  --tmpfs /opt/omd/sites/cmk/tmp:uid=1000,gid=1000 \
  --mount "type=bind,source=$REPOSITORY_ROOT/.tools,target=/contract-tools,readonly" \
  --mount "type=bind,source=$CONTRACT_DIRECTORY,target=/contract-fixture,readonly" \
  "$CONTRACT_IMAGE" >/dev/null
CONTAINER_CREATED=true
docker start "$CONTAINER_NAME" >/dev/null
echo "Waiting for the disposable Checkmk site to start."

# The initial site setup takes longer under AMD64 emulation on ARM64 hosts.
READY_DEADLINE=$((SECONDS + 300))
until docker exec "$CONTAINER_NAME" timeout 20 \
  omd status cmk >"$CONTRACT_DIRECTORY/readiness.log" 2>&1; do
  if [ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" != true ]; then
    echo "Checkmk exited before becoming ready." >&2
    exit 1
  fi
  if [ "$SECONDS" -ge "$READY_DEADLINE" ]; then
    echo "Checkmk did not become ready within 300 seconds." >&2
    exit 1
  fi
  sleep 2
done

docker exec "$CONTAINER_NAME" timeout 120 su - cmk -c \
  'cmk -L' >"$CONTRACT_DIRECTORY/plugins.txt"
if ! awk '$1 == "watchpost" { found = 1 } END { exit !found }' \
  "$CONTRACT_DIRECTORY/plugins.txt"; then
  echo "The built image did not register the Watchpost check plugin." >&2
  cat "$CONTRACT_DIRECTORY/plugins.txt" >&2
  exit 1
fi

docker exec "$CONTAINER_NAME" timeout 120 su - cmk -c \
  'python3 /contract-tools/checkmk_contract_smoke.py check /contract-fixture/agent-output.txt'
echo "Checkmk plugin registration and producer/plugin contract verified."
