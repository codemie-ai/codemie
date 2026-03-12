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

from __future__ import annotations

import logging
from datetime import datetime, timezone

from codemie.configs import config
from codemie.service.analytics.metric_names import MetricName

logger = logging.getLogger(__name__)


class CLICostProcessor:
    """Processes and adjusts CLI-related costs based on business rules.

    This class centralizes the logic for adjusting cost metrics based on a
    configured cutoff date (CLI_METRICS_CUTOFF_DATE).
    """

    @staticmethod
    def adjust_date_range_for_cutoff(start_date: datetime, end_date: datetime) -> tuple[datetime, datetime] | None:
        """Adjust query date range to exclude period before CLI metrics cutoff.

        Applies the configured CLI_METRICS_CUTOFF_DATE to limit queries to dates
        on or after the cutoff.

        Args:
            start_date: Original query start date (timezone-aware)
            end_date: Original query end date (timezone-aware)

        Returns:
            tuple[datetime, datetime]: Adjusted (start_date, end_date) to use for query
            None: If entire date range is before cutoff (should return zero costs)

        Examples:
            Cutoff: Feb 2, 2026
            - Query Jan 1 - Jan 31 → Returns None (entire range before cutoff)
            - Query Jan 1 - Feb 3 → Returns (Feb 2, Feb 3) (adjust start to cutoff)
            - Query Feb 3 - Feb 10 → Returns (Feb 3, Feb 10) (no adjustment needed)
        """
        cutoff_date_str = config.CLI_METRICS_CUTOFF_DATE
        if not cutoff_date_str:
            return start_date, end_date  # No cutoff configured, use original dates

        try:
            # Parse cutoff date (format: YYYY-MM-DD) and make it timezone-aware (UTC)
            cutoff_dt = datetime.strptime(cutoff_date_str, "%Y-%m-%d")
            cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)

            # Case 1: Entire range is before cutoff → return None (show zeros)
            if end_date < cutoff_dt:
                logger.info(
                    f"CLI metrics query: entire range {start_date.date()} to {end_date.date()} "
                    f"is before cutoff {cutoff_dt.date()}, will return zero costs"
                )
                return None

            # Case 2: Range spans cutoff → adjust start date to cutoff
            if start_date < cutoff_dt:
                logger.info(
                    f"CLI metrics query: adjusting start date from {start_date.date()} to cutoff {cutoff_dt.date()} "
                    f"(end date: {end_date.date()})"
                )
                return cutoff_dt, end_date

            # Case 3: Entire range is after cutoff → use original dates
            return start_date, end_date

        except (ValueError, AttributeError) as e:
            logger.warning(f"Invalid CLI_METRICS_CUTOFF_DATE '{cutoff_date_str}': {e}. Using original dates.")
            return start_date, end_date


class CLICostAdjustmentMixin:
    """Mixin providing CLI cost query and adjustment functionality.

    This mixin encapsulates the pattern of:
    1. Checking if date range needs adjustment for CLI metrics cutoff
    2. Querying CLI costs separately with adjusted dates
    3. Merging adjusted costs back into main results

    Usage:
        class MyHandler(CLICostAdjustmentMixin, BaseHandler):
            async def get_data(self, ...):
                cli_costs = await self.get_cli_costs_with_adjustment(...)
    """

    @staticmethod
    def adjust_date_range_for_cutoff(start_date: datetime, end_date: datetime) -> tuple[datetime, datetime] | None:
        """Adjust query date range to exclude period before CLI metrics cutoff.

        Args:
            start_date: Original query start date
            end_date: Original query end date

        Returns:
            Adjusted (start_date, end_date) or None if entire range is before cutoff
        """
        return CLICostProcessor.adjust_date_range_for_cutoff(start_date, end_date)

    async def get_cli_costs_with_adjustment(
        self,
        start_date: datetime,
        end_date: datetime,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        include_cache_costs: bool = True,
    ) -> dict[str, float]:
        """Query CLI costs with automatic date range adjustment for cutoff.

        This method handles the full pattern:
        1. Check if dates need adjustment
        2. Return zeros if entire range before cutoff
        3. Query with adjusted dates if needed
        4. Return costs

        Args:
            start_date: Original query start date
            end_date: Original query end date
            users: Optional user filter
            projects: Optional project filter
            include_cache_costs: Whether to include cache read/creation costs (default: True)

        Returns:
            dict with keys: total_cost, cache_read_cost (if enabled), cache_creation_cost (if enabled)
        """
        adjusted_dates = self.adjust_date_range_for_cutoff(start_date, end_date)

        if adjusted_dates is None:
            # Entire range before cutoff, return zeros
            logger.info(
                f"CLI costs: entire range {start_date.date()} to {end_date.date()} before cutoff, returning zeros"
            )
            result = {"total_cost": 0.0}
            if include_cache_costs:
                result["cache_read_cost"] = 0.0
                result["cache_creation_cost"] = 0.0  # Field removed from new metric
            return result

        adj_start_dt, adj_end_dt = adjusted_dates

        # Query CLI costs with adjusted dates
        return await self._query_cli_costs(adj_start_dt, adj_end_dt, users, projects, include_cache_costs)

    async def get_cli_costs_grouped_by(
        self,
        start_date: datetime,
        end_date: datetime,
        group_by_field: str,
        entity_name: str,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict[str, float]:
        """Query CLI costs grouped by a field with adjusted dates.

        This query uses adjusted dates to exclude the period before CLI metrics cutoff.
        Generic method that can group by any field (project, user, etc.).

        Args:
            start_date: Original query start date
            end_date: Original query end date
            group_by_field: Field to group by (e.g., "attributes.project.keyword")
            entity_name: Name for the entity (for logging, e.g., "project", "user")
            users: Optional user filter
            projects: Optional project filter

        Returns:
            dict mapping entity_value -> adjusted CLI cost
        """
        adjusted_dates = self.adjust_date_range_for_cutoff(start_date, end_date)

        if adjusted_dates is None:
            # Entire range before cutoff, return empty dict (all entities have $0 CLI cost)
            logger.info(f"CLI costs by {entity_name}: entire range before cutoff, returning zeros")
            return {}

        adj_start_dt, adj_end_dt = adjusted_dates

        # Build query with filters (use CLI_LLM_USAGE_TOTAL for cost/token data)
        query = self._pipeline._build_query(
            start_dt=adj_start_dt,
            end_dt=adj_end_dt,
            users=users,
            projects=projects,
            metric_filters=[MetricName.CLI_LLM_USAGE_TOTAL.value],
        )

        # Add cli_request filter
        if "bool" not in query:
            query = {"bool": {"filter": [query]}}
        if "filter" not in query["bool"]:
            query["bool"]["filter"] = []
        elif not isinstance(query["bool"]["filter"], list):
            query["bool"]["filter"] = [query["bool"]["filter"]]

        query["bool"]["filter"].append({"term": {"attributes.cli_request": True}})

        # Build aggregation grouped by field
        agg_body = {
            "query": query,
            "size": 0,
            "aggs": {
                "grouped_entities": {
                    "terms": {
                        "field": group_by_field,
                        # Fetch all entities for CLI cost mapping (not paginated)
                        "size": 10000,
                    },
                    "aggs": {
                        "cli_cost": {"sum": {"field": "attributes.money_spent"}},
                    },
                }
            },
        }

        result = await self.repository.execute_aggregation_query(agg_body)
        buckets = result.get("aggregations", {}).get("grouped_entities", {}).get("buckets", [])

        # Build mapping: entity_value -> cli_cost
        cli_costs = {}
        for bucket in buckets:
            entity_value = bucket["key"]
            cli_cost = bucket.get("cli_cost", {}).get("value", 0) or 0
            cli_costs[entity_value] = round(cli_cost, 2)

        logger.debug(f"CLI costs by {entity_name}: {len(cli_costs)} {entity_name}s with CLI usage")
        return cli_costs

    async def _query_cli_costs(
        self,
        start_date: datetime,
        end_date: datetime,
        users: list[str] | None,
        projects: list[str] | None,
        include_cache_costs: bool,
    ) -> dict[str, float]:
        """Execute the actual CLI cost query (to be called with adjusted dates).

        Args:
            start_date: Start date for query
            end_date: End date for query
            users: Optional user filter
            projects: Optional project filter
            include_cache_costs: Whether to include cache costs

        Returns:
            dict with cost values
        """
        # Build query with filters (use CLI_LLM_USAGE_TOTAL for cost/token data)
        query = self._pipeline._build_query(
            start_dt=start_date,
            end_dt=end_date,
            users=users,
            projects=projects,
            metric_filters=[MetricName.CLI_LLM_USAGE_TOTAL.value],
        )

        # Add cli_request filter
        if "bool" not in query:
            query = {"bool": {"filter": [query]}}
        if "filter" not in query["bool"]:
            query["bool"]["filter"] = []
        elif not isinstance(query["bool"]["filter"], list):
            query["bool"]["filter"] = [query["bool"]["filter"]]

        query["bool"]["filter"].append({"term": {"attributes.cli_request": True}})

        # Build aggregation
        aggs_dict = {
            "total_cost": {"sum": {"field": "attributes.money_spent"}},
        }

        if include_cache_costs:
            aggs_dict["cache_read_cost"] = {"sum": {"field": "attributes.cost.cache_read_cost"}}
            # cache_creation_cost field removed from new metric - will return 0

        agg_body = {
            "query": query,
            "size": 0,
            "aggs": aggs_dict,
        }

        result = await self.repository.execute_aggregation_query(agg_body)
        aggs = result.get("aggregations", {})

        costs = {
            "total_cost": round(aggs.get("total_cost", {}).get("value", 0) or 0, 2),
        }

        if include_cache_costs:
            costs["cache_read_cost"] = round(aggs.get("cache_read_cost", {}).get("value", 0) or 0, 2)
            costs["cache_creation_cost"] = 0.0  # Field removed from new metric

        return costs
