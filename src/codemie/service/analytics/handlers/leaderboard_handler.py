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

"""Leaderboard analytics handler.

Reads pre-computed leaderboard data from PostgreSQL and formats responses
using the standard analytics response contract (SummariesResponse, TabularResponse).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import get_async_session
from codemie.configs import logger
from codemie.repository.leaderboard_repository import leaderboard_repository
from codemie.rest_api.models.leaderboard import LeaderboardEntry, LeaderboardSnapshot
from codemie.rest_api.security.user import User
from codemie.service.leaderboard.config import (
    DIMENSIONS,
    SNAPSHOT_TYPE_ROLLING,
    VIEW_CURRENT,
    VIEW_MONTHLY,
    VIEW_QUARTERLY,
    VIEW_TO_SNAPSHOT_TYPE,
)


def _snapshot_metadata(snapshot: LeaderboardSnapshot | None) -> dict:
    if not snapshot:
        return {}
    return {
        "snapshot_id": snapshot.id,
        "snapshot_type": snapshot.snapshot_type,
        "season_key": snapshot.season_key,
        "period_label": snapshot.period_label,
        "period_start": snapshot.period_start.isoformat() if snapshot.period_start else None,
        "period_end": snapshot.period_end.isoformat() if snapshot.period_end else None,
        "period_days": snapshot.period_days,
        "is_final": snapshot.is_final,
        "status": snapshot.status,
        "completed_at": snapshot.completed_at.isoformat() if snapshot.completed_at else None,
        "comparison_snapshot_id": snapshot.comparison_snapshot_id,
    }


def _metadata(
    execution_time_ms: float,
    *,
    filters: dict | None = None,
    snapshot: LeaderboardSnapshot | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    snapshot_meta = _snapshot_metadata(snapshot)
    return {
        "timestamp": now,
        "data_as_of": snapshot_meta.get("completed_at") or now,
        "filters_applied": filters or {},
        "execution_time_ms": round(execution_time_ms, 2),
        "snapshot": snapshot_meta,
    }


def _pagination(page: int, per_page: int, total: int) -> dict:
    return {
        "page": page,
        "per_page": per_page,
        "total_count": total,
        "has_more": (page + 1) * per_page < total,
    }


_background_tasks: set[asyncio.Task] = set()
SNAPSHOT_COLUMNS = [
    {"id": "id", "label": "ID", "type": "string"},
    {"id": "snapshot_type", "label": "Type", "type": "string"},
    {"id": "season_key", "label": "Season Key", "type": "string"},
    {"id": "period_label", "label": "Period", "type": "string"},
    {"id": "period_start", "label": "Period Start", "type": "date"},
    {"id": "period_end", "label": "Period End", "type": "date"},
    {"id": "period_days", "label": "Days", "type": "number"},
    {"id": "is_final", "label": "Final", "type": "boolean"},
    {"id": "total_users", "label": "Users", "type": "number"},
    {"id": "status", "label": "Status", "type": "string"},
    {"id": "source_run_type", "label": "Run Type", "type": "string"},
    {"id": "created_at", "label": "Created", "type": "date"},
    {"id": "completed_at", "label": "Completed", "type": "date"},
]
SEASON_COLUMNS = [
    {"id": "season_key", "label": "Season Key", "type": "string"},
    {"id": "period_label", "label": "Period", "type": "string"},
    {"id": "snapshot_id", "label": "Snapshot ID", "type": "string"},
    {"id": "period_start", "label": "Period Start", "type": "date"},
    {"id": "period_end", "label": "Period End", "type": "date"},
    {"id": "total_users", "label": "Users", "type": "number"},
    {"id": "completed_at", "label": "Completed", "type": "date"},
]


class LeaderboardHandler:
    """Handler for leaderboard analytics endpoints. Reads from PostgreSQL."""

    def __init__(self, user: User) -> None:
        self._repository = leaderboard_repository
        self._user = user

    async def _resolve_snapshot(
        self,
        session: AsyncSession,
        *,
        snapshot_id: str | None,
        view: str | None,
        season_key: str | None,
    ) -> LeaderboardSnapshot | None:
        if snapshot_id:
            return await self._repository.get_snapshot_by_id(session, snapshot_id)

        effective_view = view or VIEW_CURRENT
        snapshot_type = VIEW_TO_SNAPSHOT_TYPE.get(effective_view, SNAPSHOT_TYPE_ROLLING)

        if season_key and effective_view in (VIEW_MONTHLY, VIEW_QUARTERLY):
            return await self._repository.get_snapshot_by_type_and_key(
                session,
                snapshot_type,
                season_key,
                status="completed",
                final_only=True,
            )

        is_final = effective_view != VIEW_CURRENT
        return await self._repository.get_latest_snapshot_by_type(
            session,
            snapshot_type,
            status="completed",
            is_final=is_final,
        )

    async def get_leaderboard_summary(
        self,
        snapshot_id: str | None = None,
        *,
        view: str | None = None,
        season_key: str | None = None,
    ) -> dict:
        start = monotonic()
        filters = {"view": view or VIEW_CURRENT, "season_key": season_key, "snapshot_id": snapshot_id}
        async with get_async_session() as session:
            snapshot = await self._resolve_snapshot(session, snapshot_id=snapshot_id, view=view, season_key=season_key)
            if not snapshot:
                return {
                    "data": {"metrics": []},
                    "metadata": _metadata((monotonic() - start) * 1000, filters=filters),
                }

            from codemie.service.leaderboard.framework_metadata import get_framework_metadata

            tiers = await self._repository.get_tier_distribution(session, snapshot.id)
            tier_map = {t["tier_name"]: t["user_count"] for t in tiers}

            top, avg_score = await asyncio.gather(
                self._repository.get_top_entries(session, snapshot.id, limit=1),
                self._repository.get_average_score(session, snapshot.id),
            )
            top_score = top[0].total_score if top else 0.0

            metrics = [
                {
                    "id": "total_users",
                    "label": "Total Users",
                    "type": "number",
                    "value": snapshot.total_users,
                    "format": "number",
                },
                {
                    "id": "avg_score",
                    "label": "Average Score",
                    "type": "number",
                    "value": avg_score if avg_score is not None else 0,
                    "format": "number",
                },
            ]
            for tier_def in get_framework_metadata().get("tiers", []):
                metrics.append(
                    {
                        "id": f"{tier_def['name']}_count",
                        "label": tier_def.get("plural_label", tier_def.get("label", tier_def["name"].capitalize())),
                        "type": "number",
                        "value": tier_map.get(tier_def["name"], 0),
                        "format": "number",
                    }
                )
            metrics.extend(
                [
                    {"id": "top_score", "label": "Top Score", "type": "number", "value": top_score, "format": "number"},
                    {"id": "period_label", "label": "Period", "type": "string", "value": snapshot.period_label},
                    {
                        "id": "last_computed_at",
                        "label": "Last Computed",
                        "type": "date",
                        "value": snapshot.completed_at.isoformat() if snapshot.completed_at else None,
                    },
                ]
            )

        elapsed = (monotonic() - start) * 1000
        return {"data": {"metrics": metrics}, "metadata": _metadata(elapsed, filters=filters, snapshot=snapshot)}

    async def get_leaderboard_entries(
        self,
        snapshot_id: str | None = None,
        tier: str | None = None,
        page: int = 0,
        per_page: int = 20,
        search: str | None = None,
        intent: str | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
        *,
        view: str | None = None,
        season_key: str | None = None,
    ) -> dict:
        start = monotonic()
        filters = {
            "view": view or VIEW_CURRENT,
            "season_key": season_key,
            "snapshot_id": snapshot_id,
            "tier": tier,
            "search": search,
            "intent": intent,
            "sort_by": sort_by,
            "sort_order": sort_order,
        }
        async with get_async_session() as session:
            snapshot = await self._resolve_snapshot(session, snapshot_id=snapshot_id, view=view, season_key=season_key)
            if not snapshot:
                return self._empty_tabular(start, filters=filters)

            entries, total = await self._repository.get_entries(
                session, snapshot.id, page, per_page, tier, search, intent, sort_by, sort_order
            )
            comparison_map = await self._get_comparison_entries(session, snapshot, [entry.user_id for entry in entries])

            columns = [
                {"id": "rank", "label": "#", "type": "number"},
                {"id": "previous_rank", "label": "Prev Rank", "type": "number"},
                {"id": "rank_delta", "label": "Rank Delta", "type": "number"},
                {"id": "user_id", "label": "User ID", "type": "string"},
                {"id": "user_name", "label": "User", "type": "string"},
                {"id": "user_email", "label": "Email", "type": "string"},
                {"id": "total_score", "label": "Score", "type": "number", "format": "number"},
                {"id": "previous_score", "label": "Prev Score", "type": "number", "format": "number"},
                {"id": "score_delta", "label": "Score Delta", "type": "number", "format": "number"},
                {"id": "tier_name", "label": "Tier", "type": "string"},
            ]
            for dim in DIMENSIONS:
                columns.append(
                    {
                        "id": f"{dim.id}_score",
                        "label": f"{dim.label}: {dim.name}",
                        "type": "number",
                        "format": "number",
                    }
                )
            columns.extend(
                [
                    {"id": "total_spend", "label": "Total Spend", "type": "number", "format": "currency"},
                    {"id": "usage_intent", "label": "Intent", "type": "string"},
                ]
            )

            rows = [self._entry_to_row(e, comparison_map.get(e.user_id)) for e in entries]

        elapsed = (monotonic() - start) * 1000
        return {
            "data": {"columns": columns, "rows": rows},
            "metadata": _metadata(elapsed, filters=filters, snapshot=snapshot),
            "pagination": _pagination(page, per_page, total),
        }

    async def get_leaderboard_user_detail(
        self,
        user_id: str,
        snapshot_id: str | None = None,
        *,
        view: str | None = None,
        season_key: str | None = None,
    ) -> dict:
        from codemie.service.leaderboard.framework_metadata import (
            get_dimension_metadata,
            get_intent_by_id,
            get_tier_by_name,
        )

        start = monotonic()
        filters = {
            "view": view or VIEW_CURRENT,
            "season_key": season_key,
            "snapshot_id": snapshot_id,
            "user_id": user_id,
        }
        async with get_async_session() as session:
            snapshot = await self._resolve_snapshot(session, snapshot_id=snapshot_id, view=view, season_key=season_key)
            if not snapshot:
                return {"data": {}, "metadata": _metadata((monotonic() - start) * 1000, filters=filters)}

            entry = await self._repository.get_entry_by_user(session, snapshot.id, user_id)
            if not entry:
                return {
                    "data": {},
                    "metadata": _metadata((monotonic() - start) * 1000, filters=filters, snapshot=snapshot),
                }

            comparison_entry = None
            if snapshot.comparison_snapshot_id:
                comparison_entry = await self._repository.get_entry_by_user(
                    session, snapshot.comparison_snapshot_id, user_id
                )

            enriched_dimensions = self._enrich_dimensions(entry.dimensions or [], get_dimension_metadata)
            tier_meta = get_tier_by_name(entry.tier_name)
            intent_meta = get_intent_by_id(entry.usage_intent or "explorer")

            data = {
                "user_id": entry.user_id,
                "user_name": entry.user_name,
                "user_email": entry.user_email,
                "rank": entry.rank,
                "total_score": entry.total_score,
                "comparison": self._comparison_payload(entry, comparison_entry),
                "tier": {
                    "name": entry.tier_name,
                    "label": tier_meta.get("label", entry.tier_name.capitalize()),
                    "level": entry.tier_level,
                    "color": tier_meta.get("color", "#6b7280"),
                    "description": tier_meta.get("description", ""),
                },
                "intent": {
                    "id": entry.usage_intent or "explorer",
                    "label": intent_meta.get("label", ""),
                    "emoji": intent_meta.get("emoji", ""),
                    "color": intent_meta.get("color", "#6b7280"),
                    "description": intent_meta.get("description", ""),
                },
                "projects": entry.projects or [],
                "summary_metrics": entry.summary_metrics or {},
                "dimensions": enriched_dimensions,
            }

        elapsed = (monotonic() - start) * 1000
        return {"data": data, "metadata": _metadata(elapsed, filters=filters, snapshot=snapshot)}

    async def get_leaderboard_tier_distribution(
        self,
        snapshot_id: str | None = None,
        *,
        view: str | None = None,
        season_key: str | None = None,
    ) -> dict:
        start = monotonic()
        filters = {"view": view or VIEW_CURRENT, "season_key": season_key, "snapshot_id": snapshot_id}
        async with get_async_session() as session:
            snapshot = await self._resolve_snapshot(session, snapshot_id=snapshot_id, view=view, season_key=season_key)
            if not snapshot:
                return self._empty_tabular(start, filters=filters)

            from codemie.service.leaderboard.framework_metadata import get_tier_by_name

            tiers = await self._repository.get_tier_distribution(session, snapshot.id)
            total_users = sum(t["user_count"] for t in tiers)

            columns = [
                {"id": "tier_name", "label": "Tier", "type": "string"},
                {"id": "tier_level", "label": "Level", "type": "number"},
                {"id": "user_count", "label": "Users", "type": "number"},
                {"id": "percentage", "label": "Percentage", "type": "number", "format": "percent"},
                {"id": "color", "label": "Color", "type": "string"},
            ]
            rows = [
                {
                    "tier_name": t["tier_name"],
                    "tier_level": t["tier_level"],
                    "user_count": t["user_count"],
                    "percentage": round(t["user_count"] / max(total_users, 1) * 100, 1),
                    "color": get_tier_by_name(t["tier_name"]).get("color", "#6b7280"),
                }
                for t in tiers
            ]

        elapsed = (monotonic() - start) * 1000
        return {
            "data": {"columns": columns, "rows": rows},
            "metadata": _metadata(elapsed, filters=filters, snapshot=snapshot),
        }

    async def get_leaderboard_score_distribution(
        self,
        snapshot_id: str | None = None,
        *,
        view: str | None = None,
        season_key: str | None = None,
    ) -> dict:
        start = monotonic()
        filters = {"view": view or VIEW_CURRENT, "season_key": season_key, "snapshot_id": snapshot_id}
        async with get_async_session() as session:
            snapshot = await self._resolve_snapshot(session, snapshot_id=snapshot_id, view=view, season_key=season_key)
            if not snapshot:
                return self._empty_tabular(start, filters=filters)

            bins = await self._repository.get_score_distribution(session, snapshot.id)
            columns = [
                {"id": "range", "label": "Score Range", "type": "string"},
                {"id": "count", "label": "Users", "type": "number"},
            ]

        elapsed = (monotonic() - start) * 1000
        return {
            "data": {"columns": columns, "rows": bins},
            "metadata": _metadata(elapsed, filters=filters, snapshot=snapshot),
        }

    async def get_leaderboard_dimension_breakdown(
        self,
        snapshot_id: str | None = None,
        *,
        view: str | None = None,
        season_key: str | None = None,
    ) -> dict:
        start = monotonic()
        filters = {"view": view or VIEW_CURRENT, "season_key": season_key, "snapshot_id": snapshot_id}
        async with get_async_session() as session:
            snapshot = await self._resolve_snapshot(session, snapshot_id=snapshot_id, view=view, season_key=season_key)
            if not snapshot:
                return self._empty_tabular(start, filters=filters)

            dim_ids = [d.id for d in DIMENSIONS]
            dim_averages = await self._repository.get_dimension_averages(session, snapshot.id, dim_ids)

            columns = [
                {"id": "dimension_id", "label": "Dimension", "type": "string"},
                {"id": "dimension_label", "label": "Label", "type": "string"},
                {"id": "weight", "label": "Weight", "type": "number", "format": "percent"},
                {"id": "avg_score", "label": "Avg Score", "type": "number", "format": "number"},
            ]
            rows = [
                {
                    "dimension_id": dim_def.id,
                    "dimension_label": dim_def.name,
                    "weight": dim_def.weight,
                    "avg_score": dim_averages.get(dim_def.id, 0.0),
                }
                for dim_def in DIMENSIONS
            ]

        elapsed = (monotonic() - start) * 1000
        return {
            "data": {"columns": columns, "rows": rows},
            "metadata": _metadata(elapsed, filters=filters, snapshot=snapshot),
        }

    async def get_leaderboard_top_performers(
        self,
        snapshot_id: str | None = None,
        limit: int = 3,
        *,
        view: str | None = None,
        season_key: str | None = None,
    ) -> dict:
        start = monotonic()
        filters = {"view": view or VIEW_CURRENT, "season_key": season_key, "snapshot_id": snapshot_id, "limit": limit}
        async with get_async_session() as session:
            snapshot = await self._resolve_snapshot(session, snapshot_id=snapshot_id, view=view, season_key=season_key)
            if not snapshot:
                return self._empty_tabular(start, filters=filters)

            entries = await self._repository.get_top_entries(session, snapshot.id, limit)
            comparison_map = await self._get_comparison_entries(session, snapshot, [entry.user_id for entry in entries])

            columns = [
                {"id": "rank", "label": "#", "type": "number"},
                {"id": "previous_rank", "label": "Prev Rank", "type": "number"},
                {"id": "rank_delta", "label": "Rank Delta", "type": "number"},
                {"id": "user_name", "label": "User", "type": "string"},
                {"id": "total_score", "label": "Score", "type": "number", "format": "number"},
                {"id": "previous_score", "label": "Prev Score", "type": "number", "format": "number"},
                {"id": "score_delta", "label": "Score Delta", "type": "number", "format": "number"},
                {"id": "tier_name", "label": "Tier", "type": "string"},
                {"id": "dimensions", "label": "Dimensions", "type": "json"},
                {"id": "summary_metrics", "label": "Metrics", "type": "json"},
            ]
            rows = [
                {
                    "rank": e.rank,
                    "previous_rank": comparison_map.get(e.user_id).rank if comparison_map.get(e.user_id) else None,
                    "rank_delta": self._rank_delta(
                        e.rank, comparison_map.get(e.user_id).rank if comparison_map.get(e.user_id) else None
                    ),
                    "user_name": e.user_name,
                    "total_score": e.total_score,
                    "previous_score": comparison_map.get(e.user_id).total_score
                    if comparison_map.get(e.user_id)
                    else None,
                    "score_delta": self._score_delta(
                        e.total_score,
                        comparison_map.get(e.user_id).total_score if comparison_map.get(e.user_id) else None,
                    ),
                    "tier_name": e.tier_name,
                    "dimensions": e.dimensions,
                    "summary_metrics": e.summary_metrics,
                }
                for e in entries
            ]

        elapsed = (monotonic() - start) * 1000
        return {
            "data": {"columns": columns, "rows": rows},
            "metadata": _metadata(elapsed, filters=filters, snapshot=snapshot),
        }

    async def get_leaderboard_snapshots(
        self,
        page: int = 0,
        per_page: int = 10,
        *,
        view: str | None = None,
        status: str | None = None,
        is_final: bool | None = None,
    ) -> dict:
        start = monotonic()
        snapshot_type = VIEW_TO_SNAPSHOT_TYPE.get(view) if view else None
        filters = {"view": view, "snapshot_type": snapshot_type, "status": status, "is_final": is_final}
        async with get_async_session() as session:
            snapshots, total = await self._repository.list_snapshots(
                session,
                page,
                per_page,
                snapshot_type=snapshot_type,
                status=status,
                is_final=is_final,
            )
            columns = SNAPSHOT_COLUMNS
            rows = [self._snapshot_to_row(snapshot) for snapshot in snapshots]

        return {
            "data": {"columns": columns, "rows": rows},
            "metadata": _metadata((monotonic() - start) * 1000, filters=filters),
            "pagination": _pagination(page, per_page, total),
        }

    async def get_leaderboard_seasons(self, view: str, page: int = 0, per_page: int = 50) -> dict:
        from codemie.core.exceptions import ValidationException

        start = monotonic()
        snapshot_type = VIEW_TO_SNAPSHOT_TYPE.get(view)
        if not snapshot_type:
            valid = ", ".join(VIEW_TO_SNAPSHOT_TYPE)
            raise ValidationException(f"Invalid leaderboard view: {view!r}. Must be one of: {valid}")
        filters = {"view": view, "snapshot_type": snapshot_type, "is_final": True}
        async with get_async_session() as session:
            snapshots, total = await self._repository.list_snapshots(
                session,
                page,
                per_page,
                snapshot_type=snapshot_type,
                status="completed",
                is_final=True,
            )
            columns = SEASON_COLUMNS
            rows = [self._season_to_row(snapshot) for snapshot in snapshots]

        elapsed = (monotonic() - start) * 1000
        return {
            "data": {"columns": columns, "rows": rows},
            "metadata": _metadata(elapsed, filters=filters),
            "pagination": _pagination(page, per_page, total),
        }

    def get_framework_metadata(self) -> dict:
        from codemie.service.leaderboard.framework_metadata import get_framework_metadata

        start = monotonic()
        data = get_framework_metadata()
        elapsed = (monotonic() - start) * 1000
        return {"data": data, "metadata": _metadata(elapsed)}

    async def trigger_computation(
        self,
        *,
        period_days: int = 30,
        view: str = VIEW_CURRENT,
        season_key: str | None = None,
    ) -> dict:
        from codemie.core.exceptions import ValidationException

        start = monotonic()
        snapshot_type = VIEW_TO_SNAPSHOT_TYPE.get(view, SNAPSHOT_TYPE_ROLLING)
        async with get_async_session() as session:
            running = await self._repository.get_latest_snapshot(session, status="running", snapshot_type=snapshot_type)
            if running:
                raise ValidationException(
                    f"A leaderboard computation is already running (snapshot_id={running.id}). "
                    "Please wait for it to complete before triggering a new one."
                )

        task = asyncio.create_task(self._run_computation(period_days=period_days, view=view, season_key=season_key))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        task.add_done_callback(self._computation_done_callback)
        elapsed = (monotonic() - start) * 1000
        return {
            "data": {
                "status": "started",
                "message": "Leaderboard computation triggered in background",
                "view": view,
                "season_key": season_key,
            },
            "metadata": _metadata(elapsed, filters={"view": view, "season_key": season_key}),
        }

    @staticmethod
    async def _run_computation(period_days: int, view: str, season_key: str | None) -> str:
        from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
        from codemie.service.leaderboard.leaderboard_service import LeaderboardService

        async with get_async_session() as session:
            service = LeaderboardService(session, MetricsElasticRepository())
            return await service.compute_for_view(
                view=view,
                period_days=period_days,
                season_key=season_key,
            )

    @staticmethod
    def _computation_done_callback(task: asyncio.Task) -> None:
        if task.exception():
            logger.error(f"Background leaderboard computation failed: {task.exception()}", exc_info=True)
        else:
            logger.info(f"Background leaderboard computation completed: snapshot_id={task.result()}")

    async def _get_comparison_entries(
        self,
        session: AsyncSession,
        snapshot: LeaderboardSnapshot,
        user_ids: list[str],
    ) -> dict[str, LeaderboardEntry]:
        if not snapshot.comparison_snapshot_id:
            return {}
        return await self._repository.get_entries_by_users(session, snapshot.comparison_snapshot_id, user_ids)

    @staticmethod
    def _enrich_dimensions(raw_dimensions: list[dict], get_dim_meta: Callable[[str], dict]) -> list[dict]:
        enriched = []
        for dim_data in raw_dimensions:
            dim_id = dim_data.get("id", "")
            meta = get_dim_meta(dim_id)
            comp_meta = meta.get("components", {})

            enriched_components = []
            for comp in dim_data.get("components", []):
                comp_key = comp.get("key", "")
                cm = comp_meta.get(comp_key, {})
                enriched_components.append(
                    {
                        **comp,
                        "what": cm.get("what", ""),
                        "calc": cm.get("calc", ""),
                        "evidence": cm.get("evidence", ""),
                    }
                )

            enriched.append(
                {
                    "id": dim_id,
                    "label": dim_data.get("label", meta.get("label", "")),
                    "name": meta.get("name", dim_data.get("label", "")),
                    "weight": dim_data.get("weight", meta.get("weight", 0)),
                    "score": dim_data.get("score", 0),
                    "color": meta.get("color", "#6b7280"),
                    "icon": meta.get("icon", ""),
                    "description": meta.get("description", ""),
                    "components": enriched_components,
                }
            )
        return enriched

    @classmethod
    def _entry_to_row(cls, entry: LeaderboardEntry, comparison_entry: LeaderboardEntry | None) -> dict:
        from codemie.service.leaderboard.framework_metadata import get_intent_by_id

        row: dict = {
            "rank": entry.rank,
            "previous_rank": comparison_entry.rank if comparison_entry else None,
            "rank_delta": cls._rank_delta(entry.rank, comparison_entry.rank if comparison_entry else None),
            "user_id": entry.user_id,
            "user_name": entry.user_name,
            "user_email": entry.user_email,
            "total_score": entry.total_score,
            "previous_score": comparison_entry.total_score if comparison_entry else None,
            "score_delta": cls._score_delta(
                entry.total_score, comparison_entry.total_score if comparison_entry else None
            ),
            "tier_name": entry.tier_name,
        }
        for dim_data in entry.dimensions or []:
            dim_id = dim_data.get("id")
            if dim_id:
                row[f"{dim_id}_score"] = round(float(dim_data.get("score", 0)) * 100, 1)
        row["total_spend"] = float((entry.summary_metrics or {}).get("total_spend", 0))
        row["usage_intent"] = entry.usage_intent
        intent_meta = get_intent_by_id(entry.usage_intent or "explorer")
        row["usage_intent_label"] = f"{intent_meta.get('emoji', '')} {intent_meta.get('label', '')}".strip()
        return row

    @classmethod
    def _comparison_payload(cls, entry: LeaderboardEntry, comparison_entry: LeaderboardEntry | None) -> dict:
        return {
            "previous_rank": comparison_entry.rank if comparison_entry else None,
            "rank_delta": cls._rank_delta(entry.rank, comparison_entry.rank if comparison_entry else None),
            "previous_score": comparison_entry.total_score if comparison_entry else None,
            "score_delta": cls._score_delta(
                entry.total_score, comparison_entry.total_score if comparison_entry else None
            ),
            "comparison_snapshot_id": comparison_entry.snapshot_id if comparison_entry else None,
        }

    @staticmethod
    def _rank_delta(current_rank: int, previous_rank: int | None) -> int | None:
        if previous_rank is None:
            return None
        return previous_rank - current_rank

    @staticmethod
    def _score_delta(current_score: float, previous_score: float | None) -> float | None:
        if previous_score is None:
            return None
        delta = Decimal(str(current_score)) - Decimal(str(previous_score))
        return float(delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _snapshot_to_row(snapshot: LeaderboardSnapshot) -> dict:
        return {
            "id": snapshot.id,
            "snapshot_type": snapshot.snapshot_type,
            "season_key": snapshot.season_key,
            "period_label": snapshot.period_label,
            "period_start": snapshot.period_start.isoformat() if snapshot.period_start else None,
            "period_end": snapshot.period_end.isoformat() if snapshot.period_end else None,
            "period_days": snapshot.period_days,
            "is_final": snapshot.is_final,
            "total_users": snapshot.total_users,
            "status": snapshot.status,
            "source_run_type": snapshot.source_run_type,
            "created_at": snapshot.date.isoformat() if snapshot.date else None,
            "completed_at": snapshot.completed_at.isoformat() if snapshot.completed_at else None,
        }

    @staticmethod
    def _season_to_row(snapshot: LeaderboardSnapshot) -> dict:
        return {
            "season_key": snapshot.season_key,
            "period_label": snapshot.period_label,
            "snapshot_id": snapshot.id,
            "period_start": snapshot.period_start.isoformat() if snapshot.period_start else None,
            "period_end": snapshot.period_end.isoformat() if snapshot.period_end else None,
            "total_users": snapshot.total_users,
            "completed_at": snapshot.completed_at.isoformat() if snapshot.completed_at else None,
        }

    @staticmethod
    def _empty_tabular(start_time: float, *, filters: dict | None = None) -> dict:
        elapsed = (monotonic() - start_time) * 1000
        return {
            "data": {"columns": [], "rows": []},
            "metadata": _metadata(elapsed, filters=filters),
        }
