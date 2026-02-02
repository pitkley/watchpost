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

from watchpost.result import CheckState, build_result, crit


def test_no_results_is_ok():
    r = build_result("OK", "FAIL")
    check_result = r.to_check_result()
    assert check_result.check_state == CheckState.OK
    assert check_result.summary == "OK"
    assert check_result.details is None


def test_correct_summary_is_used():
    r = build_result(
        ok_summary="OK",
        fail_summary="FAIL",
    )

    r.ok("OK - 1")
    assert r.to_check_result().summary == "OK"
    r.crit("CRIT")
    assert r.to_check_result().summary == "FAIL"
    r.ok("OK - 2")

    check_result = r.to_check_result()
    assert check_result.check_state == CheckState.CRIT
    assert check_result.summary == "FAIL"
    assert check_result.details == "OK - 1\n\nCRIT\n\nOK - 2"


def test_crit_trumps_unknown():
    r = build_result("OK", "FAIL")

    r.unknown("UNKNOWN")
    assert r.to_check_result().check_state == CheckState.UNKNOWN
    r.crit("CRIT")
    assert r.to_check_result().check_state == CheckState.CRIT
    r.unknown("UNKNOWN")
    assert r.to_check_result().check_state == CheckState.CRIT


def test_check_results_can_be_used():
    r = build_result("OK", "FAIL")

    assert r.to_check_result().check_state == CheckState.OK
    r.add_check_result(crit("CRIT"))
    assert r.to_check_result().check_state == CheckState.CRIT


def test_details_are_included():
    r = build_result("OK", "FAIL")

    r.ok("OK Summary", "OK Details")
    check_result = r.to_check_result()
    assert check_result.check_state == CheckState.OK
    assert check_result.summary == "OK"
    assert check_result.details == "OK Summary (.):\nOK Details"

    r.crit("CRIT Summary", "CRIT Details")
    check_result = r.to_check_result()
    assert check_result.check_state == CheckState.CRIT
    assert check_result.summary == "FAIL"
    assert (
        check_result.details
        == "OK Summary (.):\nOK Details\n\nCRIT Summary (!!):\nCRIT Details"
    )


def test_complex_details_are_supported():
    r = build_result(
        ok_summary="OK",
        fail_summary="FAIL",
        base_details={"key": "value"},
    )

    check_result = r.to_check_result()
    assert check_result.check_state == CheckState.OK
    assert check_result.details == "key: value"

    r.ok("OK - 1", details={"subkey1": "subvalue1"})
    check_result = r.to_check_result()
    assert check_result.check_state == CheckState.OK
    assert check_result.details == "key: value\n\nOK - 1 (.):\nsubkey1: subvalue1"

    try:
        raise ValueError("test error in details")
    except ValueError as e:
        r.crit("CRIT", details=e)
    check_result = r.to_check_result()
    assert check_result.check_state == CheckState.CRIT
    assert check_result.details
    assert "Traceback" in check_result.details
    assert "ValueError" in check_result.details
    assert "test error in details" in check_result.details
