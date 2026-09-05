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

import importlib
import re
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest

from watchpost.check import Check
from watchpost.discover_checks import DiscoveryError, discover_checks


@pytest.fixture()
def temp_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Create a temporary package structure with multiple modules and checks.

    Layout:
    temp_pkg/
      __init__.py            # re-exports check from a.sub
      a/
        __init__.py
        sub.py               # defines check_a_sub
      b/
        __init__.py
        mod.py               # defines check_b_mod
      bad/
        __init__.py
        mod.py               # raises Exception upon import
    """

    # Adding a random value to the package name ensures that the packages
    # created do not overlap with any other tests, given the modification of the
    # syspath below.
    random_id = uuid.uuid4().hex
    pkg = tmp_path / f"temp_pkg_{random_id}"
    (pkg / "a").mkdir(parents=True)
    (pkg / "b").mkdir(parents=True)
    (pkg / "bad").mkdir(parents=True)

    # Common helper for modules that define a Check
    check_module_src = (
        "from watchpost.check import check\n"
        "from watchpost.environment import Environment\n"
        "env = Environment('e')\n"
        "@check(name='svc_{tag}', service_labels={{}}, environments=[env], cache_for=None)\n"
        "def {name}():\n"
        "    return []\n"
    )

    # a/sub.py defines check_a_sub
    (pkg / "a" / "__init__.py").write_text("")
    (pkg / "a" / "sub.py").write_text(
        check_module_src.format(tag="a_sub", name="check_a_sub")
    )

    # b/mod.py defines check_b_mod
    (pkg / "b" / "__init__.py").write_text("")
    (pkg / "b" / "mod.py").write_text(
        check_module_src.format(tag="b_mod", name="check_b_mod")
    )

    # bad/mod.py raises during import
    (pkg / "bad" / "__init__.py").write_text("")
    (pkg / "bad" / "mod.py").write_text("raise RuntimeError('boom')\n")

    # Package __init__ re-exports the a.sub.check_a_sub (to test deduplication)
    (pkg / "__init__.py").write_text(
        "from .a.sub import check_a_sub as reexported_check\n"
    )

    # Make tmp package importable
    monkeypatch.syspath_prepend(str(tmp_path))

    # Import and return the package module for convenience
    mod = importlib.import_module(f"temp_pkg_{random_id}")
    return mod


def test_basic_discovery_from_module_object(temp_pkg):
    checks = discover_checks(temp_pkg, recursive=False)
    # Should find only the reexported check in the root module when not recursive
    names = sorted(c.service_name for c in checks)
    assert names == ["svc_a_sub"]


def test_recursive_discovery_across_subpackages_and_dedup(temp_pkg):
    checks = discover_checks(temp_pkg, recursive=True)
    # Expect to find a.sub.check_a_sub and b.mod.check_b_mod, but not duplicate from re-export
    names = sorted(c.service_name for c in checks)
    assert names == ["svc_a_sub", "svc_b_mod"]


def test_include_module_predicate_preserves_descendant_traversal(temp_pkg):
    # Both the root and intermediate package are omitted from scanning.
    checks = discover_checks(
        temp_pkg,
        recursive=True,
        include_module=lambda m: m.__name__ == f"{temp_pkg.__name__}.b.mod",
    )
    names = sorted(c.service_name for c in checks)
    assert names == ["svc_b_mod"]


def test_exclude_module_predicate_skips_modules(temp_pkg):
    # Exclude modules under the 'a' package; note the root module still re-exports a_sub
    excluded_package = f"{temp_pkg.__name__}.a"
    scanned_modules: list[str] = []

    def record_check(_check: Check, module: ModuleType, _name: str) -> bool:
        scanned_modules.append(module.__name__)
        return True

    checks = discover_checks(
        temp_pkg,
        recursive=True,
        exclude_module=lambda m: (
            m.__name__ == excluded_package
            or m.__name__.startswith(f"{excluded_package}.")
        ),
        check_filter=record_check,
    )
    names = sorted(c.service_name for c in checks)
    assert names == ["svc_a_sub", "svc_b_mod"]
    assert scanned_modules == [temp_pkg.__name__, f"{temp_pkg.__name__}.b.mod"]


def test_reexport_rejected_by_filter_does_not_hide_original_check(temp_pkg):
    checks = discover_checks(
        temp_pkg,
        check_filter=lambda _check, module, _name: module is not temp_pkg,
    )
    assert sorted(c.service_name for c in checks) == ["svc_a_sub", "svc_b_mod"]
    assert sum(check is temp_pkg.reexported_check for check in checks) == 1


def test_check_filter_can_filter_specific_checks(temp_pkg):
    # Only keep checks whose service_name ends with 'a_sub'
    checks = discover_checks(
        temp_pkg,
        recursive=True,
        check_filter=lambda check, _module, _name: check.service_name.endswith("a_sub"),
    )
    names = sorted(c.service_name for c in checks)
    assert names == ["svc_a_sub"]


def test_raise_on_import_error_false_skips_bad_modules(temp_pkg):
    # With default raise_on_import_error=False, the bad module is skipped
    checks = discover_checks(temp_pkg, recursive=True)
    names = sorted(c.service_name for c in checks)
    assert names == ["svc_a_sub", "svc_b_mod"]


def test_raise_on_import_error_true_raises(temp_pkg):
    with pytest.raises(DiscoveryError) as exc:
        discover_checks(temp_pkg, recursive=True, raise_on_import_error=True)
    assert "Failed to import" in str(exc.value)


def test_accepts_module_as_string_and_object(temp_pkg):
    # As string
    checks_str = discover_checks(temp_pkg.__name__, recursive=False)
    # As module object
    checks_mod = discover_checks(temp_pkg, recursive=False)
    assert {c.service_name for c in checks_str} == {c.service_name for c in checks_mod}


@pytest.fixture()
def traversal_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """A package whose import side effects expose traversal and retry behavior."""
    package = tmp_path / f"traversal_pkg_{uuid.uuid4().hex}"
    (package / "skipped").mkdir(parents=True)
    (package / "__init__.py").write_text("import_attempts = []\n")
    (package / "skipped" / "__init__.py").write_text(
        "from .. import import_attempts\nimport_attempts.append('skipped')\n"
    )
    (package / "skipped" / "child.py").write_text(
        "from .. import import_attempts\n"
        "import_attempts.append('skipped.child')\n"
        "raise RuntimeError('excluded descendant was imported')\n"
    )
    # This sibling sorts after the failing package to verify traversal continues.
    (package / "z_healthy.py").write_text(
        "from . import import_attempts\n"
        "from watchpost.check import check\n"
        "from watchpost.environment import Environment\n"
        "import_attempts.append('healthy')\n"
        "@check(name='healthy', service_labels={}, environments=[Environment('e')], cache_for=None)\n"
        "def healthy_check():\n"
        "    return []\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    return importlib.import_module(package.name)


@pytest.mark.parametrize("exception_type", [RuntimeError, ImportError])
@pytest.mark.parametrize("strict", [False, True])
def test_broken_package_initializer_obeys_import_error_policy(
    traversal_pkg: ModuleType,
    exception_type: type[Exception],
    strict: bool,
) -> None:
    assert traversal_pkg.__file__ is not None
    initializer = Path(traversal_pkg.__file__).parent / "skipped" / "__init__.py"
    initializer.write_text(
        "from .. import import_attempts\n"
        "import_attempts.append('skipped')\n"
        f"raise {exception_type.__name__}('initializer failed')\n"
    )

    if strict:
        with pytest.raises(
            DiscoveryError,
            match=re.escape(f"Failed to import {traversal_pkg.__name__}.skipped"),
        ) as exc:
            discover_checks(traversal_pkg, raise_on_import_error=True)
        assert isinstance(exc.value.__cause__, exception_type)
        assert str(exc.value.__cause__) == "initializer failed"
        assert getattr(traversal_pkg, "import_attempts") == ["skipped"]
    else:
        checks = discover_checks(traversal_pkg, raise_on_import_error=False)
        assert [check.service_name for check in checks] == ["healthy"]
        assert getattr(traversal_pkg, "import_attempts") == ["skipped", "healthy"]

    assert f"{traversal_pkg.__name__}.skipped.child" not in sys.modules


def test_exclude_module_prunes_package_descendants(traversal_pkg: ModuleType) -> None:
    checks = discover_checks(
        traversal_pkg,
        exclude_module=lambda module: (
            module.__name__ == f"{traversal_pkg.__name__}.skipped"
        ),
        raise_on_import_error=True,
    )
    assert [check.service_name for check in checks] == ["healthy"]
    assert getattr(traversal_pkg, "import_attempts") == ["skipped", "healthy"]
    assert f"{traversal_pkg.__name__}.skipped.child" not in sys.modules


def test_exclude_module_prunes_even_when_include_rejects_package(
    traversal_pkg: ModuleType,
) -> None:
    checks = discover_checks(
        traversal_pkg,
        include_module=lambda module: (
            module.__name__ == f"{traversal_pkg.__name__}.z_healthy"
        ),
        exclude_module=lambda module: (
            module.__name__ == f"{traversal_pkg.__name__}.skipped"
        ),
        raise_on_import_error=True,
    )
    assert [check.service_name for check in checks] == ["healthy"]
    assert getattr(traversal_pkg, "import_attempts") == ["skipped", "healthy"]


def test_exclude_root_module_prunes_entire_tree(traversal_pkg: ModuleType) -> None:
    assert (
        discover_checks(
            traversal_pkg,
            exclude_module=lambda module: module is traversal_pkg,
            raise_on_import_error=True,
        )
        == []
    )
    assert getattr(traversal_pkg, "import_attempts") == []


def test_exclude_module_name_prevents_package_initializer_import(
    traversal_pkg: ModuleType,
) -> None:
    assert traversal_pkg.__file__ is not None
    initializer = Path(traversal_pkg.__file__).parent / "skipped" / "__init__.py"
    initializer.write_text(
        "from .. import import_attempts\n"
        "import_attempts.append('skipped')\n"
        "raise RuntimeError('excluded initializer was imported')\n"
    )
    excluded_name = f"{traversal_pkg.__name__}.skipped"
    checks = discover_checks(
        traversal_pkg,
        exclude_module_name=lambda name: name == excluded_name,
        raise_on_import_error=True,
    )
    assert [check.service_name for check in checks] == ["healthy"]
    assert getattr(traversal_pkg, "import_attempts") == ["healthy"]
    assert excluded_name not in sys.modules
    assert f"{excluded_name}.child" not in sys.modules


def test_exclude_root_module_name_prevents_initial_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / f"excluded_pkg_{uuid.uuid4().hex}"
    package.mkdir()
    (package / "__init__.py").write_text(
        "raise RuntimeError('excluded root was imported')\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert (
        discover_checks(
            package.name,
            exclude_module_name=lambda name: name == package.name,
            raise_on_import_error=True,
        )
        == []
    )
    assert package.name not in sys.modules


def test_exclude_root_module_name_also_prunes_module_objects(
    traversal_pkg: ModuleType,
) -> None:
    assert (
        discover_checks(
            traversal_pkg,
            exclude_module_name=lambda name: name == traversal_pkg.__name__,
            raise_on_import_error=True,
        )
        == []
    )
    assert getattr(traversal_pkg, "import_attempts") == []
