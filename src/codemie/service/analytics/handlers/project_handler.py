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

"""Handler for project analytics."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from codemie.clients.postgres import get_async_session
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.repository.project_budget_repository import project_budget_assignment_repository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD, PROJECT_KEYWORD_FIELD
from codemie.service.analytics.handlers.money_coverage import (
    money_coverage_aggs,
    pre_floor_boundary,
    read_money_coverage,
    split_at_floor,
)
from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.query_pipeline import (
    CANDIDATE_ORDER,
    AnalyticsQueryFilters,
    AnalyticsQueryPipeline,
    EnrichedRows,
    enriched_spend_rows,
)
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.ledger import ProjectHolder, ReportFilter
from codemie.service.spend.ledger_registry import ledger_coverage
from codemie.service.spend.rendering import (
    RenderedSpend,
    combine_rendered_spend,
    render_spend,
    report_period_or_none,
)
from codemie.service.spend.spend_service import spend_service

logger = logging.getLogger(__name__)

# Elasticsearch field constants
MONEY_SPENT_FIELD = "attributes.money_spent"
INPUT_TOKENS_FIELD = "attributes.input_tokens"
OUTPUT_TOKENS_FIELD = "attributes.output_tokens"
USER_EMAIL_KEYWORD_FIELD = "attributes.user_email.keyword"
CLI_REQUEST_FIELD = "attributes.cli_request"

#: The emission-time field the projects-spending query already filters on, and the field the pre-floor
#: money sub-aggregation is bounded at `F` by. One spelling, because a money bucket bounded on a field
#: the surrounding query does not filter is scoped to a window nobody asked about.
TIMESTAMP_FIELD = "@timestamp"

#: The bucket key `_build_projects_spending_aggregation`'s script emits for a document that names
#: neither a project nor a user. A document with no project of its own is filed under its
#: `attributes.user_email`, and under this sentinel when it has neither - and `get_project_for_metric()`
#: returns `'-'` in ordinary cases, so both fallbacks are a routine part of this table rather than an
#: edge of it. Named rather than spelled inline so the script that emits it and the test that pins it
#: cannot drift.
UNATTRIBUTED_PROJECT_KEY = "unknown"

#: The pre-floor money sub-aggregation, its sum, and the row key that figure travels to the enricher
#: on. The row key is underscore-prefixed and **popped** before the row is published: it carries a
#: `RenderedSpend`, which is an internal shape and not a wire value.
PRE_FLOOR_MONEY_BUCKET = "pre_floor_money"
PRE_FLOOR_MONEY_SUM = "sum"
PRE_FLOOR_MONEY_KEY = "_pre_floor_money"


def _budget_category(category: str) -> BudgetCategory | None:
    """An assignment's category as the enum the port requires, or None if it names no category.

    The column is a free-form varchar with no database-level vocabulary, so a value outside the enum
    is reachable and must not fail the whole table.
    """
    try:
        return BudgetCategory(category)
    except ValueError:
        logger.warning(f"Assigned budget category {category!r} names no budget category, so no figure is read for it")
        return None


def _pre_floor_spend(money_bucket: dict) -> RenderedSpend:
    """One candidate bucket's pre-floor money, kept distinguishable from a project that spent nothing.

    `read_money_coverage` rounds to two places so that the one figure a currency card shows is not
    rounded in three handlers three ways - a **display** decision, and this is the one call site where
    it lands in the middle of a **filtering** one. A project whose entire pre-floor spend is under half
    a cent rounds to an exact `0.0`, and `enriched_spend_rows` drops an exact zero, so the row leaves
    the table altogether - a rounded value taken for a measured one, and only below `F`. Elasticsearch's
    own `filter_zero_cost` selector, the rule that drop reproduces, tested `params.cost > 0` on the
    **unrounded** sum and kept exactly this row: rounding it away prints one row fewer than production.

    So the unrounded sum is kept in the single case where rounding would invent a measured zero, and
    nowhere else. Three states stay three: a bucket that settled nothing is absent, a bucket that
    measured `0.0` is a measured zero and still leaves the table, and every other figure keeps the
    rounded one it had.

    **Fixed here rather than in `read_money_coverage`.** The other surfaces reading that helper render
    cards, not rows: none of them can lose a row to this, and moving the rounding would change what
    every one of them displays for a defect that is not theirs.
    """
    rendered = read_money_coverage(money_bucket, PRE_FLOOR_MONEY_SUM)
    measured = money_bucket.get(PRE_FLOOR_MONEY_SUM, {}).get("value")
    # The one case: a sum that exists and is not zero, rendered as a zero. An absent sum renders no
    # figure, and a genuine zero was not produced by rounding - both keep what they have.
    rounded_away = bool(measured) and rendered.figure is not None and not rendered.figure
    if not rounded_away:
        return rendered
    return rendered.with_figure(float(measured))


class ProjectHandler:
    """Handler for project analytics."""

    def __init__(self, user: User, repository: MetricsElasticRepository):
        """Initialize project handler."""
        self._pipeline = AnalyticsQueryPipeline(user, repository)
        self.repository = repository

    async def get_projects_spending(
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
        """Get projects spending analytics.

        Returns project spending data grouped by project with total cost across all resource types
        (assistants, workflows, datasources, CLI).

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page
            client_source: Filter by client source (optional)

        Returns:
            Tabular response with columns: project_name, total_cost_usd
        """
        logger.info("Requesting projects-spending analytics")

        # Read **once** for the whole request and handed to both halves of the split: the
        # aggregation that scopes the pre-floor money and the enricher that decides which legs run.
        # See `_pre_floor_boundary` - two reads a request apart is how one table's money is scoped to
        # one side of a floor and footed against the other.
        pre_floor = self._pre_floor_boundary()

        result = await self._pipeline.execute_enriched_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_projects_spending_aggregation(
                query, fetch_size, pre_floor
            ),
            result_parser=self._parse_projects_spending_rows_raw,
            columns=self._get_projects_spending_columns(),
            group_by_field=PROJECT_KEYWORD_FIELD,
            # Every candidate bucket in one query: the enricher orders and counts the whole set,
            # because the figure it sorts on is not in Elasticsearch.
            row_enricher=lambda rows, start_dt, end_dt: self._enrich_projects_spending(
                rows, start_dt, end_dt, pre_floor, client_source, users=users, projects=projects
            ),
            filters=AnalyticsQueryFilters(
                metric_filters=MetricName.to_list_from_group(MetricName.SPENDING_METRICS),
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
        return result

    async def _enrich_projects_spending(
        self,
        rows: list[dict],
        start_dt: datetime,
        end_dt: datetime,
        pre_floor: datetime | None,
        client_source: str | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> EnrichedRows:
        """Fill each project's cost, then order and total the table by it.

        **Narrowed by the caller's boundary, because the figure answers a project's *whole* total.**
        The rows come from a query `SecureQueryBuilder` had already narrowed to what this caller may
        see, so an unnarrowed figure footed a plain member's own row set with every member's spend in
        that project. Narrowed, a plain member sees their own share and a project admin the project
        total - which is exactly what the two clauses of the boundary say, and what this table showed
        while its money still came from the Elasticsearch aggregation. The at-or-above-`F` read takes
        that boundary from the pipeline, the single derivation point from a `User`; the below-`F`
        figure needs no boundary threaded to it at all, because it is measured *inside* the very
        query `SecureQueryBuilder` narrowed.

        **The pre-floor figure arrives on the row rather than being fetched.** It was summed by the
        candidate aggregation itself, so it is popped here rather than read: every key left on a row
        is published, and this one holds a `RenderedSpend`. A row built without one is **absent**, not
        zero - a parser and a builder that drifted apart would empty the pre-floor column visibly
        instead of filling it with fabricated zeroes.

        **Which read produces that figure depends on where the window sits relative to the coverage
        floor**, and `_money_by_project` owns the whole of that decision: the row-building here is the
        same whichever shape answered, because the shape never reaches the wire.

        **`users` and `projects` are the dashboard's filter, and the at-or-above-`F` read carries it
        for the reason it carries the boundary.** The rows came from a query that filter had already
        narrowed, so a project row read without it showed the whole project's spend under a filter
        naming one of its users. It is built once, here, because it costs an identity lookup, and
        only when there is a period and a row to read for.
        """
        project_names = [row["project_name"] for row in rows]
        measured = [row.pop(PRE_FLOOR_MONEY_KEY, render_spend(None)) for row in rows]
        period = report_period_or_none(start_dt, end_dt, client_source)
        # Not built without a period or a row, because no read could use it. A window wholly below
        # the floor still pays the lookup: which legs run is decided later, in `_money_by_project`.
        report_filter = await report_filter_for(users, projects) if period and rows else None
        money = await self._money_by_project(project_names, measured, period, pre_floor, report_filter)
        return enriched_spend_rows(rows, money, "total_cost_usd")

    @staticmethod
    def _pre_floor_boundary() -> datetime | None:
        """The instant below which this table serves its own money, or None if it never does.

        The registered coverage is read **here**, at the one seam a test publishes a floor through, and
        the decision is `money_coverage.pre_floor_boundary`'s - shared with every other surface that
        splits at `F`, so two tables cannot read one flag two ways. Read once per request and handed to
        both halves of the split (see `get_projects_spending`).
        """
        return pre_floor_boundary(ledger_coverage())

    async def _money_by_project(
        self,
        projects: list[str],
        measured: list[RenderedSpend],
        period: TimeRange | None,
        floor: datetime | None,
        report_filter: ReportFilter | None,
    ) -> list[RenderedSpend]:
        """Each candidate row's total, in whichever shape its side of the coverage floor can answer.

        **The read SHAPE changes at `F` because the dimension the above-floor read groups on does not
        exist below it.** At or above `F` a project's money is the sum over the budget *categories*
        its assigned budgets name, and `litellm_spend_logs` carries that category on every row. Below
        `F` there is no such row, and no emission has ever written a budget category, so a
        category-keyed holder is answered with no figure at all - correctly, because the dimension the
        caller asked about was never recorded. Asking the same question in the same shape on both
        sides therefore does not merely degrade the pre-October table, it empties it: every money cell
        absent for every window the platform had before the rollout, which is the exact regression the
        cutover exists to prevent.

        **So below `F` the money is the candidate aggregation's own, and no second read is made at
        all.** The aggregation that produced these rows already sums `attributes.money_spent` per
        bucket, and that sum is by construction the number this table printed before the cutover: it
        is computed inside the one query that carries the normalised bucket keys, the `users` request
        filter, the `SecureQueryBuilder` boundary and the spending metric set. A *parallel* dimension
        read over `attributes.project.keyword` had to re-derive all four and got three of them wrong -
        it matched nothing for a bucket key the normalising script invented and published real money
        as a measured `$0.00`, it never received the `users` filter and footed a user-filtered row with
        the project's whole spend, and it twice named the wrong metric set. None of those is reachable
        from a figure the row's own bucket measured, which is why this method now takes that figure as
        an argument rather than fetching one.

        **A crossing window still splits, and the two legs may not overlap.** The aggregation covers
        the *whole* requested window, so its money is scoped to `[start, F)` at the source - a range
        filter on `@timestamp` bounded at `F`, built by `_pre_floor_money_aggs` - and `[F, end)` goes
        to the holder read. Handing either side the whole crossing period is the trap: the above-floor
        money would be counted twice, once through each shape, and the row would foot at nearly double
        while every annotation beside it still read clean.

        **The two legs tile the requested period as *instants*, and they do not tile the rows.** This
        surface inherits `cutover.py`'s two-clock exposure entire, because it is the same two stores
        cut on the same instant: the pre-floor leg filters Elasticsearch on `@timestamp`, when a metric
        document was *emitted*, while the at-or-above leg filters `litellm_spend_logs` on `start_time`,
        when the call *started*. A call whose two stamps straddle `F` is claimed by **neither** leg or
        by **both**, and either way the row foots short or doubled and **reads exact** - no flag, no
        portion, nothing separating it from a window in which that money was never spent.
        `cutover.py`'s module docstring works the case through with dates, bounds the band at the
        emission lag `L` and names the three mitigations, none of which is a reader's to choose;
        nothing here can repair it, because a metric document carries no `start_time` for any query of
        this table's to partition on. This surface's exposure is pinned by tests of its own rather
        than fixed.

        **One seamless total, and no second wire field.** The legs combine through
        `combine_rendered_spend`, exactly as a project's categories do, so a window spanning the floor
        renders as one figure that still carries every qualification either leg produced: a leg that
        found no figure leaves the row labelled rather than quietly shortening it.

        **Absent stays absent on both sides.** A project with no assigned budget names no holder and
        is absent above `F`; a project whose pre-floor bucket settled no money at all is absent below
        it, which is what `read_money_coverage` returns rather than the `.get(..., 0.0)` the bucket
        read used to default to. A zero is a positive claim that nothing was spent, and it is the one
        figure this package never fabricates.

        The comparisons in `split_at_floor` mirror `CutoverSpendLedger._split` term for term, and
        must: a period this method calls crossing but that ledger calls whole would be read in two
        shapes and answered by one engine.
        """
        if not projects or period is None:
            # There is no window to read, so every figure is absent rather than a fabricated zero.
            return [render_spend(None) for _ in projects]

        below, above = split_at_floor(period, floor)
        legs: list[list[RenderedSpend]] = [[] for _ in projects]

        if below is not None:
            for parts, pre_floor_money in zip(legs, measured, strict=True):
                parts.append(pre_floor_money)

        if above is not None:
            above_money = await self._budget_category_money(projects, above, report_filter)
            for parts, project in zip(legs, projects, strict=True):
                parts.append(above_money[project])

        # `or [render_spend(None)]` is unreachable while a split always yields a leg, and is kept for
        # the same reason `_project_rendered_spend` keeps it: combining nothing produces a figure of
        # None with no flag on it, which is an absence that says it is not one.
        return [combine_rendered_spend(parts or [render_spend(None)]) for parts in legs]

    async def _budget_category_money(
        self, projects: list[str], period: TimeRange, report_filter: ReportFilter | None
    ) -> dict[str, RenderedSpend]:
        """Each project's total over the categories its assigned budgets name - the at-or-above-`F` shape.

        This is the read this table has made since its money moved off the Elasticsearch aggregation,
        moved here unchanged so that a window wholly at or above the floor is answered exactly as it
        was before the cutover existed: one `ProjectHolder` per distinct assigned category, one round
        trip for the whole candidate set, and the caller's own boundary applied to all of it.

        One round trip because all holders share the one window, one boundary and one report filter -
        both belong to the request rather than to a holder, so there is nothing per-holder about them to
        get wrong.
        """
        categories = await self._assigned_budget_categories(projects)
        holders = [
            ProjectHolder(project=project, category=category)
            for project, project_categories in categories.items()
            for category in project_categories
            if category is not None
        ]
        spend = (
            await spend_service.consumed_for_holders(
                holders, period, access=self._pipeline.access_scope, report_filter=report_filter
            )
            if holders
            else {}
        )
        logger.info(
            f"Projects-spending read the ledger for {len(holders)} holder(s) over {len(projects)} candidate row(s)"
        )
        return {
            project: self._project_rendered_spend(project, categories.get(project, []), spend) for project in projects
        }

    @staticmethod
    def _project_rendered_spend(
        project: str,
        categories: list[BudgetCategory | None],
        spend: dict[ProjectHolder, SpendResult],
    ) -> RenderedSpend:
        """One project's total over the categories its assigned budgets name.

        There is no category-free project holder, so a project with no assigned budget names no
        holder at all: the port is never asked about it and its figure is absent, which is a
        different fact from having spent nothing and is labelled as such.
        """
        results = [
            None if category is None else spend.get(ProjectHolder(project=project, category=category))
            for category in categories
        ]
        return combine_rendered_spend([render_spend(result) for result in results] or [render_spend(None)])

    @staticmethod
    async def _assigned_budget_categories(project_names: list[str]) -> dict[str, list[BudgetCategory | None]]:
        """Each project's distinct assigned budget categories - the holders its total is summed over.

        Distinct, because two budgets sharing a category name one holder, and summing that holder
        twice would double the project's spend.

        A category outside the enum is kept as ``None`` rather than dropped: it identifies no holder,
        so it contributes an absent figure and makes the project's total say it is short. A budget
        silently left out would shorten the total instead, and a short total looks like a whole one.
        """
        if not project_names:
            return {}

        async with get_async_session() as session:
            rows_by_project = await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
                session, project_names
            )

        return {
            project: list(dict.fromkeys(_budget_category(row.budget_category) for row in budget_rows))
            for project, budget_rows in rows_by_project.items()
        }

    def _build_projects_spending_aggregation(self, query: dict, fetch_size: int, pre_floor: datetime | None) -> dict:
        """Build terms aggregation for projects spending with fetch-and-slice.

        The candidate project names, and - where a coverage floor is in play - the pre-floor money for
        each of them, measured **here** rather than fetched again. No zero-cost selector and no money
        order: the figure the table is sorted and filtered on is only fully known once the at-or-above
        leg has answered too, so both move to Python.
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Normalise empty project name to "unknown" at aggregation time so that
        # pagination totals and sort order remain accurate.
        #
        # The key this emits is therefore a *normalised* project and not always an
        # `attributes.project` value - and the money sub-aggregation below is grouped by this same
        # key, which is exactly why it can be trusted where a separate read filtering the raw
        # attribute could not: a key invented here matches no `attributes.project.keyword` term.
        normalize_script = (
            f"def v = doc['{PROJECT_KEYWORD_FIELD}'].size() == 0 ? '' "
            f": doc['{PROJECT_KEYWORD_FIELD}'].value; "
            f"if (v != '' && v != '-') return v; "
            f"def email = doc['{USER_EMAIL_KEYWORD_FIELD}'].size() == 0 ? '' "
            f": doc['{USER_EMAIL_KEYWORD_FIELD}'].value; "
            f"return (email != '' && email != '-') ? email : '{UNATTRIBUTED_PROJECT_KEY}';"
        )

        sub_aggs = self._pre_floor_money_aggs(pre_floor)

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=PROJECT_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order=CANDIDATE_ORDER,
            sub_aggs=sub_aggs,
            script=normalize_script,
        )
        if not sub_aggs:
            # No floor, so nothing below one to measure and nothing to sub-aggregate: the empty
            # container is not sent at all, and this query is what it was before the cutover existed.
            del terms_agg["aggs"]

        # Construct full aggregation body
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": terms_agg,
            },
        }

        return agg_body

    @staticmethod
    def _pre_floor_money_aggs(pre_floor: datetime | None) -> dict:
        """The per-project money the below-`F` leg is served from, scoped to `[start, F)` at the source.

        **Bounded at `F` here rather than trimmed afterwards.** The surrounding query covers the whole
        requested window, so a bucket money sum taken over all of it would include the at-or-above
        part - which the holder read also reports - and a crossing row would foot at nearly double
        with nothing on it saying so. The range clause makes the two legs disjoint before either is
        read, and it is `lt` rather than `lte` because every interval on these paths is half-open:
        `F` belongs to the at-or-above leg, and adjacent half-open ranges tile exactly.

        **Asked on both sides of the floor, because this is built before the window is parsed.** The
        pipeline resolves `time_period` into a window only after the aggregation body exists, so a
        query whose window lies wholly at or above `F` still carries this bucket - where the range
        matches nothing and the result is never read. That is one filter sub-aggregation of wasted
        work, against the alternative of re-deriving the window from the query body to save it.

        **Narrowed to the documents a cost was expected on, because the metric filter does not do
        that job here.** It is tempting to read the surrounding `SPENDING_METRICS` filter as *every
        document in this bucket is one a money attribute is expected on* - the premise
        `default_ledger._money_aggs` really does satisfy, and the reason it does not narrow. It is
        false on this scope, because a metric **name** is not an emitter and one of these names has
        two. `codemie_tools_usage_tokens` is written by
        `emit_llm_token_metric` for a tool call that billed a completion - with that call's token
        counts, and its money once the cost settles - and by `AgentMonitoringService.send_tool_metrics`
        for *every successful tool invocation*, through `send_count_metric`, with no token counts and
        no money at all, because no model was called. Counted as unsettled, that second emitter's
        documents are non-zero in nearly every project and every window, so every pre-floor row would
        be published `partial`: an "(at least; ...)" on figures that are exactly right, which this
        table never showed before the cutover and must not start showing now. An alarm that is always
        on is the same as no alarm.

        **The discriminator is the token counts, not the metric name.** Every emitter that may write
        money writes `attributes.input_tokens` unconditionally and `attributes.money_spent` only once
        the cost has settled - the same gate, spelled the same way, in each of them - so within this
        bucket a document carrying token counts and no money is exactly a call whose cost is genuinely
        outstanding, and a document carrying neither recorded no model call to have a cost at all.
        Narrowing by metric name instead - excluding `codemie_tools_usage_tokens` from the count -
        would take the first emitter's real holes out along with the second's phantom ones, and leave
        a short figure reading exact. The point of the count is accuracy, not silence.
        """
        if pre_floor is None:
            return {}
        return {
            PRE_FLOOR_MONEY_BUCKET: {
                "filter": {
                    "range": {TIMESTAMP_FIELD: {"lt": pre_floor.isoformat(), "format": "strict_date_optional_time"}}
                },
                "aggs": {
                    PRE_FLOOR_MONEY_SUM: {"sum": {"field": MONEY_SPENT_FIELD}},
                    **money_coverage_aggs(MONEY_SPENT_FIELD, cost_bearing={"exists": {"field": INPUT_TOKENS_FIELD}}),
                },
            }
        }

    def _parse_projects_spending_result(self, result: dict) -> list[dict]:
        """Parse result for projects spending.

        Args:
            result: Elasticsearch aggregation result
        """
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []

        for bucket in buckets:
            rows.append(
                {
                    "project_name": bucket["key"],
                    # Filled from the spend port after parsing: Elasticsearch carries no money.
                    "total_cost_usd": None,
                }
            )

        logger.debug(f"Parsed projects-spending result: total_project_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _parse_projects_spending_rows_raw(self, result: dict) -> list[dict]:
        """Parse ES buckets into candidate rows, each carrying the pre-floor money its own bucket measured.

        **Read through `read_money_coverage`, never `.get("value", 0.0)`.** A `sum` over
        `attributes.money_spent` silently skips every cost-bearing document whose cost was never
        settled, so what it returns is the settled remainder of the window; defaulted to `0.0` it
        turns *nothing settled* into the claim that nothing was spent - and `enriched_spend_rows` then
        drops the row on the strength of that zero, taking the money and the annotation off the table
        together. The same helper qualifies the money on every other Elasticsearch-sourced surface, so
        an absence has one spelling here rather than one per handler. `_pre_floor_spend` wraps that
        read for the one case where the helper's own display rounding recreates the same dropped row
        out of a sub-cent sum.

        The money cell itself stays None: which side of the floor the window falls on decides what
        fills it, and a bucket does not know that. The enricher pops the carrier key before the row is
        published.
        """
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])
        rows = []
        for bucket in buckets:
            rows.append(
                {
                    "project_name": bucket["key"],
                    # Filled by the enricher, from this row's own pre-floor money below `F` and from
                    # the ledger at or above it.
                    "total_cost_usd": None,
                    PRE_FLOOR_MONEY_KEY: _pre_floor_spend(bucket.get(PRE_FLOOR_MONEY_BUCKET, {})),
                }
            )
        logger.debug(f"Parsed projects-spending rows (raw): {len(rows)} rows")
        return rows

    def _get_projects_spending_columns(self) -> list[dict]:
        """Get column definitions for projects spending."""
        return [
            {"id": "project_name", "label": "Project", "type": "string"},
            {"id": "total_cost_usd", "label": "Total Cost ($)", "type": "number", "format": "currency"},
        ]

    async def get_projects_activity(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get projects activity analytics with comprehensive metrics and cost/token breakdown.

        Returns project activity including:
        - Assistant cost (money spent on assistant usage)
        - Workflow cost (money spent on workflow execution)
        - Datasource cost (money spent on datasource processing)
        - CLI cost (money spent on CLI usage)
        - Total cost (sum of all costs)
        - Assistant tokens (input + output tokens from assistant usage)
        - Workflow tokens (input + output tokens from workflow execution)
        - Datasource tokens (input + output tokens from datasource processing)
        - Unique users count
        - Total tokens (sum of all tokens from web + CLI)

        Groups by project.keyword and orders by total cost descending.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            Tabular response with columns: project_name, assistant_cost, workflow_cost,
            datasource_cost, cli_cost, total_cost, assistant_tokens, workflow_tokens, datasource_tokens,
            unique_users, total_tokens
        """
        logger.info("Requesting projects-activity analytics")

        return await self._pipeline.execute_tabular_query(
            agg_builder=lambda query, fetch_size: self._build_projects_activity_aggregation(query, fetch_size),
            result_parser=self._parse_projects_activity_result,
            columns=self._get_projects_activity_columns(),
            group_by_field=PROJECT_KEYWORD_FIELD,
            filters=AnalyticsQueryFilters(
                metric_filters=MetricName.to_list_from_group(MetricName.ACTIVITY_METRICS),
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
                page=page,
                per_page=per_page,
            ),
            use_bucket_selector=True,
        )

    def _build_projects_activity_aggregation(self, query: dict, fetch_size: int) -> dict:
        """Build terms aggregation for projects activity with fetch-and-slice.

        Includes sub-aggregations for:
        - Unique users (all metrics)
        - Total money spent (all metrics)
        - Separate costs by metric type (assistant, workflow, datasource)
        - Separate tokens by metric type (assistant, workflow, datasource)
        - Total input tokens (web + CLI fields)
        - Total output tokens (web + CLI fields)
        """
        from codemie.service.analytics.aggregation_builder import AggregationBuilder

        # Define sub-aggregations (metrics)
        sub_aggs = {
            # Unique users - filtered to exclude empty/missing user_email values
            # (cardinality alone counts empty strings, but terms aggregation in User Activity skips them)
            "2": {
                "filter": {
                    "bool": {
                        "must": [{"exists": {"field": USER_EMAIL_KEYWORD_FIELD}}],
                        "must_not": [{"term": {USER_EMAIL_KEYWORD_FIELD: ""}}],
                    }
                },
                "aggs": {
                    "unique_users": {
                        "cardinality": {
                            "field": USER_EMAIL_KEYWORD_FIELD,
                        }
                    }
                },
            },
            # Assistant costs
            "assistant_cost_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CONVERSATION_ASSISTANT_USAGE.value}},
                "aggs": {"assistant_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # Workflow costs
            "workflow_cost_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                "aggs": {"workflow_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # Datasource costs
            "datasource_cost_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                "aggs": {"datasource_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # CLI costs (use NEW LiteLLM proxy metric)
            "cli_cost_bucket": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            {"term": {CLI_REQUEST_FIELD: True}},
                        ]
                    }
                },
                "aggs": {"cli_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
            },
            # Assistant tokens (input + output)
            "assistant_tokens_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CONVERSATION_ASSISTANT_USAGE.value}},
                "aggs": {
                    "input": {"sum": {"field": INPUT_TOKENS_FIELD}},
                    "output": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                },
            },
            # Workflow tokens (input + output)
            "workflow_tokens_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                "aggs": {
                    "input": {"sum": {"field": INPUT_TOKENS_FIELD}},
                    "output": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                },
            },
            # Datasource tokens (input + output)
            "datasource_tokens_bucket": {
                "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                "aggs": {
                    "input": {"sum": {"field": INPUT_TOKENS_FIELD}},
                    "output": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                },
            },
            # Total cost aggregation (sum of Assistant + Workflow + Datasource + CLI)
            # Note: CLI uses NEW LiteLLM proxy metric for accurate server-side tracking
            "1-bucket": {
                "filter": {
                    "bool": {
                        "should": [
                            # Assistant costs
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CONVERSATION_ASSISTANT_USAGE.value}},
                            # Workflow costs
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.WORKFLOW_EXECUTION_TOTAL.value}},
                            # Datasource costs
                            {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.DATASOURCE_TOKENS_USAGE.value}},
                            # CLI costs (NEW metric with cli_request filter)
                            {
                                "bool": {
                                    "filter": [
                                        {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                                        {"term": {CLI_REQUEST_FIELD: True}},
                                    ]
                                }
                            },
                        ],
                        "minimum_should_match": 1,
                    }
                },
                "aggs": {
                    "1-metric": {
                        "sum": {"field": MONEY_SPENT_FIELD},
                    },
                },
            },
            **AggregationBuilder.build_zero_token_filter_aggs(),
        }

        # Build terms aggregation using helper
        terms_agg = AggregationBuilder.build_terms_agg(
            group_by_field=PROJECT_KEYWORD_FIELD,
            fetch_size=fetch_size,
            order={"1-bucket>1-metric": "desc"},
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

    def _parse_projects_activity_result(self, result: dict) -> list[dict]:
        """Parse result for projects activity with metric breakdown (combines web + CLI tokens)."""
        # Extract buckets from paginated_results (already sliced by pipeline)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            project_name = bucket["key"]

            unique_users = bucket.get("2", {}).get("unique_users", {}).get("value", 0)
            total_cost = bucket.get("1-bucket", {}).get("1-metric", {}).get("value", 0.0)

            # Extract separate costs for each metric type (calculated in ES)
            assistant_cost = bucket.get("assistant_cost_bucket", {}).get("assistant_cost", {}).get("value", 0) or 0
            workflow_cost = bucket.get("workflow_cost_bucket", {}).get("workflow_cost", {}).get("value", 0) or 0
            datasource_cost = bucket.get("datasource_cost_bucket", {}).get("datasource_cost", {}).get("value", 0) or 0
            cli_cost = bucket.get("cli_cost_bucket", {}).get("cli_cost", {}).get("value", 0) or 0

            # Calculate separate tokens for each metric type (sum input + output)
            assistant_input = bucket.get("assistant_tokens_bucket", {}).get("input", {}).get("value", 0) or 0
            assistant_output = bucket.get("assistant_tokens_bucket", {}).get("output", {}).get("value", 0) or 0
            assistant_tokens = int(assistant_input + assistant_output)

            workflow_input = bucket.get("workflow_tokens_bucket", {}).get("input", {}).get("value", 0) or 0
            workflow_output = bucket.get("workflow_tokens_bucket", {}).get("output", {}).get("value", 0) or 0
            workflow_tokens = int(workflow_input + workflow_output)

            datasource_input = bucket.get("datasource_tokens_bucket", {}).get("input", {}).get("value", 0) or 0
            datasource_output = bucket.get("datasource_tokens_bucket", {}).get("output", {}).get("value", 0) or 0
            datasource_tokens = int(datasource_input + datasource_output)

            # LLM tokens (web + CLI, excludes embeddings)
            web_input = bucket.get("3-bucket", {}).get("3-metric", {}).get("value", 0) or 0
            web_output = bucket.get("4-bucket", {}).get("4-metric", {}).get("value", 0) or 0
            cli_input = bucket.get("5-bucket", {}).get("5-metric", {}).get("value", 0) or 0
            cli_output = bucket.get("6-bucket", {}).get("6-metric", {}).get("value", 0) or 0
            llm_tokens = int(web_input + web_output + cli_input + cli_output)

            rows.append(
                {
                    "project_name": project_name,
                    "unique_users": int(unique_users),
                    "assistant_cost": round(assistant_cost, 2),
                    "workflow_cost": round(workflow_cost, 2),
                    "datasource_cost": round(datasource_cost, 2),
                    "cli_cost": round(cli_cost, 2),
                    "total_cost": round(total_cost, 2),
                    "assistant_tokens": assistant_tokens,
                    "workflow_tokens": workflow_tokens,
                    "datasource_tokens": datasource_tokens,
                    "llm_tokens": llm_tokens,
                }
            )

        logger.debug(f"Parsed projects-activity result: total_project_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_projects_activity_columns(self) -> list[dict]:
        """Get column definitions for projects activity with metric breakdown."""
        return [
            {"id": "project_name", "label": "Project", "type": "string"},
            {"id": "unique_users", "label": "Unique Users", "type": "number"},
            {"id": "assistant_cost", "label": "Assistant Cost ($)", "type": "number", "format": "currency"},
            {"id": "workflow_cost", "label": "Workflow Cost ($)", "type": "number", "format": "currency"},
            {"id": "datasource_cost", "label": "Datasource Cost ($)", "type": "number", "format": "currency"},
            {"id": "cli_cost", "label": "CLI Cost ($)", "type": "number", "format": "currency"},
            {"id": "total_cost", "label": "Total Cost ($)", "type": "number", "format": "currency"},
            {"id": "assistant_tokens", "label": "Assistant Tokens", "type": "number"},
            {"id": "workflow_tokens", "label": "Workflow Tokens", "type": "number"},
            {"id": "datasource_tokens", "label": "Datasource Tokens", "type": "number"},
            {"id": "llm_tokens", "label": "LLM Tokens", "type": "number"},
        ]

    async def get_projects_unique_daily(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get unique projects per day analytics.

        Returns time-series data showing the number of unique active projects for each day,
        based on conversation_assistant_usage metric. Returns all date records without pagination.

        Args:
            time_period: Predefined time range (e.g., 'last_30_days')
            start_date: Custom range start
            end_date: Custom range end
            users: Filter by specific users (optional)
            projects: Filter by specific projects (optional)

        Returns:
            Tabular response with columns: date, unique_projects (all records)
        """
        logger.info("Requesting projects-unique-daily analytics")

        # Type-annotated aggregation builder
        def build_agg(query: dict, fetch_size: int) -> dict:
            return self._build_projects_unique_daily_aggregation(query)

        return await self._pipeline.execute_tabular_query(
            agg_builder=build_agg,
            result_parser=self._parse_projects_unique_daily_result,
            columns=self._get_projects_unique_daily_columns(),
            group_by_field="time",
            filters=AnalyticsQueryFilters(
                metric_filters=MetricName.to_list_from_group(MetricName.ACTIVITY_METRICS),
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
                page=0,
                per_page=10000,
            ),
        )

    def _build_projects_unique_daily_aggregation(self, query: dict) -> dict:
        """Build date histogram aggregation for unique daily projects with fetch-and-slice.

        Uses date_histogram on time field (daily intervals) with nested cardinality
        aggregation on project. Includes extended_bounds for complete date range coverage.
        """
        # Extract time range from query for extended_bounds
        time_range = query.get("bool", {}).get("filter", [])
        min_time = None
        max_time = None
        for filter_clause in time_range:
            if "range" in filter_clause and TIMESTAMP_FIELD in filter_clause["range"]:
                time_filter = filter_clause["range"][TIMESTAMP_FIELD]
                min_time = time_filter.get("gte")
                max_time = time_filter.get("lte")
                break

        # Build date_histogram aggregation
        date_histogram_base = {
            "date_histogram": {
                "field": "time",
                "calendar_interval": "1d",
                "time_zone": "UTC",
                "order": {"_key": "asc"},
            },
            "aggs": {
                "unique_projects": {
                    "cardinality": {
                        "field": PROJECT_KEYWORD_FIELD,
                    }
                }
            },
        }

        # Add extended_bounds if time range is available
        if min_time and max_time:
            try:
                # Convert ISO strings to epoch millis if needed
                if isinstance(min_time, str):
                    min_dt = datetime.fromisoformat(min_time.replace("Z", "+00:00"))
                    min_time = int(min_dt.timestamp() * 1000)
                if isinstance(max_time, str):
                    max_dt = datetime.fromisoformat(max_time.replace("Z", "+00:00"))
                    max_time = int(max_dt.timestamp() * 1000)

                date_histogram_base["date_histogram"]["extended_bounds"] = {"min": min_time, "max": max_time}
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to parse time bounds for extended_bounds: {e}. Proceeding without.")
        else:
            logger.warning("Time range not found in query filter. Extended bounds will not be set.")

        # Construct full aggregation body
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "paginated_results": date_histogram_base,
            },
        }

        return agg_body

    def _parse_projects_unique_daily_result(self, result: dict) -> list[dict]:
        """Parse result for projects unique daily.

        Extracts date buckets and formats dates as ISO date strings (YYYY-MM-DD).
        """
        # Extract buckets from paginated_results (date_histogram buckets)
        buckets = result.get("aggregations", {}).get("paginated_results", {}).get("buckets", [])

        rows = []
        for bucket in buckets:
            try:
                # Parse date from bucket key
                # ES returns key_as_string (ISO format) or key (epoch millis)
                date_str = bucket.get("key_as_string")
                if date_str:
                    # Validate ISO format and extract date portion
                    if "T" not in date_str:
                        logger.warning(f"Unexpected date format in key_as_string: {date_str}. Expected ISO format.")
                        continue
                    # Format as YYYY-MM-DD (remove time portion)
                    date_formatted = date_str.split("T")[0]
                else:
                    # Fallback: parse epoch millis
                    key_millis = bucket.get("key")
                    if key_millis is None or key_millis < 0:
                        logger.warning(f"Bucket has invalid key value: {bucket}")
                        continue
                    date_obj = datetime.fromtimestamp(key_millis / 1000, tz=timezone.utc)
                    date_formatted = date_obj.strftime("%Y-%m-%d")

                # Extract unique projects count from cardinality aggregation
                unique_projects = bucket.get("unique_projects", {}).get("value", 0)

                rows.append({"date": date_formatted, "unique_projects": int(unique_projects)})
            except (ValueError, TypeError, OSError) as e:
                logger.warning(f"Failed to parse bucket {bucket}: {e}. Skipping bucket.")
                continue

        logger.debug(f"Parsed projects-unique-daily result: total_date_buckets={len(buckets)}, rows_parsed={len(rows)}")
        return rows

    def _get_projects_unique_daily_columns(self) -> list[dict]:
        """Get column definitions for projects unique daily."""
        return [
            {"id": "date", "label": "Date", "type": "date"},
            {"id": "unique_projects", "label": "Unique Projects", "type": "number"},
        ]
