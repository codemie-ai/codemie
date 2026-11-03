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

"""Embedding model usage analytics handler."""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import (
    CANDIDATE_ORDER,
    AnalyticsQueryFilters,
    AnalyticsQueryPipeline,
    EnrichedRows,
    ordered_spend_rows,
)
from codemie.service.spend.coverage import SpendDimension
from codemie.service.spend.ledger import ReportFilter
from codemie.service.spend.rendering import render_spend, time_range_or_none
from codemie.service.spend.spend_service import spend_service

logger = logging.getLogger(__name__)

# Elasticsearch field for embedding model name
EMBEDDINGS_MODEL_KEYWORD_FIELD = "attributes.embeddings_model.keyword"


class EmbeddingsHandler:
    """Handler for embedding model usage analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize embeddings handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_embeddings_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get embedding model usage analytics.

        Returns embedding model usage data grouped by model name with:
        - Input tokens used
        - Total cost in USD
        - Invocation count

        **Money is not in the aggregation.** The cost column is read from the spend port after the
        query, per model, so it means the same thing here as on every other surface; it can be
        absent (`None` beside `spend_unavailable`) where no ledger can answer, and a model that
        indexed something at a measured cost of nothing keeps its row - this is a usage table.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with embedding model usage data
        """
        logger.info("Requesting embeddings-usage analytics")

        return await self._pipeline.execute_enriched_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_embeddings_usage_aggregation(query, fetch_size),
            result_parser=self._parse_embeddings_usage_result,
            columns=self._get_embeddings_usage_columns(),
            group_by_field=EMBEDDINGS_MODEL_KEYWORD_FIELD,
            # Every candidate bucket in one query: the enricher orders and counts the whole set,
            # because the figure it sorts on is not in Elasticsearch.
            row_enricher=partial(
                self._enrich_embeddings_spending, report_filter=await report_filter_for(users, projects)
            ),
            filters=AnalyticsQueryFilters(
                metric_filters=[MetricName.DATASOURCE_TOKENS_USAGE.value],
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
                page=page,
                per_page=per_page,
            ),
        )

    async def _enrich_embeddings_spending(
        self, rows: list[dict], start_dt: datetime, end_dt: datetime, report_filter: ReportFilter | None = None
    ) -> EnrichedRows:
        """Fill each embedding model's cost from the spend port, then order and total the table by it.

        **Every row stays**, ordered by cost with an absent figure last: this is a usage table, and a
        model that indexed at a measured cost of nothing is still a model that indexed. Dropping the
        row would take its tokens and invocations out of the table and its footer on the strength of
        a money column that was never its reason to be listed.

        The money leaves the aggregation because a model's spend has to mean one thing on every
        surface, and only the port can make it mean that: below the coverage floor the figure comes
        from the metric store's model attributes and above it from the ledger's `model_group`, and
        composing those two halves is the port's job, never a handler's. Tokens and invocations stay
        on the metric store in the same query, because the ledger holds no token vocabulary this
        table wants.

        **The read is narrowed by the caller's own boundary**, taken from the pipeline: the rows
        beside the figure came from a query `SecureQueryBuilder` had already narrowed to what this
        caller may see, and a figure computed without that boundary would foot them with the whole
        platform's spend on the model. The scope has one derivation point, and a handler deriving a
        second one from a `User` is free to drift from the very query the rows came from.
        `report_filter` - the dashboard's users and projects - narrows it the same way, because the
        ledger keys a model's rows on the model alone and would otherwise answer for every project.

        Known and unfixed here: a search-time query embedding is billed under the same model as an
        indexing one, so both land in a single row until the ledger carries an origin to split on.
        """
        period = time_range_or_none(start_dt, end_dt)
        model_names = list(dict.fromkeys(row["model_name"] for row in rows))
        # One round trip for the whole candidate set - every model shares the one requested window
        # and the one boundary, which belongs to the caller rather than to a model.
        spend = {}
        if model_names and period:
            spend = await spend_service.aggregate_by_dimension(
                period,
                SpendDimension.MODEL,
                model_names,
                access=self._pipeline.access_scope,
                report_filter=report_filter,
            )
            logger.info(f"Embeddings-usage read the spend port for {len(model_names)} model(s) over {len(rows)} row(s)")

        rendered = [render_spend(spend.get(row["model_name"])) for row in rows]
        return ordered_spend_rows(rows, rendered, "total_cost_usd")

    def _build_embeddings_usage_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for embeddings usage with fetch-and-slice."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations for embedding metrics (embeddings only have input tokens)
        sub_aggs = {
            "total_input_tokens": {"sum": {"field": "attributes.input_tokens"}},
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=EMBEDDINGS_MODEL_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order=CANDIDATE_ORDER,
            sub_aggs=sub_aggs,
        )

        # Construct full aggregation body
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": terms_agg,
            },
        }

        return agg_body

    def _parse_embeddings_usage_result(self, result: dict) -> list[dict]:
        """Parse result for embeddings usage."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            model_name = bucket["key"]
            invocation_count = bucket.get("doc_count", 0)
            total_input_tokens = int(bucket.get("total_input_tokens", {}).get("value", 0) or 0)

            # No cost key: the enricher owns that cell, and a placeholder written here would be a
            # zero nobody measured until it is overwritten.
            rows.append(
                {
                    "model_name": model_name,
                    "total_input_tokens": total_input_tokens,
                    "invocation_count": invocation_count,
                }
            )

        logger.debug(f"Parsed embeddings-usage result: total_model_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_embeddings_usage_columns(self) -> list[dict]:
        """Get column definitions for embeddings usage."""
        return [
            {"id": "model_name", "label": "Embedding Model", "type": "string"},
            {"id": "total_input_tokens", "label": "Input Tokens", "type": "number"},
            {"id": "total_cost_usd", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "invocation_count", "label": "Invocations", "type": "number"},
        ]
