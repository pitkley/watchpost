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

from typing import Annotated

from watchpost.app import Watchpost
from watchpost.check import check
from watchpost.datasource import Datasource, DatasourceFactory, FromFactory
from watchpost.environment import Environment
from watchpost.executor import BlockingCheckExecutor
from watchpost.globals import current_app
from watchpost.result import ok
from watchpost.scheduling_strategy import MustRunInTargetEnvironmentStrategy

from .utils import decode_checkmk_output

ENVIRONMENT1 = Environment(name="env1")
ENVIRONMENT2 = Environment(name="env2")


class Env1Datasource(Datasource, DatasourceFactory):
    scheduling_strategies = (MustRunInTargetEnvironmentStrategy(),)

    def __init__(self):
        if current_app.execution_environment != ENVIRONMENT1:
            raise RuntimeError("Cannot instantiate this class outside of env1.")

    @classmethod
    def new(cls) -> Env1Datasource:
        if current_app.execution_environment != ENVIRONMENT1:
            raise RuntimeError("Cannot create this class outside of env1.")
        return cls()


class UninstantiableDatasource(Datasource, DatasourceFactory):
    scheduling_strategies = (MustRunInTargetEnvironmentStrategy(),)

    def __init__(self):
        raise RuntimeError("Cannot instantiate this class.")

    @classmethod
    def new(cls) -> UninstantiableDatasource:
        return cls()


def test_datasources_are_not_eagerly_instantiated():
    @check(
        name="uninvokable",
        service_labels={},
        environments=[ENVIRONMENT1],
        cache_for=None,
    )
    def uninvokable(_: UninstantiableDatasource):
        raise RuntimeError("Check should never be invoked")

    @check(
        name="uninvokable-factory",
        service_labels={},
        environments=[ENVIRONMENT1],
        cache_for=None,
    )
    def uninvokable_factory(_: Annotated[UninstantiableDatasource, FromFactory()]):
        raise RuntimeError("Check should never be invoked")

    app = Watchpost(
        checks=[
            uninvokable,
            uninvokable_factory,
        ],
        execution_environment=ENVIRONMENT1,
        executor=BlockingCheckExecutor(),
    )
    app.register_datasource(UninstantiableDatasource)
    app.register_datasource_factory(UninstantiableDatasource)

    # Check scheduling verification should work without issues because the
    # datasources are not being instantiated.
    app.verify_check_scheduling()

    # Construction failures become per-check results, including factory failures.
    results = decode_checkmk_output(b"".join(app.run_checks()))
    failures = [r for r in results if r["service_name"].startswith("uninvokable")]
    assert failures
    assert all(r["check_state"] == "CRIT" for r in failures)
    assert all(r["summary"] == "Cannot instantiate this class." for r in failures)


def test_uninstantiable_datasource_does_not_affect_other_checks():
    @check(
        name="uninvokable",
        service_labels={},
        environments=[ENVIRONMENT2],
        cache_for=None,
    )
    def uninvokable(_: UninstantiableDatasource):
        raise RuntimeError("Check should never be invoked")

    @check(
        name="env1",
        service_labels={},
        environments=[ENVIRONMENT1],
        cache_for=None,
    )
    def env1(ds: Env1Datasource):
        return ok(f"{type(ds)}")

    app = Watchpost(
        checks=[
            uninvokable,
            env1,
        ],
        execution_environment=ENVIRONMENT1,
        executor=BlockingCheckExecutor(),
    )

    app.register_datasource(Env1Datasource)
    app.register_datasource_factory(Env1Datasource)
    app.register_datasource(UninstantiableDatasource)
    app.register_datasource_factory(UninstantiableDatasource)

    # Check scheduling verification should work without issues because the
    # datasources are not being instantiated.
    app.verify_check_scheduling()

    # Check execution should work as well, because the uninvokable check is not
    # schedulable for env1.
    checkmk_output = decode_checkmk_output(b"".join(app.run_checks()))
    for item in checkmk_output:
        item.pop("check_definition", None)

    assert len(checkmk_output) == 2
    assert sorted(checkmk_output, key=lambda result: result["service_name"]) == sorted(
        [
            {
                "service_name": "Watchpost: executed checks",
                "service_labels": {},
                "environment": "env1",
                "check_state": "OK",
                "summary": "1 check/environment pairs eligible to run",
                "metrics": [],
                "details": "Eligible check/environment pairs:\n- tests.test_datasource_instantiation.test_uninstantiable_datasource_does_not_affect_other_checks.<locals>.env1 [env1]",
            },
            {
                "service_name": "env1",
                "service_labels": {},
                "environment": "env1",
                "check_state": "OK",
                "summary": "<class 'tests.test_datasource_instantiation.Env1Datasource'>",
                "metrics": [],
                "details": None,
            },
        ],
        key=lambda result: result["service_name"],
    )

    # In env2 the failing check emits CRIT without aborting the stream:
    app.execution_environment = ENVIRONMENT2
    results = decode_checkmk_output(b"".join(app.run_checks()))
    failures = [r for r in results if r["service_name"].startswith("uninvokable")]
    assert failures
    assert all(r["check_state"] == "CRIT" for r in failures)
    assert all(r["summary"] == "Cannot instantiate this class." for r in failures)
