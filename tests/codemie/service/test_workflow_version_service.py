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

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import UserEntity
from codemie.core.workflow_models.workflow_config import WorkflowMode, YamlConfigHistory
from codemie.rest_api.security.user import User


EXAMPLE_PROJECT = "example_project"


@pytest.fixture
def user():
    return User(id="user-123", username="testuser", name="Test User", project_names=[EXAMPLE_PROJECT])


@pytest.fixture
def user_entity():
    return UserEntity(user_id="user-123", username="testuser", name="Test User")


def _make_entry(yaml_config: str, when: str = "2026-01-01T00:00:00") -> YamlConfigHistory:
    return YamlConfigHistory(yaml_config=yaml_config, date=datetime.fromisoformat(when))


def _make_history(count: int) -> list[YamlConfigHistory]:
    return [_make_entry(f"rev-{i}") for i in range(count)]


def _make_workflow(yaml_config: str = "current: yaml", history=None):
    wf = MagicMock()
    wf.id = "workflow-123"
    wf.name = "Test Workflow"
    wf.yaml_config = yaml_config
    wf.yaml_config_history = history if history is not None else []
    wf.assistants = []
    wf.tools = []
    wf.is_global = False
    wf.mode = WorkflowMode.SEQUENTIAL
    return wf


class TestGetVersionHistory:
    def test_returns_history_response_with_correct_fields(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        entry = _make_entry("old: yaml")
        workflow = _make_workflow(history=[entry])

        result = WorkflowVersionService.get_version_history(workflow)

        assert result.workflow_id == "workflow-123"
        assert result.workflow_name == "Test Workflow"
        assert result.total_revisions == 1
        assert len(result.revisions) == 1
        assert result.revisions[0].yaml_config == "old: yaml"
        assert result.revisions[0].revision_index == 0

    def test_slices_requested_page(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=_make_history(5))

        result = WorkflowVersionService.get_version_history(workflow, page=1, per_page=2)

        assert [e.yaml_config for e in result.revisions] == ["rev-2", "rev-3"]
        assert [e.revision_index for e in result.revisions] == [2, 3]
        assert result.total_revisions == 5

    def test_last_partial_page_keeps_absolute_indices(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=_make_history(5))

        result = WorkflowVersionService.get_version_history(workflow, page=2, per_page=2)

        assert [e.yaml_config for e in result.revisions] == ["rev-4"]
        assert [e.revision_index for e in result.revisions] == [4]

    def test_page_past_end_returns_empty_with_total(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=_make_history(5))

        result = WorkflowVersionService.get_version_history(workflow, page=10, per_page=2)

        assert result.revisions == []
        assert result.total_revisions == 5

    def test_defaults_to_first_twenty(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=_make_history(25))

        result = WorkflowVersionService.get_version_history(workflow)

        assert len(result.revisions) == 20
        assert result.revisions[0].revision_index == 0
        assert result.revisions[-1].revision_index == 19
        assert result.total_revisions == 25

    def test_none_history_returns_empty_page(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow()
        workflow.yaml_config_history = None

        result = WorkflowVersionService.get_version_history(workflow)

        assert result.revisions == []
        assert result.total_revisions == 0

    def test_returns_empty_list_when_no_history(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[])

        result = WorkflowVersionService.get_version_history(workflow)

        assert result.revisions == []
        assert result.total_revisions == 0

    def test_does_not_include_current_yaml_config(self):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        entry = _make_entry("prior: yaml")
        workflow = _make_workflow(yaml_config="current: yaml", history=[entry])

        result = WorkflowVersionService.get_version_history(workflow)

        assert all(r.yaml_config != "current: yaml" for r in result.revisions)


class TestRollbackToVersion:
    @pytest.fixture(autouse=True)
    def _stub_save_path_validation(self):
        with patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"):
            yield

    def test_rolls_back_autonomous_workflow(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(yaml_config="current: yaml", history=[_make_entry("old: yaml")])
        workflow.mode = WorkflowMode.AUTONOMOUS
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert workflow.yaml_config == "old: yaml"
        workflow.update.assert_called_once()

    def test_rolls_back_to_revision_index_from_later_page(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(yaml_config="current: yaml", history=_make_history(5))
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity
        entry = WorkflowVersionService.get_version_history(workflow, page=1, per_page=2).revisions[1]

        WorkflowVersionService.rollback_to_version(workflow, revision_index=entry.revision_index, user=mock_user)

        assert entry.revision_index == 3
        assert workflow.yaml_config == entry.yaml_config == "rev-3"

    def test_raises_400_when_history_is_empty(self, user):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService
        from codemie.core.exceptions import ExtendedHTTPException

        workflow = _make_workflow(history=[])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=user)

        assert exc_info.value.code == 400

    def test_raises_400_when_index_out_of_range(self, user):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService
        from codemie.core.exceptions import ExtendedHTTPException

        workflow = _make_workflow(history=[_make_entry("old: yaml")])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=5, user=user)

        assert exc_info.value.code == 400

    def test_raises_400_when_negative_index(self, user):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService
        from codemie.core.exceptions import ExtendedHTTPException

        workflow = _make_workflow(history=[_make_entry("old: yaml")])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=-1, user=user)

        assert exc_info.value.code == 400

    def test_calls_update_with_target_yaml_and_prepended_history(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        old_entry = _make_entry("old: yaml")
        workflow = _make_workflow(yaml_config="current: yaml", history=[old_entry])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert workflow.yaml_config == "old: yaml"
        workflow.update.assert_called_once()
        passed_history = workflow.update.call_args.kwargs["yaml_config_history"]
        assert passed_history[0].yaml_config == "current: yaml"
        assert passed_history[1] == old_entry

    def test_new_history_entry_has_created_by_set_to_user(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        new_entry = workflow.update.call_args.kwargs["yaml_config_history"][0]
        assert new_entry.created_by == user_entity

    def test_new_history_entry_persists_change_notes(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user, change_notes="hotfix")

        new_entry = workflow.update.call_args.kwargs["yaml_config_history"][0]
        assert new_entry.change_notes == "hotfix"

    def test_returns_updated_workflow(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        result = WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert result is workflow

    def test_reparses_execution_config_and_sets_updated_by(self, user_entity):
        from unittest.mock import patch

        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        current_yaml = (
            "assistants:\n"
            "  - id: current_asst\n"
            "    assistant_id: current_asst\n"
            "    name: Current\n"
            "    model: test_model\n"
            "tools: []\n"
            "states: []\n"
        )
        prior_yaml = (
            "assistants:\n"
            "  - id: restored_asst\n"
            "    assistant_id: restored_asst\n"
            "    name: Restored\n"
            "    model: test_model\n"
            "tools: []\n"
            "states: []\n"
        )
        workflow = WorkflowConfig(
            id="workflow-123",
            name="Test Workflow",
            description="desc",
            yaml_config=current_yaml,
            yaml_config_history=[_make_entry(prior_yaml)],
        )
        workflow.parse_execution_config()
        assert workflow.assistants[0].id == "current_asst"

        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with (
            patch.object(WorkflowConfig, "update", return_value=None),
            patch("codemie.service.mcp.access_control.MCPAccessControlService.validate_on_save"),
            patch(
                "codemie.service.mcp.access_control.MCPAccessControlService.strip_inline_config",
                side_effect=lambda servers: servers,
            ),
            patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        ):
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert workflow.assistants[0].id == "restored_asst"
        assert workflow.updated_by == user_entity

    def test_omitted_states_in_target_yaml_clears_current_states(self, user_entity):
        from unittest.mock import patch

        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        current_yaml = (
            "assistants:\n"
            "  - id: current_asst\n"
            "    assistant_id: current_asst\n"
            "    name: Current\n"
            "    model: test_model\n"
            "tools: []\n"
            "states:\n"
            "  - id: keep_me\n"
            "    assistant_id: current_asst\n"
            "    next: {}\n"
        )
        prior_yaml = (
            "assistants:\n"
            "  - id: restored_asst\n"
            "    assistant_id: restored_asst\n"
            "    name: Restored\n"
            "    model: test_model\n"
            "tools: []\n"
        )
        workflow = WorkflowConfig(
            id="workflow-123",
            name="Test Workflow",
            description="desc",
            yaml_config=current_yaml,
            yaml_config_history=[_make_entry(prior_yaml)],
        )
        workflow.parse_execution_config()
        assert workflow.states[0].id == "keep_me"

        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with (
            patch.object(WorkflowConfig, "update", return_value=None),
            patch("codemie.service.mcp.access_control.MCPAccessControlService.validate_on_save"),
            patch(
                "codemie.service.mcp.access_control.MCPAccessControlService.strip_inline_config",
                side_effect=lambda servers: servers,
            ),
            patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        ):
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert workflow.states == []

    def test_persists_yaml_and_history_in_a_single_update(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        old_entry = _make_entry("old: yaml")
        workflow = _make_workflow(yaml_config="current: yaml", history=[old_entry])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        workflow.update.assert_called_once()
        passed_history = workflow.update.call_args.kwargs["yaml_config_history"]
        assert passed_history[0].yaml_config == "current: yaml"
        assert passed_history[1] == old_entry
        assert "yaml_config_history" in workflow.update.call_args.kwargs

    def test_validates_mcp_and_workflow_before_persist(self, user_entity):
        from unittest.mock import patch

        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        prior_yaml = (
            "assistants:\n"
            "  - id: restored_asst\n"
            "    assistant_id: restored_asst\n"
            "    name: Restored\n"
            "    model: test_model\n"
            "tools: []\n"
            "states: []\n"
        )
        workflow = WorkflowConfig(
            id="workflow-123",
            name="Test Workflow",
            description="desc",
            yaml_config="assistants: []\ntools: []\nstates: []\n",
            yaml_config_history=[_make_entry(prior_yaml)],
        )
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity
        manager = MagicMock()

        with (
            patch.object(WorkflowConfig, "update", return_value=None) as mock_update,
            patch("codemie.service.mcp.access_control.MCPAccessControlService.validate_on_save") as mock_mcp,
            patch(
                "codemie.service.mcp.access_control.MCPAccessControlService.strip_inline_config",
                side_effect=lambda servers: servers,
            ),
            patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow") as mock_validate,
        ):
            manager.attach_mock(mock_mcp, "mcp")
            manager.attach_mock(mock_validate, "validate")
            manager.attach_mock(mock_update, "update")
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        names = [call[0] for call in manager.mock_calls]
        assert names.index("mcp") < names.index("update")
        assert names.index("validate") < names.index("update")
        mock_mcp.assert_called_once()
        mock_validate.assert_called_once()

    def test_validate_on_save_receives_restored_mcp_servers(self, user_entity):
        from unittest.mock import patch

        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        prior_yaml = (
            "assistants:\n"
            "  - id: restored_asst\n"
            "    assistant_id: restored_asst\n"
            "    name: Restored\n"
            "    model: test_model\n"
            "    mcp_servers:\n"
            "      - name: catalog_mcp\n"
            "        mcp_config_id: mcp-config-123\n"
            "tools:\n"
            "  - id: tool_1\n"
            "    tool: some_tool\n"
            "    mcp_server:\n"
            "      name: tool_mcp\n"
            "      mcp_config_id: mcp-config-456\n"
            "states: []\n"
        )
        workflow = WorkflowConfig(
            id="workflow-123",
            name="Test Workflow",
            description="desc",
            yaml_config="assistants: []\ntools: []\nstates: []\n",
            yaml_config_history=[_make_entry(prior_yaml)],
        )
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with (
            patch.object(WorkflowConfig, "update", return_value=None),
            patch("codemie.service.mcp.access_control.MCPAccessControlService.validate_on_save") as mock_mcp,
            patch(
                "codemie.service.mcp.access_control.MCPAccessControlService.strip_inline_config",
                side_effect=lambda servers: servers,
            ),
            patch(
                "codemie.service.mcp.access_control.MCPAccessControlService._strip_one",
                side_effect=lambda server: server,
            ),
            patch("codemie.workflows.workflow.WorkflowExecutor.validate_workflow"),
        ):
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        mock_mcp.assert_called_once()
        passed_servers = mock_mcp.call_args.args[0]
        assert [server.name for server in passed_servers] == ["catalog_mcp", "tool_mcp"]
        assert [server.mcp_config_id for server in passed_servers] == ["mcp-config-123", "mcp-config-456"]

    def test_raises_400_when_parse_execution_config_fails(self, user_entity):
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        workflow.parse_execution_config.side_effect = ValueError("bad yaml")
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with pytest.raises(ExtendedHTTPException) as exc_info:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Workflow Configuration error"
        workflow.update.assert_not_called()

    def test_raises_400_when_validate_workflow_raises_valueerror(self, user_entity):
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with patch(
            "codemie.workflows.workflow.WorkflowExecutor.validate_workflow",
            side_effect=ValueError("invalid graph"),
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert exc_info.value.code == 400
        assert "invalid graph" in str(exc_info.value.details)
        workflow.update.assert_not_called()

    def test_propagates_validation_exception_without_wrapping(self, user_entity):
        from codemie.core.exceptions import ValidationException
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with patch(
            "codemie.service.mcp.access_control.MCPAccessControlService.validate_on_save",
            side_effect=ValidationException("duplicate mcp"),
        ):
            with pytest.raises(ValidationException, match="duplicate mcp"):
                WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        workflow.update.assert_not_called()

    def test_raises_400_when_restored_yaml_is_invalid(self, user_entity):
        from unittest.mock import patch

        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = WorkflowConfig(
            id="workflow-123",
            name="Test Workflow",
            description="desc",
            yaml_config="assistants: []\ntools: []\nstates: []\n",
            yaml_config_history=[_make_entry("assistants: [")],
        )
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with patch.object(WorkflowConfig, "update", return_value=None) as mock_update:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert exc_info.value.code == 400
        mock_update.assert_not_called()

    def test_raises_400_when_target_yaml_is_empty(self, user_entity):
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with pytest.raises(ExtendedHTTPException) as exc_info:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert exc_info.value.code == 400
        assert "empty yaml_config" in exc_info.value.message
        workflow.parse_execution_config.assert_not_called()
        workflow.update.assert_not_called()

    def test_raises_400_when_target_yaml_is_whitespace(self, user_entity):
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("  \n")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with pytest.raises(ExtendedHTTPException) as exc_info:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert exc_info.value.code == 400
        workflow.update.assert_not_called()

    def test_empty_target_yaml_leaves_current_graph_unchanged(self, user_entity):
        from unittest.mock import patch

        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        current_yaml = (
            "assistants:\n"
            "  - id: current_asst\n"
            "    assistant_id: current_asst\n"
            "    name: Current\n"
            "    model: test_model\n"
            "tools: []\n"
            "states: []\n"
        )
        workflow = WorkflowConfig(
            id="workflow-123",
            name="Test Workflow",
            description="desc",
            yaml_config=current_yaml,
            yaml_config_history=[_make_entry("")],
        )
        workflow.parse_execution_config()
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with patch.object(WorkflowConfig, "update", return_value=None) as mock_update:
            with pytest.raises(ExtendedHTTPException) as exc_info:
                WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        assert exc_info.value.code == 400
        assert workflow.yaml_config == current_yaml
        assert workflow.assistants[0].id == "current_asst"
        mock_update.assert_not_called()

    def test_validates_marketplace_when_workflow_is_global(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        workflow.is_global = True
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity
        with patch("codemie.service.workflow_service._marketplace_service") as mock_marketplace:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        mock_marketplace.validate.assert_called_once_with(workflow, mock_user)
        workflow.update.assert_called_once()

    def test_skips_marketplace_when_workflow_is_not_global(self, user_entity):
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with patch("codemie.service.workflow_service._marketplace_service") as mock_marketplace:
            WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        mock_marketplace.validate.assert_not_called()
        workflow.update.assert_called_once()

    def test_yaml_config_restored_when_validation_fails(self, user_entity):
        from unittest.mock import patch

        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.core.workflow_models.workflow_config import WorkflowConfig
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        prior_yaml = "assistants: []\ntools: []\nstates: []\n"
        current_yaml = "assistants: []\ntools: []\nstates: []\ncurrent: true\n"
        workflow = WorkflowConfig(
            id="workflow-123",
            name="Test Workflow",
            description="desc",
            yaml_config=current_yaml,
            yaml_config_history=[_make_entry(prior_yaml)],
        )
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with (
            patch.object(WorkflowConfig, "update"),
            patch("codemie.service.mcp.access_control.MCPAccessControlService.validate_on_save"),
            patch(
                "codemie.service.mcp.access_control.MCPAccessControlService.strip_inline_config",
                side_effect=lambda s: s,
            ),
            patch(
                "codemie.workflows.workflow.WorkflowExecutor.validate_workflow",
                side_effect=ValueError("graph rejected"),
            ),
        ):
            with pytest.raises(ExtendedHTTPException):
                WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        # yaml_config must be restored to its pre-rollback value after failure
        assert workflow.yaml_config == current_yaml

    def test_marketplace_validation_failure_does_not_persist(self, user_entity):
        from codemie.core.exceptions import ValidationException
        from codemie.service.workflow.workflow_version_service import WorkflowVersionService

        workflow = _make_workflow(history=[_make_entry("old: yaml")])
        workflow.is_global = True
        mock_user = MagicMock()
        mock_user.as_user_model.return_value = user_entity

        with patch("codemie.service.workflow_service._marketplace_service") as mock_marketplace:
            mock_marketplace.validate.side_effect = ValidationException("external assistants")
            with pytest.raises(ValidationException, match="external assistants"):
                WorkflowVersionService.rollback_to_version(workflow, revision_index=0, user=mock_user)

        workflow.update.assert_not_called()
