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

from codemie.rest_api.models.cli_analytics import LocalAnalyticsRepositoryRow, LocalAnalyticsSessionRow


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
        tool_success_rate=0.0,
    )
    assert row.repository is None
