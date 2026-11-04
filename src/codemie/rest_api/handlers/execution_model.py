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

"""The model one chat request runs on, for the parts of a request that need it outside build_agent."""

from __future__ import annotations

from typing import Any

from codemie.core.exceptions import ModelNotWhitelistedException, NoDefaultModelException
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.service.assistant_service import AssistantService
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService


class RequestExecutionModel:
    """Tracks the model a request runs on: resolved before the agent exists, then the built agent's own.

    History replay is chosen before build_agent runs, so the model is resolved once (silently; build_agent
    reports any substitution) and kept for the request. Once the agent is built, its llm_model replaces it.
    A2A and Bedrock assistants run no platform model, so they keep the raw pick.
    """

    def __init__(self, assistant: Assistant) -> None:
        self._assistant = assistant
        self._model: str | None = None

    def record_agent(self, agent: Any) -> None:
        """Keep the model the built agent runs on."""
        model = getattr(agent, "llm_model", None)
        if isinstance(model, str) and model:
            self._model = model

    def for_request(self, request: AssistantChatRequest) -> str | None:
        """The built agent's llm_model once it exists, else the model resolved from the request.

        When the project allows no model, build_agent raises; the raw pick is reported then.
        """
        if self._model is None:
            self._model = self._resolve_before_build(request)
        return self._model

    def _resolve_before_build(self, request: AssistantChatRequest) -> str | None:
        runs_platform_agent = self._assistant.type != AssistantType.A2A and not (
            BedrockOrchestratorService.is_bedrock_assistant(self._assistant)
        )
        if runs_platform_agent:
            try:
                return AssistantService.resolve_execution_model(self._assistant, request)
            except (ModelNotWhitelistedException, NoDefaultModelException):
                pass
        return request.llm_model or self._assistant.llm_model_type
