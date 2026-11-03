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

"""Assistant and agent analytics handler."""

from __future__ import annotations

import copy
import logging
from datetime import datetime
from functools import partial

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import (
    METRIC_NAME_KEYWORD_FIELD,
    PLACEHOLDER_USER_IDS,
    USER_EMAIL_KEYWORD_FIELD,
    USER_ID_KEYWORD_FIELD,
)
from codemie.service.analytics.handlers.money_coverage import (
    money_coverage_aggs,
    pre_floor_boundary,
    read_money_coverage,
    split_at_floor,
)
from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.analytics.handlers.user_identity_resolver import UserIdentityResolver
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import AnalyticsQueryFilters, AnalyticsQueryPipeline, EnrichedRows
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
    time_range_or_none,
)
from codemie.service.spend.spend_service import spend_service

logger = logging.getLogger(__name__)

# Elasticsearch field constants
ASSISTANT_NAME_KEYWORD_FIELD = "attributes.assistant_name.keyword"
#: What a table groups on **wherever every document it spans carries it**: the spend port keys the
#: assistant dimension on this id, so one bucket and one figure name the same thing. The name above is
#: then a label only - mutable and not unique, so grouping by it splits one assistant across a rename
#: and merges two that share a name, which AD-29 calls no bucket assignment at all. Two of this
#: handler's three tables can hold to that; `get_agents_usage` cannot, and `_render_agents_usage_money`
#: records why and what follows from it.
ASSISTANT_ID_KEYWORD_FIELD = "attributes.assistant_id.keyword"
MONEY_SPENT_FIELD = "attributes.money_spent"
TIMESTAMP_FIELD = "@timestamp"
UNIQUE_USERS_LABEL = "Unique Users"

# The top-agents-usage table's ledger-filled money column, the row key carrying its grouping id, and
# the sub-aggregation carrying its fallback label. `CHATS_ASSISTANT_ID_KEY` below is the same row key
# under the chats surface's own name; one name serving both is a follow-up, because that constant
# sits in a region this change may not edit.
TOP_AGENTS_MONEY_COLUMN = "money_spent"
TOP_AGENTS_ASSISTANT_ID_KEY = "assistant_id"
DISPLAY_NAME_AGG = "display_name"

# The assistants-chats table's two ledger-filled columns, named once so the enricher that writes them
# and the column list that displays them cannot drift apart. The other surfaces in this module keep
# their own money columns in Elasticsearch and are not addressed by these.
CHATS_MONEY_COLUMN = "total_cost_usd"
CHATS_COST_PER_OP_COLUMN = "cost_per_op_usd"

#: Carries the grouping id from the parser to the enricher, which is the only reader of it: the port
#: is keyed on the id while the table displays the name. Consumed there, so it reaches no response.
CHATS_ASSISTANT_ID_KEY = "assistant_id"

#: The agents-usage table's money column, the below-floor money bucket with the sub-aggregation
#: holding its sum, and the private key that carries the read of that sum - already qualified - from
#: the parser to the renderer. The row key is underscore-prefixed and **popped** before the row is
#: published: it carries a `RenderedSpend`, which is an internal shape and not a wire value.
#:
#: **The rows are keyed on the assistant *name*, unlike both of its siblings above**, because the
#: table spans the tool-usage metrics, whose whole history predates `assistant_id` emission. Only the
#: money below the coverage floor is measured here; at or above it the figure comes from the spend
#: port, by the id a name resolves to when it resolves to exactly one. `_render_agents_usage_money`
#: records the whole reason.
AGENTS_USAGE_MONEY_COLUMN = "total_cost"
AGENTS_USAGE_MONEY_BUCKET = "2-bucket"
AGENTS_USAGE_MONEY_SUM_AGG = "2-metric"
AGENTS_USAGE_MONEY_KEY = "_money"


def _pre_floor_money_aggs(pre_floor: datetime | None, *, within: dict) -> dict:
    """The per-name money the below-`F` leg is served from, scoped to `[start, F)` at the source.

    **Bounded at `F` here rather than trimmed afterwards.** The surrounding query covers the whole
    requested window, so a bucket money sum taken over all of it would include the at-or-above part -
    which the port read also reports - and a crossing row would foot at nearly double with nothing on
    it saying so. `lt` rather than `lte`: every interval on these paths is half-open, `F` belongs to
    the at-or-above leg, and adjacent half-open ranges tile exactly.

    **No floor means no bucket at all.** Where the ledger covers the whole window this table never
    serves its own money, and an emptied bucket would still be a second money source in the body.

    **`within` is the metric term the money has always sat behind**, and is the counting bucket's own
    filter: the table's query carries no metric filter - its buckets hold every document naming an
    assistant, tool-usage included - so summing the whole bucket would count documents this table
    never counted the money of.

    **No `cost_bearing` narrowing**, unlike the workflow tables. `within` already restricts the bucket
    to `conversation_assistant_usage`, which is a spending metric, so every document it holds is one a
    cost is expected on and a second filter would narrow nothing.
    """
    if pre_floor is None:
        return {}
    below_floor = {"range": {TIMESTAMP_FIELD: {"lt": pre_floor.isoformat(), "format": "strict_date_optional_time"}}}
    return {
        AGENTS_USAGE_MONEY_BUCKET: {
            "filter": {"bool": {"filter": [below_floor, within]}},
            "aggs": {
                AGENTS_USAGE_MONEY_SUM_AGG: {"sum": {"field": MONEY_SPENT_FIELD}},
                # `attributes.money_spent` is a **settled remainder** - the monitoring services omit
                # it whenever a cost has not settled - so the sum is never published bare; these two
                # counts let `read_money_coverage` turn it into a figure that carries its own
                # qualification.
                **money_coverage_aggs(MONEY_SPENT_FIELD),
            },
        }
    }


def _pre_floor_spend(bucket: dict) -> RenderedSpend:
    """One name bucket's pre-floor money, qualified by its coverage.

    Absent when the bucket carries no money bucket at all - a query built without a floor - and absent
    when nothing in it settled; a measured `0.0` stays a measured zero.
    """
    return read_money_coverage(bucket.get(AGENTS_USAGE_MONEY_BUCKET, {}), AGENTS_USAGE_MONEY_SUM_AGG)


class AssistantHandler:
    """Handler for assistant and agent analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize assistant handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)

    async def get_assistants_chats(
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
        """Get assistants/chats analytics with performance metrics.

        Uses ES|QL for accurate 2-stage aggregation: first by conversation, then by assistant.
        This ensures correct error rate calculation and message counting.

        **Money is not in the aggregation.** Elasticsearch supplies tokens and counts; every cost on
        this table comes from the spend port, filled per assistant id by `_enrich_assistants_spending`
        after the query has run (AD-1, AD-14).

        Args:
            time_period: Predefined time range
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users
            projects: Filter by specific projects
            page: Page number (zero-indexed)
            per_page: Items per page
            client_source: Filter by client source (optional)

        Returns:
            Tabular response with assistants metrics including min/median/max statistics
        """
        logger.info("Requesting assistants-chats analytics")

        # Build ES|QL query with 2-stage aggregation for accurate metrics.
        # Grouped by attributes.assistant_id.keyword, not assistant_name: the name is
        # mutable, so grouping by it splits one assistant across a rename and merges two
        # assistants that happen to share a name (AD-29). assistant_name is still carried
        # through both stages via MAX(), for display only.
        esql_query = """
FROM codemie_metrics_logs*
| WHERE metric_name.keyword == \"conversation_assistant_usage\"
| STATS
    messages_per_conv = COUNT(*),
    total_tokens_per_conv = SUM(attributes.input_tokens + attributes.output_tokens),
    errors_per_conv = SUM(CASE(attributes.status != \"success\", 1, 0)),
    avg_exec_time_per_conv = AVG(attributes.execution_time),
    user_name = MAX(attributes.user_name.keyword),
    assistant_name = MAX(attributes.assistant_name.keyword),
    last_error_timestamp = MAX(CASE(attributes.status != \"success\", @timestamp, null))
  BY attributes.assistant_id.keyword, attributes.conversation_id.keyword
| STATS
    total_conversations = COUNT(*),
    total_messages = SUM(messages_per_conv),
    avg_messages_per_chat = AVG(messages_per_conv),
    median_messages_per_chat = PERCENTILE(messages_per_conv, 50),
    min_messages_per_chat = MIN(messages_per_conv),
    max_messages_per_chat = MAX(messages_per_conv),
    failed_conversations = SUM(CASE(errors_per_conv > 0, 1, 0)),
    total_errors = SUM(errors_per_conv),
    total_tokens = SUM(total_tokens_per_conv),
    avg_execution_time = AVG(avg_exec_time_per_conv),
    unique_users = COUNT_DISTINCT(user_name),
    last_error_time = MAX(last_error_timestamp),
    assistant_name = MAX(assistant_name)
  BY attributes.assistant_id.keyword
| EVAL
    successful_operations = total_messages - total_errors,
    error_rate_percent = ROUND((TO_DOUBLE(failed_conversations) / TO_DOUBLE(total_conversations)) * 100, 2),
    avg_messages_rounded = ROUND(avg_messages_per_chat, 2),
    median_messages_rounded = ROUND(median_messages_per_chat, 2),
    avg_execution_rounded = ROUND(avg_execution_time, 2)
| SORT total_messages DESC, attributes.assistant_id.keyword ASC
| LIMIT 1000
"""

        return await self._pipeline.execute_esql_query(
            esql_query=esql_query,
            result_parser=self._parse_assistants_chats_result,
            columns=self._get_assistants_chats_columns(),
            filters=AnalyticsQueryFilters(
                metric_filters=[MetricName.CONVERSATION_ASSISTANT_USAGE.value],
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
                page=page,
                per_page=per_page,
                client_source=client_source,
            ),
            row_enricher=partial(
                self._enrich_assistants_spending,
                client_source=client_source,
                report_filter=await report_filter_for(users, projects),
            ),
        )

    async def _enrich_assistants_spending(
        self,
        rows: list[dict],
        start_dt: datetime,
        end_dt: datetime,
        client_source: str | None = None,
        report_filter: ReportFilter | None = None,
    ) -> EnrichedRows:
        """Fill each assistant's cost from the spend port, then order and total the table by it.

        The port is keyed on the **assistant ids the query grouped by**, never on the display names:
        a name is mutable, so keying on it would ask the ledger about a bucket the aggregation never
        formed (AD-29). `_parse_assistants_chats_result` carries the id for exactly this read and it
        is consumed here, so the wire shape is the one it has always been.

        **The candidate set is the query's own `LIMIT 1000`, ranked by `total_messages`.** Money can
        only be ordered once it exists, which is after this point, so an assistant with heavy ledger
        spend but few metric messages is cut by that cap before the money sort ever sees it - the same
        consequence the users and projects tables carry at 10 000 buckets, tighter here.

        **No row is dropped for costing nothing**, unlike a spending table: a row here is an
        assistant's activity record, and an assistant that was used and cost nothing still belongs in
        a table of assistant usage.

        **The figure is narrowed by the same access boundary the rows were selected under.** The
        pipeline injects that boundary into this table's own query at construction, so a figure read
        without it foots a narrowed row set with the whole platform's spend on a shared assistant -
        the disclosure `access_scope` exists to close. The scope comes from the pipeline rather than
        from an `AccessFilter` built here, so one context answers both halves.

        **And by the dashboard's own filter** (`report_filter`, built once from the request's users and
        projects). An assistant's ids select its calls in every project and for every person, so a
        filtered table read without it foots its rows with the assistant's spend everywhere else and
        sizes the "unattributed" residue on the whole window.
        """
        period = report_period_or_none(start_dt, end_dt, client_source)
        assistant_ids = [row.pop(CHATS_ASSISTANT_ID_KEY) for row in rows]
        # One round trip for the whole candidate set - every id shares the one requested window.
        spend = (
            await spend_service.aggregate_by_dimension(
                period,
                SpendDimension.ASSISTANT,
                assistant_ids,
                access=self._pipeline.access_scope,
                report_filter=report_filter,
            )
            if period is not None and assistant_ids
            else {}
        )
        logger.info(f"Assistants-chats read the ledger for {len(set(assistant_ids))} assistant(s) over one window")

        rendered = [render_spend(spend.get(assistant_id)) for assistant_id in assistant_ids]
        apply_spend_cells(rows, rendered, CHATS_MONEY_COLUMN)
        for row, figure in zip(rows, rendered, strict=True):
            # No annotation of its own: the ratio is derived from the same `SpendResult` as the money
            # cell, so the row's single triple already qualifies both. A second copy would be a second
            # thing to keep in sync, saying nothing the first does not.
            row[CHATS_COST_PER_OP_COLUMN] = self._cost_per_operation(figure.figure, row["success_ops"])

        # The ledger figure decides the order and an absent one sorts last rather than as a zero: the
        # first key separates the two outright, so no absent row is ranked among the figures.
        ordered = sorted(
            zip(rows, rendered, strict=True),
            key=lambda pair: (pair[1].figure is not None, pair[1].figure or 0.0),
            reverse=True,
        )
        return EnrichedRows(
            rows=[row for row, _ in ordered],
            # Only the money column names a footer key. `TotalsCalculator` excludes a per-something
            # column from every footer by pattern, so the ratio claims nothing there.
            columns=(CHATS_MONEY_COLUMN,),
            totals=spend_footer(CHATS_MONEY_COLUMN, [figure for _, figure in ordered]),
        )

    @staticmethod
    def _cost_per_operation(figure: float | None, successful_operations: int) -> float | None:
        """The per-operation cost, or **nothing at all** where no such cost exists.

        Two absences used to arrive as `0`, and on a screen a zero is a claim: *this assistant costs
        nothing per operation*. Neither absence supports it.

        - **no successful operations**: there is no ratio to state, the way there is no percentage of
          an absent limit (`budget_usage_service._build_spending_row`);
        - **no figure**: an unknown numerator makes the quotient unknown too, and dividing the absence
          into a count does not make it knowable.

        A figure that exists but is *qualified* does yield a ratio, which inherits the qualification
        from the row's annotation exactly as the figure does - it is the same money, divided.
        """
        if figure is None or successful_operations <= 0:
            return None
        return round(figure / successful_operations, 5)

    @staticmethod
    def _resolve_assistant_names(assistant_ids: list[str]) -> tuple[dict[str, str], bool]:
        """Current names for the buckets, resolved from the **owning entity**, plus a failure flag.

        **Deliberately not read from the metric documents.** A name stored on a metric row is the
        mutable value the id exists to escape: after a rename the historical rows still carry the old
        name, so any aggregate over them - `MAX`, `LAST`, whichever - renders a name the assistant no
        longer has. Grouping by the stable id and then labelling from the same rows fixes the buckets
        and leaves the labels wrong, which is the harder defect to notice of the two.

        The boolean says whether the lookup itself failed, because that is a different situation from
        an id the lookup could not find, and the two must not render the same way.
        """
        unique_ids = [one for one in dict.fromkeys(assistant_ids) if one]
        if not unique_ids:
            return {}, False

        from codemie.rest_api.models.assistant import Assistant

        try:
            found = Assistant.get_by_ids_no_permission_check(unique_ids)
        except Exception as error:
            # A name lookup failing degrades the report's labels; it never fails the report.
            logger.warning(f"Could not resolve assistant names for display: {error}")
            return {}, True
        return {one.id: one.name for one in found if one.name}, False

    @staticmethod
    def _display_name(
        *,
        assistant_id: str,
        current_names: dict[str, str],
        resolution_failed: bool,
        name_in_metrics: str | None,
    ) -> str:
        """What a bucket is called, with the two absences kept apart.

        - resolved: the assistant's **current** name, which is the correction grouping by id buys;
        - **not found** - a deleted assistant: the id itself. Its money stays in the total, because
          dropping the bucket would truncate the total; and the stale name in the metrics is *not*
          used, because presenting it would claim an entity still exists under that name;
        - **lookup failed**: fall back to the name the metrics carry. Stale labels over a correct set
          of buckets is a far better degraded report than a screen of bare ids, and unlike the case
          above nothing is being asserted about whether the entity exists.
        """
        resolved = current_names.get(assistant_id)
        if resolved:
            return resolved
        if resolution_failed and name_in_metrics:
            return name_in_metrics
        return assistant_id

    def _parse_assistants_chats_result(self, result: dict) -> list[dict]:
        """Parse ES|QL result for assistants/chats analytics.

        These are **candidate** rows: neither money column is written here, because neither is in the
        aggregation any more, and a placeholder for a figure that has not been read yet is the
        fabricated zero this surface used to emit. `_enrich_assistants_spending` writes both.
        """
        columns_metadata = result.get("columns", [])
        values = result.get("values", [])
        # An unconfigured Elasticsearch answers with no columns at all, so there is no id column to look up.
        if not values:
            return []

        # Create column name to index mapping
        col_map = {col["name"]: idx for idx, col in enumerate(columns_metadata)}

        id_index = col_map["attributes.assistant_id.keyword"]
        current_names, resolution_failed = self._resolve_assistant_names(
            [row_values[id_index] for row_values in values]
        )

        rows = []
        for row_values in values:
            assistant_id = row_values[id_index]
            assistant_name = self._display_name(
                assistant_id=assistant_id,
                current_names=current_names,
                resolution_failed=resolution_failed,
                name_in_metrics=row_values[col_map["assistant_name"]],
            )
            total_chats = int(row_values[col_map["total_conversations"]])
            total_messages = int(row_values[col_map["total_messages"]])
            unique_users = int(row_values[col_map["unique_users"]])
            min_msg_per_chat = int(row_values[col_map["min_messages_per_chat"]])
            median_msg_per_chat = float(row_values[col_map["median_messages_rounded"]])
            avg_msg_per_chat = float(row_values[col_map["avg_messages_rounded"]])
            max_msg_per_chat = int(row_values[col_map["max_messages_per_chat"]])
            success_ops = int(row_values[col_map["successful_operations"]])
            total_errors = int(row_values[col_map["total_errors"]])
            error_rate = float(row_values[col_map["error_rate_percent"]] or 0)
            total_tokens = int(row_values[col_map["total_tokens"]])
            avg_time = float(row_values[col_map["avg_execution_rounded"]] or 0)
            last_error_time = row_values[col_map["last_error_time"]] if "last_error_time" in col_map else None

            rows.append(
                {
                    CHATS_ASSISTANT_ID_KEY: assistant_id,
                    "assistant": assistant_name,
                    "total_chats": total_chats,
                    "total_messages": total_messages,
                    "unique_users": unique_users,
                    "min_msg_per_chat": min_msg_per_chat,
                    "median_msg_per_chat": median_msg_per_chat,
                    "avg_msg_per_chat": avg_msg_per_chat,
                    "max_msg_per_chat": max_msg_per_chat,
                    "success_ops": success_ops,
                    "total_errors": total_errors,
                    "error_rate_percent": error_rate,
                    "total_tokens": total_tokens,
                    "avg_time_seconds": avg_time,
                    "last_error_timestamp": last_error_time,
                }
            )

        logger.debug(
            f"Parsed assistants-chats result: total_columns={len(columns_metadata)}, "
            f"total_rows={len(values)}, rows_parsed={len(rows)}, "
            f"column_names={[col['name'] for col in columns_metadata]}"
        )
        return rows

    def _get_assistants_chats_columns(self) -> list[dict]:
        """Get column definitions for assistants/chats response."""
        return [
            {"id": "assistant", "label": "Assistant", "type": "string"},
            {"id": "total_chats", "label": "Total Chats", "type": "number"},
            {"id": "total_messages", "label": "Total Messages", "type": "number"},
            {"id": "unique_users", "label": UNIQUE_USERS_LABEL, "type": "number"},
            {"id": "min_msg_per_chat", "label": "Min Msg/Chat", "type": "number"},
            {"id": "median_msg_per_chat", "label": "Median Msg/Chat", "type": "number"},
            {"id": "avg_msg_per_chat", "label": "Avg Msg/Chat", "type": "number"},
            {"id": "max_msg_per_chat", "label": "Max Msg/Chat", "type": "number"},
            {"id": "success_ops", "label": "Success Ops", "type": "number"},
            {"id": "total_errors", "label": "Total Errors", "type": "number"},
            {"id": "error_rate_percent", "label": "Error Rate (%)", "type": "number", "format": "percentage"},
            {"id": "total_tokens", "label": "Total Tokens", "type": "number"},
            {"id": CHATS_MONEY_COLUMN, "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": CHATS_COST_PER_OP_COLUMN, "label": "Cost/Op ($)", "type": "number", "format": "currency"},
            {"id": "avg_time_seconds", "label": "Avg Time (s)", "type": "number"},
            {"id": "last_error_timestamp", "label": "Last Error Time", "type": "string", "format": "timestamp"},
        ]

    async def get_agents_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 30,
    ) -> dict:
        """Get assistant and tool usage analytics with cost, users, and error tracking.

        This endpoint provides comprehensive analytics for assistant usage including:
        - Conversation counts per assistant
        - Total cost (money spent)
        - Unique users per assistant
        - Unique tools used
        - Tool error counts and last error details

        **Money arrives in two legs joined at the coverage floor `F`, as the workflow tables' does.**
        Below `F` the ledger holds nothing, so each name bucket's own `attributes.money_spent` sum is
        measured inside the query and read through `read_money_coverage`, which makes a sum that is
        only the settled remainder say so. At or above `F` the figure comes from the spend port, by the
        id each name resolves to when it resolves to exactly one live assistant.
        `_render_agents_usage_money` combines the two and records why a name may get no figure at all.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination (default: 0)
            per_page: Results per page (default: 30)

        Returns:
            Tabular response with assistant usage metrics
        """
        logger.info("Requesting agents-usage analytics")

        # Read **once** for the whole request and handed to both halves of the split: the aggregation
        # that scopes the pre-floor money and the renderer that decides which legs run.
        pre_floor = self._pre_floor_boundary()
        report_filter = await report_filter_for(users, projects)

        return await self._pipeline.execute_enriched_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_agents_usage_aggregation(query, fetch_size, pre_floor),
            result_parser=self._parse_agents_usage_result,
            columns=self._get_agents_usage_columns(),
            group_by_field=ASSISTANT_NAME_KEYWORD_FIELD,
            # The renderer owns the money column's footer, and the pipeline hands it every candidate
            # row. It costs no extra fetch: the paged path was already running a second 10 000-bucket
            # copy of this same aggregation just to foot the table.
            row_enricher=lambda rows, start_dt, end_dt: self._render_agents_usage_money(
                rows, start_dt, end_dt, pre_floor, report_filter
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
            ),
        )

    def _build_agents_usage_aggregation(self, query: dict, fetch_size: int, pre_floor: datetime | None = None) -> dict:
        """Build terms aggregation for agents usage with fetch-and-slice.

        Grouped on the assistant **name** - the constants above say why - with the money below `F`
        measured inside the bucket and nothing money-shaped above it.
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (nested metrics)
        sub_aggs = {
            "1-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CONVERSATION_ASSISTANT_USAGE.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                }
            },
            "3-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CONVERSATION_ASSISTANT_USAGE.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {"3-metric": {"cardinality": {"field": "user_id.keyword"}}},
            },
            "4-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CODEMIE_TOOLS_USAGE_TOTAL.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {"4-metric": {"cardinality": {"field": "attributes.base_tool_name.keyword"}}},
            },
            "5-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CODEMIE_TOOLS_USAGE_ERRORS.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                }
            },
            "6-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CODEMIE_TOOLS_USAGE_ERRORS.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
                "aggs": {
                    "6-metric": {
                        "top_metrics": {
                            "metrics": {"field": "attributes.error_class.keyword"},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
        }

        # The below-floor money reuses the counting bucket's own filter, so the money and the
        # conversations it sits beside can never be measured over two different sets of documents.
        # A copy, not the live object: the two buckets must be one filter by value, never one dict
        # that an edit to either bucket would silently change in both.
        sub_aggs.update(_pre_floor_money_aggs(pre_floor, within=copy.deepcopy(sub_aggs["1-bucket"]["filter"])))

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=ASSISTANT_NAME_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"1-bucket": "desc"},
            sub_aggs=sub_aggs,
        )

        # Construct full aggregation body
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": terms_agg,
            },
        }

    def _parse_agents_usage_result(self, result: dict) -> list[dict]:
        """Parse result for agents usage.

        These are **candidate** rows: the money cell is not written here. The pre-floor money the
        bucket measured travels on a private key the renderer pops, and the at-or-above money has not
        been read yet - a placeholder for it would be a fabricated zero. `_render_agents_usage_money`
        owns the column and its footer, so one writer fills both rather than the row and the footer
        coming from two places that are free to disagree.
        """
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            assistant_name = bucket["key"]

            total_conversations = bucket.get("1-bucket", {}).get("doc_count", 0)
            money = _pre_floor_spend(bucket)
            unique_users = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0)
            unique_tools = bucket.get("4-bucket", {}).get("4-metric", {}).get("value", 0)
            tool_errors = bucket.get("5-bucket", {}).get("doc_count", 0)

            # Extract last error if available
            last_error = None
            top_metrics = bucket.get("6-bucket", {}).get("6-metric", {}).get("top", [])
            if top_metrics and len(top_metrics) > 0:
                metrics_data = top_metrics[0].get("metrics", {})
                last_error = metrics_data.get("attributes.error_class.keyword")

            rows.append(
                {
                    "assistant_name": assistant_name,
                    "total_conversations": total_conversations,
                    # Consumed by `_render_agents_usage_money`, so it reaches no response.
                    AGENTS_USAGE_MONEY_KEY: money,
                    "unique_users": unique_users,
                    "unique_tools_used": unique_tools,
                    "tool_errors": tool_errors,
                    "last_error": last_error or "N/A",
                }
            )

        logger.debug(
            f"Parsed agents-usage result: total_buckets={len(buckets)}, "
            f"rows_parsed={len(rows)}, "
            f"has_aggregations={bool(result.get('aggregations'))}"
        )
        return rows

    async def _render_agents_usage_money(
        self,
        rows: list[dict],
        start_dt: datetime,
        end_dt: datetime,
        pre_floor: datetime | None = None,
        report_filter: ReportFilter | None = None,
    ) -> EnrichedRows:
        """Write this table's money cells from whichever source its side of the floor can answer, and
        total the column.

        **Why the rows stay keyed on the assistant name while both sibling tables key on the id.**
        `get_assistants_chats` and `get_top_agents_usage` group on `attributes.assistant_id`, which the
        spend port is keyed on, so one bucket and one figure name the same thing. This table cannot
        follow them: its rows span the tool-usage metrics, whose entire history predates `assistant_id`
        emission, so keying the rows on the id would blank the table for every past window. The rows are
        keyed on the mutable `attributes.assistant_name` instead, and the money is fitted to that key
        rather than the key to the money.

        **A name reaches the ledger only through the assistants directory, and only when it resolves to
        exactly one live assistant.** The refused approach was resolving the id from the *metric
        documents* - a `terms` sub-aggregation with `"size": 1` inside each name bucket - and asking the
        port with whatever came back. That fails in both directions, because name-to-id is not a
        bijection, and those two failures are the reason for the exactly-one rule:

        - **rename - one id, two name rows.** `asst-42` renamed mid-window, $120.00 of true spend: both
          rows rendered `120.0` and the footer read $240.00, unqualified. `spend_footer` adds its
          figures on the stated premise that they come from disjoint row sets, which a repeated
          dimension value breaks; it deduplicates *holes* by identity, not figures.
        - **collision - two ids, one name row.** `asst-A` at $90.00 and `asst-B` at $410.00 both
          displayed as "Reviewer": the row read `90.0` and the other $410.00 appeared in neither the
          figure nor any residue. `"size": 1` is a page size, not a total.

        Resolving from the owning entity instead answers a different question - which live assistants
        carry this name *now* - and a name that is not exactly one of them gets **no** figure and a
        label, never an arbitrarily chosen bucket. That is what the workflow tables do at the same seam.

        **Below the floor the figure is still this table's own qualified sum.**
        `attributes.money_spent` is a *settled remainder* - the monitoring services omit it whenever a
        cost has not settled - so the sum is never published as exact: nothing settled in a bucket is
        **no figure** and never `0.0`; something unsettled is the subtotal, marked partial; everything
        settled is exact, and a *measured* zero stays `0.0`.

        **The two legs measure time on two different clocks.** The pre-floor leg filters Elasticsearch
        on `@timestamp`, when a document was emitted, while the at-or-above leg filters the ledger on
        `start_time`, when the call started, so a call whose two stamps straddle the floor is claimed by
        neither leg or by both; that exposure is inherited here rather than repaired, and pinned by
        tests of its own.

        Row order is left exactly as the aggregation ranked it: this is an activity table and the money
        column was never its sort key.
        """
        rendered = await self._agents_usage_spend(rows, time_range_or_none(start_dt, end_dt), pre_floor, report_filter)
        apply_spend_cells(rows, rendered, AGENTS_USAGE_MONEY_COLUMN)
        return EnrichedRows(
            rows=rows,
            columns=(AGENTS_USAGE_MONEY_COLUMN,),
            totals=spend_footer(AGENTS_USAGE_MONEY_COLUMN, rendered),
        )

    async def _agents_usage_spend(
        self,
        rows: list[dict],
        period: TimeRange | None,
        pre_floor: datetime | None,
        report_filter: ReportFilter | None = None,
    ) -> list[RenderedSpend]:
        """Each candidate row's figure, in whichever shape its side of the coverage floor can answer.

        **The pre-floor figure arrives on the row rather than being fetched.** It was summed by the
        candidate aggregation itself - inside the one query that carries the normalised bucket keys, the
        request filters and the access boundary the rows were selected under - so it is popped here
        rather than read. Popped on every path, because every key left on a row is published and this
        one holds an internal shape. A row built without one is **absent**, not zero.

        **A crossing window splits, and the two legs may not overlap.** The aggregation covers the whole
        requested window, so its money is scoped to `[start, F)` at the source and `[F, end)` goes to the
        port. Handing the port the whole crossing period is the trap: the ledger would flag `[start, F)`
        unavailable on top of the money the aggregation already measured there, and the row would
        double-label.

        **One seamless total, and no second wire field.** The legs combine through
        `combine_rendered_spend`, so a window spanning the floor renders as one figure that still
        carries every qualification either leg produced: a leg that found no figure leaves the row
        labelled rather than quietly shortening it.
        """
        names = [row["assistant_name"] for row in rows]
        measured = [row.pop(AGENTS_USAGE_MONEY_KEY, render_spend(None)) for row in rows]
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

        # `or [render_spend(None)]` is unreachable while a split always yields a leg, and is kept
        # because combining nothing produces a figure of None with no flag on it, which is an absence
        # that says it is not one.
        return [combine_rendered_spend(parts or [render_spend(None)]) for parts in legs]

    async def _at_or_above_floor_money(
        self, names: list[str], period: TimeRange, report_filter: ReportFilter | None = None
    ) -> dict[str, RenderedSpend]:
        """Each name's figure from the spend port over the at-or-above-`F` leg, keyed on the name.

        **The port is keyed on the id, and a name reaches it only where it resolves to exactly one live
        assistant.** A name shared by two live assistants would need its money placed in both buckets or
        in one arbitrarily, and a name no live assistant carries has no id to ask about at all. Either
        is absent here, and the row says so. A rename inside the window lands every at-or-above row of
        that assistant on its current name - the ledger keys on the id - so the old name's row is absent
        for that leg; the table still foots, because a live id has exactly one current name.

        **The directory read is platform-wide and unscoped.** Ids never reach the wire and the money
        read is narrowed by the caller's boundary, so resolving a name discloses nothing; scoping the
        lookup would instead break shared and global assistants, which are used from projects other than
        the one that owns them. A directory failure degrades this leg for every row rather than the
        report: the name is kept, the at-or-above money is absent, and the row is labelled.

        `report_filter` narrows the money to the dashboard's filter, for `_enrich_assistants_spending`'s
        reason.
        """
        ids_by_name, _ = self._resolve_assistant_ids(names)
        unique = {name: ids[0] for name, ids in ids_by_name.items() if len(ids) == 1}
        assistant_ids = list(dict.fromkeys(unique.values()))
        # One round trip for the whole candidate set - every id shares the one requested window. The
        # boundary is the pipeline's: the single derivation point from a `User`, and the very context
        # the rows above were selected under.
        spend = (
            await spend_service.aggregate_by_dimension(
                period,
                SpendDimension.ASSISTANT,
                assistant_ids,
                access=self._pipeline.access_scope,
                report_filter=report_filter,
            )
            if assistant_ids
            else {}
        )
        logger.info(
            f"Agents-usage read the ledger for {len(assistant_ids)} of {len(set(names))} assistant name(s) "
            "over one window; the rest resolve to no single live assistant"
        )
        return {name: render_spend(spend.get(unique[name])) if name in unique else render_spend(None) for name in names}

    @staticmethod
    def _resolve_assistant_ids(names: list[str | None]) -> tuple[dict[str, list[str]], bool]:
        """Every live assistant id per name, resolved from the **owning entity**, plus a failure flag.

        Deliberately not read from the metric documents: a bucket's documents may name two ids after a
        rename, and the directory is the one place a name's *current* holders are known.

        The boolean says whether the lookup itself failed, because that is a different situation from a
        name the lookup could not find - though both leave the at-or-above leg absent, the first is
        worth a warning and the second is not.
        """
        unique_names = [one for one in dict.fromkeys(names) if one]
        if not unique_names:
            return {}, False

        from codemie.rest_api.models.assistant import Assistant

        try:
            found = Assistant.get_ids_by_names(unique_names)
        except Exception as error:
            # A name lookup failing degrades one leg of the report; it never fails the report.
            logger.warning(f"Could not resolve assistant ids for the at-or-above-floor read: {error}")
            return {}, True

        by_name: dict[str, list[str]] = {}
        for assistant_id, name in found:
            by_name.setdefault(name, []).append(assistant_id)
        return by_name, False

    @staticmethod
    def _pre_floor_boundary() -> datetime | None:
        """The instant below which this table serves its own money, or None if it never does.

        The registered coverage is read **here**, at the one seam a test publishes a floor through, and
        the decision is `money_coverage.pre_floor_boundary`'s - shared with the other tables, so none of
        them can read one flag two ways. Read once per request and handed to both halves of the split.
        """
        return pre_floor_boundary(ledger_coverage())

    def _get_agents_usage_columns(self) -> list[dict]:
        """Get column definitions for agents usage."""
        return [
            {"id": "assistant_name", "label": "Assistant Name", "type": "string"},
            {"id": "total_conversations", "label": "Total Conversations", "type": "number"},
            {"id": "total_cost", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "unique_users", "label": UNIQUE_USERS_LABEL, "type": "number"},
            {"id": "unique_tools_used", "label": "Unique Tools Used", "type": "number"},
            {"id": "tool_errors", "label": "Tool Errors", "type": "number"},
            {"id": "last_error", "label": "Last Error", "type": "string"},
        ]

    async def get_top_agents_usage(
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
        """Get top agents usage: invocations, cost, unique users, and most recent user per assistant.

        **Money is not in the aggregation.** Elasticsearch supplies invocations and users; the cost
        comes from the spend port, filled per assistant id by `_enrich_top_agents_usage_spending`
        after the query has run (AD-1, AD-14).
        """
        logger.info("Requesting top-agents-usage analytics")

        return await self._pipeline.execute_enriched_tabular_query(
            agg_builder=self._build_top_agents_usage_aggregation,
            result_parser=self._parse_top_agents_usage_result,
            columns=self._get_top_agents_usage_columns(),
            group_by_field=ASSISTANT_ID_KEYWORD_FIELD,
            # The enricher orders and totals the whole candidate set, which only this path fetches.
            row_enricher=partial(
                self._enrich_top_agents_usage_spending,
                client_source=client_source,
                report_filter=await report_filter_for(users, projects),
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

    def _build_top_agents_usage_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for top agents usage.

        Grouped on the assistant **id**, so a renamed assistant stays one bucket and two assistants
        sharing a name stay two; the name travels as a `top_metrics` label only.

        No money sub-aggregation: the figure comes from the spend port, so Elasticsearch supplies
        the candidate assistants and their counts only. The numbered keys therefore skip
        `2-bucket`, which held nothing but that money sum - the remaining numbers are left where
        they are so this builder and its parser still read alike.
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        sub_aggs = {
            "1-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CONVERSATION_ASSISTANT_USAGE.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                }
            },
            "3-bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {
                                "bool": {
                                    "should": [
                                        {
                                            "term": {
                                                METRIC_NAME_KEYWORD_FIELD: {
                                                    "value": MetricName.CONVERSATION_ASSISTANT_USAGE.value
                                                }
                                            }
                                        }
                                    ],
                                    "minimum_should_match": 1,
                                }
                            }
                        ]
                    }
                },
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
            # The most recent name seen in the bucket, sorted by time rather than reduced with a
            # `max`, because the question is what this assistant was last called and a lexical
            # maximum answers a different one. Read only when the assistant directory cannot be
            # reached at all (`_display_name`), so on the normal path it is fetched and discarded -
            # cheap, against a screen of bare ids the moment the database blinks.
            DISPLAY_NAME_AGG: {
                "top_metrics": {
                    "metrics": {"field": ASSISTANT_NAME_KEYWORD_FIELD},
                    "size": 1,
                    "sort": {TIMESTAMP_FIELD: "desc"},
                }
            },
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=ASSISTANT_ID_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"1-bucket": "desc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": query,
            "size": 0,
            "aggs": {"paginated_results": terms_agg},
        }

    def _parse_top_agents_usage_result(self, result: dict) -> list[dict]:
        """Parse result for top agents usage.

        These are **candidate** rows: `money_spent` is not written here, because it is not in the
        aggregation any more, and a placeholder for a figure that has not been read yet is exactly
        the fabricated zero this surface used to emit. `_enrich_top_agents_usage_spending` writes it.
        """
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        current_names, resolution_failed = self._resolve_assistant_names([bucket.get("key") for bucket in buckets])
        rows = []
        for bucket in buckets:
            assistant_id = bucket["key"]
            invocations = bucket.get("1-bucket", {}).get("doc_count", 0)
            unique_users = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0)

            recent_user = None
            top = bucket.get("4-bucket", {}).get("4-metric", {}).get("top", [])
            if top:
                recent_user = top[0].get("metrics", {}).get(USER_EMAIL_KEYWORD_FIELD)

            rows.append(
                {
                    TOP_AGENTS_ASSISTANT_ID_KEY: assistant_id,
                    "assistant_name": self._display_name(
                        assistant_id=assistant_id,
                        current_names=current_names,
                        resolution_failed=resolution_failed,
                        name_in_metrics=self._name_in_metrics(bucket),
                    ),
                    "invocations": invocations,
                    "unique_users": unique_users,
                    "recent_user": recent_user or "N/A",
                }
            )
        logger.debug(f"Parsed top-agents-usage result: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    @staticmethod
    def _name_in_metrics(bucket: dict) -> str | None:
        """The fallback label a bucket carries, or nothing when the sub-aggregation matched no document."""
        top = bucket.get(DISPLAY_NAME_AGG, {}).get("top", [])
        return top[0].get("metrics", {}).get(ASSISTANT_NAME_KEYWORD_FIELD) if top else None

    async def _enrich_top_agents_usage_spending(
        self,
        rows: list[dict],
        start_dt: datetime,
        end_dt: datetime,
        client_source: str | None = None,
        report_filter: ReportFilter | None = None,
    ) -> EnrichedRows:
        """Fill each assistant's spend from the spend port, then order and total the table by it.

        The port is keyed on the **assistant ids the aggregation grouped by**, never on the display
        names: a name is mutable, so keying on it would ask the ledger about a bucket the
        aggregation never formed (AD-29). `_parse_top_agents_usage_result` carries the id for
        exactly this read and it is consumed here, so the wire shape is today's plus the three
        annotation keys.

        **The candidate set is the pipeline's 10 000 buckets, ranked by invocations.** Money can
        only be ordered once it exists, which is after this point, so an assistant with heavy ledger
        spend but few invocations is cut by that cap before the money sort ever sees it - the same
        consequence every other enriched table carries.

        **No row is dropped for costing nothing**, unlike a spending table: a row here is an
        assistant's activity record, and an assistant that was invoked and cost nothing still
        belongs in a table of assistant usage.

        **The figure is narrowed by the same access boundary the rows were selected under**, for the
        reason `_enrich_assistants_spending` gives about the identical read one endpoint over: a
        shared assistant's platform-wide total footing a plain member's own invocations is the
        disclosure, and the scope is the pipeline's single derivation rather than a second one here.
        `report_filter` narrows it to the dashboard's filter for `_enrich_assistants_spending`'s reason.
        """
        period = report_period_or_none(start_dt, end_dt, client_source)
        assistant_ids = [row.pop(TOP_AGENTS_ASSISTANT_ID_KEY) for row in rows]
        # One round trip for the whole candidate set - every id shares the one requested window.
        spend = (
            await spend_service.aggregate_by_dimension(
                period,
                SpendDimension.ASSISTANT,
                assistant_ids,
                access=self._pipeline.access_scope,
                report_filter=report_filter,
            )
            if period is not None and assistant_ids
            else {}
        )
        logger.info(f"Top-agents-usage read the ledger for {len(set(assistant_ids))} assistant(s) over one window")

        rendered = [render_spend(spend.get(assistant_id)) for assistant_id in assistant_ids]
        apply_spend_cells(rows, rendered, TOP_AGENTS_MONEY_COLUMN)

        # The ledger figure decides the order and an absent one sorts last rather than as a zero: the
        # first key separates the two outright, so no absent row is ranked among the figures.
        ordered = sorted(
            zip(rows, rendered, strict=True),
            key=lambda pair: (pair[1].figure is not None, pair[1].figure or 0.0),
            reverse=True,
        )
        return EnrichedRows(
            rows=[row for row, _ in ordered],
            columns=(TOP_AGENTS_MONEY_COLUMN,),
            totals=spend_footer(TOP_AGENTS_MONEY_COLUMN, [figure for _, figure in ordered]),
        )

    def _get_top_agents_usage_columns(self) -> list[dict]:
        """Get column definitions for top agents usage."""
        return [
            {"id": "assistant_name", "label": "Assistant Name", "type": "string"},
            {"id": "invocations", "label": "Invocations", "type": "number"},
            {"id": TOP_AGENTS_MONEY_COLUMN, "label": "Money Spent ($)", "type": "number", "format": "currency"},
            {"id": "unique_users", "label": UNIQUE_USERS_LABEL, "type": "number"},
            {"id": "recent_user", "label": "Recent User", "type": "string"},
        ]

    async def get_published_to_marketplace(
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
        """Get published to marketplace analytics: assistants published per user."""
        logger.info("Requesting published-to-marketplace analytics")

        result = await self._pipeline.execute_tabular_query(
            agg_builder=self._build_published_to_marketplace_aggregation,
            result_parser=self._parse_published_to_marketplace_result,
            columns=self._get_published_to_marketplace_columns(),
            group_by_field=USER_ID_KEYWORD_FIELD,
            filters=AnalyticsQueryFilters(
                metric_filters=[MetricName.PUBLISH_TO_MARKETPLACE.value],
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
        await UserIdentityResolver.resolve_rows(result.get("data", {}).get("rows", []), "user_email")
        return result

    def _build_published_to_marketplace_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for published-to-marketplace analytics."""
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        def _marketplace_filter() -> dict:
            return {
                "bool": {
                    "filter": [
                        {
                            "bool": {
                                "should": [
                                    {
                                        "term": {
                                            METRIC_NAME_KEYWORD_FIELD: {
                                                "value": MetricName.PUBLISH_TO_MARKETPLACE.value
                                            }
                                        }
                                    }
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    ]
                }
            }

        sub_aggs = {
            "3-bucket": {
                "filter": _marketplace_filter(),
            },
            "1-bucket": {
                "filter": _marketplace_filter(),
                "aggs": {
                    "1-metric": {
                        "top_metrics": {
                            "metrics": {"field": ASSISTANT_NAME_KEYWORD_FIELD},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
            "2-bucket": {
                "filter": _marketplace_filter(),
                "aggs": {
                    "2-metric": {
                        "top_metrics": {
                            "metrics": {"field": "attributes.project.keyword"},
                            "size": 1,
                            "sort": {TIMESTAMP_FIELD: "desc"},
                        }
                    }
                },
            },
        }

        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=USER_ID_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"3-bucket": "desc"},
            sub_aggs=sub_aggs,
        )

        return {
            "query": {
                "bool": {
                    "must": [query],
                    "must_not": [{"terms": {USER_ID_KEYWORD_FIELD: PLACEHOLDER_USER_IDS}}],
                }
            },
            "size": 0,
            "aggs": {"paginated_results": terms_agg},
        }

    def _parse_published_to_marketplace_result(self, result: dict) -> list[dict]:
        """Parse result for published-to-marketplace analytics."""
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []
        for bucket in buckets:
            user_email = bucket["key"]
            if not user_email:
                continue

            total_count = bucket.get("3-bucket", {}).get("doc_count", 0)
            if total_count == 0:
                continue

            last_assistant = None
            top1 = bucket.get("1-bucket", {}).get("1-metric", {}).get("top", [])
            if top1:
                last_assistant = top1[0].get("metrics", {}).get(ASSISTANT_NAME_KEYWORD_FIELD)

            project = None
            top2 = bucket.get("2-bucket", {}).get("2-metric", {}).get("top", [])
            if top2:
                project = top2[0].get("metrics", {}).get("attributes.project.keyword")

            rows.append(
                {
                    "user_email": user_email,
                    "last_assistant_name": last_assistant or "N/A",
                    "project": project or "N/A",
                    "total_count": total_count,
                }
            )
        logger.debug(f"Parsed published-to-marketplace result: total_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_published_to_marketplace_columns(self) -> list[dict]:
        """Get column definitions for published to marketplace."""
        return [
            {"id": "user_email", "label": "User Email", "type": "string"},
            {"id": "last_assistant_name", "label": "Last Assistant Name", "type": "string"},
            {"id": "project", "label": "Project", "type": "string"},
            {"id": "total_count", "label": "Total Published", "type": "number"},
        ]
