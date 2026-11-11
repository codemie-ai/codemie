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

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from pydantic import ValidationError

from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User


EXAMPLE_PROJECT = "example_project"


@pytest.fixture
def user():
    return User(id="user-123", username="testuser", name="Test User", project_names=[EXAMPLE_PROJECT])


@pytest.fixture
def workflow():
    wf = MagicMock()
    wf.id = "workflow-123"
    wf.name = "Test Workflow"
    wf.project_name = EXAMPLE_PROJECT
    return wf


class TestGetWorkflowVersions:
    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    @patch("codemie.rest_api.routers.workflow.workflow_service")
    def test_loads_record_without_parsing_current_yaml(
        self, mock_svc, mock_get_by_id, mock_ability, mock_version_svc, user, workflow
    ):
        from codemie.rest_api.routers.workflow import get_workflow_versions

        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        mock_version_svc.get_version_history.return_value = MagicMock()

        get_workflow_versions(workflow_id="workflow-123", user=user)

        mock_get_by_id.assert_called_once_with("workflow-123")
        mock_svc.get_workflow.assert_not_called()

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_returns_version_history(self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow):
        from codemie.rest_api.routers.workflow import get_workflow_versions

        expected = MagicMock()
        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        mock_version_svc.get_version_history.return_value = expected

        result = get_workflow_versions(workflow_id="workflow-123", user=user)

        assert result == expected
        mock_version_svc.get_version_history.assert_called_once_with(workflow, 0, 20)

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_passes_page_and_per_page_to_service(self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow):
        from codemie.rest_api.routers.workflow import get_workflow_versions

        mock_get_by_id.return_value = workflow
        mock_ability.return_value.can.return_value = True

        get_workflow_versions(workflow_id="workflow-123", page=2, per_page=5, user=user)

        mock_version_svc.get_version_history.assert_called_once_with(workflow, 2, 5)

    def test_declares_page_and_per_page_query_params(self):
        from fastapi.routing import APIRoute

        from codemie.rest_api.routers.workflow import router

        route = next(
            r
            for r in router.routes
            if isinstance(r, APIRoute) and r.path == "/v1/workflows/{workflow_id}/versions" and "GET" in r.methods
        )
        assert {p.name for p in route.dependant.query_params} == {"page", "per_page"}

    def test_declares_version_history_response_model(self):
        from fastapi.routing import APIRoute

        from codemie.rest_api.models.workflow_version import WorkflowVersionHistoryResponse
        from codemie.rest_api.routers.workflow import router

        route = next(
            r
            for r in router.routes
            if isinstance(r, APIRoute) and r.path == "/v1/workflows/{workflow_id}/versions" and "GET" in r.methods
        )
        assert route.response_model is WorkflowVersionHistoryResponse

    def test_declares_rollback_post_route(self):
        from fastapi.routing import APIRoute

        from codemie.core.models import BaseResponse
        from codemie.rest_api.routers.workflow import router

        route = next(
            r
            for r in router.routes
            if isinstance(r, APIRoute)
            and r.path == "/v1/workflows/{workflow_id}/versions/rollback"
            and "POST" in r.methods
        )
        assert route.response_model is BaseResponse

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_raises_not_found_when_workflow_missing(self, mock_get_by_id, mock_ability, mock_version_svc, user):
        from codemie.rest_api.routers.workflow import get_workflow_versions

        mock_get_by_id.side_effect = KeyError("workflow-999")

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_workflow_versions(workflow_id="workflow-999", user=user)

        assert exc_info.value.code == 404

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_non_keyerror_from_get_by_id_propagates(self, mock_get_by_id, mock_ability, mock_version_svc, user):
        from codemie.rest_api.routers.workflow import get_workflow_versions

        # Only KeyError should be caught as 404; other exceptions must propagate
        mock_get_by_id.side_effect = ValueError("db connection failed")

        with pytest.raises(ValueError):
            get_workflow_versions(workflow_id="workflow-123", user=user)

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_raises_access_denied_when_cannot_write(
        self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow
    ):
        from codemie.rest_api.routers.workflow import get_workflow_versions

        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = False
        mock_ability.return_value = mock_ability_instance

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_workflow_versions(workflow_id="workflow-123", user=user)

        assert exc_info.value.code == 401
        mock_ability_instance.can.assert_called_once_with(Action.WRITE, workflow)
        mock_version_svc.get_version_history.assert_not_called()

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_raises_access_denied_for_read_only_user(
        self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow
    ):
        from codemie.rest_api.routers.workflow import get_workflow_versions

        mock_get_by_id.return_value = workflow
        mock_ability.return_value.can.side_effect = lambda action, _entity: action == Action.READ

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_workflow_versions(workflow_id="workflow-123", user=user)

        assert exc_info.value.code == 401
        mock_version_svc.get_version_history.assert_not_called()


class TestGetWorkflowVersionsHttp:
    @pytest.fixture
    def client(self, user):
        from codemie.rest_api.security.authentication import authenticate

        app.dependency_overrides = {authenticate: lambda: user}
        yield TestClient(app)
        app.dependency_overrides = {}

    @pytest.mark.parametrize("query", ["page=-1", "per_page=0", "per_page=101"])
    def test_rejects_out_of_bounds_params(self, client, query):
        response = client.get(f"/v1/workflows/workflow-123/versions?{query}")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_omitted_params_use_defaults(self, mock_get_by_id, mock_ability, mock_version_svc, client, workflow):
        from codemie.rest_api.models.workflow_version import WorkflowVersionHistoryResponse

        mock_get_by_id.return_value = workflow
        mock_ability.return_value.can.return_value = True
        mock_version_svc.get_version_history.return_value = WorkflowVersionHistoryResponse(
            revisions=[], total_revisions=0, workflow_name="Test Workflow", workflow_id="workflow-123"
        )

        response = client.get("/v1/workflows/workflow-123/versions")

        assert response.status_code == status.HTTP_200_OK
        mock_version_svc.get_version_history.assert_called_once_with(workflow, 0, 20)


class TestRollbackWorkflowVersion:
    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    @patch("codemie.rest_api.routers.workflow.workflow_service")
    def test_loads_record_without_parsing_current_yaml(
        self, mock_svc, mock_get_by_id, mock_ability, mock_version_svc, user, workflow
    ):
        from codemie.rest_api.models.workflow_version import WorkflowRollbackRequest
        from codemie.rest_api.routers.workflow import rollback_workflow_version

        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        mock_version_svc.rollback_to_version.return_value = workflow
        request = WorkflowRollbackRequest(revision_index=0)

        rollback_workflow_version(workflow_id="workflow-123", request=request, user=user, background_tasks=MagicMock())

        mock_get_by_id.assert_called_once_with("workflow-123")
        mock_svc.get_workflow.assert_not_called()

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_returns_success_response(self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow):
        from codemie.rest_api.routers.workflow import rollback_workflow_version
        from codemie.rest_api.models.workflow_version import WorkflowRollbackRequest

        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        request = WorkflowRollbackRequest(revision_index=0)

        result = rollback_workflow_version(
            workflow_id="workflow-123", request=request, user=user, background_tasks=MagicMock()
        )

        assert "workflow-123" in result.message
        assert "0" in result.message

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_calls_rollback_service_with_correct_args(
        self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow
    ):
        from codemie.rest_api.routers.workflow import rollback_workflow_version
        from codemie.rest_api.models.workflow_version import WorkflowRollbackRequest

        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        request = WorkflowRollbackRequest(revision_index=2, change_notes="hotfix")

        rollback_workflow_version(workflow_id="workflow-123", request=request, user=user, background_tasks=MagicMock())

        mock_version_svc.rollback_to_version.assert_called_once_with(
            workflow=workflow,
            revision_index=2,
            user=user,
            change_notes="hotfix",
        )

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_raises_not_found_when_workflow_missing(self, mock_get_by_id, mock_ability, mock_version_svc, user):
        from codemie.rest_api.routers.workflow import rollback_workflow_version
        from codemie.rest_api.models.workflow_version import WorkflowRollbackRequest

        mock_get_by_id.side_effect = KeyError("workflow-999")
        request = WorkflowRollbackRequest(revision_index=0)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            rollback_workflow_version(
                workflow_id="workflow-999", request=request, user=user, background_tasks=MagicMock()
            )

        assert exc_info.value.code == 404

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_non_keyerror_from_get_by_id_propagates(self, mock_get_by_id, mock_ability, mock_version_svc, user):
        from codemie.rest_api.routers.workflow import rollback_workflow_version
        from codemie.rest_api.models.workflow_version import WorkflowRollbackRequest

        # Only KeyError should be caught as 404; other exceptions must propagate
        mock_get_by_id.side_effect = ValueError("db connection failed")
        request = WorkflowRollbackRequest(revision_index=0)

        with pytest.raises(ValueError):
            rollback_workflow_version(
                workflow_id="workflow-123", request=request, user=user, background_tasks=MagicMock()
            )

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_raises_access_denied_when_cannot_write(
        self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow
    ):
        from codemie.rest_api.routers.workflow import rollback_workflow_version
        from codemie.rest_api.models.workflow_version import WorkflowRollbackRequest

        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = False
        mock_ability.return_value = mock_ability_instance
        request = WorkflowRollbackRequest(revision_index=0)

        with pytest.raises(ExtendedHTTPException) as exc_info:
            rollback_workflow_version(
                workflow_id="workflow-123", request=request, user=user, background_tasks=MagicMock()
            )

        assert exc_info.value.code == 401
        mock_ability_instance.can.assert_called_once_with(Action.WRITE, workflow)

    @patch("codemie.rest_api.routers.workflow.WorkflowVersionService")
    @patch("codemie.rest_api.routers.workflow.Ability")
    @patch("codemie.rest_api.routers.workflow.WorkflowConfig.get_by_id")
    def test_schedules_schema_refresh_after_rollback(
        self, mock_get_by_id, mock_ability, mock_version_svc, user, workflow
    ):
        from codemie.rest_api.models.workflow_version import WorkflowRollbackRequest
        from codemie.rest_api.routers.workflow import (
            rollback_workflow_version,
            run_in_thread_pool,
            update_workflow_schema,
        )

        mock_get_by_id.return_value = workflow
        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        rolled_back = MagicMock()
        mock_version_svc.rollback_to_version.return_value = rolled_back
        request = WorkflowRollbackRequest(revision_index=0)
        background_tasks = MagicMock()

        rollback_workflow_version(
            workflow_id="workflow-123",
            request=request,
            user=user,
            background_tasks=background_tasks,
        )

        background_tasks.add_task.assert_called_once_with(run_in_thread_pool, update_workflow_schema, rolled_back, user)


class TestWorkflowRollbackRequest:
    def test_accepts_change_notes_at_max_length(self):
        from codemie.rest_api.models.workflow_version import CHANGE_NOTES_MAX_LENGTH, WorkflowRollbackRequest

        request = WorkflowRollbackRequest(revision_index=0, change_notes="x" * CHANGE_NOTES_MAX_LENGTH)
        assert request.change_notes is not None
        assert len(request.change_notes) == CHANGE_NOTES_MAX_LENGTH

    def test_rejects_change_notes_over_max_length(self):
        from codemie.rest_api.models.workflow_version import CHANGE_NOTES_MAX_LENGTH, WorkflowRollbackRequest

        with pytest.raises(ValidationError):
            WorkflowRollbackRequest(revision_index=0, change_notes="x" * (CHANGE_NOTES_MAX_LENGTH + 1))


class TestWorkflowVersionRouteAuthentication:
    def test_get_versions_requires_authentication(self):
        app.dependency_overrides = {}
        client = TestClient(app)

        response = client.get("/v1/workflows/workflow-123/versions")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_rollback_requires_authentication(self):
        app.dependency_overrides = {}
        client = TestClient(app)

        response = client.post(
            "/v1/workflows/workflow-123/versions/rollback",
            json={"revision_index": 0},
        )

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
