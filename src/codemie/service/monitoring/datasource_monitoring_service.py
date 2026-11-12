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

from typing import Optional, List
from collections import defaultdict
from codemie.core.dependecies import get_current_project
from codemie.core.models import TokensUsage
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.request_summary_manager import LLMRun
from codemie.configs import logger


class DatasourceMonitoringService(BaseMonitoringService):
    DATASOURCE_INDEX_BASE_METRIC = "datasource_index"
    DATASOURCE_REINDEX_BASE_METRIC = "datasource_reindex"
    DATASOURCE_RESUME_BASE_METRIC = "datasource_resume"
    DATASOURCE_TOKENS_BASE_METRIC = "datasource_tokens"

    @classmethod
    def send_indexing_metrics(
        cls,
        base_metric_name: str,
        index_info: IndexInfo,
        completed: bool,
        user_id: Optional[str] = "",
        user_name: Optional[str] = "",
        additional_attributes: Optional[dict] = None,
    ):
        attributes = {
            MetricsAttributes.DATASOURCE_TYPE: index_info.index_type,
            MetricsAttributes.PROJECT: index_info.project_name,
            MetricsAttributes.REPO_NAME: index_info.repo_name,
            MetricsAttributes.EMBEDDINGS_MODEL: index_info.embeddings_model,
            MetricsAttributes.USER_NAME: user_name if user_name else index_info.created_by.username,
            MetricsAttributes.USER_ID: user_id if user_id else index_info.created_by.id,
        }
        if additional_attributes:
            attributes.update(additional_attributes)
        if completed:
            cls.send_count_metric(
                name=base_metric_name + "_total",
                attributes=attributes,
            )
            cls.send_count_metric(
                name=base_metric_name + "_documents",
                attributes=attributes,
                count=index_info.current_state,
            )
        else:
            cls.send_count_metric(
                name=base_metric_name + "_errors_total",
                attributes=attributes,
            )

    @classmethod
    def send_datasource_tokens_usage_metric(
        cls,
        index_info: IndexInfo,
        tokens_usage: TokensUsage,
        user: Optional[User] = None,
        additional_attributes: Optional[dict] = None,
    ):
        """
        Sends metrics about token usage during datasource processing.

        Args:
            index_info: Information about the processed index
            tokens_usage: Token usage statistics (input, output, money spent)
            user: Optional user who triggered the processing
            additional_attributes: Optional additional attributes to include in the metric
        """
        attributes = {
            MetricsAttributes.DATASOURCE_TYPE: index_info.index_type,
            # Spend goes to the project whose budget paid (EPMCDME-16625), as in the other spend metrics.
            MetricsAttributes.PROJECT: get_current_project(fallback=index_info.project_name),
            MetricsAttributes.REPO_NAME: index_info.repo_name,
            MetricsAttributes.EMBEDDINGS_MODEL: index_info.embeddings_model,
            MetricsAttributes.USER_ID: user.id if user else index_info.created_by.id,
            MetricsAttributes.USER_NAME: user.name if user else index_info.created_by.username,
            MetricsAttributes.USER_EMAIL: user.username if user else index_info.created_by.username,
            MetricsAttributes.INPUT_TOKENS: tokens_usage.input_tokens,
            MetricsAttributes.OUTPUT_TOKENS: tokens_usage.output_tokens,
            MetricsAttributes.CACHE_READ_INPUT_TOKENS: tokens_usage.cached_tokens,
            MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: tokens_usage.cached_tokens_money_spent,
        }
        # A metric emitted while cost was pending used to carry a fabricated zero, since nothing
        # re-emits this document when the ledger settles - the zero froze into history permanently.
        if not (tokens_usage.money_spent_pending or tokens_usage.money_spent_unavailable):
            attributes[MetricsAttributes.MONEY_SPENT] = tokens_usage.money_spent

        if additional_attributes:
            attributes.update(additional_attributes)

        cls.send_count_metric(name=cls.DATASOURCE_TOKENS_BASE_METRIC + "_usage", attributes=attributes)

    @classmethod
    def send_datasource_tokens_usage_metrics_by_model(
        cls,
        index_info: IndexInfo,
        llm_runs: List[LLMRun],
        user: Optional[User] = None,
        additional_attributes: Optional[dict] = None,
    ):
        """
        Sends individual metrics about token usage per model during datasource processing.
        This allows per-model breakdown in Kibana/Elasticsearch dashboards.

        Args:
            index_info: Information about the processed index
            llm_runs: List of LLM runs with individual model information
            user: Optional user who triggered the processing
            additional_attributes: Optional additional attributes to include in metrics
        """
        # Aggregate runs by model
        model_usage = defaultdict(
            lambda: {
                'input_tokens': 0,
                'output_tokens': 0,
                'cached_tokens': 0,
                'money_spent': 0.0,
                'cached_tokens_money_spent': 0.0,
            }
        )

        for run in llm_runs:
            model = run.llm_model
            model_usage[model]['input_tokens'] += run.input_tokens
            model_usage[model]['output_tokens'] += run.output_tokens
            model_usage[model]['cached_tokens'] += run.cached_tokens
            # AD-28 rule 4: a run costs `None` while unsettled. Skipping it is not enough - the
            # remaining sum would be this model's *settled part* emitted as its cost. So `None` is
            # sticky here and carries the same meaning it carries on `LLMRun.money_spent`: **not
            # settled**. One unsettled run makes the model's figure unavailable, and the attribute
            # is then omitted below rather than truncated.
            if run.money_spent is None:
                model_usage[model]['money_spent'] = None
            elif model_usage[model]['money_spent'] is not None:
                model_usage[model]['money_spent'] += run.money_spent
            model_usage[model]['cached_tokens_money_spent'] += run.cached_tokens_money_spent

        # Send one metric per model
        for model, usage in model_usage.items():
            attributes = {
                MetricsAttributes.DATASOURCE_TYPE: index_info.index_type,
                MetricsAttributes.PROJECT: get_current_project(fallback=index_info.project_name),
                MetricsAttributes.REPO_NAME: index_info.repo_name,
                MetricsAttributes.EMBEDDINGS_MODEL: index_info.embeddings_model,
                MetricsAttributes.USER_ID: user.id if user else index_info.created_by.id,
                MetricsAttributes.USER_NAME: user.name if user else index_info.created_by.username,
                MetricsAttributes.USER_EMAIL: user.username if user else index_info.created_by.username,
                MetricsAttributes.LLM_MODEL: model,  # Include individual model
                MetricsAttributes.INPUT_TOKENS: usage['input_tokens'],
                MetricsAttributes.OUTPUT_TOKENS: usage['output_tokens'],
                MetricsAttributes.CACHE_READ_INPUT_TOKENS: usage['cached_tokens'],
                MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: usage['cached_tokens_money_spent'],
            }
            # Gated exactly as the sibling emitter above is, and for the same reason: **nothing
            # re-emits this document when the ledger settles**, so a truncated total or a `0.0`
            # freezes into metric history uncorrectably. A metric has no annotation slot to say
            # "this figure is short", so the only honest options are the whole figure or none.
            if usage['money_spent'] is not None:
                attributes[MetricsAttributes.MONEY_SPENT] = usage['money_spent']

            if additional_attributes:
                attributes.update(additional_attributes)

            cls.send_count_metric(name=cls.DATASOURCE_TOKENS_BASE_METRIC + "_usage", attributes=attributes)

        logger.debug(f"Sent datasource token usage metrics for {len(model_usage)} models: {list(model_usage.keys())}")
