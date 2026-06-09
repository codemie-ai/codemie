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
from codemie.configs.logger import current_user_email, logging_user_id
from codemie.core.dependecies import get_llm_by_credentials, get_project_for_metric
from codemie.core.models import AssistantChatRequest
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.base_monitoring_service import emit_llm_token_metric
from codemie.service.monitoring.metrics_constants import WORKFLOW_OUTPUT_CHANGE_TOTAL_METRIC, MetricsAttributes
from codemie.service.request_summary_manager import request_summary_manager
from codemie.templates.workflow_output_change_prompt import PROMPT


class WorkflowOutputChangeRequestService:
    """Based on workflow execution output, ask LLM to change the output according to the request."""

    @staticmethod
    def run(original_output: str, changes_request: str, request_id: str | None = None) -> str:
        llm_model = llm_service.default_llm_model
        chat_request = AssistantChatRequest(text=changes_request)
        try:
            llm = get_llm_by_credentials(llm_model=llm_model, request_id=request_id)
            response = PureChatChain(
                request=chat_request,
                system_prompt=PROMPT.format(output=original_output),
                llm_model=llm_model,
                llm=llm,
            ).generate()

            emit_llm_token_metric(
                name=WORKFLOW_OUTPUT_CHANGE_TOTAL_METRIC,
                request_id=request_id,
                base_attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                    MetricsAttributes.PROJECT: get_project_for_metric(),
                },
            )

            return response.generated
        finally:
            if request_id:
                request_summary_manager.clear_summary(request_id)
