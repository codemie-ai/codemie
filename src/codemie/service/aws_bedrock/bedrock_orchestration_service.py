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

from typing import List, Optional
from codemie.rest_api.models.assistant import Assistant, AssistantType
from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService, InvokeAgentResponse
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import (
    BedrockAgentCoreRuntimeService,
    InvokeAgentCoreRuntimeResponse,
)
from codemie.service.aws_bedrock.bedrock_flow_service import BedrockFlowService
from codemie.service.aws_bedrock.bedrock_guardrail_service import BedrockGuardrailService
from codemie.service.aws_bedrock.bedrock_knowledge_base_service import BedrockKnowledgeBaseService


class BedrockOrchestratorService:
    @staticmethod
    def delete_all_entities(setting_id):
        BedrockAgentService.delete_entities(setting_id)
        BedrockFlowService.delete_entities(setting_id)
        BedrockGuardrailService.delete_entities(setting_id)
        BedrockKnowledgeBaseService.delete_entities(setting_id)
        BedrockAgentCoreRuntimeService.delete_entities(setting_id)

    @staticmethod
    def invoke_bedrock_assistant(
        assistant: Assistant,
        input_text: str,
        conversation_id: str,
        chat_history: Optional[List] = None,
    ) -> InvokeAgentResponse | InvokeAgentCoreRuntimeResponse:
        """
        Unified invocation method for AWS Bedrock assistants.

        Determines the assistant type (Bedrock Agent or AgentCore Runtime) and
        invokes the appropriate service method.

        Args:
            assistant: The assistant to invoke
            input_text: The input text to send
            conversation_id: The conversation session ID
            chat_history: Optional chat history (used for Bedrock Agents)

        Returns:
            Response dict with 'output' and 'time_elapsed' keys

        Raises:
            ValueError: If the assistant is not a Bedrock assistant
        """
        if assistant.type == AssistantType.BEDROCK_AGENT:
            return BedrockAgentService.invoke_agent(
                assistant=assistant,
                input_text=input_text,
                conversation_id=conversation_id,
                chat_history=chat_history,
            )
        elif assistant.type == AssistantType.BEDROCK_AGENTCORE_RUNTIME:
            return BedrockAgentCoreRuntimeService.invoke_agentcore_runtime(
                assistant=assistant,
                input_text=input_text,
                conversation_id=conversation_id,
            )
        else:
            raise ValueError(f"Assistant type {assistant.type} is not a Bedrock assistant type")

    @staticmethod
    def is_bedrock_assistant(assistant: Assistant) -> bool:
        """
        Check if an assistant is a Bedrock-based assistant (Agent or AgentCore Runtime).

        Args:
            assistant: The assistant to check

        Returns:
            True if the assistant is a Bedrock assistant, False otherwise
        """
        if not assistant:
            return False

        return bool(
            (assistant.bedrock and assistant.bedrock.bedrock_agent_id)
            or (assistant.bedrock_agentcore_runtime and assistant.bedrock_agentcore_runtime.runtime_endpoint_arn)
        )
