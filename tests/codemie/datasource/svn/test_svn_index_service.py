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

"""Tests for SVNIndexService."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.datasource.svn.svn_index_service import SVNIndexService


# --- validate_credentials ---


class TestValidateCredentials:
    def test_no_setting_id_skips_validation(self):
        with patch("codemie.datasource.svn.svn_index_service.SettingsService") as mock_svc:
            SVNIndexService.validate_credentials(
                user_id="u1", project_name="proj", repo_link="http://svn", setting_id=None
            )
        mock_svc.get_svn_creds.assert_not_called()

    def test_empty_setting_id_skips_validation(self):
        with patch("codemie.datasource.svn.svn_index_service.SettingsService") as mock_svc:
            SVNIndexService.validate_credentials(
                user_id="u1", project_name="proj", repo_link="http://svn", setting_id=""
            )
        mock_svc.get_svn_creds.assert_not_called()

    def test_valid_setting_id_calls_get_svn_creds(self):
        with patch("codemie.datasource.svn.svn_index_service.SettingsService") as mock_svc:
            SVNIndexService.validate_credentials(
                user_id="u1",
                project_name="proj",
                repo_link="http://svn",
                setting_id="s1",
            )
        mock_svc.get_svn_creds.assert_called_once_with(
            user_id="u1",
            project_name="proj",
            repo_link="http://svn",
            setting_id="s1",
        )

    def test_get_svn_creds_raises_becomes_extended_http_exception(self):
        with patch("codemie.datasource.svn.svn_index_service.SettingsService") as mock_svc:
            mock_svc.get_svn_creds.side_effect = Exception("bad creds")
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SVNIndexService.validate_credentials(
                    user_id="u1", project_name="proj", repo_link="http://svn", setting_id="s1"
                )
        assert exc_info.value.code == 422
        assert "SVN Integration Error" in exc_info.value.message

    def test_exception_message_included_in_details(self):
        with patch("codemie.datasource.svn.svn_index_service.SettingsService") as mock_svc:
            mock_svc.get_svn_creds.side_effect = Exception("connection refused")
            with pytest.raises(ExtendedHTTPException) as exc_info:
                SVNIndexService.validate_credentials(
                    user_id="u1", project_name="proj", repo_link="http://svn", setting_id="s1"
                )
        assert "connection refused" in exc_info.value.details


# --- reindex ---


class TestReindexStamp:
    @pytest.mark.parametrize("resume_indexing", [True, False])
    def test_svn_reindex_stamps_trigger_time_once(self, resume_indexing):
        stamp = object()
        index = MagicMock()
        index.id = "idx-1"
        manager = MagicMock()

        with (
            patch.object(SVNIndexService, "validate_credentials"),
            patch("codemie.datasource.code.code_datasource_processor.run_in_background") as mock_run,
            patch("codemie.datasource.svn.svn_datasource_processor.SVNDatasourceProcessor") as mock_proc_cls,
            patch("codemie.rest_api.models.index.IndexInfo") as mock_index_cls,
        ):
            mock_index_cls.filter_by_project_and_repo.return_value = [index]
            mock_index_cls.stamp_reindex_triggered_at.return_value = stamp
            manager.attach_mock(mock_index_cls.stamp_reindex_triggered_at, "stamp")
            manager.attach_mock(mock_proc_cls.create_processor, "create_processor")

            SVNIndexService.reindex(
                svn_repo=MagicMock(),
                index_info=MagicMock(),
                user=MagicMock(),
                app_name="app",
                repo_name="repo",
                request_uuid="uuid",
                background_tasks=MagicMock(),
                resume_indexing=resume_indexing,
            )
            mock_run.call_args[0][0]()

        mock_index_cls.stamp_reindex_triggered_at.assert_called_once_with("idx-1")
        assert [c[0] for c in manager.mock_calls[:2]] == ["stamp", "create_processor"]
        assert index.last_reindex_triggered_at is stamp
