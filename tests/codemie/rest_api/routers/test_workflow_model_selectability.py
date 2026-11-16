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

from unittest.mock import patch

import pytest
import yaml
from fastapi import BackgroundTasks

from codemie.configs.llm_config import LLMModel, ModelStatus
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.workflow_models import CreateWorkflowRequest, UpdateWorkflowRequest, WorkflowConfig
from codemie.rest_api.routers import workflow as workflow_router
from codemie.rest_api.security.user import User


def _model_section(section: str, entries: list[tuple[str, str]]) -> dict:
    """Build a minimal YAML section for workflow model-selection tests."""
    return {
        section: [
            {
                "id": item_id,
                "model": model,
                **({"custom_node_id": "state_processor_node"} if section == "custom_nodes" else {}),
            }
            for item_id, model in entries
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "update", "validate"])
@pytest.mark.parametrize("model_name", ["retired-model", "dep-retired-model-2025", " dep-retired-model-2025 "])
@pytest.mark.parametrize("model_status", [ModelStatus.DEPRECATED, ModelStatus.DISABLED])
@pytest.mark.parametrize("section", ["assistants", "custom_nodes"])
async def test_workflow_rejects_unselectable_model_from_yaml(operation, model_name, model_status, section):
    """Exercise YAML parsing and the real guard before normalization or persistence."""
    model = LLMModel(
        base_name="retired-model", deployment_name="dep-retired-model-2025", enabled=True, status=model_status
    )
    payload = {
        "name": "Model guard workflow",
        "description": "Workflow model selectability regression",
        "project": "demo",
        "yaml_config": yaml.safe_dump(_model_section(section, [("a1", model_name)])),
    }
    workflow = WorkflowConfig(id="workflow-123", **payload)
    workflow.yaml_config = yaml.safe_dump(_model_section(section, [("a1", "active-model")]))
    workflow.parse_execution_config()
    request = CreateWorkflowRequest(**payload) if operation == "create" else UpdateWorkflowRequest(**payload)
    assert not request.model_dump().get(section)
    user = User(id="user-123", username="testuser")

    with (
        patch.object(workflow_router.llm_service, "get_all_llm_model_info", return_value=[model]),
        patch.object(workflow_router, "project_access_check"),
        patch.object(workflow_router, "_normalize_workflow_model_ids") as normalize,
        patch.object(workflow_router, "_persist_and_finalize_new_workflow") as create,
        patch.object(workflow_router, "_run_pre_persist_update_validation") as validate,
        patch.object(workflow_router.workflow_service, "get_workflow", return_value=workflow),
        patch.object(workflow_router.workflow_service, "update_workflow") as update,
        patch("codemie.core.ability.Ability.can", return_value=True),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            if operation == "create":
                workflow_router.create_workflow(request, BackgroundTasks(), user)
            elif operation == "update":
                await workflow_router.update_workflow(workflow.id, request, BackgroundTasks(), user)
            else:
                await workflow_router.validate_workflow(workflow.id, request, user)

    assert exc_info.value.code == 400
    assert exc_info.value.message == "Model not selectable"
    assert model_name.strip() in exc_info.value.details
    create.assert_not_called()
    update.assert_not_called()
    validate.assert_not_called()
    normalize.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "validate"])
@pytest.mark.parametrize(
    "previous_name,requested_name",
    [
        ("retired-model", "retired-model"),
        ("dep-retired-model-2025", "dep-retired-model-2025"),
        ("retired-model", "dep-retired-model-2025"),
        ("dep-retired-model-2025", "retired-model"),
        ("retired-model", "active-model"),
    ],
)
@pytest.mark.parametrize("section", ["assistants", "custom_nodes"])
async def test_workflow_update_keeps_existing_deprecated_selection(operation, previous_name, requested_name, section):
    retired = LLMModel(
        base_name="retired-model",
        deployment_name="dep-retired-model-2025",
        enabled=True,
        status=ModelStatus.DEPRECATED,
    )
    active = LLMModel(base_name="active-model", deployment_name="dep-active-model", enabled=True)
    payload = {
        "name": "Existing workflow",
        "description": "Original description",
        "project": "demo",
        "yaml_config": yaml.safe_dump(_model_section(section, [("a1", previous_name), ("a2", "active-model")])),
    }
    workflow = WorkflowConfig(id="workflow-123", **payload)
    workflow.parse_execution_config()
    payload["description"] = "Updated description"
    # Reorder entries as well to ensure the old selection is matched by workflow entry id.
    payload["yaml_config"] = yaml.safe_dump(_model_section(section, [("a2", "active-model"), ("a1", requested_name)]))
    request = UpdateWorkflowRequest(**payload)
    user = User(id="user-123", username="testuser")

    with (
        patch.object(workflow_router.llm_service, "get_all_llm_model_info", return_value=[retired, active]),
        patch.object(workflow_router, "project_access_check"),
        patch.object(workflow_router, "_normalize_workflow_model_ids"),
        patch.object(workflow_router, "_run_pre_persist_update_validation") as validate,
        patch.object(workflow_router, "_consumer_slot_warnings", return_value=[]),
        patch.object(workflow_router.GuardrailService, "sync_guardrail_assignments_for_entity"),
        patch.object(workflow_router.GuardrailService, "get_entity_guardrail_assignments", return_value=None),
        patch.object(workflow_router.workflow_service, "get_workflow", return_value=workflow),
        patch.object(workflow_router.workflow_service, "update_workflow", return_value=workflow) as update,
        patch("codemie.core.ability.Ability.can", return_value=True),
    ):
        if operation == "update":
            result = await workflow_router.update_workflow(workflow.id, request, BackgroundTasks(), user)
            assert result["message"] == "Workflow updated successfully"
            update.assert_called_once()
            proposed = update.call_args.args[1]
        else:
            result = await workflow_router.validate_workflow(workflow.id, request, user)
            assert result.message == "Workflow configuration is valid"
            update.assert_not_called()
            proposed = validate.call_args.args[1]

    validate.assert_awaited_once()
    assert proposed.description == "Updated description"
    assert {item.id: item.model for item in getattr(proposed, section)} == {
        "a1": requested_name,
        "a2": "active-model",
    }
    assert getattr(workflow, section)[0].model == previous_name


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "validate"])
@pytest.mark.parametrize("model_name", ["retired-model", "dep-retired-model-2025"])
@pytest.mark.parametrize("section", ["assistants", "custom_nodes"])
async def test_new_workflow_entry_cannot_reuse_another_existing_entries_deprecated_model(
    operation, model_name, section
):
    model = LLMModel(
        base_name="retired-model",
        deployment_name="dep-retired-model-2025",
        enabled=True,
        status=ModelStatus.DEPRECATED,
    )
    payload = {
        "name": "Existing workflow",
        "description": "Existing workflow entry keeps its model",
        "project": "demo",
        "yaml_config": yaml.safe_dump(_model_section(section, [("a1", "retired-model")])),
    }
    workflow = WorkflowConfig(id="workflow-123", **payload)
    workflow.parse_execution_config()
    payload["yaml_config"] = yaml.safe_dump(
        _model_section(section, [("a1", "retired-model"), ("new-entry", model_name)])
    )
    request = UpdateWorkflowRequest(**payload)
    user = User(id="user-123", username="testuser")
    with (
        patch.object(workflow_router.llm_service, "get_all_llm_model_info", return_value=[model]),
        patch.object(workflow_router, "project_access_check"),
        patch.object(workflow_router, "_run_pre_persist_update_validation") as validate,
        patch.object(workflow_router.workflow_service, "get_workflow", return_value=workflow),
        patch.object(workflow_router.workflow_service, "update_workflow") as update,
        patch("codemie.core.ability.Ability.can", return_value=True),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            if operation == "update":
                await workflow_router.update_workflow(workflow.id, request, BackgroundTasks(), user)
            else:
                await workflow_router.validate_workflow(workflow.id, request, user)

    assert exc_info.value.code == 400
    assert exc_info.value.message == "Model not selectable"
    validate.assert_not_called()
    update.assert_not_called()
