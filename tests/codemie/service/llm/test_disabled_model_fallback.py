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

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from codemie.configs.llm_config import LLMConfig, LLMModel, ModelStatus
from codemie.core.exceptions import ModelNotSelectableError
from codemie.service.llm.model_availability_service import ModelAvailabilityService
from codemie.service.llm_service.llm_service import LLMService


@pytest.fixture
def fallback_context():
    models = [
        LLMModel(base_name="disabled", deployment_name="dep-disabled", enabled=True, status=ModelStatus.DISABLED),
        LLMModel(base_name="deprecated", deployment_name="dep-deprecated", enabled=True, status=ModelStatus.DEPRECATED),
        LLMModel(base_name="global-default", deployment_name="dep-global", enabled=True, default=True),
        LLMModel(base_name="project-default", deployment_name="dep-project", enabled=True),
    ]
    service = LLMService(
        LLMConfig(yaml_file=Path('tests/service/llm_test_config.yaml'), llm_models=[], embeddings_models=[])
    )
    service.get_all_llm_model_info = Mock(return_value=models)
    service.get_all_embedding_model_info = Mock(return_value=[])
    service.get_admin_global_default_choice = Mock(return_value=None)
    project = SimpleNamespace(id="project", default_model="project-default", allowed_models=None)
    customer = Mock()
    customer.is_feature_enabled.return_value = False
    with (
        patch("codemie.service.llm_service.llm_service.llm_service", service),
        patch("codemie.agents.assistant_agent.llm_service", service),
        patch("codemie.service.llm.model_availability_service.Application.get_by_id", return_value=project) as load,
        patch("codemie.service.llm.model_availability_service.customer_config", customer),
        patch("codemie.service.llm_service.llm_service.customer_config", customer),
    ):
        yield SimpleNamespace(
            service=service, models=models, project=project, load=load, flag=customer.is_feature_enabled
        )


@pytest.mark.parametrize("model_name", ["disabled", "dep-disabled"])
@pytest.mark.parametrize("is_global", [False, True])
@pytest.mark.parametrize("feature_enabled", [False, True])
def test_execution_uses_project_or_global_default(fallback_context, model_name, is_global, feature_enabled):
    context = fallback_context
    context.flag.return_value = feature_enabled
    context.project.allowed_models = ["disabled", "dep-disabled", "project-default", "global-default"]

    resolved, overridden = ModelAvailabilityService.resolve_model_for_execution(
        model_name, "project", is_global=is_global
    )

    assert resolved == ("global-default" if is_global else "project-default")
    assert overridden is True
    if is_global:
        context.load.assert_not_called()


@pytest.mark.parametrize("model_name", ["deprecated", "dep-deprecated", "global-default"])
def test_ongoing_deprecated_and_enabled_models_do_not_fall_back(fallback_context, model_name):
    resolved, overridden = ModelAvailabilityService.resolve_model_for_execution(model_name, "project")

    assert (resolved, overridden) == (model_name, False)
    fallback_context.load.assert_not_called()


def test_model_details_cannot_return_disabled_model_to_a_provider(fallback_context):
    service = fallback_context.service
    assert service.get_model_details("dep-disabled").base_name == "global-default"
    assert service.get_model_details("disabled", project=fallback_context.project).base_name == "project-default"
    assert service.get_model_details("dep-deprecated").base_name == "deprecated"


def test_disabled_project_default_uses_enabled_allowed_fallback(fallback_context):
    context = fallback_context
    context.models[-1].status = ModelStatus.DISABLED
    context.project.allowed_models = ["disabled", "project-default", "global-default"]
    context.flag.return_value = True

    assert ModelAvailabilityService.resolve_model_for_execution("disabled", "project") == ("global-default", True)


def test_no_enabled_fallback_fails_explicitly(fallback_context):
    context = fallback_context
    for model in context.models:
        model.status = ModelStatus.DISABLED

    with pytest.raises(ModelNotSelectableError, match="no selectable fallback"):
        ModelAvailabilityService.resolve_model_for_execution("disabled", "project", is_global=True)


def test_provider_receives_project_default_details(fallback_context):
    from codemie.core.dependecies import get_llm_by_credentials

    with (
        patch("codemie.core.dependecies.llm_service", fallback_context.service),
        patch("codemie.enterprise.litellm.get_litellm_chat_model", return_value=Mock()) as factory,
        patch("codemie.core.dependecies._should_wrap_llm_client", return_value=False),
    ):
        get_llm_by_credentials(llm_model="dep-disabled", project=fallback_context.project)

    assert factory.call_args.kwargs["llm_model_details"].base_name == "project-default"


@pytest.mark.parametrize("is_global", [False, True])
def test_assistant_execution_passes_resolved_model_to_llm(fallback_context, is_global):
    from codemie.agents.assistant_agent import AIToolsAgent

    agent = object.__new__(AIToolsAgent)
    agent.assistant = SimpleNamespace(project="project", is_global=is_global)
    agent.llm_model = "dep-disabled"
    agent._resolved_llm_model = None
    agent.temperature = None
    agent.top_p = None
    agent.request_uuid = "request-id"
    with patch("codemie.agents.assistant_agent.get_llm_by_credentials") as factory:
        agent._initialize_llm()

    assert factory.call_args.kwargs["llm_model"] == ("global-default" if is_global else "project-default")
    assert agent.llm_model == "dep-disabled"


@pytest.mark.parametrize("is_global", [False, True])
def test_workflow_execution_resolves_disabled_model_without_project_flag(fallback_context, is_global):
    from codemie.workflows.nodes.base_node import BaseNode

    node = SimpleNamespace(
        workflow_config=SimpleNamespace(project="project", is_global=is_global), execution_id="execution-id"
    )
    resolved, overridden = BaseNode.resolve_execution_model(node, "dep-disabled")

    assert resolved == ("global-default" if is_global else "project-default")
    assert overridden is True
