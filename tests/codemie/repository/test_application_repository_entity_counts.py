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

"""Unit tests for ApplicationRepository.get_project_entity_counts_bulk and update_fields."""

from datetime import datetime
from unittest.mock import MagicMock, patch


from codemie.core.models import Application
from codemie.repository.application_repository import application_repository


def _make_app(name: str, description: str = "Test") -> Application:
    return Application(
        id=name,
        name=name,
        description=description,
        project_type="shared",
        date=datetime.now(),
        update_date=datetime.now(),
    )


# ---------------------------------------------------------------------------
# Tests for ApplicationRepository.get_project_entity_counts_bulk
# ---------------------------------------------------------------------------


class TestGetProjectEntityCountsBulk:
    """Tests for get_project_entity_counts_bulk."""

    def test_empty_project_names_returns_empty_dict(self):
        """get_project_entity_counts_bulk returns {} without touching the DB."""
        mock_session = MagicMock()

        result = application_repository.get_project_entity_counts_bulk(mock_session, [])

        assert result == {}
        mock_session.exec.assert_not_called()

    def test_all_zero_counts_for_empty_project(self):
        """Returns zeroed counters when project has no resources."""
        mock_session = MagicMock()
        # All 5 GROUP BY queries return no rows
        mock_session.exec.return_value.all.return_value = []

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["empty-proj"])

        assert result == {
            "empty-proj": {
                "assistants_count": 0,
                "workflows_count": 0,
                "integrations_count": 0,
                "datasources_count": 0,
                "skills_count": 0,
            }
        }

    def test_assistants_counted_correctly(self):
        """Assistants count is populated from the first GROUP BY query."""
        mock_session = MagicMock()
        # Exec is called 5 times (one per entity type).
        # The order is: assistants, workflows, skills, index_info/datasources, settings/integrations.
        # Return assistants=4 on first call; empty for the rest.
        call_results = [
            MagicMock(all=MagicMock(return_value=[("my-proj", 4)])),  # assistants
            MagicMock(all=MagicMock(return_value=[])),  # workflows
            MagicMock(all=MagicMock(return_value=[])),  # skills
            MagicMock(all=MagicMock(return_value=[])),  # datasources
            MagicMock(all=MagicMock(return_value=[])),  # integrations
        ]
        mock_session.exec.side_effect = call_results

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["my-proj"])

        assert result["my-proj"]["assistants_count"] == 4
        assert result["my-proj"]["workflows_count"] == 0

    def test_workflows_counted_correctly(self):
        """Workflows count is populated from the second GROUP BY query."""
        mock_session = MagicMock()
        call_results = [
            MagicMock(all=MagicMock(return_value=[])),  # assistants
            MagicMock(all=MagicMock(return_value=[("my-proj", 7)])),  # workflows
            MagicMock(all=MagicMock(return_value=[])),  # skills
            MagicMock(all=MagicMock(return_value=[])),  # datasources
            MagicMock(all=MagicMock(return_value=[])),  # integrations
        ]
        mock_session.exec.side_effect = call_results

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["my-proj"])

        assert result["my-proj"]["workflows_count"] == 7
        assert result["my-proj"]["assistants_count"] == 0

    def test_skills_counted_correctly(self):
        """Skills count is populated from the third GROUP BY query."""
        mock_session = MagicMock()
        call_results = [
            MagicMock(all=MagicMock(return_value=[])),  # assistants
            MagicMock(all=MagicMock(return_value=[])),  # workflows
            MagicMock(all=MagicMock(return_value=[("my-proj", 3)])),  # skills
            MagicMock(all=MagicMock(return_value=[])),  # datasources
            MagicMock(all=MagicMock(return_value=[])),  # integrations
        ]
        mock_session.exec.side_effect = call_results

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["my-proj"])

        assert result["my-proj"]["skills_count"] == 3

    def test_datasources_counted_correctly(self):
        """Datasources count is populated from the fourth GROUP BY query."""
        mock_session = MagicMock()
        call_results = [
            MagicMock(all=MagicMock(return_value=[])),  # assistants
            MagicMock(all=MagicMock(return_value=[])),  # workflows
            MagicMock(all=MagicMock(return_value=[])),  # skills
            MagicMock(all=MagicMock(return_value=[("my-proj", 10)])),  # datasources
            MagicMock(all=MagicMock(return_value=[])),  # integrations
        ]
        mock_session.exec.side_effect = call_results

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["my-proj"])

        assert result["my-proj"]["datasources_count"] == 10

    def test_integrations_counted_correctly(self):
        """Integrations count is populated from the fifth GROUP BY query."""
        mock_session = MagicMock()
        call_results = [
            MagicMock(all=MagicMock(return_value=[])),  # assistants
            MagicMock(all=MagicMock(return_value=[])),  # workflows
            MagicMock(all=MagicMock(return_value=[])),  # skills
            MagicMock(all=MagicMock(return_value=[])),  # datasources
            MagicMock(all=MagicMock(return_value=[("my-proj", 2)])),  # integrations
        ]
        mock_session.exec.side_effect = call_results

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["my-proj"])

        assert result["my-proj"]["integrations_count"] == 2

    def test_multiple_projects_returned_correctly(self):
        """Multiple projects each get their own counters in the result dict."""
        mock_session = MagicMock()
        call_results = [
            MagicMock(all=MagicMock(return_value=[("proj-a", 2), ("proj-b", 1)])),  # assistants
            MagicMock(all=MagicMock(return_value=[])),  # workflows
            MagicMock(all=MagicMock(return_value=[])),  # skills
            MagicMock(all=MagicMock(return_value=[])),  # datasources
            MagicMock(all=MagicMock(return_value=[])),  # integrations
        ]
        mock_session.exec.side_effect = call_results

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["proj-a", "proj-b"])

        assert result["proj-a"]["assistants_count"] == 2
        assert result["proj-b"]["assistants_count"] == 1
        assert result["proj-a"]["workflows_count"] == 0
        assert result["proj-b"]["workflows_count"] == 0

    def test_unknown_project_in_db_result_is_ignored(self):
        """Counts for a project not in the input list are not added to the result."""
        mock_session = MagicMock()
        call_results = [
            # DB returns a row for "unknown-proj" which wasn't requested
            MagicMock(all=MagicMock(return_value=[("my-proj", 1), ("unknown-proj", 99)])),
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(all=MagicMock(return_value=[])),
            MagicMock(all=MagicMock(return_value=[])),
        ]
        mock_session.exec.side_effect = call_results

        result = application_repository.get_project_entity_counts_bulk(mock_session, ["my-proj"])

        assert "unknown-proj" not in result
        assert result["my-proj"]["assistants_count"] == 1

    def test_runs_five_queries(self):
        """Exactly 5 GROUP BY queries are executed (one per entity type)."""
        mock_session = MagicMock()
        mock_session.exec.return_value.all.return_value = []

        application_repository.get_project_entity_counts_bulk(mock_session, ["proj"])

        assert mock_session.exec.call_count == 5


# ---------------------------------------------------------------------------
# Tests for ApplicationRepository.update_fields
# ---------------------------------------------------------------------------


class TestApplicationRepositoryUpdateFields:
    """Tests for ApplicationRepository.update_fields."""

    def test_update_name_only(self):
        """update_fields sets new name when new_name is provided."""
        mock_session = MagicMock()
        app = _make_app("old-name")

        with patch.object(application_repository, "get_by_name", return_value=app):
            result = application_repository.update_fields(
                session=mock_session,
                project_name="old-name",
                new_name="new-name",
                new_description=None,
            )

        assert result.name == "new-name"
        assert result.description == "Test"  # unchanged
        mock_session.add.assert_called_once()
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    def test_update_description_only(self):
        """update_fields sets new description when new_description is provided."""
        mock_session = MagicMock()
        app = _make_app("my-project", description="old description")

        with patch.object(application_repository, "get_by_name", return_value=app):
            result = application_repository.update_fields(
                session=mock_session,
                project_name="my-project",
                new_name=None,
                new_description="fresh description",
            )

        assert result.description == "fresh description"
        assert result.name == "my-project"  # unchanged
        mock_session.flush.assert_called_once()

    def test_update_both_name_and_description(self):
        """update_fields sets both name and description when both are provided."""
        mock_session = MagicMock()
        app = _make_app("my-project", description="old description")

        with patch.object(application_repository, "get_by_name", return_value=app):
            result = application_repository.update_fields(
                session=mock_session,
                project_name="my-project",
                new_name="renamed-project",
                new_description="new description",
            )

        assert result.name == "renamed-project"
        assert result.description == "new description"
        mock_session.flush.assert_called_once()
        mock_session.refresh.assert_called_once()

    def test_returns_refreshed_instance(self):
        """update_fields returns the same Application instance after refresh."""
        mock_session = MagicMock()
        app = _make_app("my-project")

        with patch.object(application_repository, "get_by_name", return_value=app):
            result = application_repository.update_fields(
                session=mock_session,
                project_name="my-project",
                new_name="new-name",
            )

        # The result is the same object (mutated in-place)
        assert result is app

    def test_update_date_is_refreshed(self):
        """update_fields updates the update_date field."""
        mock_session = MagicMock()
        old_update_date = datetime(2025, 1, 1)
        app = _make_app("my-project")
        app.update_date = old_update_date

        with patch.object(application_repository, "get_by_name", return_value=app):
            application_repository.update_fields(
                session=mock_session,
                project_name="my-project",
                new_name="new-name",
            )

        # update_date should have been changed from the old value
        assert app.update_date != old_update_date
