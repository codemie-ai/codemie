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

import pytest

from codemie.rest_api.models.cli_analytics import (
    LocalAnalyticsCodeChangesKPIs,
    LocalAnalyticsDispatchRow,
    LocalAnalyticsOverviewDailyBucket,
    LocalAnalyticsOverviewKPIs,
    LocalAnalyticsRepositoryBranchRow,
    LocalAnalyticsRepositoryRow,
    LocalAnalyticsSessionRow,
    LocalAnalyticsUserRow,
)


def test_session_row_repository_nullable():
    row = LocalAnalyticsSessionRow(
        trace_id="abc",
        developer_name="dev",
        repository=None,
        branch=None,
        start_time="2026-01-01T00:00:00",
        duration_ms=0,
        model_name="claude",
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
    )
    assert row.repository is None
    assert row.branch is None


def test_repository_row_repository_nullable():
    row = LocalAnalyticsRepositoryRow(
        repository=None,
        session_count=0,
        turns=0,
        cost_usd=0.0,
        files_changed=0,
        lines_added=0,
        lines_removed=0,
        net_lines=0,
        tool_calls=0,
        tool_calls_success=0,
        tool_success_rate=0.0,
    )
    assert row.repository is None


def test_dispatch_row_keeps_an_unknown_subagent_duration_as_null():
    # An agent whose stop never arrived has no duration: null, not 0 (the events contract).
    row = LocalAnalyticsDispatchRow(label="agent · Explore", kind="agent", start_offset_ms=7905, duration_ms=None)
    assert row.model_dump()["duration_ms"] is None


_REPO_BASE = {
    "session_count": 1,
    "turns": 1,
    "cost_usd": 0.0,
    "files_changed": 0,
    "tool_calls": 0,
    "tool_calls_success": 0,
    "tool_success_rate": 0.0,
}
_KPI_BASE = {
    "total_sessions": 0,
    "total_cost_usd": 0.0,
    "duration_ms": 0,
    "total_turns": 0,
    "total_tool_calls": 0,
    "tool_call_success_rate": 0.0,
    "total_files_changed": 0,
    "dead_sessions": 0,
    "total_input_tokens": 0,
    "total_output_tokens": 0,
    "total_cache_creation_tokens": 0,
    "total_cache_read_tokens": 0,
    "total_tokens": 0,
    "cache_read_cost_usd": 0.0,
    "bloat_pct": 0.0,
    "avg_context_per_call": 0.0,
}
_USER_BASE = {
    "developer_name": "dev",
    "session_count": 1,
    "input_tokens": 0,
    "output_tokens": 0,
    "cost_usd": 0.0,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
}
_SESSION_BASE = {
    "trace_id": "abc",
    "developer_name": "dev",
    "start_time": "2026-01-01T00:00:00",
    "duration_ms": 0,
    "model_name": "claude",
    "input_tokens": 0,
    "output_tokens": 0,
    "cost_usd": 0.0,
}


@pytest.mark.parametrize(
    "model, base, fields",
    [
        (LocalAnalyticsRepositoryRow, _REPO_BASE, ("lines_added", "lines_removed", "net_lines")),
        (LocalAnalyticsRepositoryBranchRow, _REPO_BASE, ("lines_added", "lines_removed", "net_lines")),
        (LocalAnalyticsOverviewKPIs, _KPI_BASE, ("net_lines",)),
        (LocalAnalyticsOverviewDailyBucket, {"day": "2026-01-01"}, ("net_lines",)),
        (LocalAnalyticsUserRow, _USER_BASE, ("net_lines",)),
        (LocalAnalyticsSessionRow, _SESSION_BASE, ("net_lines",)),
        (LocalAnalyticsCodeChangesKPIs, {}, ("net_lines",)),
    ],
)
def test_lines_fields_accept_null_and_zero(model, base, fields):
    nulls = model(**base, **dict.fromkeys(fields))
    zeros = model(**base, **dict.fromkeys(fields, 0))
    for name in fields:
        assert getattr(nulls, name) is None
        assert getattr(zeros, name) == 0


@pytest.mark.parametrize("model", [LocalAnalyticsUserRow, LocalAnalyticsSessionRow, LocalAnalyticsCodeChangesKPIs])
def test_defaulted_net_lines_is_null(model):
    base = {
        LocalAnalyticsUserRow: _USER_BASE,
        LocalAnalyticsSessionRow: _SESSION_BASE,
        LocalAnalyticsCodeChangesKPIs: {},
    }[model]
    assert model(**base).net_lines is None


def test_session_row_has_no_added_removed_fields():
    assert "lines_added" not in LocalAnalyticsSessionRow.model_fields
    assert "lines_removed" not in LocalAnalyticsSessionRow.model_fields


def test_repository_row_schema_marks_lines_added_nullable():
    prop = LocalAnalyticsRepositoryRow.model_json_schema()["properties"]["lines_added"]
    assert {"type": "null"} in prop["anyOf"]
