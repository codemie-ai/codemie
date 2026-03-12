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

from codemie.core.utils import calculate_tokens
from codemie.datasource.callback.base_datasource_callback import DatasourceProcessorCallback
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.datasource_monitoring_service import DatasourceMonitoringService
from codemie.service.request_summary_manager import LLMRun, request_summary_manager


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
        """
        Stub method to be overridden by subclasses or instances.
        This method will be called after the data source is split into documents.
        """
        if self.request_uuid:
            llm_model = self.index.embeddings_model
            model_costs = llm_service.get_embeddings_model_cost(llm_model)
            input_tokens = sum([calculate_tokens(str(doc.page_content)) for doc in docs])
            money_spent = input_tokens * model_costs.input
            llm_run = LLMRun(
                run_id=str(uuid.uuid4()),
                input_tokens=input_tokens,
                output_tokens=0,
                money_spent=money_spent,
                llm_model=llm_model,
            )
            request_summary_manager.update_llm_run(request_id=self.request_uuid, llm_run=llm_run)

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
            self.index.tokens_usage = usage_summary.tokens_usage
            self.index.update()

            # Send individual tokens usage metrics per model if we have LLM runs
            # This allows per-model breakdown in Kibana/Elasticsearch
            if usage_summary.llm_runs:
                DatasourceMonitoringService.send_datasource_tokens_usage_metrics_by_model(
                    index_info=self.index,
                    llm_runs=usage_summary.llm_runs,
                    user=self.user,
                )
            # Fallback: Send aggregated metric if no individual runs but we have token usage
            elif usage_summary.tokens_usage:
                DatasourceMonitoringService.send_datasource_tokens_usage_metric(
                    index_info=self.index,
                    tokens_usage=usage_summary.tokens_usage,
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
            self.index.tokens_usage = usage_summary.tokens_usage
            self.index.update()

            # Send individual tokens usage metrics per model even on error
            error_attrs = {"error": "true", "error_class": exception.__class__.__name__}
            if usage_summary.llm_runs:
                DatasourceMonitoringService.send_datasource_tokens_usage_metrics_by_model(
                    index_info=self.index,
                    llm_runs=usage_summary.llm_runs,
                    user=self.user,
                    additional_attributes=error_attrs,
                )
            # Fallback: Send aggregated metric if no individual runs but we have token usage
            elif usage_summary.tokens_usage:
                DatasourceMonitoringService.send_datasource_tokens_usage_metric(
                    index_info=self.index,
                    tokens_usage=usage_summary.tokens_usage,
                    user=self.user,
                    additional_attributes=error_attrs,
                )

            request_summary_manager.clear_summary(self.request_uuid)
