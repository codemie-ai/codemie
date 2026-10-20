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

"""The SVN router reindex closure stamps last_reindex_triggered_at before reprocessing."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from codemie.rest_api.routers.index import code_repositories as module
from codemie.rest_api.routers.index.code_repositories import UpdateIndexRequest, update_svn_index_application


@pytest.mark.parametrize("resume_indexing", [False, True])
def test_svn_router_reindex_stamps_trigger_time_before_processing(resume_indexing):
    stamp = object()
    index = Mock(id="idx-1", project_name="app")
    svn_repo = Mock(link="svn://repo", setting_id="s1")
    svn_repo.name = "repo"
    manager = MagicMock()
    captured = {}

    with (
        patch.object(module, "request_summary_manager"),
        patch.object(module, "IndexInfo") as index_cls,
        patch.object(module, "Ability") as ability,
        patch.object(module.SVNRepo, "get_by_app_id", return_value=[svn_repo]),
        patch.object(module.SVNIndexService, "validate_credentials"),
        patch.object(module.SVNDatasourceProcessor, "create_processor") as create_processor,
        patch.object(module, "run_in_background", side_effect=lambda fn, *_: captured.setdefault("process", fn)),
    ):
        ability.return_value.can.return_value = True
        index_cls.filter_by_project_and_repo.return_value = [index]
        index_cls.stamp_reindex_triggered_at.return_value = stamp
        manager.attach_mock(index_cls.stamp_reindex_triggered_at, "stamp")
        manager.attach_mock(create_processor, "create_processor")

        update_svn_index_application(
            app_name="app",
            repo_name="repo",
            tasks=Mock(),
            request=UpdateIndexRequest(),
            raw_request=Mock(),
            resume_indexing=resume_indexing,
            user=Mock(),
        )
        captured["process"]()

    index_cls.stamp_reindex_triggered_at.assert_called_once_with("idx-1")
    assert [c[0] for c in manager.mock_calls[:2]] == ["stamp", "create_processor"]
    assert index.last_reindex_triggered_at is stamp
