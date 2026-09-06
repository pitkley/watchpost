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

"""Check licenses inside the isolated environment prepared by the shell scripts."""

import subprocess
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> int:
    with Path("pyproject.toml").open("rb") as source:
        config = tomllib.load(source)["tool"]["licensecheck"]

    # Licensecheck 2026.0.8 overwrites TOML settings with unset CLI defaults.
    # Pass the policy explicitly so an upgrade cannot silently relax it.
    arguments = []
    for name, value in config.items():
        # The frozen environment already contains every selected group/extra.
        if name in {"groups", "extras"} or not value:
            continue
        arguments.append(f"--{name.replace('_', '-')}")
        if isinstance(value, list):
            arguments.extend(value)
        elif value is not True:
            arguments.append(str(value))

    with TemporaryDirectory(prefix="watchpost-licenses-") as directory:
        requirements = Path(directory) / "requirements.txt"
        # Licensecheck 2026 no longer reads piped stdin implicitly.
        with requirements.open("w") as output:
            subprocess.run(["uv", "pip", "freeze"], stdout=output, check=True)
        return subprocess.call(
            [
                "licensecheck",
                *arguments,
                "--requirements-paths",
                str(requirements),
                "--skip-dependencies",
                "watchpost",
                *sys.argv[1:],
            ]
        )


if __name__ == "__main__":
    sys.exit(main())
