# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Unit tests for the baseline compare helper (EPMCDME-15651 step 0)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.codemie.rest_api.router_guards.baseline import assert_matches_baseline, canonical_json, load_baseline

REGENERATE_COMMAND = "python -m tests.codemie.rest_api.router_guards.regenerate_baselines"


def _write_baseline(tmp_path: Path, live: object) -> Path:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(canonical_json(live), encoding="utf-8")
    return baseline


def test_canonical_json_has_sorted_keys_two_space_indent_and_trailing_newline():
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{\n  "a": [\n    1,\n    2\n  ],\n  "b": 1\n}\n'


def test_matching_live_object_passes(tmp_path):
    live = {"paths": {"/v1/x": {"get": {"operationId": "x"}}}}

    assert_matches_baseline(live, _write_baseline(tmp_path, live))


def test_changed_value_fails_with_diff_lines_and_regenerate_command(tmp_path):
    baseline = _write_baseline(tmp_path, {"a": 1, "b": 1})

    with pytest.raises(AssertionError) as failure:
        assert_matches_baseline({"a": 1, "b": 2}, baseline)

    message = str(failure.value)
    assert '-  "b": 1' in message
    assert '+  "b": 2' in message
    assert REGENERATE_COMMAND in message


def test_failing_comparison_leaves_the_baseline_bytes_unchanged(tmp_path):
    baseline = _write_baseline(tmp_path, {"a": 1})
    before = baseline.read_bytes()

    with pytest.raises(AssertionError):
        assert_matches_baseline({"a": 2}, baseline)

    assert baseline.read_bytes() == before


def test_missing_baseline_fails_naming_the_file_and_regenerate_command(tmp_path):
    missing = tmp_path / "missing.json"

    with pytest.raises(AssertionError) as failure:
        assert_matches_baseline({"a": 1}, missing)

    message = str(failure.value)
    assert str(missing) in message
    assert REGENERATE_COMMAND in message
    assert not missing.exists()


def test_comparison_is_exact_text_without_normalization(tmp_path):
    baseline = _write_baseline(tmp_path, {"a": 1})
    baseline.write_text(canonical_json({"a": 1}).rstrip("\n"), encoding="utf-8")

    with pytest.raises(AssertionError):
        assert_matches_baseline({"a": 1}, baseline)


def test_large_diff_is_capped_and_says_so(tmp_path):
    baseline = _write_baseline(tmp_path, {f"key{i:04d}": 0 for i in range(1000)})

    with pytest.raises(AssertionError) as failure:
        assert_matches_baseline({f"key{i:04d}": 1 for i in range(1000)}, baseline)

    message = str(failure.value)
    assert len(message.splitlines()) <= 210
    assert "truncated" in message


def test_load_baseline_returns_the_stored_object(tmp_path):
    stored = {"pairs": [{"method": "GET"}], "total": 1}

    assert load_baseline(_write_baseline(tmp_path, stored)) == stored


def test_load_baseline_of_a_missing_file_fails_naming_it_and_the_regenerate_command(tmp_path):
    missing = tmp_path / "missing.json"

    with pytest.raises(AssertionError) as failure:
        load_baseline(missing)

    assert str(missing) in str(failure.value)
    assert REGENERATE_COMMAND in str(failure.value)
