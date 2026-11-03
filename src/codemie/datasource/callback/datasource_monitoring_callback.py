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

import time
import uuid
from typing import Optional

from langchain_core.documents import Document

from codemie.configs import logger
from codemie.core.utils import calculate_tokens
from codemie.datasource.callback.base_datasource_callback import DatasourceProcessorCallback
from codemie.rest_api.models.index import IndexDeletedException, IndexInfo, ProgressUpdate
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.datasource_monitoring_service import DatasourceMonitoringService
from codemie.service.request_summary_manager import LLMRun, request_summary_manager
from codemie.service.spend.routing import LLMCallClass, uses_litellm


class DatasourceMonitoringCallback(DatasourceProcessorCallback):
    start_time = None

    def __init__(
        self,
        index: IndexInfo,
        user: User,
        is_full_reindex=False,
        is_resume_indexing=False,
        request_uuid: Optional[str] = None,
    ):
        """
        Initialize the DatasourceMonitoringCallback with the given parameters.

        :param index: Information about the index being processed
        :param user: Information about the user who triggered action
        :param is_full_reindex: Flag indicating whether this is a full reindex
        :param is_resume_indexing: Flag indicating whether this is resumed indexing
        """
        self.index = index
        self.user = user
        self.request_uuid = request_uuid
        self._estimated_embedding_tokens: int = 0
        if is_resume_indexing:
            self.datasource_metric_name = DatasourceMonitoringService.DATASOURCE_RESUME_BASE_METRIC
        elif is_full_reindex:
            self.datasource_metric_name = DatasourceMonitoringService.DATASOURCE_REINDEX_BASE_METRIC
        else:
            self.datasource_metric_name = DatasourceMonitoringService.DATASOURCE_INDEX_BASE_METRIC

    def on_start(self):
        """
        Record the start time of the data source processing.
        This method will be called before the data source processing starts.
        """
        self.start_time = time.time()

    def on_split_documents(self, docs: list[Document]):
        if self.request_uuid:
            try:
                batch_tokens = sum(calculate_tokens(str(doc.page_content)) for doc in docs)
            except Exception as e:
                # The estimate is best-effort and must never abort indexing; do not log document content.
                logger.warning(f"Skipping embedding token estimate for split documents: {type(e).__name__}")
                return
            self._estimated_embedding_tokens += batch_tokens

    def _estimated_embedding_money_spent(self, llm_model: str) -> Optional[float]:
        """Money for embeddings that completed but whose own usage was never captured.

        On the LiteLLM path the real cost is recorded in ``litellm_spend_logs.spend`` and has
        not arrived yet, so the money is **unsettled**: ``None`` (AD-28), settled later by the
        ledger. Never a zero - these embeddings succeeded and were billed - and never a local
        estimate, because LiteLLM already priced them (AD-4). Off that path the local price
        table *is* the ledger, so the estimate is the right answer and survives (C2).

        Embeddings are 92.4% of ledger rows, which makes an estimate here the platform's
        highest-volume re-pricing site.
        """
        if uses_litellm(LLMCallClass.EMBEDDINGS):
            return None
        return self._local_embedding_estimate(llm_model)

    def _failed_embedding_money_spent(self, llm_model: str) -> float:
        """Money for an indexing run that **failed** with no embedding usage captured at all.

        **On the LiteLLM path this is a real, measured zero** - nothing was billed, and AD-20 says a
        failure row contributes zero to any spend total. It is deliberately *not* the completion
        branch's unsettled `None`: zero means *no charge*, `None` means *the charge exists and has
        not arrived*, and reporting one as the other either fabricates money or loses it.

        **Off that path the estimate stands, and the gate is why.** AD-20 scopes itself to the ledger
        (*"counts come from the ledger, never from the callback"*), and this branch's own condition -
        no proxy data captured - is the **ordinary** case for a non-LiteLLM deployment, where proxy
        data never exists. So an unconditional zero here would replace the only figure such a
        deployment ever had for a failed index with a claim that it cost nothing.
        """
        if uses_litellm(LLMCallClass.EMBEDDINGS):
            return 0.0
        return self._local_embedding_estimate(llm_model)

    def _local_embedding_estimate(self, llm_model: str) -> float:
        """The local price table applied to the estimated token count - the non-LiteLLM authority.

        Shared by the two branches above so that "what the local estimate is" has one definition,
        while "when it applies" stays each branch's own answer.
        """
        return self._estimated_embedding_tokens * llm_service.get_embeddings_model_cost(llm_model).input

    def on_complete(self, result):
        """
        Send indexing metrics indicating completion.
        This method will be called after the data source processing ends.

        :param result: The result of the data source processing
        """
        DatasourceMonitoringService.send_indexing_metrics(
            base_metric_name=self.datasource_metric_name,
            index_info=self.index,
            user_id=self.user.id,
            user_name=self.user.name,
            completed=True,
        )
        if self.request_uuid:
            usage_summary = request_summary_manager.get_summary(self.request_uuid)
            self.index.tokens_usage = usage_summary.tokens_usage if usage_summary else None
            try:
                self.index.update_progress(ProgressUpdate(tokens_usage=self.index.tokens_usage))
            except IndexDeletedException:
                request_summary_manager.clear_summary(self.request_uuid)
                return

            # Send individual tokens usage metrics per model if we have LLM runs
            # This allows per-model breakdown in Kibana/Elasticsearch
            if usage_summary and usage_summary.llm_runs:
                DatasourceMonitoringService.send_datasource_tokens_usage_metrics_by_model(
                    index_info=self.index,
                    llm_runs=usage_summary.llm_runs,
                    user=self.user,
                )
            # Fallback: Send aggregated metric if no individual runs but we have token usage
            elif usage_summary and usage_summary.tokens_usage:
                DatasourceMonitoringService.send_datasource_tokens_usage_metric(
                    index_info=self.index,
                    tokens_usage=usage_summary.tokens_usage,
                    user=self.user,
                )
            # Last-resort fallback: no actual LLM proxy data captured — use token estimation
            elif self._estimated_embedding_tokens > 0:
                llm_model = self.index.embeddings_model
                llm_run = LLMRun(
                    run_id=str(uuid.uuid4()),
                    input_tokens=self._estimated_embedding_tokens,
                    output_tokens=0,
                    money_spent=self._estimated_embedding_money_spent(llm_model),
                    llm_model=llm_model,
                )
                DatasourceMonitoringService.send_datasource_tokens_usage_metrics_by_model(
                    index_info=self.index,
                    llm_runs=[llm_run],
                    user=self.user,
                )

            request_summary_manager.clear_summary(self.request_uuid)

    def on_error(self, exception: Exception):
        """
        Send indexing metrics indicating an error.
        This method will be called if an error occurs during the data source processing.

        :param exception: The exception that occurred
        """
        DatasourceMonitoringService.send_indexing_metrics(
            base_metric_name=self.datasource_metric_name,
            index_info=self.index,
            user_id=self.user.id,
            user_name=self.user.name,
            completed=False,
            additional_attributes={"error_class": exception.__class__.__name__},
        )
        if self.request_uuid:
            usage_summary = request_summary_manager.get_summary(self.request_uuid)
            self.index.tokens_usage = usage_summary.tokens_usage if usage_summary else None
            try:
                self.index.update_progress(ProgressUpdate(tokens_usage=self.index.tokens_usage))
            except IndexDeletedException:
                request_summary_manager.clear_summary(self.request_uuid)
                return

            # Send individual tokens usage metrics per model even on error
            error_attrs = {"error": "true", "error_class": exception.__class__.__name__}
            if usage_summary and usage_summary.llm_runs:
                DatasourceMonitoringService.send_datasource_tokens_usage_metrics_by_model(
                    index_info=self.index,
                    llm_runs=usage_summary.llm_runs,
                    user=self.user,
                    additional_attributes=error_attrs,
                )
            # Fallback: Send aggregated metric if no individual runs but we have token usage
            elif usage_summary and usage_summary.tokens_usage:
                DatasourceMonitoringService.send_datasource_tokens_usage_metric(
                    index_info=self.index,
                    tokens_usage=usage_summary.tokens_usage,
                    user=self.user,
                    additional_attributes=error_attrs,
                )
            # Last-resort fallback: no actual LLM proxy data captured — use token estimation
            elif self._estimated_embedding_tokens > 0:
                llm_model = self.index.embeddings_model
                llm_run = LLMRun(
                    run_id=str(uuid.uuid4()),
                    input_tokens=self._estimated_embedding_tokens,
                    output_tokens=0,
                    # A measured zero on the LiteLLM path, the local estimate off it - see
                    # `_failed_embedding_money_spent`, which is a separate helper from the
                    # completion branch's on purpose: the two differ in their LiteLLM-side
                    # answer (a zero versus an unsettled `None`) even though they share a gate.
                    money_spent=self._failed_embedding_money_spent(llm_model),
                    llm_model=llm_model,
                )
                DatasourceMonitoringService.send_datasource_tokens_usage_metrics_by_model(
                    index_info=self.index,
                    llm_runs=[llm_run],
                    user=self.user,
                    additional_attributes=error_attrs,
                )

            request_summary_manager.clear_summary(self.request_uuid)
