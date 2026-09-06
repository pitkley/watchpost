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

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".tools" / "check-release.py"


@pytest.mark.parametrize(
    ("version", "prerelease"),
    [
        ("0.2.0", "false"),
        ("1.0.0", "false"),
        ("12.34.56", "false"),
        ("0.2.0rc1", "true"),
        ("0.2.0rc2", "true"),
        ("0.2.0a1", "true"),
        ("0.2.0b1", "true"),
    ],
)
def test_release_metadata(tmp_path: Path, version: str, prerelease: str) -> None:
    project = tmp_path / "pyproject.toml"
    content = f'[project]\nversion = "{version}"\n'
    project.write_text(content)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), f"v{version}"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == f"version={version}\nprerelease={prerelease}\n"
    assert not result.stderr
    assert project.read_text() == content


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("0.1.5", "v0.2.0rc1"),
        ("0.2.0rc1", "v0.2.0"),
        ("0.2.0", "v0.2.0rc1"),
        ("0.2.0", "0.2.0"),
        ("0.2.0", "main"),
        ("0.2.0rc1", "v0.2.0-rc.1"),
        ("0.2.0", "v0.2"),
        ("0.2.0", "v00.2.0"),
        ("0.2.0rc1", "v0.2.0rc01"),
        ("0.2.0", "v0.2.0+local"),
        ("0.2.0", "v0.2.0\nprerelease=false"),
    ],
)
def test_invalid_release_emits_no_metadata(
    tmp_path: Path, version: str, tag: str
) -> None:
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    result = subprocess.run(
        [sys.executable, str(SCRIPT), tag],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not result.stdout
    assert "error:" in result.stderr


@pytest.mark.parametrize("content", [None, "invalid TOML", "[project]\n"])
def test_missing_release_version_emits_no_metadata(
    tmp_path: Path, content: str | None
) -> None:
    if content is not None:
        (tmp_path / "pyproject.toml").write_text(content)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "v0.2.0"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not result.stdout
    assert "cannot read project.version" in result.stderr
