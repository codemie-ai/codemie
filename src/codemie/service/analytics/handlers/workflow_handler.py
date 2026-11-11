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

"""Workflow analytics handler.

**Both tables group on the workflow name, and the reason is what the metric store holds.** The
`workflow_id` attribute was first emitted by this ticket's own commit, so every production workflow
document written before that deploy carries only the mutable, non-unique `workflow_name`. A table
grouped on the id shows nothing at all for any window before the deploy - a direct breach of the
requirement that a pre-cutover window shows today's figures. The name splits a renamed workflow
across two rows and merges two workflows that share a name, exactly as it did before the ledger,
and that is the accepted cost until ids have history of their own.

**Money comes from two legs joined at the coverage floor `F`, the shape `project_handler` already
uses.** Below `F` a row's money is this table's own Elasticsearch sum per name, measured inside the
candidate query and qualified by its coverage rather than published bare. At or above `F` the money
is the spend port's, and the port is keyed on the **id**: a name is asked about only where it
resolves to **exactly one** live workflow (AD-29) - a name shared by two workflows, or belonging to
none, names no bucket the ledger could have formed, so its at-or-above leg is absent and the row says
so rather than being placed in some other workflow's bucket.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from codemie.core.run_attributes import PAIRS_RECORD_FIELD, attributes_from_record
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import USER_EMAIL_KEYWORD_FIELD, USER_NAME_KEYWORD_FIELD
from codemie.service.analytics.handlers.money_coverage import (
    money_coverage_aggs,
    pre_floor_boundary,
    read_money_coverage,
    split_at_floor,
)
from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.analytics.handlers.request_spend import (
    Search,
    collect_request_usage,
    price_filtered_requests,
    price_requests,
)
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import (
    AnalyticsQueryFilters,
    AnalyticsQueryPipeline,
    EnrichedRows,
    ordered_spend_rows,
)
from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.ledger import ReportFilter
from codemie.service.spend.ledger_registry import ledger_coverage
from codemie.service.spend.rendering import (
    RenderedSpend,
    apply_spend_cells,
    combine_rendered_spend,
    render_spend,
    report_period_or_none,
    spend_footer,
)
from codemie.service.spend.spend_service import spend_service

logger = logging.getLogger(__name__)

# Elasticsearch field constants
STATUS_FIELD = "attributes.status.keyword"
EXECUTION_ID_KEYWORD_FIELD = "attributes.execution_id.keyword"
MONEY_SPENT_FIELD = "attributes.money_spent"
INPUT_TOKENS_FIELD = "attributes.input_tokens"

#: The emission-time field both queries already filter on, and the field the pre-floor money bucket
#: is bounded at `F` by. One spelling: a money bucket bounded on a field the surrounding query does not
#: filter would be scoped to a window nobody asked about.
TIMESTAMP_FIELD = "@timestamp"

#: What both tables group on: the only key every workflow document in the metric store carries.
#: Mutable and not unique, so a rename is two rows and a shared name is one - see the module docstring.
WORKFLOW_NAME_KEYWORD_FIELD = "attributes.workflow_name.keyword"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
EXECUTION_LATEST_FIELDS = (
    "workflow_name",
    "status",
    "user_id",
    "user_name",
    "user_email",
    "project",
    PAIRS_RECORD_FIELD,
)

# The ledger-filled columns of the two tables, named once so the enricher that writes each one and
# the column list that displays it cannot drift apart.
WORKFLOWS_MONEY_COLUMN = "total_cost_usd"
WORKFLOWS_AVG_COST_COLUMN = "avg_cost_usd"
TOP_USAGE_MONEY_COLUMN = "money_spent"
EXECUTIONS_MONEY_COLUMN = "money_spent"

#: The pre-floor money sub-aggregation, its sum, and the row key that figure travels to the enricher
#: on. The row key is underscore-prefixed and **popped** before the row is published: it carries a
#: `RenderedSpend`, which is an internal shape and not a wire value.
PRE_FLOOR_MONEY_BUCKET = "pre_floor_money"
PRE_FLOOR_MONEY_SUM = "sum"
PRE_FLOOR_MONEY_KEY = "_pre_floor_money"


def _workflow_filter(metric_name_field: str) -> dict:
    """The execution-metric term the top-usage table's counts and money have always sat behind."""
    return {
        "bool": {
            "filter": [
                {
                    "bool": {
                        "should": [{"term": {metric_name_field: {"value": MetricName.WORKFLOW_EXECUTION_TOTAL.value}}}],
                        "minimum_should_match": 1,
                    }
                }
            ]
        }
    }


def _pre_floor_money_aggs(pre_floor: datetime | None, *, within: dict | None = None) -> dict:
    """The per-name money the below-`F` leg is served from, scoped to `[start, F)` at the source.

    **Bounded at `F` here rather than trimmed afterwards.** The surrounding query covers the whole
    requested window, so a bucket money sum taken over all of it would include the at-or-above part -
    which the port read also reports - and a crossing row would foot at nearly double with nothing
    on it saying so. `lt` rather than `lte`: every interval on these paths is half-open, `F` belongs
    to the at-or-above leg, and adjacent half-open ranges tile exactly.

    **`within` is the metric term the old money sum sat behind, where the query itself carries none.**
    The workflows table's query is already filtered to the execution metric, so it passes nothing;
    the top-usage query has no metric filter at all - its buckets hold every document that names a
    workflow - and its money always lived behind an execution-metric filter of its own. Summing the
    whole bucket there would count documents this table never counted, and the pre-floor figure would
    not be today's figure.

    **Narrowed to the documents a cost was expected on, the way `project_handler` narrows its own.**
    A metric name is not an emitter, and a name may have two: one that writes token counts and,
    once settled, money, and one that writes neither because no model was called. Counting the second
    kind as unsettled would publish every pre-floor row `partial` in every window. The discriminator
    is the token count, written unconditionally by every emitter that may write money.
    """
    if pre_floor is None:
        return {}
    below_floor = {"range": {TIMESTAMP_FIELD: {"lt": pre_floor.isoformat(), "format": "strict_date_optional_time"}}}
    scope = below_floor if within is None else {"bool": {"filter": [below_floor, within]}}
    return {
        PRE_FLOOR_MONEY_BUCKET: {
            "filter": scope,
            "aggs": {
                PRE_FLOOR_MONEY_SUM: {"sum": {"field": MONEY_SPENT_FIELD}},
                **money_coverage_aggs(MONEY_SPENT_FIELD, cost_bearing={"exists": {"field": INPUT_TOKENS_FIELD}}),
            },
        }
    }


def _pre_floor_spend(bucket: dict) -> RenderedSpend:
    """One candidate bucket's pre-floor money, qualified by its coverage.

    Absent when the bucket carries no money bucket at all - a query built without a floor - and absent
    when nothing in it settled; a measured `0.0` stays a measured zero. No unrounded special case as in
    `project_handler._pre_floor_spend`: these tables never drop a zero row, so display rounding cannot
    cost a row here.
    """
    return read_money_coverage(bucket.get(PRE_FLOOR_MONEY_BUCKET, {}), PRE_FLOOR_MONEY_SUM)


class WorkflowHandler:
    """Handler for workflow execution analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize workflow handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_workflows(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        client_source: str | None = None,
        attributes: dict[str, str] | None = None,
    ) -> dict:
        """Get workflow execution analytics.

        Returns metrics for workflow runs, success rates, costs, and performance.

        **Money is not in the aggregation above the floor.** Elasticsearch supplies runs, tokens,
        timings and - below `F` only - each name's own money sum; both cost columns are written by
        `_enrich_workflows_spending` after the query has run (AD-1, AD-14).
        """
        logger.info("Requesting workflows analytics")

        # Read **once** for the whole request and handed to both halves of the split: the
        # aggregation that scopes the pre-floor money and the enricher that decides which legs run.
        pre_floor = None if attributes else self._pre_floor_boundary()
        report_filter = await report_filter_for(users, projects)
        filters = AnalyticsQueryFilters(
            metric_filters=[MetricName.WORKFLOW_EXECUTION_TOTAL.value],
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
            client_source=client_source,
            attributes=attributes,
        )

        return await self._pipeline.execute_enriched_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_workflows_aggregation(query, fetch_size, pre_floor),
            result_parser=self._parse_workflows_result,
            columns=self._get_workflows_columns(),
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            row_enricher=lambda rows, start_dt, end_dt: self._enrich_workflows_spending(
                rows, start_dt, end_dt, pre_floor, client_source, report_filter, filters
            ),
            filters=filters,
        )

    def _build_workflows_aggregation(self, query: dict, fetch_size: int, pre_floor: datetime | None = None) -> dict:
        """Build terms aggregation for workflows with fetch-and-slice.

        Grouped on the workflow **name** - the module docstring says why - with the money below `F`
        measured inside the bucket and nothing money-shaped above it.

        Note: Uses Elasticsearch cardinality aggregations for unique execution counts.
        Cardinality uses HyperLogLog++ algorithm which provides approximate counts
        (not exact) with ~3% error rate for large datasets. Default precision_threshold
        is 3000 - counts below this are exact, counts above are approximate.
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            "unique_executions": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}},
            "successful": {
                "filter": {"term": {STATUS_FIELD: "SUCCEEDED"}},
                "aggs": {"unique_count": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}}},
            },
            "failed": {
                "filter": {"term": {STATUS_FIELD: "FAILED"}},
                "aggs": {"unique_count": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}}},
            },
            "aborted": {
                "filter": {"term": {STATUS_FIELD: "ABORTED"}},
                "aggs": {"unique_count": {"cardinality": {"field": EXECUTION_ID_KEYWORD_FIELD}}},
            },
            "total_tokens": {
                "sum": {
                    "script": {
                        "source": (
                            "(doc.containsKey('attributes.input_tokens') && "
                            "!doc['attributes.input_tokens'].empty ? "
                            "doc['attributes.input_tokens'].value : 0) + "
                            "(doc.containsKey('attributes.output_tokens') && "
                            "!doc['attributes.output_tokens'].empty ? "
                            "doc['attributes.output_tokens'].value : 0)"
                        )
                    }
                }
            },
            "avg_time": {"avg": {"field": "attributes.execution_time"}},
            "unique_users": {"cardinality": {"field": USER_NAME_KEYWORD_FIELD}},
            "last_run": {"max": {"field": TIMESTAMP_FIELD}},
            # The query is already filtered to the execution metric, so the bucket needs no narrowing
            # of its own beyond the floor.
            **_pre_floor_money_aggs(pre_floor),
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"_count": "desc"},
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

    def _parse_workflows_result(self, result: dict) -> list[dict]:
        """Parse result for workflows.

        These are **candidate** rows: neither cost column is written here. The pre-floor money the
        bucket measured travels on a private key the enricher pops, and the at-or-above money has not
        been read yet - a placeholder for it would be the fabricated zero this surface used to emit.
        """
        rows = []
        # Extract buckets from paginated_results (already sliced by pipeline)
        workflows_buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        for bucket in workflows_buckets:
            try:
                workflow_name = bucket["key"]
                total_runs = bucket["unique_executions"]["value"]
                successful = bucket["successful"]["unique_count"]["value"]
                failed = bucket["failed"]["unique_count"]["value"]
                aborted = bucket["aborted"]["unique_count"]["value"]
                total_tokens = bucket["total_tokens"]["value"]
                avg_time = bucket["avg_time"]["value"] or 0
                unique_users = bucket["unique_users"]["value"]
                last_run = bucket["last_run"]["value_as_string"]
            except KeyError as e:
                logger.error(
                    f"Missing expected aggregation field in Elasticsearch response: {e}, "
                    f"available bucket keys: {bucket.keys()}"
                )
                continue

            success_rate = (successful / total_runs * 100) if total_runs > 0 else 0

            rows.append(
                {
                    "workflow_name": workflow_name,
                    "total_runs": int(total_runs),
                    "success": int(successful),
                    "failed": int(failed),
                    "aborted": int(aborted),
                    "success_rate_percent": round(success_rate, 2),
                    "total_tokens": int(total_tokens),
                    "avg_time_seconds": round(avg_time, 2),
                    "users": int(unique_users),
                    "last_run": last_run,
                    PRE_FLOOR_MONEY_KEY: _pre_floor_spend(bucket),
                }
            )

        logger.debug(
            f"Parsed workflows result: total_workflow_buckets={len(workflows_buckets)}, "
            f"rows_parsed={len(rows)}, "
            f"successful_workflows={sum(1 for row in rows if row['success'] > 0)}, "
            f"failed_workflows={sum(1 for row in rows if row['failed'] > 0)}, "
            f"aborted_workflows={sum(1 for row in rows if row['aborted'] > 0)}"
        )
        return rows

    async def _enrich_workflows_spending(
        self,
        rows: list[dict],
        start_dt: datetime,
        end_dt: datetime,
        pre_floor: datetime | None = None,
        client_source: str | None = None,
        report_filter: ReportFilter | None = None,
        filters: AnalyticsQueryFilters | None = None,
    ) -> EnrichedRows:
        """Fill each workflow's cost and per-run cost, then order and total the table by it.

        Under an `attributes` filter the ledger cannot narrow a workflow's spend to the labelled runs, so
        each workflow's cost is the sum of its filtered executions, priced per execution id.
        """
        if filters is not None and filters.attributes and rows:
            for row in rows:
                row.pop(PRE_FLOOR_MONEY_KEY, None)
            priced = await price_filtered_requests(
                self._pipeline,
                filters,
                start_dt,
                end_dt,
                group_field=WORKFLOW_NAME_KEYWORD_FIELD,
                id_fields=(EXECUTION_ID_KEYWORD_FIELD,),
                cost_bearing={"exists": {"field": INPUT_TOKENS_FIELD}},
            )
            rendered = [priced.get(row["workflow_name"], render_spend(None)) for row in rows]
        else:
            rendered = await self._workflow_spend(
                rows, report_period_or_none(start_dt, end_dt, client_source), pre_floor, report_filter
            )
        apply_spend_cells(rows, rendered, WORKFLOWS_MONEY_COLUMN)
        for row, figure in zip(rows, rendered, strict=True):
            # No annotation of its own: the ratio is derived from the same figure as the money cell,
            # so the row's single triple already qualifies both. A second copy would be a second
            # thing to keep in sync, saying nothing the first does not.
            row[WORKFLOWS_AVG_COST_COLUMN] = self._avg_cost(figure.figure, row["total_runs"])

        return ordered_spend_rows(rows, rendered, WORKFLOWS_MONEY_COLUMN)

    def _get_workflows_columns(self) -> list[dict]:
        """Get column definitions for workflows."""
        return [
            {"id": "workflow_name", "label": "Workflow Name", "type": "string"},
            {"id": "total_runs", "label": "Total Runs", "type": "number"},
            {"id": "success", "label": "Success", "type": "number"},
            {"id": "failed", "label": "Failed", "type": "number"},
            {"id": "aborted", "label": "Aborted", "type": "number"},
            {"id": "success_rate_percent", "label": "Success Rate (%)", "type": "number", "format": "percentage"},
            {"id": WORKFLOWS_MONEY_COLUMN, "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": WORKFLOWS_AVG_COST_COLUMN, "label": "Avg Cost ($)", "type": "number", "format": "currency"},
            {"id": "total_tokens", "label": "Total Tokens", "type": "number"},
            {"id": "avg_time_seconds", "label": "Avg Time (s)", "type": "number"},
            {"id": "users", "label": "Users", "type": "number"},
            {"id": "last_run", "label": "Last Run", "type": "string", "format": "timestamp"},
        ]

    async def get_workflows_executions(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        client_source: str | None = None,
        attributes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Get one row per workflow execution (its workflow_execution_total documents grouped), newest first."""
        logger.info("Requesting workflows-executions analytics")

        return await self._pipeline.execute_exact_tabular_query(
            selection_builder=self._build_workflows_executions_selection,
            details_builder=self._build_workflows_executions_aggregation,
            result_parser=self._parse_workflows_executions_result,
            columns=self._get_workflows_executions_columns(),
            group_by_field=EXECUTION_ID_KEYWORD_FIELD,
            filters=AnalyticsQueryFilters(
                metric_filters=[MetricName.WORKFLOW_EXECUTION_TOTAL.value],
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
                page=page,
                per_page=per_page,
                client_source=client_source,
                attributes=attributes,
            ),
            totals_aggs={
                "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
            },
            row_enricher=self._cost_workflows_executions,
        )

    @staticmethod
    async def _cost_workflows_executions(
        search: Search, query: dict[str, Any], rows: list[dict[str, Any]]
    ) -> EnrichedRows:
        """Each execution's cost, and the total over every execution in range, priced per execution id."""
        listed = await collect_request_usage(
            search,
            query,
            group_field=None,
            id_fields=(EXECUTION_ID_KEYWORD_FIELD,),
            cost_bearing={"exists": {"field": INPUT_TOKENS_FIELD}},
        )
        priced = await price_requests({usage.request_id: [usage] for usage in listed.get(None, [])})
        apply_spend_cells(
            rows,
            [priced.get(row["execution_id"], render_spend(None)) for row in rows],
            EXECUTIONS_MONEY_COLUMN,
        )
        return EnrichedRows(
            rows=rows,
            columns=(EXECUTIONS_MONEY_COLUMN,),
            totals=spend_footer(EXECUTIONS_MONEY_COLUMN, list(priced.values())),
        )

    def _build_workflows_executions_selection(self, query: dict[str, Any], fetch_size: int) -> dict[str, Any]:
        """Select execution ids in newest-activity order without using approximate per-bucket sums."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        return {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": AggregationBuilder.build_terms_agg(
                    group_by_field=EXECUTION_ID_KEYWORD_FIELD,
                    fetch_size=fetch_size,
                    order={"last_activity": "desc"},
                    sub_aggs={"last_activity": {"max": {"field": TIMESTAMP_FIELD}}},
                )
            },
        }

    def _build_workflows_executions_aggregation(
        self, query: dict[str, Any], execution_ids: list[str]
    ) -> dict[str, Any]:
        """Aggregate every matching document for each selected id; all groups fit on every shard."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        sub_aggs = {
            "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
            "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
            "execution_time": {"max": {"field": "attributes.execution_time"}},
            "last_activity": {"max": {"field": TIMESTAMP_FIELD}},
            "latest": {
                "top_hits": {
                    "size": 1,
                    "sort": [{TIMESTAMP_FIELD: {"order": "desc"}}],
                    "_source": {"includes": [f"attributes.{field}" for field in EXECUTION_LATEST_FIELDS]},
                }
            },
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=EXECUTION_ID_KEYWORD_FIELD,
            fetch_size=len(execution_ids),
            order={"_key": "asc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": {"bool": {"filter": [query, {"terms": {EXECUTION_ID_KEYWORD_FIELD: execution_ids}}]}},
            "size": 0,
            "aggs": {"paginated_results": terms_agg},
        }

    @staticmethod
    def _parse_workflows_executions_result(result: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse one row per execution bucket; absent run fields become None."""
        rows = []
        for bucket in result.get("aggregations", {}).get("paginated_results", {}).get("buckets", []):
            hits = bucket.get("latest", {}).get("hits", {}).get("hits", [])
            latest = hits[0].get("_source", {}).get("attributes", {}) if hits else {}
            input_tokens = int(bucket.get("input_tokens", {}).get("value") or 0)
            output_tokens = int(bucket.get("output_tokens", {}).get("value") or 0)
            execution_time = bucket.get("execution_time", {}).get("value")
            rows.append(
                {
                    "execution_id": bucket["key"],
                    "last_activity": bucket.get("last_activity", {}).get("value_as_string"),
                    "workflow_name": latest.get("workflow_name"),
                    "status": latest.get("status"),
                    "user_id": latest.get("user_id"),
                    "user_name": latest.get("user_name"),
                    "user_email": latest.get("user_email"),
                    "project": latest.get("project"),
                    "usage_records": bucket.get("doc_count", 0),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    EXECUTIONS_MONEY_COLUMN: None,
                    "execution_time": round(execution_time, 2) if execution_time is not None else None,
                    "attributes": attributes_from_record(latest),
                }
            )
        return rows

    @staticmethod
    def _get_workflows_executions_columns() -> list[dict[str, Any]]:
        """Get column definitions for workflows-executions."""
        return [
            {"id": "execution_id", "label": "Execution", "type": "string"},
            {"id": "last_activity", "label": "Last Activity", "type": "date"},
            {"id": "workflow_name", "label": "Workflow", "type": "string"},
            {"id": "status", "label": "Status", "type": "string"},
            {"id": "user_id", "label": "User ID", "type": "string"},
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "user_email", "label": "User Email", "type": "string"},
            {"id": "project", "label": "Project", "type": "string"},
            {"id": "usage_records", "label": "Usage Records", "type": "number"},
            {"id": "input_tokens", "label": "Input Tokens", "type": "number"},
            {"id": "output_tokens", "label": "Output Tokens", "type": "number"},
            {"id": "total_tokens", "label": "Total Tokens", "type": "number"},
            {"id": EXECUTIONS_MONEY_COLUMN, "label": "Cost ($)", "type": "number", "format": "currency"},
            {"id": "execution_time", "label": "Time (s)", "type": "number"},
            {"id": "attributes", "label": "Attributes", "type": "json"},
        ]

    async def get_top_workflow_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
        client_source: str | None = None,
    ) -> dict:
        """Get top workflow usage: invocations, cost, unique users, and most recent user per workflow.

        **Money is not in the aggregation above the floor**; below `F` it is the bucket's own sum
        behind the execution-metric term, at or above `F` the spend port's, and
        `_enrich_top_workflow_usage_spending` writes the one column from both (AD-1, AD-14).
        """
        from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD

        logger.info("Requesting top-workflow-usage analytics")

        pre_floor = self._pre_floor_boundary()
        report_filter = await report_filter_for(users, projects)

        return await self._pipeline.execute_enriched_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_top_workflow_usage_aggregation(
                query, fetch_size, METRIC_NAME_KEYWORD_FIELD, pre_floor
            ),
            result_parser=self._parse_top_workflow_usage_result,
            columns=self._get_top_workflow_usage_columns(),
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            row_enricher=lambda rows, start_dt, end_dt: self._enrich_top_workflow_usage_spending(
                rows, start_dt, end_dt, pre_floor, client_source, report_filter
            ),
            filters=AnalyticsQueryFilters(
                metric_filters=None,
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
                page=page,
                per_page=per_page,
                client_source=client_source,
            ),
        )

    def _build_top_workflow_usage_aggregation(
        self, query: dict, fetch_size: int, metric_name_field: str, pre_floor: datetime | None = None
    ) -> dict:
        """Build terms aggregation for top workflow usage.

        Grouped on the workflow **name**. The numbered keys skip `2-bucket`, which held the money sum
        over the whole window; below `F` that sum lives in the pre-floor bucket instead, behind the
        same execution-metric term, and above `F` the figure comes from the spend port. The remaining
        numbers are left where they are so the parser and this builder still read alike.
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        execution_metric = _workflow_filter(metric_name_field)

        sub_aggs = {
            "1-bucket": {"filter": execution_metric},
            "3-bucket": {
                "filter": execution_metric,
                "aggs": {"3-metric": {"cardinality": {"field": "attributes.user_id.keyword"}}},
            },
            "4-bucket": {
                "filter": {"exists": {"field": USER_EMAIL_KEYWORD_FIELD}},
                "aggs": {
                    "4-metric": {
                        "top_metrics": {
                            "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
            **_pre_floor_money_aggs(pre_floor, within=execution_metric),
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=WORKFLOW_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"1-bucket": "desc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": query,
            "size": 0,
            "aggs": {"paginated_results": terms_agg},
        }

    def _parse_top_workflow_usage_result(self, result: dict) -> list[dict]:
        """Parse result for top workflow usage.

        These are **candidate** rows: `money_spent` is not written here. The pre-floor money travels
        on a private key the enricher pops; `_enrich_top_workflow_usage_spending` writes the column.
        """
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []
        for bucket in buckets:
            workflow_name = bucket["key"]
            invocations = bucket.get("1-bucket", {}).get("doc_count", 0)
            unique_users = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0)

            recent_user = None
            top = bucket.get("4-bucket", {}).get("4-metric", {}).get("top", [])
            if top:
                recent_user = top[0].get("metrics", {}).get(USER_EMAIL_KEYWORD_FIELD)

            rows.append(
                {
                    "workflow_name": workflow_name,
                    "invocations": invocations,
                    "unique_users": unique_users,
                    "recent_user": recent_user or "N/A",
                    PRE_FLOOR_MONEY_KEY: _pre_floor_spend(bucket),
                }
            )
        logger.debug(f"Parsed top-workflow-usage result: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    async def _enrich_top_workflow_usage_spending(
        self,
        rows: list[dict],
        start_dt: datetime,
        end_dt: datetime,
        pre_floor: datetime | None = None,
        client_source: str | None = None,
        report_filter: ReportFilter | None = None,
    ) -> EnrichedRows:
        """Fill each workflow's spend, then order and total the table by it."""
        rendered = await self._workflow_spend(
            rows, report_period_or_none(start_dt, end_dt, client_source), pre_floor, report_filter
        )
        apply_spend_cells(rows, rendered, TOP_USAGE_MONEY_COLUMN)
        return ordered_spend_rows(rows, rendered, TOP_USAGE_MONEY_COLUMN)

    def _get_top_workflow_usage_columns(self) -> list[dict]:
        """Get column definitions for top workflow usage."""
        return [
            {"id": "workflow_name", "label": "Workflow Name", "type": "string"},
            {"id": "invocations", "label": "Invocations", "type": "number"},
            {"id": TOP_USAGE_MONEY_COLUMN, "label": "Money Spent ($)", "type": "number", "format": "currency"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
            {"id": "recent_user", "label": "Recent User", "type": "string"},
        ]

    async def _workflow_spend(
        self,
        rows: list[dict],
        period: TimeRange | None,
        pre_floor: datetime | None,
        report_filter: ReportFilter | None = None,
    ) -> list[RenderedSpend]:
        """Each candidate row's figure, in whichever shape its side of the coverage floor can answer.

        **The pre-floor figure arrives on the row rather than being fetched.** It was summed by the
        candidate aggregation itself - inside the one query that carries the normalised bucket keys,
        the request filters and the `SecureQueryBuilder` boundary - so it is popped here rather than
        read. Popped on every path, because every key left on a row is published and this one holds
        an internal shape. A row built without one is **absent**, not zero.

        **A crossing window splits, and the two legs may not overlap.** The aggregation covers the
        whole requested window, so its money is scoped to `[start, F)` at the source and `[F, end)`
        goes to the port. Handing the port the whole crossing period is the trap: the ledger would
        flag `[start, F)` unavailable on top of the money the aggregation already measured there, and
        the row would double-label.

        **The two legs tile the requested period as instants, and they do not tile the rows.** This
        surface inherits `cutover.py`'s two-clock exposure entire: the pre-floor leg filters
        Elasticsearch on `@timestamp`, when a document was *emitted*, the at-or-above leg filters
        `litellm_spend_logs` on `start_time`, when the call *started*, and a call whose two stamps
        straddle `F` is claimed by neither leg or by both, reading exact either way. Nothing here can
        repair it; it is pinned by tests of its own rather than fixed.

        **One seamless total, and no second wire field.** The legs combine through
        `combine_rendered_spend`, so a window spanning the floor renders as one figure that still
        carries every qualification either leg produced: a leg that found no figure leaves the row
        labelled rather than quietly shortening it.
        """
        names = [row["workflow_name"] for row in rows]
        measured = [row.pop(PRE_FLOOR_MONEY_KEY, render_spend(None)) for row in rows]
        if not names or period is None:
            # There is no window to read, so every figure is absent rather than a fabricated zero.
            return [render_spend(None) for _ in names]

        below, above = split_at_floor(period, pre_floor)
        legs: list[list[RenderedSpend]] = [[] for _ in names]

        if below is not None:
            for parts, pre_floor_money in zip(legs, measured, strict=True):
                parts.append(pre_floor_money)

        if above is not None:
            above_money = await self._at_or_above_floor_money(names, above, report_filter)
            for parts, name in zip(legs, names, strict=True):
                parts.append(above_money[name])

        # `or [render_spend(None)]` is unreachable while a split always yields a leg, and is kept for
        # the same reason `project_handler` keeps it: combining nothing produces a figure of None with
        # no flag on it, which is an absence that says it is not one.
        return [combine_rendered_spend(parts or [render_spend(None)]) for parts in legs]

    async def _at_or_above_floor_money(
        self, names: list[str], period: TimeRange, report_filter: ReportFilter | None = None
    ) -> dict[str, RenderedSpend]:
        """Each name's figure from the spend port over the at-or-above-`F` leg, keyed on the name.

        **The port is keyed on the id, and a name reaches it only where it resolves to exactly one
        live workflow.** A name shared by two live workflows would need its money placed in both
        buckets or in one arbitrarily - AD-29 forbids both - and a name no live workflow carries has no
        id to ask about at all. Either is absent here, and the row says so. A rename inside the window
        lands every at-or-above row of that workflow on its current name (the id is what the ledger
        keys on), so the old name's row is absent for that leg; the table still foots, because a live
        id has exactly one current name.

        **The directory read is platform-wide and unscoped.** Ids never reach the wire and the money
        read is narrowed by the caller's boundary, so resolving a name discloses nothing; scoping the
        lookup would instead break shared and global workflows, which execute in projects other than
        the one that owns them. A directory failure degrades this leg for every row rather than the
        report: the name is kept, the at-or-above money is absent, and the row is labelled.

        **The candidate set is the pipeline's 10 000 buckets, ranked by document count.** Money can
        only be ordered once it exists, which is after this point, so a workflow with heavy ledger
        spend but few metric documents is cut by that cap before the money sort ever sees it - the
        same consequence the users and projects spending tables carry.

        **`report_filter` narrows the read to the dashboard's users and projects.** The ledger keys a
        workflow's rows on its own column, which holds its executions in every project and by every
        person, so a filtered table read without it shows the workflow's spend everywhere.
        """
        # A synchronous directory read: off the event loop, which serves every request (EPMCDME-15537).
        ids_by_name, _ = await asyncio.to_thread(self._resolve_workflow_ids, names)
        unique = {name: ids[0] for name, ids in ids_by_name.items() if len(ids) == 1}
        workflow_ids = list(dict.fromkeys(unique.values()))
        # One round trip for the whole candidate set - every id shares the one requested window. The
        # boundary is the pipeline's: the single derivation point from a `User`, and the very context
        # the rows above were selected under.
        spend = (
            await spend_service.aggregate_by_dimension(
                period,
                SpendDimension.WORKFLOW,
                workflow_ids,
                access=self._pipeline.access_scope,
                report_filter=report_filter,
            )
            if workflow_ids
            else {}
        )
        logger.info(
            f"Workflow analytics read the ledger for {len(workflow_ids)} of {len(set(names))} workflow name(s) "
            "over one window; the rest resolve to no single live workflow"
        )
        return {name: render_spend(spend.get(unique[name])) if name in unique else render_spend(None) for name in names}

    @staticmethod
    def _resolve_workflow_ids(names: list[str | None]) -> tuple[dict[str, list[str]], bool]:
        """Every live workflow id per name, resolved from the **owning entity**, plus a failure flag.

        Deliberately not read from the metric documents: a document carries the id only from this
        ticket's deploy forward, and a bucket's documents may name two ids after a rename - the
        directory is the one place a name's *current* holders are known.

        The boolean says whether the lookup itself failed, because that is a different situation
        from a name the lookup could not find - though both leave the at-or-above leg absent, the
        first is worth a warning and the second is not.
        """
        unique_names = [one for one in dict.fromkeys(names) if one]
        if not unique_names:
            return {}, False

        from codemie.core.workflow_models.workflow_config import WorkflowConfig

        try:
            found = WorkflowConfig.get_ids_by_names(unique_names)
        except Exception as error:
            # A name lookup failing degrades one leg of the report; it never fails the report.
            logger.warning(f"Could not resolve workflow ids for the at-or-above-floor read: {error}")
            return {}, True

        by_name: dict[str, list[str]] = {}
        for workflow_id, name in found:
            by_name.setdefault(name, []).append(workflow_id)
        return by_name, False

    @staticmethod
    def _pre_floor_boundary() -> datetime | None:
        """The instant below which these tables serve their own money, or None if they never do.

        The registered coverage is read **here**, at the one seam a test publishes a floor through, and
        the decision is `money_coverage.pre_floor_boundary`'s - shared with `project_handler`, so the
        two tables cannot read one flag two ways. Read once per request and handed to both halves of
        the split.
        """
        return pre_floor_boundary(ledger_coverage())

    @staticmethod
    def _avg_cost(figure: float | None, total_runs: int) -> float | None:
        """The per-run cost, or **nothing at all** where no such cost exists.

        Two absences used to arrive as `0`, and on a screen a zero is a claim: *this workflow costs
        nothing per run*. Neither absence supports it.

        - **no runs**: there is no ratio to state, the way there is no percentage of an absent limit
          (`budget_usage_service._build_spending_row`);
        - **no figure**: an unknown numerator makes the quotient unknown too, and dividing the
          absence into a count does not make it knowable.

        A figure that exists but is *qualified* does yield a ratio, which inherits the qualification
        from the row's annotation exactly as the figure does - it is the same money, divided.
        """
        if figure is None or total_runs <= 0:
            return None
        return round(figure / total_runs, 5)
