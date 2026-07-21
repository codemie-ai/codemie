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

"""Service for detecting and marking stale datasources.

This module implements the business logic for identifying datasources
that haven't been used for a configurable period and marking them
with the appropriate lifecycle state.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, UTC
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, or_

from codemie.agents.utils import OPEN_AI_TOOL_NAME_LIMIT, adapt_tool_name, generate_tool_hash
from codemie.configs import logger, config
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.models.index import IndexInfo, LifecycleState
from codemie.service.analytics.metric_names import MetricName


_DATASOURCE_LIFECYCLE_METRICS: list[str] = MetricName.to_list(
    MetricName.DATASOURCE_TOKENS_USAGE,
    MetricName.DATASOURCE_INDEX_TOTAL,
    MetricName.DATASOURCE_INDEX_DOCUMENTS,
    MetricName.DATASOURCE_INDEX_ERRORS_TOTAL,
    MetricName.DATASOURCE_REINDEX_TOTAL,
    MetricName.DATASOURCE_REINDEX_DOCUMENTS,
    MetricName.DATASOURCE_REINDEX_ERRORS_TOTAL,
    MetricName.DATASOURCE_RESUME_TOTAL,
    MetricName.DATASOURCE_RESUME_DOCUMENTS,
    MetricName.DATASOURCE_RESUME_ERRORS_TOTAL,
    MetricName.UPDATE_DATASOURCE,
)

_TOOL_USAGE_METRICS: list[str] = MetricName.to_list(
    MetricName.CODEMIE_TOOLS_USAGE_TOTAL,
    MetricName.CODEMIE_TOOLS_USAGE_TOKENS,
)

_CODE_INDEX_TYPES: frozenset[str] = frozenset({"code", "summary", "chunk-summary"})

_CODE_TOOL_PREFIXES: list[str] = [
    "search_code_repo",
    "search_code_repo_by_path",
    "search_code_repo_v2",
    "get_repository_file_tree",
    "get_repository_file_tree_v2",
    "read_files_content",
    "read_files_content_summary",
]

_KB_TOOL_PREFIX = "search_kb"


def _legacy_adapt_tool_name(prefix: str, repo: str) -> str:
    # Naming scheme used before EPMCDME-11979 (to_snake_case collapsed runs and
    # replaced hyphens); metrics recorded back then still fall inside the
    # staleness lookback window, so both variants must be matched.
    snake = re.sub(r"[^0-9a-zA-Z]+", "_", repo).strip("_")
    full = f"{prefix}_{snake}"
    if len(full) > OPEN_AI_TOOL_NAME_LIMIT:
        full = f"{prefix}_{generate_tool_hash(repo)}"
    return full.lower()


def _compute_base_tool_names(datasource: IndexInfo) -> list[str]:
    """Expected base_tool_name values stored in codemie_metrics_logs for this datasource.

    Uses the runtime adapt_tool_name for names recorded by current monitoring
    callbacks, plus the legacy pre-EPMCDME-11979 variant for older metrics.
    """
    repo = datasource.repo_name
    prefixes = _CODE_TOOL_PREFIXES if datasource.index_type in _CODE_INDEX_TYPES else [_KB_TOOL_PREFIX]

    names: list[str] = []
    for prefix in prefixes:
        current = adapt_tool_name(f"{prefix}_{{}}", repo).lower()
        legacy = _legacy_adapt_tool_name(prefix, repo)
        for name in (current, legacy):
            if name not in names:
                names.append(name)
    return names


def _keep_latest(timestamps: dict[str, datetime], ds_id: str, dt: datetime) -> None:
    if ds_id not in timestamps or dt > timestamps[ds_id]:
        timestamps[ds_id] = dt


class StaleDatasourceService:
    """Service for detecting and marking stale datasources.

    Identifies datasources that:
    1. Have no usage metrics in Elasticsearch for X days, OR
    2. Haven't been updated in Y days (for datasources without metrics)
    3. Are not in grace period (created within Z days)

    Then marks them with lifecycle_state = "stale".
    """

    def __init__(
        self,
        session: AsyncSession,
        metrics_repository: MetricsElasticRepository,
    ):
        """Initialize service with database session and metrics repository.

        Args:
            session: AsyncIO SQLAlchemy session for database operations
            metrics_repository: Repository for querying Elasticsearch metrics
        """
        self.session = session
        self.metrics_repository = metrics_repository

    async def detect_and_mark_stale_datasources(self) -> dict:
        """Main entry point: detect and mark stale datasources.

        Returns:
            dict: Summary statistics with keys:
                - total_evaluated: Number of datasources evaluated
                - newly_marked_stale: Number newly marked as stale
                - already_stale: Number already marked stale
                - errors: Number of errors encountered
        """
        logger.info("Starting stale datasource detection")

        stats = {
            "total_evaluated": 0,
            "newly_marked_stale": 0,
            "already_stale": 0,
            "errors": 0,
        }

        try:
            # Get candidate datasources for staleness check
            candidates = await self._get_candidate_datasources()
            stats["total_evaluated"] = len(candidates)

            logger.info(f"Evaluating {len(candidates)} candidate datasources")

            # Fetch all available activity signals from Elasticsearch
            lifecycle_map = await self._fetch_lifecycle_metrics(candidates)
            tool_map = await self._fetch_tool_usage_metrics(candidates)

            # Evaluate each datasource and mark if stale
            for datasource in candidates:
                try:
                    if datasource.lifecycle_state == LifecycleState.STALE:
                        stats["already_stale"] += 1
                        continue

                    lifecycle_ts = lifecycle_map.get(datasource.id)
                    tool_ts = tool_map.get(datasource.id)
                    update_ts = datasource.update_date
                    if update_ts and update_ts.tzinfo is not None:
                        update_ts = update_ts.replace(tzinfo=None)
                    last_activity = max(filter(None, [lifecycle_ts, tool_ts, update_ts]), default=None)
                    is_stale = self._is_datasource_stale(datasource, last_activity)

                    if is_stale:
                        self._mark_as_stale(datasource)
                        stats["newly_marked_stale"] += 1
                        logger.info(
                            f"Marked datasource as stale: {datasource.repo_name} "
                            f"(project: {datasource.project_name}, id: {datasource.id})"
                        )

                except Exception as e:
                    stats["errors"] += 1
                    logger.error(
                        f"Error evaluating datasource {datasource.id}: {e}",
                        exc_info=True,
                    )

            await self.session.commit()

            logger.info(
                f"Stale datasource detection completed: "
                f"{stats['newly_marked_stale']} newly marked stale, "
                f"{stats['already_stale']} already stale, "
                f"{stats['errors']} errors"
            )

        except Exception as e:
            logger.error(f"Stale datasource detection failed: {e}", exc_info=True)
            await self.session.rollback()
            raise

        return stats

    async def _get_candidate_datasources(self) -> list[IndexInfo]:
        """Get datasources that should be evaluated for staleness.

        Includes:
        - Active datasources (lifecycle_state = 'active')
        - Completed datasources (not currently processing)
        - Not in error state
        - Not currently being fetched
        - Not in grace period (created > X days ago)

        Returns:
            List of IndexInfo objects
        """
        grace_cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=config.STALE_DATASOURCE_GRACE_DAYS)

        statement = (
            select(IndexInfo)
            .where(IndexInfo.lifecycle_state == LifecycleState.ACTIVE)
            .where(IndexInfo.completed == True)  # noqa: E712
            .where(IndexInfo.error == False)  # noqa: E712
            .where(
                or_(
                    IndexInfo.is_fetching == False,  # noqa: E712
                    IndexInfo.is_fetching.is_(None),
                )
            )
            .where(IndexInfo.date < grace_cutoff)
        )

        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def _paginate_composite_agg(
        self,
        query: dict,
        sources: list[dict],
        sub_aggs: dict,
    ) -> list[dict]:
        """Page through a composite aggregation until all buckets are retrieved.

        Raises on ES error (fail-closed): incomplete signals must abort the
        detection run, otherwise every datasource silently degrades to the
        update_date fallback and risks being mass-marked stale during an outage.
        """
        buckets: list[dict] = []
        after_key: Optional[dict] = None
        while True:
            composite: dict = {"size": config.STALE_DATASOURCE_BATCH_SIZE, "sources": sources}
            if after_key:
                composite["after"] = after_key
            body = {
                "size": 0,
                "query": query,
                "aggs": {"result": {"composite": composite, "aggs": sub_aggs}},
            }
            response = await self.metrics_repository.execute_aggregation_query(body=body, request_timeout=60)
            agg = response.get("aggregations", {}).get("result", {})
            page = agg.get("buckets", [])
            buckets.extend(page)
            after_key = agg.get("after_key")
            if not after_key or not page:
                break
        return buckets

    async def _fetch_lifecycle_metrics(self, datasources: list[IndexInfo]) -> dict[str, Optional[datetime]]:
        """Latest lifecycle-event timestamp per datasource id.

        Aggregates all datasource lifecycle metric names (index, reindex, config
        update, etc.) grouped by (project, repo_name, datasource_type) so multiple
        datasources sharing project+repo with different types are attributed
        independently.
        """
        if not datasources:
            return {}

        lookup: dict[tuple[str, str, str], str] = {
            (ds.project_name, ds.repo_name, ds.index_type): ds.id for ds in datasources
        }

        buckets = await self._paginate_composite_agg(
            query={"terms": {"metric_name.keyword": _DATASOURCE_LIFECYCLE_METRICS}},
            sources=[
                {"project": {"terms": {"field": "attributes.project.keyword"}}},
                {"repo_name": {"terms": {"field": "attributes.repo_name.keyword"}}},
                {"datasource_type": {"terms": {"field": "attributes.datasource_type.keyword"}}},
            ],
            sub_aggs={"last_seen": {"max": {"field": "@timestamp"}}},
        )

        usage_map: dict[str, Optional[datetime]] = {}
        for bucket in buckets:
            key = bucket["key"]
            ds_id = lookup.get((key.get("project", ""), key.get("repo_name", ""), key.get("datasource_type", "")))
            if not ds_id:
                continue
            ts = bucket.get("last_seen", {}).get("value_as_string")
            if ts:
                usage_map[ds_id] = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)

        return usage_map

    async def _fetch_tool_usage_metrics(self, datasources: list[IndexInfo]) -> dict[str, Optional[datetime]]:
        """Latest tool-invocation timestamp per datasource id.

        Aggregates tool-usage metrics by (base_tool_name, project) and matches
        each bucket back to candidate datasources via pre-computed expected
        names. A usage signal from the datasource's own project is preferred;
        when the same base_tool_name maps to multiple datasources (same
        repo_name in different projects) and no same-project signal exists,
        the timestamp is attributed to all of them (name-only fallback,
        fail-safe against staling an active datasource).
        """
        if not datasources:
            return {}

        tool_to_ids: dict[str, list[str]] = defaultdict(list)
        for ds in datasources:
            for tool_name in _compute_base_tool_names(ds):
                tool_to_ids[tool_name].append(ds.id)
        project_by_id = {ds.id: ds.project_name for ds in datasources}

        buckets = await self._paginate_composite_agg(
            query={"terms": {"metric_name.keyword": _TOOL_USAGE_METRICS}},
            sources=[
                {"base_tool_name": {"terms": {"field": "attributes.base_tool_name.keyword"}}},
                {"project": {"terms": {"field": "attributes.project.keyword", "missing_bucket": True}}},
            ],
            sub_aggs={"last_seen": {"max": {"field": "@timestamp"}}},
        )

        same_project: dict[str, datetime] = {}
        any_project: dict[str, datetime] = {}
        for bucket in buckets:
            ds_ids = tool_to_ids.get(bucket["key"].get("base_tool_name", "").lower())
            ts = bucket.get("last_seen", {}).get("value_as_string")
            if not ds_ids or not ts:
                continue
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
            bucket_project = bucket["key"].get("project")
            for ds_id in ds_ids:
                if bucket_project == project_by_id.get(ds_id):
                    _keep_latest(same_project, ds_id, dt)
                _keep_latest(any_project, ds_id, dt)

        return {ds_id: same_project.get(ds_id, any_ts) for ds_id, any_ts in any_project.items()}

    def _is_datasource_stale(self, datasource: IndexInfo, last_activity: Optional[datetime]) -> bool:
        """Return True if the datasource should be marked stale.

        last_activity is the most recent timestamp across all available signals
        (lifecycle events, tool invocations, and update_date — all folded in by
        the caller). Returns True unconditionally when no signal exists at all.
        """
        now = datetime.now(UTC).replace(tzinfo=None)

        if last_activity:
            if last_activity.tzinfo is not None:
                last_activity = last_activity.replace(tzinfo=None)
            return (now - last_activity).days >= config.STALE_DATASOURCE_NO_USAGE_DAYS

        logger.warning(f"Datasource {datasource.id} has no activity signal and no update_date, marking as stale")
        return True

    def _mark_as_stale(self, datasource: IndexInfo) -> None:
        """Mark a datasource as stale in the database.

        Args:
            datasource: The datasource to mark as stale
        """
        datasource.lifecycle_state = LifecycleState.STALE
        datasource.marked_stale_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(datasource)
