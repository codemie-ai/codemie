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

"""Tests for project visibility query behavior in application_repository."""

from unittest.mock import MagicMock, patch

from sqlalchemy.dialects import postgresql

from codemie.repository.application_repository import application_repository


def _compile_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


class TestApplicationRepositoryVisibility:
    """Test visibility checks and visibility-filtered query building."""

    def test_can_user_see_project_uses_visible_lookup(self):
        """can_user_see_project delegates to get_visible_project."""
        mock_session = MagicMock()

        with patch.object(
            application_repository, "get_visible_project", return_value=MagicMock(name="project")
        ) as mock_get:
            assert (
                application_repository.can_user_see_project(
                    mock_session,
                    project_name="proj-a",
                    user_id="user-1",
                    is_super_admin=False,
                )
                is True
            )
            mock_get.assert_called_once_with(mock_session, "proj-a", "user-1", False)

    def test_can_user_see_project_returns_false_when_visible_lookup_fails(self):
        """If no visible project found, visibility check returns False."""
        mock_session = MagicMock()

        with patch.object(application_repository, "get_visible_project", return_value=None):
            assert (
                application_repository.can_user_see_project(
                    mock_session,
                    project_name="proj-a",
                    user_id="user-1",
                    is_super_admin=False,
                )
                is False
            )

    def test_get_visible_project_non_super_admin_applies_membership_visibility_filter(self):
        """Non-super-admin visible lookup applies Story 11 condition."""
        mock_session = MagicMock()
        mock_session.exec.return_value.first.return_value = None

        with patch.object(application_repository, "_build_visibility_condition", return_value=True) as mock_visibility:
            application_repository.get_visible_project(
                mock_session,
                project_name="proj-a",
                user_id="user-1",
                is_super_admin=False,
            )

        mock_visibility.assert_called_once_with("user-1")
        query_text = _compile_sql(mock_session.exec.call_args[0][0])
        assert "applications.name" in query_text

    def test_list_visible_projects_non_super_admin_applies_search_and_visibility(self):
        """Non-super-admin listing applies both search and visibility filters."""
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        with (
            patch.object(
                application_repository, "_apply_search", side_effect=lambda statement, _: statement
            ) as mock_search,
            patch.object(
                application_repository,
                "_build_visibility_condition",
                return_value=True,
            ) as mock_visibility,
        ):
            application_repository.list_visible_projects(
                session=mock_session,
                user_id="user-1",
                is_super_admin=False,
                search="proj",
                limit=25,
            )

        mock_search.assert_called_once()
        mock_visibility.assert_called_once_with("user-1")
        query_text = _compile_sql(mock_session.exec.call_args[0][0])
        assert "limit" in query_text

    def test_list_visible_projects_super_admin_skips_visibility_filter(self):
        """Super-admin listing should not build non-admin visibility condition."""
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        with patch.object(application_repository, "_build_visibility_condition") as mock_visibility:
            application_repository.list_visible_projects(
                session=mock_session,
                user_id="admin-1",
                is_super_admin=True,
                search=None,
            )

        mock_visibility.assert_not_called()

    def test_get_project_authorization_context_uses_single_join_query(self):
        """Authorization context should be loaded via one query with membership join."""
        mock_session = MagicMock()
        mock_row = (MagicMock(name="project"), True)
        mock_session.exec.return_value.first.return_value = mock_row

        result = application_repository.get_project_authorization_context(
            session=mock_session,
            project_name="shared-proj",
            user_id="user-1",
        )

        assert result == mock_row
        query_text = _compile_sql(mock_session.exec.call_args[0][0])
        assert "join" in query_text
        assert "user_projects.user_id" in query_text
        assert "applications.name" in query_text


class TestApplicationRepositoryCreationAndLookup:
    def test_get_by_name_case_insensitive_uses_lower_expression(self):
        mock_session = MagicMock()
        expected_project = MagicMock(name="Project")
        mock_session.exec.return_value.first.return_value = expected_project

        result = application_repository.get_by_name_case_insensitive(mock_session, "MyProject")

        assert result is expected_project
        query_text = _compile_sql(mock_session.exec.call_args[0][0])
        assert "lower(applications.name)" in query_text

    def test_exists_by_name_case_insensitive_delegates_to_lookup(self):
        mock_session = MagicMock()

        with patch.object(application_repository, "get_by_name_case_insensitive", return_value=MagicMock()) as mock_get:
            assert application_repository.exists_by_name_case_insensitive(mock_session, "myproject") is True
            mock_get.assert_called_once_with(mock_session, "myproject")

    def test_create_persists_description_project_type_and_creator(self):
        mock_session = MagicMock()

        created = application_repository.create(
            session=mock_session,
            name="DataPipeline",
            description="Pipeline project",
            project_type="shared",
            created_by="user-1",
        )

        assert created.name == "DataPipeline"
        assert created.description == "Pipeline project"
        assert created.project_type == "shared"
        assert created.created_by == "user-1"
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once_with(created)


class TestApplicationRepositoryProjectLimitCount:
    def test_count_shared_projects_created_by_user_uses_story_14_filters(self):
        mock_session = MagicMock()
        mock_session.exec.return_value.one.return_value = 3

        result = application_repository.count_shared_projects_created_by_user(mock_session, "user-1")

        assert result == 3
        query_text = _compile_sql(mock_session.exec.call_args[0][0])
        assert "count(applications.id)" in query_text
        assert "applications.created_by" in query_text
        assert "applications.project_type = 'shared'" in query_text
        assert "deleted_at" in query_text
        assert "is null" in query_text
