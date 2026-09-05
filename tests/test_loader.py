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

import sys
from pathlib import Path

import pytest

import watchpost
from watchpost.cli.loader import AppNotFound, find_app


@pytest.mark.parametrize("filename", ["watchpost.py", "app.py", "main.py"])
@pytest.mark.parametrize("explicit", [False, True])
def test_conventional_files_load_without_framework_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    explicit: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    if filename != "watchpost.py":
        monkeypatch.delitem(sys.modules, filename[:-3], raising=False)
    (tmp_path / filename).write_text(
        "from watchpost import Watchpost, Environment\n"
        "app = Watchpost(checks=[], execution_environment=Environment('local'))\n"
    )
    app = find_app(f"{filename[:-3]}:app" if explicit else None)
    try:
        assert isinstance(app, watchpost.Watchpost)
        assert app.execution_environment.name == "local"
        assert find_app(f"{filename[:-3]}:app") is app
        assert sys.modules["watchpost"] is watchpost
    finally:
        app.shutdown()


def test_convention_is_isolated_between_working_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apps = []
    try:
        for name in ["first", "second"]:
            directory = tmp_path / name
            directory.mkdir()
            (directory / "watchpost.py").write_text(
                "from watchpost import Watchpost, Environment\n"
                f"app = Watchpost(checks=[], execution_environment=Environment({name!r}))\n"
            )
            monkeypatch.chdir(directory)
            apps.append(find_app(None))
            assert apps[-1].execution_environment.name == name
        assert apps[0] is not apps[1]
    finally:
        for app in apps:
            app.shutdown()


def test_failed_file_import_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "watchpost.py"
    original_path = sys.path.copy()
    path.write_text("raise RuntimeError('initializer failed')\n")
    with pytest.raises(RuntimeError, match="initializer failed"):
        find_app("watchpost:app")
    assert sys.path == original_path
    path.write_text("app = 'not an application'\n")
    with pytest.raises(AppNotFound, match="Watchpost instance"):
        find_app("watchpost:app")
    assert sys.modules["watchpost"] is watchpost


@pytest.mark.parametrize("module_name", ["app", "main"])
def test_conventional_modules_keep_canonical_import_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    monkeypatch.delitem(sys.modules, "local_checks", raising=False)
    (tmp_path / f"{module_name}.py").write_text(
        "from watchpost import Watchpost, Environment\n"
        "environment = Environment('canonical')\n"
        "from local_checks import probe\n"
        "app = Watchpost(checks=[probe], execution_environment=environment)\n"
    )
    (tmp_path / "local_checks.py").write_text(
        f"from {module_name} import environment\n"
        "from watchpost import check, ok\n"
        "@check(name='probe', environments=[environment], service_labels={}, cache_for=None)\n"
        "def probe(): return ok('canonical')\n"
    )
    app = find_app(f"{module_name}:app")
    try:
        app.verify_check_scheduling()
        assert app.checks[0].environments[0] is app.execution_environment
        assert sys.modules[module_name].__name__ == module_name
    finally:
        app.shutdown()
