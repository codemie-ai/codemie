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

import threading
from datetime import datetime, timezone
from typing import Dict
from typing import List, Optional

from pydantic import BaseModel, Field

from codemie.configs import logger
from codemie.core.models import UserEntity, TokensUsage


class LLMRun(BaseModel):
    run_id: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    money_spent: float
    cached_tokens_money_spent: float = 0.0
    llm_model: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RequestSummary(BaseModel):
    request_id: str
    llm_runs: List[LLMRun] = []
    project_name: Optional[str] = None
    user: Optional[UserEntity] = None
    tokens_usage: Optional[TokensUsage] = None

    def calculate(self):
        total_input_tokens = sum(run.input_tokens for run in self.llm_runs)
        total_output_tokens = sum(run.output_tokens for run in self.llm_runs)
        total_cached_tokens = sum(run.cached_tokens for run in self.llm_runs)
        total_money_spent = sum(run.money_spent for run in self.llm_runs)
        total_cached_tokens_money_spent = sum(run.cached_tokens_money_spent for run in self.llm_runs)

        self.tokens_usage = TokensUsage(
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cached_tokens=total_cached_tokens,
            money_spent=total_money_spent,
            cached_tokens_money_spent=total_cached_tokens_money_spent,
        )

        logger.debug(
            f"Calculate LLM Costs. Request: {self.request_id}, "
            f"Input tokens: {total_input_tokens}, Output tokens: {total_output_tokens}, "
            f"Cached tokens: {total_cached_tokens}, Total cost: ${total_money_spent:.6f}, "
            f"Cached cost: ${total_cached_tokens_money_spent:.6f}"
        )


class RequestSummaryManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(RequestSummaryManager, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.request_summaries: Dict[str, RequestSummary] = {}

    def create_request_summary(
        self, request_id: str, project_name: Optional[str] = None, user: Optional[UserEntity] = None
    ) -> RequestSummary:
        if request_id in self.request_summaries:
            return self.request_summaries[request_id]
        else:
            new_summary = RequestSummary(request_id=request_id, project_name=project_name, user=user)
            self.request_summaries[request_id] = new_summary
            return new_summary

    def update_llm_run(self, request_id: str, llm_run: LLMRun):
        if request_id not in self.request_summaries:
            self.create_request_summary(request_id)
        existing_runs = self.request_summaries[request_id].llm_runs
        for i, run in enumerate(existing_runs):
            if run.run_id == llm_run.run_id:
                existing_runs[i] = llm_run
                break
        else:
            existing_runs.append(llm_run)

    def get_summary(self, request_id: str) -> RequestSummary:
        summary = self.request_summaries.get(request_id, None)
        if summary:
            summary.calculate()
            return summary
        else:
            return RequestSummary(
                request_id=request_id, tokens_usage=TokensUsage(input_tokens=0, output_tokens=0, money_spent=0)
            )

    def clear_summary(self, request_id: str):
        if request_id in self.request_summaries:
            del self.request_summaries[request_id]


# Singleton instance
request_summary_manager = RequestSummaryManager()
