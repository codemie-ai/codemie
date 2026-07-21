# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.models.index import LifecycleState
from codemie.service.stale_datasource.stale_datasource_service import (
    StaleDatasourceService,
    _compute_base_tool_names,
)


def _ds(repo_name: str, index_type: str) -> MagicMock:
    ds = MagicMock()
    ds.repo_name = repo_name
    ds.index_type = index_type
    return ds


def _make_service() -> StaleDatasourceService:
    return StaleDatasourceService(
        session=AsyncMock(),
        metrics_repository=AsyncMock(),
    )


def _make_ds(
    ds_id: str = "ds-1",
    project: str = "proj",
    repo: str = "myrepo",
    index_type: str = "knowledge_base_file",
) -> MagicMock:
    ds = MagicMock()
    ds.id = ds_id
    ds.project_name = project
    ds.repo_name = repo
    ds.index_type = index_type
    return ds


def _one_page_response(buckets: list[dict]) -> dict:
    return {"aggregations": {"result": {"buckets": buckets, "after_key": None}}}


class TestComputeBaseToolNames:
    def test_kb_datasource_returns_single_search_kb_name(self):
        names = _compute_base_tool_names(_ds("MyRepo", "knowledge_base_file"))
        assert names == ["search_kb_myrepo"]

    def test_kb_datasource_with_dashes_returns_current_and_legacy_variants(self):
        names = _compute_base_tool_names(_ds("my-confluence-kb", "knowledge_base_confluence"))
        assert names == ["search_kb_my-confluence-kb", "search_kb_my_confluence_kb"]

    def test_kb_datasource_special_chars_replaced(self):
        names = _compute_base_tool_names(_ds("Jira_Support_Promt", "knowledge_base_jira"))
        assert names == ["search_kb_jira_support_promt"]

    def test_kb_datasource_at_sign_replaced(self):
        names = _compute_base_tool_names(_ds("user@epam.com-repo", "knowledge_base_sharepoint"))
        assert names == ["search_kb_user_epam_com-repo", "search_kb_user_epam_com_repo"]

    def test_code_datasource_returns_all_tool_prefixes(self):
        names = _compute_base_tool_names(_ds("alx-lhp-frontend", "code"))
        assert "search_code_repo_alx-lhp-frontend" in names
        assert "search_code_repo_alx_lhp_frontend" in names
        assert "search_code_repo_v2_alx-lhp-frontend" in names
        assert "search_code_repo_v2_alx_lhp_frontend" in names
        assert "get_repository_file_tree_alx-lhp-frontend" in names
        assert "get_repository_file_tree_alx_lhp_frontend" in names
        assert "read_files_content_alx-lhp-frontend" in names
        assert "read_files_content_alx_lhp_frontend" in names
        assert len(names) == 14

    def test_summary_index_type_is_treated_as_code(self):
        names = _compute_base_tool_names(_ds("myrepo", "summary"))
        assert any("search_code_repo" in n for n in names)

    def test_long_name_uses_hash_and_stays_under_64_chars(self):
        long_repo = "a" * 60
        names = _compute_base_tool_names(_ds(long_repo, "knowledge_base_file"))
        assert len(names) == 1
        assert len(names[0]) <= 64
        suffix = names[0][len("search_kb_") :]
        assert suffix.isdigit()

    def test_hash_is_deterministic(self):
        long_repo = "x" * 60
        assert _compute_base_tool_names(_ds(long_repo, "knowledge_base_file")) == _compute_base_tool_names(
            _ds(long_repo, "knowledge_base_file")
        )

    def test_short_name_does_not_use_hash(self):
        names = _compute_base_tool_names(_ds("short", "knowledge_base_file"))
        assert names == ["search_kb_short"]
        assert names[0].replace("search_kb_", "").isalpha()


class TestFetchLifecycleMetrics:
    @pytest.mark.asyncio
    async def test_returns_last_seen_for_matching_datasource(self):
        service = _make_service()
        ds = _make_ds()
        bucket = {
            "key": {"project": "proj", "repo_name": "myrepo", "datasource_type": "knowledge_base_file"},
            "last_seen": {"value_as_string": "2026-05-07T08:55:23.000Z"},
        }
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_lifecycle_metrics([ds])

        assert result["ds-1"] == datetime(2026, 5, 7, 8, 55, 23)

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_matching_bucket(self):
        service = _make_service()
        ds = _make_ds(project="proj-a", repo="repo-a", index_type="code")
        bucket = {
            "key": {"project": "proj-b", "repo_name": "repo-b", "datasource_type": "code"},
            "last_seen": {"value_as_string": "2026-05-01T00:00:00.000Z"},
        }
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_lifecycle_metrics([ds])

        assert result == {}

    @pytest.mark.asyncio
    async def test_collision_fixed_different_types_same_repo(self):
        ds_code = _make_ds(ds_id="id-code", project="p", repo="r", index_type="code")
        ds_kb = _make_ds(ds_id="id-kb", project="p", repo="r", index_type="knowledge_base_file")
        buckets = [
            {
                "key": {"project": "p", "repo_name": "r", "datasource_type": "code"},
                "last_seen": {"value_as_string": "2026-06-01T00:00:00.000Z"},
            },
            {
                "key": {"project": "p", "repo_name": "r", "datasource_type": "knowledge_base_file"},
                "last_seen": {"value_as_string": "2026-03-01T00:00:00.000Z"},
            },
        ]
        service = _make_service()
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response(buckets))

        result = await service._fetch_lifecycle_metrics([ds_code, ds_kb])

        assert result["id-code"] == datetime(2026, 6, 1)
        assert result["id-kb"] == datetime(2026, 3, 1)

    @pytest.mark.asyncio
    async def test_es_error_propagates(self):
        service = _make_service()
        ds = _make_ds()
        service.metrics_repository.execute_aggregation_query = AsyncMock(side_effect=Exception("ES unavailable"))

        with pytest.raises(Exception, match="ES unavailable"):
            await service._fetch_lifecycle_metrics([ds])

    @pytest.mark.asyncio
    async def test_empty_datasource_list_returns_empty(self):
        service = _make_service()
        result = await service._fetch_lifecycle_metrics([])
        assert result == {}
        service.metrics_repository.execute_aggregation_query.assert_not_called()


class TestFetchToolUsageMetrics:
    @pytest.mark.asyncio
    async def test_kb_datasource_matched_by_search_kb_name(self):
        service = _make_service()
        ds = _make_ds(ds_id="kb-1", repo="MVPscope", index_type="knowledge_base_file")
        bucket = {
            "key": {"base_tool_name": "search_kb_mvpscope"},
            "last_seen": {"value_as_string": "2026-06-15T12:00:00.000Z"},
        }
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_tool_usage_metrics([ds])

        assert result["kb-1"] == datetime(2026, 6, 15, 12, 0, 0)

    @pytest.mark.asyncio
    async def test_code_datasource_matched_by_any_code_tool(self):
        service = _make_service()
        ds = _make_ds(ds_id="code-1", repo="alx-lhp-frontend", index_type="code")
        bucket = {
            "key": {"base_tool_name": "search_code_repo_alx_lhp_frontend"},
            "last_seen": {"value_as_string": "2026-03-16T10:19:04.000Z"},
        }
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_tool_usage_metrics([ds])

        assert result["code-1"] == datetime(2026, 3, 16, 10, 19, 4)

    @pytest.mark.asyncio
    async def test_hyphenated_repo_matches_current_runtime_tool_name(self):
        service = _make_service()
        ds = _make_ds(ds_id="code-1", repo="alx-lhp-frontend", index_type="code")
        bucket = {
            "key": {"base_tool_name": "search_code_repo_alx-lhp-frontend"},
            "last_seen": {"value_as_string": "2026-07-01T10:00:00.000Z"},
        }
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_tool_usage_metrics([ds])

        assert result["code-1"] == datetime(2026, 7, 1, 10, 0, 0)

    @pytest.mark.asyncio
    async def test_ambiguous_tool_name_attributed_to_all_matching_datasources(self):
        ds_a = _make_ds(ds_id="id-a", project="proj-a", repo="frontend", index_type="code")
        ds_b = _make_ds(ds_id="id-b", project="proj-b", repo="frontend", index_type="code")
        bucket = {
            "key": {"base_tool_name": "search_code_repo_frontend"},
            "last_seen": {"value_as_string": "2026-06-20T00:00:00.000Z"},
        }
        service = _make_service()
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_tool_usage_metrics([ds_a, ds_b])

        assert result["id-a"] == datetime(2026, 6, 20)
        assert result["id-b"] == datetime(2026, 6, 20)

    @pytest.mark.asyncio
    async def test_same_project_signal_preferred_over_other_project(self):
        ds_a = _make_ds(ds_id="id-a", project="proj-a", repo="frontend", index_type="code")
        ds_b = _make_ds(ds_id="id-b", project="proj-b", repo="frontend", index_type="code")
        buckets = [
            {
                "key": {"base_tool_name": "search_code_repo_frontend", "project": "proj-a"},
                "last_seen": {"value_as_string": "2026-01-10T00:00:00.000Z"},
            },
            {
                "key": {"base_tool_name": "search_code_repo_frontend", "project": "proj-b"},
                "last_seen": {"value_as_string": "2026-06-20T00:00:00.000Z"},
            },
        ]
        service = _make_service()
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response(buckets))

        result = await service._fetch_tool_usage_metrics([ds_a, ds_b])

        assert result["id-a"] == datetime(2026, 1, 10)
        assert result["id-b"] == datetime(2026, 6, 20)

    @pytest.mark.asyncio
    async def test_name_only_fallback_when_no_same_project_signal(self):
        ds = _make_ds(ds_id="id-a", project="proj-a", repo="frontend", index_type="code")
        bucket = {
            "key": {"base_tool_name": "search_code_repo_frontend", "project": "other-proj"},
            "last_seen": {"value_as_string": "2026-06-20T00:00:00.000Z"},
        }
        service = _make_service()
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_tool_usage_metrics([ds])

        assert result["id-a"] == datetime(2026, 6, 20)

    @pytest.mark.asyncio
    async def test_missing_project_bucket_feeds_fallback(self):
        ds = _make_ds(ds_id="id-a", project="proj-a", repo="frontend", index_type="code")
        buckets = [
            {
                "key": {"base_tool_name": "search_code_repo_frontend", "project": None},
                "last_seen": {"value_as_string": "2026-06-25T00:00:00.000Z"},
            },
            {
                "key": {"base_tool_name": "search_code_repo_frontend", "project": "proj-a"},
                "last_seen": {"value_as_string": "2026-02-01T00:00:00.000Z"},
            },
        ]
        service = _make_service()
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response(buckets))

        result = await service._fetch_tool_usage_metrics([ds])

        assert result["id-a"] == datetime(2026, 2, 1)

    @pytest.mark.asyncio
    async def test_unmatched_tool_name_not_in_result(self):
        service = _make_service()
        ds = _make_ds(repo="myrepo", index_type="knowledge_base_file")
        bucket = {
            "key": {"base_tool_name": "search_kb_completely_different"},
            "last_seen": {"value_as_string": "2026-06-01T00:00:00.000Z"},
        }
        service.metrics_repository.execute_aggregation_query = AsyncMock(return_value=_one_page_response([bucket]))

        result = await service._fetch_tool_usage_metrics([ds])

        assert result == {}

    @pytest.mark.asyncio
    async def test_es_error_propagates(self):
        service = _make_service()
        ds = _make_ds()
        service.metrics_repository.execute_aggregation_query = AsyncMock(side_effect=Exception("ES timeout"))

        with pytest.raises(Exception, match="ES timeout"):
            await service._fetch_tool_usage_metrics([ds])

    @pytest.mark.asyncio
    async def test_empty_datasource_list_returns_empty(self):
        service = _make_service()
        result = await service._fetch_tool_usage_metrics([])
        assert result == {}
        service.metrics_repository.execute_aggregation_query.assert_not_called()


class TestIsDatasourceStale:
    def _service(self) -> StaleDatasourceService:
        return _make_service()

    def _ds_with_update(self, days_ago: int) -> MagicMock:
        ds = MagicMock()
        ds.id = "x"
        ds.update_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago)
        return ds

    def test_recent_last_activity_not_stale(self):
        ds = MagicMock()
        ds.id = "x"
        recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=89)
        assert self._service()._is_datasource_stale(ds, recent) is False

    def test_old_last_activity_is_stale(self):
        ds = MagicMock()
        ds.id = "x"
        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=91)
        assert self._service()._is_datasource_stale(ds, old) is True

    def test_exactly_at_threshold_is_stale(self):
        ds = MagicMock()
        ds.id = "x"
        at_threshold = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90)
        assert self._service()._is_datasource_stale(ds, at_threshold) is True

    def test_no_signals_at_all_is_stale(self):
        ds = MagicMock()
        ds.id = "x"
        ds.update_date = None
        assert self._service()._is_datasource_stale(ds, None) is True


class TestDetectAndMarkStaleWiring:
    @pytest.mark.asyncio
    async def test_lifecycle_signal_prevents_stale_marking(self):
        service = _make_service()
        ds = MagicMock()
        ds.id = "ds-1"
        ds.lifecycle_state = LifecycleState.ACTIVE
        ds.update_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=200)

        recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)

        with (
            patch.object(service, "_get_candidate_datasources", return_value=[ds]),
            patch.object(service, "_fetch_lifecycle_metrics", return_value={"ds-1": recent}),
            patch.object(service, "_fetch_tool_usage_metrics", return_value={}),
        ):
            stats = await service.detect_and_mark_stale_datasources()

        assert stats["newly_marked_stale"] == 0

    @pytest.mark.asyncio
    async def test_tool_signal_prevents_stale_marking(self):
        service = _make_service()
        ds = MagicMock()
        ds.id = "ds-1"
        ds.lifecycle_state = LifecycleState.ACTIVE
        ds.update_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=200)

        recent_tool = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)

        with (
            patch.object(service, "_get_candidate_datasources", return_value=[ds]),
            patch.object(service, "_fetch_lifecycle_metrics", return_value={}),
            patch.object(service, "_fetch_tool_usage_metrics", return_value={"ds-1": recent_tool}),
        ):
            stats = await service.detect_and_mark_stale_datasources()

        assert stats["newly_marked_stale"] == 0

    @pytest.mark.asyncio
    async def test_both_signals_old_marks_stale(self):
        service = _make_service()
        ds = MagicMock()
        ds.id = "ds-1"
        ds.lifecycle_state = LifecycleState.ACTIVE
        ds.update_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=200)

        old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=101)

        with (
            patch.object(service, "_get_candidate_datasources", return_value=[ds]),
            patch.object(service, "_fetch_lifecycle_metrics", return_value={"ds-1": old}),
            patch.object(service, "_fetch_tool_usage_metrics", return_value={}),
            patch.object(service.session, "commit", new_callable=AsyncMock),
        ):
            stats = await service.detect_and_mark_stale_datasources()

        assert stats["newly_marked_stale"] == 1
        assert ds.lifecycle_state == LifecycleState.STALE

    @pytest.mark.asyncio
    async def test_recent_update_date_prevents_stale_despite_old_es_signal(self):
        service = _make_service()
        ds = MagicMock()
        ds.id = "ds-1"
        ds.lifecycle_state = LifecycleState.ACTIVE
        ds.update_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)

        old_es = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=101)

        with (
            patch.object(service, "_get_candidate_datasources", return_value=[ds]),
            patch.object(service, "_fetch_lifecycle_metrics", return_value={"ds-1": old_es}),
            patch.object(service, "_fetch_tool_usage_metrics", return_value={}),
        ):
            stats = await service.detect_and_mark_stale_datasources()

        assert stats["newly_marked_stale"] == 0

    @pytest.mark.asyncio
    async def test_old_update_date_does_not_save_datasource_with_old_es_signal(self):
        service = _make_service()
        ds = MagicMock()
        ds.id = "ds-1"
        ds.lifecycle_state = LifecycleState.ACTIVE
        ds.update_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=120)

        old_es = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=101)

        with (
            patch.object(service, "_get_candidate_datasources", return_value=[ds]),
            patch.object(service, "_fetch_lifecycle_metrics", return_value={"ds-1": old_es}),
            patch.object(service, "_fetch_tool_usage_metrics", return_value={}),
            patch.object(service.session, "commit", new_callable=AsyncMock),
        ):
            stats = await service.detect_and_mark_stale_datasources()

        assert stats["newly_marked_stale"] == 1

    @pytest.mark.asyncio
    async def test_es_failure_aborts_run_without_marking(self):
        service = _make_service()
        ds = MagicMock()
        ds.id = "ds-1"
        ds.lifecycle_state = LifecycleState.ACTIVE
        ds.update_date = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=200)

        with (
            patch.object(service, "_get_candidate_datasources", return_value=[ds]),
            patch.object(service, "_fetch_lifecycle_metrics", side_effect=Exception("ES down")),
            patch.object(service.session, "commit", new_callable=AsyncMock) as commit,
            patch.object(service.session, "rollback", new_callable=AsyncMock) as rollback,
        ):
            with pytest.raises(Exception, match="ES down"):
                await service.detect_and_mark_stale_datasources()

        assert ds.lifecycle_state == LifecycleState.ACTIVE
        commit.assert_not_called()
        rollback.assert_called_once()
