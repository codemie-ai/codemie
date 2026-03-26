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

"""Tests for project visibility service."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.project.project_visibility_service import ProjectVisibilityService


class TestProjectVisibilityService:
    @patch("codemie.service.project.project_visibility_service.logger")
    def test_get_visible_project_or_404_logs_and_raises(self, mock_logger):
        mock_session = MagicMock()

        with patch(
            "codemie.service.project.project_visibility_service.application_repository.get_visible_project",
            return_value=None,
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                ProjectVisibilityService.get_visible_project_or_404(
                    session=mock_session,
                    project_name="hidden-proj",
                    user_id="user-1",
                    is_admin=False,
                    action="get_project_detail",
                )

        assert exc_info.value.code == 404
        assert exc_info.value.message == "Project not found"
        assert mock_logger.warning.call_count == 1
        log_message = mock_logger.warning.call_args[0][0]
        # Story 16 R3: PII removal - project_name no longer logged
        # action format is "METHOD /path" - only method part is logged
        assert "user_id=user-1" in log_message
        # action="get_project_detail" has no space, so entire string becomes method
        assert "method=get_project_detail" in log_message
        assert "timestamp=" in log_message
        # project_name should NOT be in logs (PII removal)
        assert "hidden-proj" not in log_message
