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

from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from watchpost import (
    Datasource,
    DatasourceUnavailable,
    Environment,
    Watchpost,
    check,
    ok,
)
from watchpost.check import expand_by_name_suffix
from watchpost.executor import BlockingCheckExecutor
from watchpost.result import CheckState, ExecutionResult

from .utils import decode_checkmk_output


class FailingDatasource(Datasource):
    scheduling_strategies = ()

    def __init__(self, error_type):
        raise error_type("offline")


@pytest.mark.parametrize(
    "error_type,state", [(DatasourceUnavailable, "UNKNOWN"), (RuntimeError, "CRIT")]
)
@pytest.mark.parametrize("asynchronous", [False, True])
def test_constructor_failure_does_not_abort_http_stream(
    error_type, state, asynchronous
):
    env = Environment("test")

    def sync(_ds: FailingDatasource):
        raise AssertionError("check must not be called")

    async def async_check(_ds: FailingDatasource):
        raise AssertionError("check must not be called")

    broken = check(
        name="broken",
        service_labels={},
        environments=[env],
        cache_for=None,
        error_handlers=[expand_by_name_suffix(["-a", "-b"])],
    )(async_check if asynchronous else sync)

    @check(name="healthy", service_labels={}, environments=[env], cache_for=None)
    def healthy():
        return ok("healthy")

    with BlockingCheckExecutor() as executor:
        app = Watchpost(
            checks=[broken, healthy], execution_environment=env, executor=executor
        )
        app.register_datasource(FailingDatasource, error_type=error_type)
        with TestClient(app) as client:
            response = client.get("/")
        assert response.status_code == 200
        results = {
            r["service_name"]: r for r in decode_checkmk_output(response.content)
        }
        assert results["healthy"]["check_state"] == "OK"
        for name in ("broken-a", "broken-b"):
            assert results[name]["check_state"] == state
            assert results[name]["summary"] == "offline"


def test_unavailable_constructor_uses_cached_copy_without_mutating_prior_result():
    env = Environment("test")

    @check(name="cached", service_labels={}, environments=[env], cache_for="5m")
    def cached(_ds: FailingDatasource):
        raise AssertionError("check must not be called")

    prior = ExecutionResult(
        "host", "cached", {}, "test", CheckState.OK, "prior", "original details"
    )
    app = Watchpost(checks=[cached], execution_environment=env)
    app.register_datasource(FailingDatasource, error_type=DatasourceUnavailable)
    app._check_cache.store_check_results(
        cached, env, [prior], override_cache_for=timedelta(0)
    )
    results = list(app.run_check(cached))
    assert results[0].summary == "prior"
    assert "offline" in results[0].details
    assert prior.details == "original details"
    app.shutdown()
