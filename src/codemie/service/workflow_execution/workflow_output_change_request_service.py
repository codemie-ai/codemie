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

from codemie.chains.pure_chat_chain import PureChatChain
from codemie.core.models import AssistantChatRequest
from codemie.service.llm_service.llm_service import llm_service
from codemie.core.dependecies import get_llm_by_credentials
from codemie.templates.workflow_output_change_prompt import PROMPT

LLM_MODEL = llm_service.default_llm_model
LLM = get_llm_by_credentials(llm_model=LLM_MODEL)


class WorkflowOutputChangeRequestService:
    """Based on workflow execution output, ask LLM to change the output according to the request."""

    @staticmethod
    def run(original_output: str, changes_request: str) -> str:
        chat_request = AssistantChatRequest(text=changes_request)
        response = PureChatChain(
            request=chat_request,
            system_prompt=PROMPT.format(output=original_output),
            llm_model=LLM_MODEL,
            llm=LLM,
        ).generate()

        return response.generated
