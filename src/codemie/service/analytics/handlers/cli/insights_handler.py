# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""CLI analytics insights handler."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime

from codemie.clients.postgres import get_async_session
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.repository.user_enrichment_repository import user_enrichment_repository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import (
    METRIC_NAME_KEYWORD_FIELD,
    PROJECT_KEYWORD_FIELD,
    USER_EMAIL_KEYWORD_FIELD,
)
from codemie.service.analytics.metric_names import MetricName
from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.analytics.time_parser import TimeParser

from .base_handler import CLIBaseHandler
from .classification_engine import CLIClassificationEngine, EnrichedUserScope
from .constants import (
    BRANCH_KEYWORD_FIELD,
    CACHE_CREATION_TOKENS_FIELD,
    CACHE_READ_INPUT_TOKENS_FIELD,
    FILES_CREATED_FIELD,
    FILES_DELETED_FIELD,
    FILES_MODIFIED_FIELD,
    INPUT_TOKENS_FIELD,
    LLM_MODEL_KEYWORD_FIELD,
    MONEY_SPENT_FIELD,
    NET_LINES_LABEL,
    OUTPUT_TOKENS_FIELD,
    REPOSITORY_KEYWORD_FIELD,
    SESSION_ID_KEYWORD_FIELD,
    SESSION_STATUS_KEYWORD_FIELD,
    SESSION_COMPLETED_STATUSES,
    SESSION_DURATION_MS_FIELD,
    TIMESTAMP_FIELD,
    TOTAL_COST_LABEL,
    TOTAL_LINES_ADDED_FIELD,
    TOTAL_LINES_REMOVED_FIELD,
    TOTAL_TOOL_CALLS_FIELD,
    TOTAL_USER_PROMPTS_FIELD,
    USAGE_COUNT_LABEL,
    USER_ID_KEYWORD_FIELD,
    USER_NAME_KEYWORD_FIELD,
)

logger = logging.getLogger(__name__)


class CLIInsightsHandler(CLIBaseHandler):
    """Handler for CLI analytics insights."""

    def __init__(self, user: User, repository: MetricsElasticRepository) -> None:
        """Initialize cli insights handler."""
        super().__init__(user, repository)

    async def get_cli_insights_weekday_pattern(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get weekday activity distribution for CLI insights."""
        patterns = await self._get_cli_time_pattern_rows(time_period, start_date, end_date, users, projects)
        rows = sorted(patterns["weekday"].values(), key=lambda row: row["weekday_index"])
        for row in rows:
            row.pop("weekday_index", None)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_weekday_pattern_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_hourly_usage(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 24,
    ) -> dict:
        """Get hourly activity distribution for CLI insights."""
        patterns = await self._get_cli_time_pattern_rows(time_period, start_date, end_date, users, projects)
        rows = sorted(patterns["hour"].values(), key=lambda row: row["hour"])
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_hourly_usage_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_session_depth(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get prompts-per-session distribution for CLI insights."""
        rows = await self._get_cli_session_depth_rows(time_period, start_date, end_date, users, projects)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_session_depth_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_user_classification(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get aggregated user classification metrics for CLI insights."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(
            self._build_cli_insights_classification_aggregation(query)
        )
        grouped: dict[str, dict] = defaultdict(
            lambda: {"classification": "", "user_count": 0, "total_cost": 0.0, "avg_cost": 0.0}
        )
        for b in result.get("aggregations", {}).get("users", {}).get("buckets", []):
            total_cost = round(b.get("cost_bucket", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            repositories = [r["key"] for r in b.get("repositories", {}).get("buckets", {}).get("buckets", [])]
            branches = [r["key"] for r in b.get("branches", {}).get("buckets", {}).get("buckets", [])]
            projects_used = [r["key"] for r in b.get("projects", {}).get("buckets", {}).get("buckets", [])]
            classification, _ = CLIClassificationEngine._classify_cli_entity(
                repositories=repositories,
                branches=branches,
                project_name=projects_used[0] if projects_used else None,
                total_cost=total_cost,
            )
            bucket = grouped[classification]
            bucket["classification"] = classification
            bucket["user_count"] += 1
            bucket["total_cost"] += total_cost
        result_rows = list(grouped.values())
        for row in result_rows:
            row["total_cost"] = round(row["total_cost"], 2)
            row["avg_cost"] = round(row["total_cost"] / max(row["user_count"], 1), 2)
        result_rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=result_rows,
            columns=self._get_cli_insights_user_classification_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_top_users_by_cost(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI users ranked by cost."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(self._build_cli_insights_top_users_aggregation(query))
        rows = []
        for b in result.get("aggregations", {}).get("users", {}).get("buckets", []):
            user_id = b["key"]
            user_name = self._extract_top_metric(b, "user_name", USER_NAME_KEYWORD_FIELD) or user_id
            user_email = self._extract_top_metric(b, "user_email", USER_EMAIL_KEYWORD_FIELD) or user_name
            total_cost = round(b.get("cost_bucket", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            repositories = [r["key"] for r in b.get("repositories", {}).get("buckets", {}).get("buckets", [])]
            branches = [r["key"] for r in b.get("branches", {}).get("buckets", {}).get("buckets", [])]
            projects_used = [r["key"] for r in b.get("projects", {}).get("buckets", {}).get("buckets", [])]
            classification, _ = CLIClassificationEngine._classify_cli_entity(
                repositories=repositories,
                branches=branches,
                project_name=projects_used[0] if projects_used else None,
                total_cost=total_cost,
            )
            rows.append(
                {
                    "user_id": user_id,
                    "user_name": user_name,
                    "user_email": user_email,
                    "classification": classification,
                    "total_cost": total_cost,
                }
            )
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_top_users_by_cost_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_top_spenders(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get Top Spenders table data for CLI insights."""
        rows = await self._get_cli_insights_user_rows(time_period, start_date, end_date, users, projects)
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        ranked_rows = [{"rank": index + 1, **row} for index, row in enumerate(rows)]
        return self._format_custom_tabular_response(
            rows=ranked_rows,
            columns=self._get_cli_insights_top_spenders_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_all_users(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get all CLI users table data for CLI insights."""
        rows = await self._get_cli_insights_user_rows(time_period, start_date, end_date, users, projects)
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_all_users_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_user_detail(
        self,
        user_name: str,
        user_id: str | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Get drilldown detail for a single CLI user."""
        detail = await self._get_cli_insights_user_detail_payload(
            user_name=user_name,
            user_id=user_id,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )
        return self._format_custom_detail_response(
            detail=detail,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
        )

    async def get_cli_insights_user_key_metrics(self, **kwargs) -> dict:
        """Get key metrics widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        return self._format_custom_summary_response(
            metrics=detail["key_metrics"]["data"]["metrics"],
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
        )

    async def get_cli_insights_user_tools(self, **kwargs) -> dict:
        """Get tools donut widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        tools_chart = detail["tools_chart"]
        rows = tools_chart["data"]["rows"]
        columns = tools_chart["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def get_cli_insights_user_models(self, **kwargs) -> dict:
        """Get models donut widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        models_chart = detail["models_chart"]
        rows = models_chart["data"]["rows"]
        columns = models_chart["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def get_cli_insights_user_workflow_intent(self, **kwargs) -> dict:
        """Get workflow intent widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        return self._format_custom_summary_response(
            metrics=detail["workflow_intent_metrics"]["data"]["metrics"],
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
        )

    async def get_cli_insights_user_classification_detail(self, **kwargs) -> dict:
        """Get classification widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        return self._format_custom_summary_response(
            metrics=detail["classification_metrics"]["data"]["metrics"],
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
        )

    async def get_cli_insights_user_category_breakdown(self, **kwargs) -> dict:
        """Get category breakdown widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        category_breakdown_chart = detail["category_breakdown_chart"]
        rows = category_breakdown_chart["data"]["rows"]
        columns = category_breakdown_chart["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def get_cli_insights_user_repositories(self, **kwargs) -> dict:
        """Get repositories table widget data for a CLI user detail modal."""
        detail = await self._get_cli_insights_user_detail_payload(**kwargs)
        repositories_table = detail["repositories_table"]
        rows = repositories_table["data"]["rows"]
        columns = repositories_table["data"]["columns"]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=kwargs.get("time_period"),
            start_date=kwargs.get("start_date"),
            end_date=kwargs.get("end_date"),
            users=kwargs.get("users"),
            projects=kwargs.get("projects"),
            page=0,
            per_page=max(len(rows), 1),
        )

    async def _get_cli_insights_user_detail_payload(
        self,
        user_name: str,
        user_id: str | None = None,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
    ) -> dict:
        """Build the full CLI user detail payload used by user detail widgets."""
        if not user_id:
            user_id = await self._resolve_cli_insights_user_id(
                entity_name=user_name,
                time_period=time_period,
                start_date=start_date,
                end_date=end_date,
                users=users,
                projects=projects,
            )
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        detail_query = self._build_cli_insights_entity_query(
            start_dt,
            end_dt,
            users,
            projects,
            entity_name=user_name,
            entity_id=user_id,
        )
        aggregation_result, tool_docs_result = await asyncio.gather(
            self.repository.execute_aggregation_query(self._build_cli_insights_user_detail_aggregation(detail_query)),
            self.repository.execute_search_query(
                self._build_cli_insights_tool_docs_query(detail_query),
                size=10000,
            ),
        )
        detail = self._parse_cli_insights_user_detail_result(
            user_name=user_name,
            result=aggregation_result,
            tool_docs_result=tool_docs_result,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        return detail

    async def _resolve_cli_insights_user_id(
        self,
        entity_name: str,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> str | None:
        """Resolve user_id from existing CLI insights user rows when only label/email is provided."""
        normalized_entity_name = entity_name.strip().lower()
        if not normalized_entity_name:
            return None

        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(self._build_cli_insights_identity_aggregation(query))
        for b in result.get("aggregations", {}).get("users", {}).get("buckets", []):
            user_id = b["key"]
            user_name = str(self._extract_top_metric(b, "user_name", USER_NAME_KEYWORD_FIELD) or "").strip().lower()
            user_email = str(self._extract_top_metric(b, "user_email", USER_EMAIL_KEYWORD_FIELD) or "").strip().lower()
            if normalized_entity_name in {user_name, user_email} and user_id:
                return str(user_id)
        return None

    async def get_cli_insights_project_classification(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get aggregated project classification metrics for CLI insights."""
        rows = await self._get_cli_insights_project_rows(time_period, start_date, end_date, users, projects)
        grouped: dict[str, dict] = defaultdict(lambda: {"classification": "", "project_count": 0, "total_cost": 0.0})
        for row in rows:
            bucket = grouped[row["classification"]]
            bucket["classification"] = row["classification"]
            bucket["project_count"] += 1
            bucket["total_cost"] += float(row["total_cost"])
        result_rows = list(grouped.values())
        for row in result_rows:
            row["total_cost"] = round(row["total_cost"], 2)
        result_rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=result_rows,
            columns=self._get_cli_insights_project_classification_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_cli_insights_top_projects_by_cost(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get top CLI projects ranked by cost."""
        rows = await self._get_cli_insights_project_rows(time_period, start_date, end_date, users, projects)
        rows.sort(key=lambda row: row["total_cost"], reverse=True)
        return self._format_custom_tabular_response(
            rows=rows,
            columns=self._get_cli_insights_top_projects_by_cost_columns(),
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def _get_cli_time_pattern_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> dict[str, dict]:
        """Aggregate hourly histogram into weekday and hour counts."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(
            start_dt,
            end_dt,
            users,
            projects,
            [MetricName.CLI_TOOL_USAGE_TOTAL.value],
        )
        result = await self.repository.execute_aggregation_query(
            {
                "query": query,
                "size": 0,
                "aggs": {
                    "hourly_buckets": {
                        "date_histogram": {
                            "field": TIMESTAMP_FIELD,
                            "calendar_interval": "hour",
                            "min_doc_count": 1,
                        }
                    }
                },
            }
        )

        weekday_rows: dict[str, dict] = {}
        hour_rows: dict[int, dict] = {}
        weekday_order = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

        for bucket in result.get("aggregations", {}).get("hourly_buckets", {}).get("buckets", []):
            key_as_string = bucket.get("key_as_string")
            if not key_as_string:
                continue
            bucket_dt = datetime.fromisoformat(key_as_string.replace("Z", "+00:00"))
            weekday_index = (bucket_dt.weekday() + 1) % 7
            weekday_label = weekday_order[weekday_index]
            weekday_rows.setdefault(
                weekday_label,
                {"weekday": weekday_label, "weekday_index": weekday_index, "activity_count": 0},
            )
            weekday_rows[weekday_label]["activity_count"] += bucket["doc_count"]
            hour_rows.setdefault(bucket_dt.hour, {"hour": bucket_dt.hour, "activity_count": 0})
            hour_rows[bucket_dt.hour]["activity_count"] += bucket["doc_count"]

        return {"weekday": weekday_rows, "hour": hour_rows}

    async def _get_cli_session_depth_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> list[dict]:
        """Build session depth distribution from prompts-per-session counts."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(
            start_dt,
            end_dt,
            users,
            projects,
            [MetricName.CLI_TOOL_USAGE_TOTAL.value],
        )
        result = await self.repository.execute_aggregation_query(
            {
                "query": query,
                "size": 0,
                "aggs": {
                    "sessions": {
                        "terms": {"field": SESSION_ID_KEYWORD_FIELD, "size": 10000},
                        "aggs": {"total_prompts": {"sum": {"field": "attributes.total_user_prompts"}}},
                    }
                },
            }
        )

        bucket_ranges = [
            ("1", lambda value: value <= 1),
            ("2-5", lambda value: 2 <= value <= 5),
            ("6-10", lambda value: 6 <= value <= 10),
            ("11-20", lambda value: 11 <= value <= 20),
            ("21-50", lambda value: 21 <= value <= 50),
            ("51-100", lambda value: 51 <= value <= 100),
            ("100+", lambda value: value > 100),
        ]
        counts = {label: 0 for label, _predicate in bucket_ranges}

        for bucket in result.get("aggregations", {}).get("sessions", {}).get("buckets", []):
            prompts = int(bucket.get("total_prompts", {}).get("value", 0) or 0)
            for label, predicate in bucket_ranges:
                if predicate(prompts):
                    counts[label] += 1
                    break

        return [{"range": label, "count": counts[label]} for label, _predicate in bucket_ranges]

    async def _get_cli_insights_user_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> list[dict]:
        """Build user-level CLI insight rows used by multiple widgets."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(self._build_cli_insights_user_aggregation(query))
        rows = []
        for bucket in result.get("aggregations", {}).get("users", {}).get("buckets", []):
            user_id = bucket["key"]
            user_name = self._extract_top_metric(bucket, "user_name", USER_NAME_KEYWORD_FIELD) or user_id
            user_email = self._extract_top_metric(bucket, "user_email", USER_EMAIL_KEYWORD_FIELD) or user_name
            total_cost = round(bucket.get("cost_bucket", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            repositories = [
                repo["key"] for repo in bucket.get("repositories", {}).get("buckets", {}).get("buckets", [])
            ]
            branches = [branch["key"] for branch in bucket.get("branches", {}).get("buckets", {}).get("buckets", [])]
            projects_used = [
                project["key"] for project in bucket.get("projects", {}).get("buckets", {}).get("buckets", [])
            ]
            classification, _confidence = CLIClassificationEngine._classify_cli_entity(
                repositories=repositories,
                branches=branches,
                project_name=projects_used[0] if projects_used else None,
                total_cost=total_cost,
            )
            total_lines_added = int(bucket.get("total_lines_added", {}).get("total", {}).get("value", 0) or 0)
            total_lines_removed = int(bucket.get("total_lines_removed", {}).get("total", {}).get("value", 0) or 0)
            total_sessions = int(bucket.get("total_sessions", {}).get("count", {}).get("value", 0) or 0)
            rows.append(
                {
                    "user_id": user_id,
                    "user_name": user_name,
                    "user_email": user_email,
                    "classification": classification,
                    "total_sessions": total_sessions,
                    "total_lines_added": total_lines_added,
                    "total_lines_removed": total_lines_removed,
                    "net_lines": total_lines_added - total_lines_removed,
                    "total_cost": total_cost,
                }
            )
        return rows

    async def _get_cli_insights_project_rows(
        self,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> list[dict]:
        """Build project-level CLI insight rows used by multiple widgets."""
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(self._build_cli_insights_project_aggregation(query))
        rows = []
        for bucket in result.get("aggregations", {}).get("projects", {}).get("buckets", []):
            project_name = str(bucket["key"]).strip()
            if not project_name:
                continue
            total_cost = round(bucket.get("cost_bucket", {}).get("total_cost", {}).get("value", 0) or 0, 2)
            repositories = [
                repo["key"] for repo in bucket.get("repositories", {}).get("buckets", {}).get("buckets", [])
            ]
            branches = [branch["key"] for branch in bucket.get("branches", {}).get("buckets", {}).get("buckets", [])]
            classification, _confidence = CLIClassificationEngine._classify_cli_entity(
                repositories=repositories,
                branches=branches,
                project_name=project_name,
                total_cost=total_cost,
            )
            rows.append(
                {
                    "project_name": project_name,
                    "project_type": CLIClassificationEngine._infer_project_type(project_name),
                    "classification": classification,
                    "total_cost": total_cost,
                }
            )
        return rows

    def _build_cli_insights_user_aggregation(self, query: dict) -> dict:
        """Build user aggregation for CLI insights."""
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        lines_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        session_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_SESSION_TOTAL.value}}
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "users": {
                    "terms": {"field": USER_ID_KEYWORD_FIELD, "size": 10000},
                    "aggs": {
                        "user_name": {
                            "top_metrics": {
                                "metrics": {"field": USER_NAME_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "user_email": {
                            "top_metrics": {
                                "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "projects": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10}}},
                        },
                        "repositories": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 20}}},
                        },
                        "branches": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}}},
                        },
                        "cost_bucket": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                        "total_lines_added": {
                            "filter": lines_filter,
                            "aggs": {"total": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}}},
                        },
                        "total_lines_removed": {
                            "filter": lines_filter,
                            "aggs": {"total": {"sum": {"field": "attributes.total_lines_removed"}}},
                        },
                        "total_sessions": {
                            "filter": session_filter,
                            "aggs": {"count": {"cardinality": {"field": SESSION_ID_KEYWORD_FIELD}}},
                        },
                    },
                }
            },
        }

    def _build_cli_insights_enrichment_aggregation(self, query: dict) -> dict:
        """Minimal aggregation for enriched-user dimension endpoints.

        Fetches only user_email and total_cost per user, skipping sub-aggregations
        (projects, repositories, branches, lines, sessions) that are unused for
        job_title / primary_skill / country / city grouping.
        """
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "users": {
                    "terms": {"field": USER_ID_KEYWORD_FIELD, "size": 10000},
                    "aggs": {
                        "user_email": {
                            "top_metrics": {
                                "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "cost_bucket": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                    },
                }
            },
        }

    def _build_cli_insights_classification_aggregation(self, query: dict) -> dict:
        """Aggregation for user_classification widget.

        Fetches only the fields needed to compute classification and total_cost per user.
        Omits user_name, user_email, total_sessions, total_lines_added, total_lines_removed.
        """
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "users": {
                    "terms": {"field": USER_ID_KEYWORD_FIELD, "size": 10000},
                    "aggs": {
                        "projects": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10}}},
                        },
                        "repositories": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 20}}},
                        },
                        "branches": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}}},
                        },
                        "cost_bucket": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                    },
                }
            },
        }

    def _build_cli_insights_top_users_aggregation(self, query: dict) -> dict:
        """Aggregation for top_users_by_cost widget.

        Includes user_name, user_email, classification fields, and total_cost.
        Omits total_sessions, total_lines_added, total_lines_removed.
        """
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "users": {
                    "terms": {"field": USER_ID_KEYWORD_FIELD, "size": 10000},
                    "aggs": {
                        "user_name": {
                            "top_metrics": {
                                "metrics": {"field": USER_NAME_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "user_email": {
                            "top_metrics": {
                                "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "projects": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10}}},
                        },
                        "repositories": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 20}}},
                        },
                        "branches": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}}},
                        },
                        "cost_bucket": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                    },
                }
            },
        }

    def _build_cli_insights_identity_aggregation(self, query: dict) -> dict:
        """Minimal aggregation for _resolve_user_id_from_entity_name.

        Fetches only user_name and user_email (user_id is the bucket key).
        Omits cost, sessions, lines, repos, branches, projects, and classification.
        """
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "users": {
                    "terms": {"field": USER_ID_KEYWORD_FIELD, "size": 10000},
                    "aggs": {
                        "user_name": {
                            "top_metrics": {
                                "metrics": {"field": USER_NAME_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                        "user_email": {
                            "top_metrics": {
                                "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                                "size": 1,
                                "sort": {TIMESTAMP_FIELD: "desc"},
                            }
                        },
                    },
                }
            },
        }

    def _build_cli_insights_project_aggregation(self, query: dict) -> dict:
        """Build project aggregation for CLI insights."""
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "projects": {
                    "terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10000, "exclude": ""},
                    "aggs": {
                        "repositories": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 20}}},
                        },
                        "branches": {
                            "filter": usage_filter,
                            "aggs": {"buckets": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}}},
                        },
                        "cost_bucket": {
                            "filter": {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}},
                            "aggs": {"total_cost": {"sum": {"field": MONEY_SPENT_FIELD}}},
                        },
                    },
                }
            },
        }

    def _build_cli_insights_entity_query(
        self,
        start_dt: datetime,
        end_dt: datetime,
        users: list[str] | None,
        projects: list[str] | None,
        entity_name: str,
        entity_id: str | None = None,
    ) -> dict:
        """Build base query scoped to one CLI user by name or email."""
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        should_filters = [
            {"term": {USER_NAME_KEYWORD_FIELD: entity_name}},
            {"term": {USER_EMAIL_KEYWORD_FIELD: entity_name}},
        ]
        if entity_id:
            should_filters.append({"term": {USER_ID_KEYWORD_FIELD: entity_id}})
        entity_filter = {"bool": {"should": should_filters, "minimum_should_match": 1}}
        query.setdefault("bool", {}).setdefault("filter", []).append(entity_filter)
        return query

    def _build_cli_insights_tool_docs_query(self, query: dict) -> dict:
        """Build search query for raw CLI tool usage docs."""
        return {
            "bool": {
                "filter": [
                    query,
                    {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}},
                ]
            }
        }

    def _build_cli_insights_user_detail_aggregation(self, query: dict) -> dict:
        """Build detail aggregation for one CLI user."""
        usage_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_TOOL_USAGE_TOTAL.value}}
        session_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_SESSION_TOTAL.value}}
        proxy_filter = {"term": {METRIC_NAME_KEYWORD_FIELD: MetricName.CLI_LLM_USAGE_TOTAL.value}}
        completed_session_filter = {
            "bool": {
                "filter": [
                    session_filter,
                    {"terms": {SESSION_STATUS_KEYWORD_FIELD: list(SESSION_COMPLETED_STATUSES)}},
                ]
            }
        }
        return {
            "query": query,
            "size": 0,
            "aggs": {
                "user_name": {
                    "top_metrics": {
                        "metrics": {"field": USER_NAME_KEYWORD_FIELD},
                        "size": 1,
                        "sort": {TIMESTAMP_FIELD: "desc"},
                    }
                },
                "user_email": {
                    "top_metrics": {
                        "metrics": {"field": USER_EMAIL_KEYWORD_FIELD},
                        "size": 1,
                        "sort": {TIMESTAMP_FIELD: "desc"},
                    }
                },
                "tool_usage": {
                    "filter": usage_filter,
                    "aggs": {
                        "total_prompts": {"sum": {"field": TOTAL_USER_PROMPTS_FIELD}},
                        "total_commands": {"sum": {"field": TOTAL_TOOL_CALLS_FIELD}},
                        "total_lines_added": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}},
                        "total_lines_removed": {"sum": {"field": TOTAL_LINES_REMOVED_FIELD}},
                        "files_created": {"sum": {"field": FILES_CREATED_FIELD}},
                        "files_modified": {"sum": {"field": FILES_MODIFIED_FIELD}},
                        "files_deleted": {"sum": {"field": FILES_DELETED_FIELD}},
                        "unique_repositories": {"cardinality": {"field": REPOSITORY_KEYWORD_FIELD}},
                        "projects": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 100}},
                        "branches": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 100}},
                    },
                },
                "session_usage": {
                    "filter": session_filter,
                    "aggs": {
                        "total_sessions": {"cardinality": {"field": SESSION_ID_KEYWORD_FIELD}},
                        "active_days": {
                            "date_histogram": {
                                "field": TIMESTAMP_FIELD,
                                "calendar_interval": "day",
                                "min_doc_count": 1,
                            }
                        },
                    },
                },
                "completed_sessions": {
                    "filter": completed_session_filter,
                    "aggs": {
                        "avg_duration_ms": {"avg": {"field": SESSION_DURATION_MS_FIELD}},
                    },
                },
                "proxy_usage": {
                    "filter": proxy_filter,
                    "aggs": {
                        "total_cost": {"sum": {"field": MONEY_SPENT_FIELD}},
                        "input_tokens": {"sum": {"field": INPUT_TOKENS_FIELD}},
                        "output_tokens": {"sum": {"field": OUTPUT_TOKENS_FIELD}},
                        "cache_read_tokens": {"sum": {"field": CACHE_READ_INPUT_TOKENS_FIELD}},
                        "cache_creation_tokens": {"sum": {"field": CACHE_CREATION_TOKENS_FIELD}},
                        "models": {"terms": {"field": LLM_MODEL_KEYWORD_FIELD, "size": 20}},
                    },
                },
                "repositories": {
                    "terms": {"field": REPOSITORY_KEYWORD_FIELD, "size": 1000},
                    "aggs": {
                        "usage": {
                            "filter": usage_filter,
                            "aggs": {
                                "lines_added": {"sum": {"field": TOTAL_LINES_ADDED_FIELD}},
                                "lines_removed": {"sum": {"field": TOTAL_LINES_REMOVED_FIELD}},
                                "branches": {"terms": {"field": BRANCH_KEYWORD_FIELD, "size": 20}},
                                "projects": {"terms": {"field": PROJECT_KEYWORD_FIELD, "size": 10}},
                            },
                        },
                        "sessions": {
                            "filter": session_filter,
                            "aggs": {
                                "count": {"cardinality": {"field": SESSION_ID_KEYWORD_FIELD}},
                            },
                        },
                        "proxy": {
                            "filter": proxy_filter,
                            "aggs": {
                                "total_cost": {"sum": {"field": MONEY_SPENT_FIELD}},
                            },
                        },
                    },
                },
            },
        }

    def _extract_cli_user_detail_core_metrics(
        self,
        *,
        user_name: str,
        aggs: dict,
        start_dt: datetime,
        end_dt: datetime,
    ) -> dict:
        """Extract scalar user detail metrics from aggregation buckets."""
        usage_aggs = aggs.get("tool_usage", {})
        session_aggs = aggs.get("session_usage", {})
        completed_session_aggs = aggs.get("completed_sessions", {})
        proxy_aggs = aggs.get("proxy_usage", {})
        total_sessions = int(session_aggs.get("total_sessions", {}).get("value", 0) or 0)
        total_cost = round(proxy_aggs.get("total_cost", {}).get("value", 0) or 0, 2)
        total_prompts = int(usage_aggs.get("total_prompts", {}).get("value", 0) or 0)
        total_lines_added = int(usage_aggs.get("total_lines_added", {}).get("value", 0) or 0)
        total_lines_removed = int(usage_aggs.get("total_lines_removed", {}).get("value", 0) or 0)
        active_days = len(session_aggs.get("active_days", {}).get("buckets", []))
        avg_session_duration_min = round(
            float(completed_session_aggs.get("avg_duration_ms", {}).get("value", 0) or 0) / 60000,
            2,
        )
        analysis_days = max((end_dt - start_dt).days or 0, 1)
        resolved_user_name = self._extract_top_metric(aggs, "user_name", USER_NAME_KEYWORD_FIELD) or user_name
        return {
            "resolved_user_name": resolved_user_name,
            "user_email": self._extract_top_metric(aggs, "user_email", USER_EMAIL_KEYWORD_FIELD) or resolved_user_name,
            "total_sessions": total_sessions,
            "total_cost": total_cost,
            "total_prompts": total_prompts,
            "total_commands": int(usage_aggs.get("total_commands", {}).get("value", 0) or 0),
            "net_lines": total_lines_added - total_lines_removed,
            "files_created": int(usage_aggs.get("files_created", {}).get("value", 0) or 0),
            "files_modified": int(usage_aggs.get("files_modified", {}).get("value", 0) or 0),
            "files_deleted": int(usage_aggs.get("files_deleted", {}).get("value", 0) or 0),
            "active_days": active_days,
            "avg_session_duration_min": avg_session_duration_min,
            "prompts_per_session": round(total_prompts / total_sessions, 2) if total_sessions else 0.0,
            "est_monthly_20d": round((total_cost / analysis_days) * 20, 2),
            "unique_repositories": int(usage_aggs.get("unique_repositories", {}).get("value", 0) or 0),
            "proxy_aggs": proxy_aggs,
        }

    def _extract_cli_user_detail_collections(self, aggs: dict) -> dict:
        """Extract collection fields used in CLI user detail."""
        usage_aggs = aggs.get("tool_usage", {})
        return {
            "unique_projects": [
                bucket["key"] for bucket in usage_aggs.get("projects", {}).get("buckets", []) if bucket["key"]
            ],
            "branches_used": [
                bucket["key"] for bucket in usage_aggs.get("branches", {}).get("buckets", []) if bucket["key"]
            ],
            "repositories": [
                repo_bucket["key"]
                for repo_bucket in aggs.get("repositories", {}).get("buckets", [])
                if str(repo_bucket.get("key", "")).strip()
            ],
        }

    def _build_cli_user_detail_widgets(
        self,
        *,
        metrics: dict,
        classification: str,
        category_breakdown: list[dict],
        repository_classifications: list[dict],
        tools: list[dict],
        models: list[dict],
        tool_profile: dict,
    ) -> dict:
        """Build widget-ready sections for the CLI user detail modal."""
        return {
            "key_metrics": self._build_cli_user_detail_key_metrics(
                total_cost=metrics["total_cost"],
                est_monthly_20d=metrics["est_monthly_20d"],
                total_sessions=metrics["total_sessions"],
                total_prompts=metrics["total_prompts"],
                net_lines=metrics["net_lines"],
                files_modified=metrics["files_modified"],
                active_days=metrics["active_days"],
                avg_session_duration_min=metrics["avg_session_duration_min"],
            ),
            "tools_chart": self._build_cli_user_detail_tools_chart(tools),
            "models_chart": self._build_cli_user_detail_models_chart(models),
            "workflow_intent_metrics": self._build_cli_user_detail_workflow_metrics(
                primary_intent_label=tool_profile.get("primary_intent_label") or "Unknown",
                intent_scores=tool_profile.get("intent_scores") or {},
            ),
            "classification_metrics": self._build_cli_user_detail_classification_metrics(
                primary_category=classification,
                is_multi_category=len(category_breakdown) > 1,
                category_diversity_score=CLIClassificationEngine._calculate_cli_category_diversity_score(
                    category_breakdown
                ),
                unique_repositories=metrics["unique_repositories"],
            ),
            "category_breakdown_chart": self._build_cli_user_detail_category_breakdown_chart(category_breakdown),
            "repositories_table": self._build_cli_user_detail_repositories_table(repository_classifications),
        }

    def _parse_cli_insights_user_detail_result(
        self,
        user_name: str,
        result: dict,
        tool_docs_result: dict,
        start_dt: datetime,
        end_dt: datetime,
    ) -> dict:
        """Parse CLI user detail aggregation into modal payload."""
        aggs = result.get("aggregations", {})
        metrics = self._extract_cli_user_detail_core_metrics(
            user_name=user_name,
            aggs=aggs,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        collections = self._extract_cli_user_detail_collections(aggs)

        classification, _confidence = CLIClassificationEngine._classify_cli_entity(
            repositories=collections["repositories"],
            branches=collections["branches_used"],
            project_name=collections["unique_projects"][0] if collections["unique_projects"] else None,
            total_cost=metrics["total_cost"],
        )
        repository_classifications = CLIClassificationEngine._build_cli_repository_classifications(
            aggs.get("repositories", {}).get("buckets", []),
        )
        category_breakdown = CLIClassificationEngine._build_cli_category_breakdown(repository_classifications)
        tool_counts = CLIClassificationEngine._extract_cli_tool_counts(tool_docs_result)
        tools = [{"tool_name": name, "usage_count": count} for name, count in tool_counts]
        models = [
            {"model_name": bucket["key"], "count": bucket["doc_count"]}
            for bucket in metrics["proxy_aggs"].get("models", {}).get("buckets", [])
            if bucket["key"]
        ]
        tool_profile = CLIClassificationEngine._build_cli_tool_profile(tool_counts)
        widgets = self._build_cli_user_detail_widgets(
            metrics=metrics,
            classification=classification,
            category_breakdown=category_breakdown,
            repository_classifications=repository_classifications,
            tools=tools,
            models=models,
            tool_profile=tool_profile,
        )

        return {
            "user_name": metrics["resolved_user_name"],
            "user_email": metrics["user_email"],
            "classification": classification,
            "primary_category": classification,
            "total_sessions": metrics["total_sessions"],
            "total_commands": metrics["total_commands"],
            "unique_repositories": metrics["unique_repositories"],
            "total_cost": metrics["total_cost"],
            "total_prompts": metrics["total_prompts"],
            "net_lines": metrics["net_lines"],
            "files_created": metrics["files_created"],
            "files_deleted": metrics["files_deleted"],
            "files_modified": metrics["files_modified"],
            "active_days": metrics["active_days"],
            "avg_session_duration_min": metrics["avg_session_duration_min"],
            "prompts_per_session": metrics["prompts_per_session"],
            "est_monthly_20d": metrics["est_monthly_20d"],
            "is_multi_category": len(category_breakdown) > 1,
            "category_diversity_score": CLIClassificationEngine._calculate_cli_category_diversity_score(
                category_breakdown
            ),
            "rule_reasons": CLIClassificationEngine._build_cli_rule_reasons(
                repositories=collections["repositories"],
                branches=collections["branches_used"],
                total_cost=metrics["total_cost"],
                total_sessions=metrics["total_sessions"],
                active_days=metrics["active_days"],
                net_lines=metrics["net_lines"],
            ),
            "unique_projects": collections["unique_projects"],
            "branches_used": collections["branches_used"],
            "category_breakdown": category_breakdown,
            "repository_classifications": repository_classifications,
            "tools": tools,
            "models": models,
            "tool_profile": tool_profile,
            **widgets,
        }

    def _build_cli_user_detail_key_metrics(
        self,
        *,
        total_cost: float,
        est_monthly_20d: float,
        total_sessions: int,
        total_prompts: int,
        net_lines: int,
        files_modified: int,
        active_days: int,
        avg_session_duration_min: float,
    ) -> dict:
        """Build widget-ready key metrics section for the CLI user detail modal."""
        return ResponseFormatter.format_summary_response(
            metrics=[
                {
                    "id": "total_cost",
                    "label": TOTAL_COST_LABEL,
                    "type": "number",
                    "value": total_cost,
                    "format": "currency",
                },
                {
                    "id": "est_monthly_20d",
                    "label": "Est. Monthly (20 days)",
                    "type": "number",
                    "value": est_monthly_20d,
                    "format": "currency",
                },
                {
                    "id": "total_sessions",
                    "label": "Sessions",
                    "type": "number",
                    "value": total_sessions,
                    "format": "number",
                },
                {
                    "id": "total_prompts",
                    "label": "Total Prompts",
                    "type": "number",
                    "value": total_prompts,
                    "format": "number",
                },
                {
                    "id": "net_lines",
                    "label": "Net Lines Added",
                    "type": "number",
                    "value": net_lines,
                    "format": "number",
                },
                {
                    "id": "files_modified",
                    "label": "Files Modified",
                    "type": "number",
                    "value": files_modified,
                    "format": "number",
                },
                {
                    "id": "active_days",
                    "label": "Active Days",
                    "type": "number",
                    "value": active_days,
                    "format": "number",
                },
                {
                    "id": "avg_session_duration_min",
                    "label": "Avg Session",
                    "type": "number",
                    "value": round(avg_session_duration_min * 60, 2),
                    "format": "duration",
                },
            ],
            filters_applied={},
            execution_time_ms=0.0,
        )

    def _build_cli_user_detail_workflow_metrics(
        self,
        *,
        primary_intent_label: str,
        intent_scores: dict[str, float],
    ) -> dict:
        """Build widget-ready workflow intent metrics section."""
        scores = list(intent_scores.values())
        total = sum(scores)
        strongest = max(scores) if scores else 0
        signal_strength = round(strongest / total, 4) if total > 0 else 0.0
        return ResponseFormatter.format_summary_response(
            metrics=[
                {
                    "id": "primary_intent",
                    "label": "Primary Intent",
                    "type": "string",
                    "value": primary_intent_label,
                },
                {
                    "id": "signal_strength",
                    "label": "Signal Strength",
                    "type": "number",
                    "value": signal_strength,
                    "format": "percentage",
                },
            ],
            filters_applied={},
            execution_time_ms=0.0,
        )

    def _build_cli_user_detail_classification_metrics(
        self,
        *,
        primary_category: str,
        is_multi_category: bool,
        category_diversity_score: float,
        unique_repositories: int,
    ) -> dict:
        """Build widget-ready classification metrics section."""
        return ResponseFormatter.format_summary_response(
            metrics=[
                {
                    "id": "primary_category",
                    "label": "Primary Category",
                    "type": "string",
                    "value": primary_category,
                },
                {
                    "id": "is_multi_category",
                    "label": "Multi-Category",
                    "type": "string",
                    "value": "Yes" if is_multi_category else "No",
                },
                {
                    "id": "category_diversity_score",
                    "label": "Diversity Score",
                    "type": "number",
                    "value": category_diversity_score,
                    "format": "percentage",
                },
                {
                    "id": "unique_repositories",
                    "label": "Repositories",
                    "type": "number",
                    "value": unique_repositories,
                    "format": "number",
                },
            ],
            filters_applied={},
            execution_time_ms=0.0,
        )

    def _build_cli_user_detail_tools_chart(self, tools: list[dict]) -> dict:
        """Build widget-ready tools chart section."""
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "tool_name", "label": "Tool", "type": "string"},
                {"id": "usage_count", "label": USAGE_COUNT_LABEL, "type": "number", "format": "number"},
            ],
            rows=tools[:8],
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(tools[:8]), 1),
            total_count=len(tools[:8]),
        )

    def _build_cli_user_detail_models_chart(self, models: list[dict]) -> dict:
        """Build widget-ready models chart section."""
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "model_name", "label": "Model", "type": "string"},
                {"id": "count", "label": "Requests", "type": "number", "format": "number"},
            ],
            rows=models[:8],
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(models[:8]), 1),
            total_count=len(models[:8]),
        )

    def _build_cli_user_detail_category_breakdown_chart(self, category_breakdown: list[dict]) -> dict:
        """Build widget-ready category breakdown chart section."""
        rows = [
            {
                "category": item["category"],
                "percentage": item["percentage"] / 100,
                "cost": item["cost"],
                "sessions": item["sessions"],
            }
            for item in category_breakdown
        ]
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "category", "label": "Category", "type": "string"},
                {"id": "percentage", "label": "Share", "type": "number", "format": "percentage"},
                {"id": "cost", "label": "Cost", "type": "number", "format": "currency"},
                {"id": "sessions", "label": "Sessions", "type": "number", "format": "number"},
            ],
            rows=rows,
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(rows), 1),
            total_count=len(rows),
        )

    def _build_cli_user_detail_repositories_table(self, repository_rows: list[dict]) -> dict:
        """Build widget-ready repositories table section."""
        rows = [
            {
                "repository": row["repository"],
                "classification": row["classification"],
                "cost": row["cost"],
                "sessions": row["sessions"],
                "net_lines": row["net_lines"],
                "branches": row.get("branches", []),
            }
            for row in repository_rows
        ]
        return ResponseFormatter.format_tabular_response(
            columns=[
                {"id": "repository", "label": "Repository", "type": "string"},
                {"id": "classification", "label": "Category", "type": "string"},
                {"id": "cost", "label": "Cost", "type": "number", "format": "currency"},
                {"id": "sessions", "label": "Sessions", "type": "number", "format": "number"},
                {"id": "net_lines", "label": NET_LINES_LABEL, "type": "number", "format": "number"},
            ],
            rows=rows,
            filters_applied={},
            execution_time_ms=0.0,
            page=0,
            per_page=max(len(rows), 1),
            total_count=len(rows),
        )

    def _get_cli_insights_weekday_pattern_columns(self) -> list[dict]:
        return [
            {"id": "weekday", "label": "Weekday", "type": "string"},
            {"id": "activity_count", "label": "Activity Count", "type": "number"},
        ]

    def _get_cli_insights_hourly_usage_columns(self) -> list[dict]:
        return [
            {"id": "hour", "label": "Hour", "type": "number"},
            {"id": "activity_count", "label": "Activity Count", "type": "number"},
        ]

    def _get_cli_insights_session_depth_columns(self) -> list[dict]:
        return [
            {"id": "range", "label": "Range", "type": "string"},
            {"id": "count", "label": "Count", "type": "number"},
        ]

    def _get_cli_insights_user_classification_columns(self) -> list[dict]:
        return [
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "user_count", "label": "User Count", "type": "number"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
            {"id": "avg_cost", "label": "Avg Cost", "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_top_users_by_cost_columns(self) -> list[dict]:
        return [
            {"id": "user_id", "label": "User ID", "type": "string"},
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "user_email", "label": "Email", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_top_spenders_columns(self) -> list[dict]:
        return [
            {"id": "rank", "label": "#", "type": "number"},
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_sessions", "label": "Sessions", "type": "number"},
            {"id": "net_lines", "label": NET_LINES_LABEL, "type": "number"},
            {"id": "total_cost", "label": "Cost", "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_all_users_columns(self) -> list[dict]:
        return [
            {"id": "user_name", "label": "User", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_sessions", "label": "Sessions", "type": "number"},
            {"id": "total_lines_added", "label": "Lines Added", "type": "number"},
            {"id": "total_lines_removed", "label": "Lines Removed", "type": "number"},
            {"id": "net_lines", "label": NET_LINES_LABEL, "type": "number"},
            {"id": "total_cost", "label": "Cost", "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_project_classification_columns(self) -> list[dict]:
        return [
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "project_count", "label": "Project Count", "type": "number"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
        ]

    def _get_cli_insights_top_projects_by_cost_columns(self) -> list[dict]:
        return [
            {"id": "project_name", "label": "Project", "type": "string"},
            {"id": "project_type", "label": "Project Type", "type": "string"},
            {"id": "classification", "label": "Classification", "type": "string"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
        ]

    # ------------------------------------------------------------------
    # Enriched-user dimension widgets (primary_skill / country / city / job_title)
    # ------------------------------------------------------------------

    async def _aggregate_user_rows_by_enrichment_field(
        self,
        field: str,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> list[dict]:
        """Return per-dimension aggregation rows joined with user enrichment data.

        Steps:
        1. Fetch per-user ES rows (email, cost) via a lightweight aggregation.
        2. Batch-fetch enrichment records from PostgreSQL for those emails.
        3. Group by *field* (primary_skill / country / city / job_title); users without an
           enrichment record land in the "No HR Data" bucket.
        """
        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        query = self._pipeline._build_query(start_dt, end_dt, users, projects, None)
        result = await self.repository.execute_aggregation_query(self._build_cli_insights_enrichment_aggregation(query))
        user_rows = [
            {
                "user_email": self._extract_top_metric(b, "user_email", USER_EMAIL_KEYWORD_FIELD),
                "total_cost": round(b.get("cost_bucket", {}).get("total_cost", {}).get("value", 0) or 0, 2),
            }
            for b in result.get("aggregations", {}).get("users", {}).get("buckets", [])
        ]
        emails = [r["user_email"] for r in user_rows if r.get("user_email")]

        async with get_async_session() as session:
            enrichment_map = await user_enrichment_repository.get_by_emails(session, emails)

        grouped: dict[str, dict] = defaultdict(lambda: {"user_count": 0, "total_cost": 0.0})
        for row in user_rows:
            email = (row.get("user_email") or "").lower()
            enrichment = enrichment_map.get(email) or enrichment_map.get(
                email.removesuffix(CLIClassificationEngine.CODEMIE_CLI_EMAIL_SUFFIX)
            )

            dimension_value = (
                getattr(enrichment, field, None) if enrichment else None
            ) or CLIClassificationEngine.NO_HR_DATA_LABEL

            grouped[dimension_value]["user_count"] += 1
            grouped[dimension_value]["total_cost"] = round(
                grouped[dimension_value]["total_cost"] + row.get("total_cost", 0.0), 2
            )

        return [
            {field: key, "user_count": val["user_count"], "total_cost": val["total_cost"]}
            for key, val in sorted(grouped.items(), key=lambda kv: kv[1]["total_cost"], reverse=True)
        ]

    async def get_cli_insights_by_enriched_user(
        self,
        scope: EnrichedUserScope,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 50,
        top_n: int | None = None,
    ) -> dict:
        """Users and cost aggregated by an enriched user dimension (country / city / job_title / primary_skill)."""
        rows = await self._aggregate_user_rows_by_enrichment_field(
            scope.value, time_period, start_date, end_date, users, projects
        )
        if top_n is not None:
            rows = rows[:top_n]
        label = CLIClassificationEngine.ENRICHED_SCOPE_LABELS[scope]
        columns = [
            {"id": scope.value, "label": label, "type": "string"},
            {"id": "user_count", "label": USAGE_COUNT_LABEL, "type": "number"},
            {"id": "total_cost", "label": TOTAL_COST_LABEL, "type": "number", "format": "currency"},
        ]
        return self._format_custom_tabular_response(
            rows=rows,
            columns=columns,
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )
