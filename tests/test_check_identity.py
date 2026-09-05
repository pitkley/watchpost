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

import pytest

from watchpost import DiskStorage, Environment, InMemoryStorage, Watchpost, check, ok
from watchpost.check import CheckCache
from watchpost.executor import BlockingCheckExecutor


@pytest.mark.parametrize("persistent", [False, True])
def test_generated_checks_have_distinct_stable_results(tmp_path, persistent):
    env = Environment("test")
    calls = []
    storage = DiskStorage(str(tmp_path)) if persistent else InMemoryStorage()

    def make(name, identity=None):
        @check(
            name=name,
            id=identity,
            service_labels={},
            environments=[env],
            cache_for="5m",
        )
        def generated():
            calls.append(name)
            return ok(name)

        return generated

    first, second = make("first"), make("second")
    assert first.name == second.name
    assert first.identity != second.identity
    with BlockingCheckExecutor() as executor:
        app = Watchpost(
            checks=[first, second],
            execution_environment=env,
            executor=executor,
            check_cache_storage=storage,
        )
        app.verify_check_scheduling()
        results = [result for c in app.checks for result in app.run_check(c)]
        assert [r.summary for r in results] == ["first", "second"]
        assert calls == ["first", "second"]
        rebuilt = make("first")
        assert rebuilt.identity == first.identity
        cached = list(app.run_check(rebuilt))
        assert cached[0].summary == "first"
        assert calls == ["first", "second"]


def test_duplicate_identity_requires_explicit_disambiguation():
    env = Environment("test")

    def make(identity=None):
        @check(
            name="same",
            id=identity,
            service_labels={},
            environments=[env],
            cache_for="1m",
        )
        def generated():
            return ok("same")

        return generated

    app = Watchpost(checks=[make(), make()], execution_environment=env)
    with pytest.raises(ExceptionGroup, match="well-configured") as error:
        app.verify_check_scheduling()
    assert isinstance(error.value, ExceptionGroup)
    assert "Duplicate check identity" in str(error.value.exceptions[0])
    Watchpost(
        checks=[make("a"), make("b")], execution_environment=env
    ).verify_check_scheduling()


def test_legacy_cache_keys_are_not_reused():
    env = Environment("test")

    @check(name="service", service_labels={}, environments=[env], cache_for="1m")
    def defined():
        return ok("fresh")

    key = CheckCache._generate_check_cache_key(defined, env)
    assert key.startswith("v2:")
    assert key != f"{defined.name}:{env.name}"
