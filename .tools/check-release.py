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

"""Validate a release from the repository root; emit GitHub Actions metadata."""

import argparse
import re
import sys
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Release tag, e.g. v0.2.0 or v0.2.0rc1")
    args = parser.parse_args()
    number = r"(?:0|[1-9][0-9]*)"
    match = re.fullmatch(
        rf"v{number}\.{number}\.{number}(?P<prerelease>(?:a|b|rc){number})?",
        args.tag,
    )
    if match is None:
        parser.error("use vX.Y.Z or vX.Y.ZrcN (aN and bN are also supported)")

    try:
        with Path("pyproject.toml").open("rb") as source:
            version = tomllib.load(source)["project"]["version"]
    except (OSError, tomllib.TOMLDecodeError, KeyError) as error:
        parser.error(f"cannot read project.version from pyproject.toml: {error}")

    if args.tag != f"v{version}":
        parser.error(f"tag {args.tag!r} does not match project version {version!r}")

    prerelease = "true" if match.group("prerelease") else "false"
    sys.stdout.write(f"version={version}\nprerelease={prerelease}\n")


if __name__ == "__main__":
    main()
