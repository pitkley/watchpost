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

import hashlib
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from ..app import Watchpost


class AppNotFound(Exception):
    """Custom exception for when the app cannot be found."""


def find_app(app_str: str | None) -> Watchpost:
    """
    Finds and loads the Watchpost app instance.

    The search order is:
    1. The `app_str` argument if provided (e.g., 'my_module:app').
    2. Convention: look for a `Watchpost` instance named `app` in `watchpost.py`,
       then `app.py`, then `main.py` in the current directory.

    A local `watchpost.py` uses an isolated module name so it can import the
    installed framework. Explicit `watchpost:app` uses the same local-file
    behavior, loaded once per absolute path. Other modules retain their normal
    import names so sibling modules can import application configuration.
    """
    if app_str:
        return _load_from_string(app_str)

    return _load_from_convention()


def _load_application_file(path: Path) -> ModuleType:
    """Load watchpost.py without replacing the framework's module."""
    path = path.resolve()
    name = "_watchpost_application_" + hashlib.sha256(str(path).encode()).hexdigest()
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AppNotFound(f"Could not load application file '{path}'.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _load_from_string(app_str: str) -> Watchpost:
    """Loads an app from a string like 'module:variable'."""
    if ":" not in app_str:
        raise AppNotFound(
            f"Invalid app string '{app_str}'. Expected format 'module:variable'."
        )

    module_str, app_instance_str = app_str.split(":", 1)

    # Add current working directory to path to allow local imports
    sys.path.insert(0, os.getcwd())
    try:
        local_path = Path(module_str + ".py")
        if module_str == "watchpost" and local_path.is_file():
            module = _load_application_file(local_path)
        else:
            module = importlib.import_module(module_str)
        app = getattr(module, app_instance_str)
    except (ModuleNotFoundError, AttributeError) as e:
        raise AppNotFound(f"Could not import app '{app_str}'. Error: {e}") from e
    finally:
        sys.path.pop(0)

    if not isinstance(app, Watchpost):
        raise AppNotFound(
            f"The object '{app_instance_str}' in '{module_str}' is not an Watchpost instance."
        )
    return app


def _load_from_convention() -> Watchpost:
    """Tries to find the app by convention."""
    for filename in ("watchpost.py", "app.py", "main.py"):
        if os.path.exists(filename):
            module_name = filename[:-3]
            try:
                # Load from a file path
                return _load_from_string(f"{module_name}:app")
            except AppNotFound:
                continue
    raise AppNotFound(
        "Could not find an Watchpost app. Either provide the app location with "
        "--app <module:instance> or the WATCHPOST_APP environment variable."
    )
