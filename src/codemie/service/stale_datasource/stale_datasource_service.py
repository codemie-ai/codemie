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

import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta, UTC

from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select, or_

from codemie.agents.utils import OPEN_AI_TOOL_NAME_LIMIT, adapt_tool_name, generate_tool_hash
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger, config
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.models.index import IndexInfo, LifecycleState
from codemie.service.analytics.metric_names import MetricName
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService
from codemie.service.stale_datasource.index_group_ordering import order_index_groups


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

_RECHECK_BOUND_FACTOR = 5


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
            "errors": 0,
        }

        try:
            # Get candidate datasources for staleness check
            candidates = await self._get_candidate_datasources()
            stats["total_evaluated"] = len(candidates)

            logger.info(f"Evaluating {len(candidates)} candidate datasources")

            # Fetch all available activity signals from Elasticsearch
            lifecycle_map, tool_map = await self._fetch_activity_signals(candidates)

            # Evaluate each datasource and mark if stale
            for datasource in candidates:
                try:
                    if self._evaluate_staleness(datasource, lifecycle_map, tool_map):
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

            if config.STALE_DATASOURCE_DELETION_ENABLED:
                deletion_stats = await self._delete_stale_indexes()
                stats.update(deletion_stats)

            logger.info(
                f"Stale datasource detection completed: "
                f"{stats['newly_marked_stale']} newly marked stale, "
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
        after_key: dict | None = None
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

    async def _fetch_lifecycle_metrics(self, datasources: list[IndexInfo]) -> dict[str, datetime | None]:
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

        usage_map: dict[str, datetime | None] = {}
        for bucket in buckets:
            key = bucket["key"]
            ds_id = lookup.get((key.get("project", ""), key.get("repo_name", ""), key.get("datasource_type", "")))
            if not ds_id:
                continue
            ts = bucket.get("last_seen", {}).get("value_as_string")
            if ts:
                usage_map[ds_id] = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)

        return usage_map

    async def _fetch_tool_usage_metrics(self, datasources: list[IndexInfo]) -> dict[str, datetime | None]:
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

    async def _fetch_activity_signals(
        self, datasources: list[IndexInfo]
    ) -> tuple[dict[str, datetime | None], dict[str, datetime | None]]:
        lifecycle_map = await self._fetch_lifecycle_metrics(datasources)
        tool_map = await self._fetch_tool_usage_metrics(datasources)
        return lifecycle_map, tool_map

    def _evaluate_staleness(
        self,
        datasource: IndexInfo,
        lifecycle_map: dict[str, datetime | None],
        tool_map: dict[str, datetime | None],
    ) -> bool:
        update_ts = datasource.update_date
        if update_ts and update_ts.tzinfo is not None:
            update_ts = update_ts.replace(tzinfo=None)
        last_activity = max(
            filter(None, [lifecycle_map.get(datasource.id), tool_map.get(datasource.id), update_ts]),
            default=None,
        )
        return self._is_datasource_stale(datasource, last_activity)

    def _is_datasource_stale(self, datasource: IndexInfo, last_activity: datetime | None) -> bool:
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

    @staticmethod
    def _idle_stale_stmt(ids: list[str] | None = None):
        statement = (
            select(IndexInfo)
            .where(IndexInfo.lifecycle_state == LifecycleState.STALE)
            .where(IndexInfo.completed == True)  # noqa: E712
            .where(
                or_(
                    IndexInfo.is_fetching == False,  # noqa: E712
                    IndexInfo.is_fetching.is_(None),
                )
            )
            .where(
                or_(
                    IndexInfo.is_queued == False,  # noqa: E712
                    IndexInfo.is_queued.is_(None),
                )
            )
            .where(
                or_(
                    IndexInfo.error == False,  # noqa: E712
                    IndexInfo.error.is_(None),
                )
            )
        )
        if ids is not None:
            statement = statement.where(IndexInfo.id.in_(ids))
        return statement

    async def _delete_stale_indexes(self) -> dict:
        """Delete ES indexes of idle STALE datasources, oldest group first, re-checking each group.

        Groups are walked lazily: right before a group's index would be deleted its rows are
        re-selected and re-checked against the activity signals. Recently active rows return to
        ACTIVE and keep the index; a group whose rows are all inactive has its index deleted and
        its rows archived. One commit per group that wrote anything.
        """
        stats = {
            "stale_rows_swept": 0,
            "indexes_deleted": 0,
            "datasources_archived": 0,
            "skipped_shared_index": 0,
            "deletion_errors": 0,
            "deletion_capped": False,
            "reactivated": 0,
            "recheck_errors": 0,
        }

        stale_result = await self.session.execute(self._idle_stale_stmt())
        stale_rows = list(stale_result.scalars().all())
        stats["stale_rows_swept"] = len(stale_rows)

        if not stale_rows:
            return stats

        active_stmt = select(IndexInfo).where(IndexInfo.lifecycle_state == LifecycleState.ACTIVE)
        active_result = await self.session.execute(active_stmt)
        active_index_names = {row.get_index_identifier() for row in active_result.scalars().all()}

        candidates: dict[str, list[IndexInfo]] = defaultdict(list)
        for row in stale_rows:
            index_name = row.get_index_identifier()
            if index_name in active_index_names:
                stats["skipped_shared_index"] += 1
                logger.warning(
                    f"Skipping stale datasource {row.id} ({row.project_name}/{row.repo_name}): "
                    f"index '{index_name}' still used by an ACTIVE datasource"
                )
                continue
            candidates[index_name].append(row)

        if not candidates:
            return stats

        groups = order_index_groups(candidates)
        remaining_rows = [row for rows in candidates.values() for row in rows]
        lifecycle_map, tool_map = await self._fetch_activity_signals(remaining_rows)

        es_client = ElasticSearchClient.get_client()
        limit = config.STALE_DATASOURCE_MAX_DELETIONS_PER_RUN

        bound = _RECHECK_BOUND_FACTOR * limit

        for visited, (index_name, row_ids) in enumerate(groups):
            if stats["indexes_deleted"] >= limit or visited >= bound:
                stats["deletion_capped"] = True
                logger.warning(
                    f"Stale datasource deletion capped after {stats['indexes_deleted']} deleted indexes "
                    f"and {visited} visited groups. Remainder will be processed in subsequent runs."
                )
                break
            await self._process_stale_group(index_name, row_ids, (lifecycle_map, tool_map), es_client, stats)

        return stats

    def _recheck_rows(
        self,
        rows: list[IndexInfo],
        lifecycle_map: dict[str, datetime | None],
        tool_map: dict[str, datetime | None],
        stats: dict,
    ) -> tuple[list[str], bool]:
        active_ids: list[str] = []
        errored = False
        for row in rows:
            try:
                if not self._evaluate_staleness(row, lifecycle_map, tool_map):
                    active_ids.append(row.id)
            except Exception as e:
                errored = True
                stats["recheck_errors"] += 1
                logger.error(f"Error re-checking datasource {row.id}: {e}", exc_info=True)
        return active_ids, errored

    async def _process_stale_group(
        self,
        index_name: str,
        row_ids: list[str],
        signals: tuple[dict[str, datetime | None], dict[str, datetime | None]],
        es_client,
        stats: dict,
    ) -> None:
        lifecycle_map, tool_map = signals
        result = await self.session.execute(self._idle_stale_stmt(row_ids))
        rows = list(result.scalars().all())

        if len(rows) < len(row_ids):
            logger.warning(
                f"Stale datasource group for ES index '{index_name}' changed during the run "
                f"({len(rows)} of {len(row_ids)} rows still idle STALE), keeping the index"
            )
            return

        active_ids, errored = self._recheck_rows(rows, lifecycle_map, tool_map, stats)
        archive_ids = [row.id for row in rows]
        metric_attributes = [
            {"project": row.project_name, "repo_name": row.repo_name, "datasource_type": row.index_type} for row in rows
        ]
        label = f"{rows[0].project_name}/{rows[0].repo_name}"

        if active_ids:
            reactivated = await self.session.execute(
                sa_update(IndexInfo)
                .where(IndexInfo.id.in_(active_ids))
                .where(IndexInfo.lifecycle_state == LifecycleState.STALE)
                .values(lifecycle_state=LifecycleState.ACTIVE, marked_stale_at=None)
            )
            stats["reactivated"] += reactivated.rowcount
            await self.session.commit()
            logger.info(
                f"Reactivated recently active datasource(s) {active_ids}; keeping ES index '{index_name}' ({label})"
            )
            return

        if errored:
            logger.warning(f"Keeping ES index '{index_name}' ({label}): a datasource failed its re-check")
            return

        try:
            await asyncio.to_thread(es_client.indices.delete, index=index_name, ignore_unavailable=True)
        except Exception as e:
            stats["deletion_errors"] += 1
            logger.error(f"Failed to delete ES index '{index_name}': {e}", exc_info=True)
            return

        await self.session.execute(
            sa_update(IndexInfo)
            .where(IndexInfo.id.in_(archive_ids))
            .where(IndexInfo.lifecycle_state == LifecycleState.STALE)
            .values(lifecycle_state=LifecycleState.ARCHIVED)
        )
        stats["indexes_deleted"] += 1
        stats["datasources_archived"] += len(archive_ids)
        await self.session.commit()
        for attributes in metric_attributes:
            AgentMonitoringService.send_count_metric(
                name=MetricName.STALE_DATASOURCE_INDEX_DELETED.value,
                attributes=attributes,
            )
        logger.info(f"Deleted ES index '{index_name}' for datasource(s) {archive_ids} ({label})")
