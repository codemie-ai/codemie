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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.project.project_visibility_service import ProjectVisibilityService


class TestProjectVisibilityService:
    def test_list_visible_projects_delegates_to_repository(self):
        mock_session = MagicMock()
        visible_projects = [SimpleNamespace(name="proj-a")]

        with patch(
            "codemie.service.project.project_visibility_service.application_repository.list_visible_projects",
            return_value=visible_projects,
        ) as mock_list_visible_projects:
            result = ProjectVisibilityService.list_visible_projects(
                session=mock_session,
                user_id="user-1",
                is_admin=False,
                search="proj",
                limit=10,
            )

        assert result == visible_projects
        mock_list_visible_projects.assert_called_once_with(
            session=mock_session,
            user_id="user-1",
            is_admin=False,
            search="proj",
            limit=10,
        )

    def test_list_visible_projects_paginated_enriches_projects_with_counts_and_cost_center(self):
        mock_session = MagicMock()
        project = SimpleNamespace(
            name="proj-a",
            display_name=None,
            description="Project A",
            project_type="shared",
            created_by="owner-1",
            date="2026-04-24T00:00:00Z",
            cost_center_id="cc-1",
            chargeback_enabled=False,
            chargeback_attribution="project",
        )
        cost_center = SimpleNamespace(name="Cost Center A")

        with (
            patch(
                "codemie.service.project.project_visibility_service.application_repository.list_visible_projects_paginated",
                return_value=([project], 1),
            ),
            patch(
                "codemie.service.project.project_visibility_service.application_repository.get_project_member_counts_bulk",
                return_value={"proj-a": (2, 1)},
            ),
            patch(
                "codemie.service.project.project_visibility_service.application_repository.get_project_entity_counts_bulk",
                return_value={"proj-a": {"datasources": 3}},
            ),
            patch(
                "codemie.service.project.project_visibility_service.cost_center_repository.get_by_ids",
                return_value={"cc-1": cost_center},
            ),
        ):
            result, total_count = ProjectVisibilityService.list_visible_projects_paginated(
                session=mock_session,
                user_id="user-1",
                is_admin=False,
                search="proj",
                page=0,
                per_page=20,
                include_counters=True,
            )

        assert total_count == 1
        assert result == [
            {
                "name": "proj-a",
                "display_name": None,
                "description": "Project A",
                "project_type": "shared",
                "created_by": "owner-1",
                "created_at": "2026-04-24T00:00:00Z",
                "user_count": 2,
                "admin_count": 1,
                "counters": {"datasources": 3},
                "cost_center_id": "cc-1",
                "cost_center_name": "Cost Center A",
                "chargeback_enabled": False,
                "chargeback_attribution": "project",
            }
        ]

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

    def test_get_visible_project_or_404_returns_project_when_visible(self):
        mock_session = MagicMock()
        project = SimpleNamespace(name="proj-a")

        with patch(
            "codemie.service.project.project_visibility_service.application_repository.get_visible_project",
            return_value=project,
        ):
            result = ProjectVisibilityService.get_visible_project_or_404(
                session=mock_session,
                project_name="proj-a",
                user_id="user-1",
                is_admin=False,
                action="GET /v1/projects/proj-a",
            )

        assert result is project

    def test_get_visible_project_with_members_includes_current_user_project_admin_flag(self):
        mock_session = MagicMock()
        project = SimpleNamespace(
            name="proj-a",
            display_name=None,
            description="Project A",
            project_type="shared",
            created_by="owner-1",
            date="2026-04-24T00:00:00Z",
            cost_center_id=None,
            chargeback_enabled=False,
            chargeback_attribution="project",
            allowed_models=["gpt-4"],
            default_model="gpt-4",
        )
        current_member = SimpleNamespace(user_id="user-1", is_project_admin=True, date="2026-04-24T00:00:00Z")
        other_member = SimpleNamespace(user_id="user-2", is_project_admin=False, date="2026-04-24T00:00:00Z")

        with (
            patch.object(ProjectVisibilityService, "get_visible_project_or_404", return_value=project),
            patch(
                "codemie.service.project.project_visibility_service.application_repository.get_project_member_counts_bulk",
                return_value={"proj-a": (2, 1)},
            ),
            patch(
                "codemie.service.project.project_visibility_service.application_repository.get_project_members",
                return_value=[current_member, other_member],
            ),
        ):
            result = ProjectVisibilityService.get_visible_project_with_members(
                session=mock_session,
                project_name="proj-a",
                user_id="user-1",
                is_admin=False,
                action="GET /v1/projects/proj-a",
            )

        assert result["user_count"] == 2
        assert result["admin_count"] == 1
        assert result["is_project_admin"] is True
        assert result["chargeback_enabled"] is False
        assert result["chargeback_attribution"] == "project"
        assert result["members"] == [
            {"user_id": "user-1", "is_project_admin": True, "date": "2026-04-24T00:00:00Z"},
            {"user_id": "user-2", "is_project_admin": False, "date": "2026-04-24T00:00:00Z"},
        ]
