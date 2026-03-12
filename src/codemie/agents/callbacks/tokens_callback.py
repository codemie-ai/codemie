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

import uuid
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import LLMResult

from codemie.configs import logger
from codemie.core.utils import calculate_token_cost
from codemie.service.request_summary_manager import request_summary_manager, LLMRun
from codemie.service.llm_service.llm_service import llm_service


class TokensCalculationCallback(AsyncCallbackHandler):
    def __init__(self, request_id: str, llm_model: str):
        super().__init__()
        self.internal_run_id = str(uuid.uuid4())
        self.request_id = request_id
        self.llm_model = llm_model
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Run when LLM ends running."""
        try:
            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0
            for gen in response.generations:
                for gen_result in gen:
                    if gen_result.message and gen_result.message.usage_metadata:
                        usage_metadata: UsageMetadata = gen_result.message.usage_metadata
                        input_tokens += usage_metadata.get("input_tokens", 0)
                        output_tokens += usage_metadata.get("output_tokens", 0)
                        cached_tokens += usage_metadata.get("input_token_details", {}).get("cache_read", 0)
                        logger.debug(f"On LLM End. Usage metadata: {usage_metadata}")
            model_costs = llm_service.get_model_cost(self.llm_model)

            # Use the utility function to calculate cost
            # Returns: (total_cost, cached_cost, cache_creation_cost)
            money_spent, cached_tokens_money_spent, _ = calculate_token_cost(
                llm_model=self.llm_model,
                cost_config=model_costs,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                cache_creation_tokens=0,  # Not exposed by LangChain yet
            )

            llm_run = LLMRun(
                run_id=str(run_id),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                money_spent=money_spent,
                cached_tokens_money_spent=cached_tokens_money_spent,
                llm_model=self.llm_model,
            )

            request_summary_manager.update_llm_run(request_id=self.request_id, llm_run=llm_run)
        except Exception as e:
            logger.error(f"Error while calculating tokens: {str(e)}")

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], **kwargs: Any) -> None:
        """Run when LLM starts running.

        Args:
            serialized (Dict[str, Any]): The serialized LLM.
            messages (List[List[BaseMessage]]): The messages to run.
            **kwargs (Any): Additional keyword arguments.
        """
