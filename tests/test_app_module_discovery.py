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

from __future__ import annotations

import importlib
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from watchpost.app import Watchpost
from watchpost.check import check
from watchpost.environment import Environment
from watchpost.executor import BlockingCheckExecutor

from .utils import decode_checkmk_output


@pytest.fixture()
def temp_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Create a temporary package with nested modules that define checks.

    Layout:
    temp_pkg/
      __init__.py
      a/
        __init__.py
        mod.py   # defines check_a
      b/
        __init__.py
        mod.py   # defines check_b
    """

    # Adding a random value to the package name ensures that the packages
    # created do not overlap with any other tests, given the modification of the
    # syspath below.
    random_id = uuid.uuid4().hex
    pkg = tmp_path / f"temp_pkg_{random_id}"
    (pkg / "a").mkdir(parents=True)
    (pkg / "b").mkdir(parents=True)

    # Common helper for modules that define a Check
    check_module_src = (
        "from watchpost.check import check\n"
        "from watchpost.environment import Environment\n"
        "env = Environment('E')\n"
        "@check(name='{svc}', service_labels={{}}, environments=[env], cache_for=None)\n"
        "def {fn}():\n"
        "    return []\n"
    )

    # a/mod.py defines check_a
    (pkg / "a" / "__init__.py").write_text("")
    (pkg / "a" / "mod.py").write_text(
        check_module_src.format(svc="svc_a", fn="check_a")
    )

    # b/mod.py defines check_b
    (pkg / "b" / "__init__.py").write_text("")
    (pkg / "b" / "mod.py").write_text(
        check_module_src.format(svc="svc_b", fn="check_b")
    )

    # root __init__
    (pkg / "__init__.py").write_text("")

    # Make tmp package importable
    monkeypatch.syspath_prepend(str(tmp_path))

    # Import and return the package module for convenience
    mod = importlib.import_module(f"temp_pkg_{random_id}")
    return mod


def test_watchpost_accepts_module_and_discovers_checks(temp_pkg):
    env = Environment("E")

    app = Watchpost(
        checks=[temp_pkg],
        execution_environment=env,
        executor=BlockingCheckExecutor(),
    )

    names = sorted(c.service_name for c in app.checks)
    assert names == ["svc_a", "svc_b"]


def test_watchpost_mixed_checks_and_module_discovery_and_run_once(temp_pkg):
    env = Environment("E")

    @check(name="svc_direct", service_labels={}, environments=[env], cache_for=None)
    def direct_check():
        """A direct test check that yields no results (normalized to UNKNOWN)."""
        return []

    app = Watchpost(
        checks=[temp_pkg, direct_check],
        execution_environment=env,
        executor=BlockingCheckExecutor(),
    )

    names = sorted(c.service_name for c in app.checks)
    assert names == ["svc_a", "svc_b", "svc_direct"]

    # Capture Checkmk output and ensure the synthetic "Watchpost: executed checks" entry mentions all checks
    with patch("sys.stdout.buffer.write") as mock_write:
        app.run_checks_once()

        all_data = b"".join(call_args[0][0] for call_args in mock_write.call_args_list)
        results = decode_checkmk_output(all_data)

        # Find the synthetic result
        synthetic = next(
            r for r in results if r["service_name"] == "Watchpost: executed checks"
        )
        assert (
            synthetic["summary"]
            == f"{len(app.checks)} check/environment pairs eligible to run"
        )
        # Each discovered check targets E and is eligible to run there.
        details = synthetic["details"] or ""
        assert "Eligible check/environment pairs:\n- " in details
        for chk in app.checks:
            assert f"- {chk.name} [E]" in details
