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

"""Leaderboard data collector.

Collects raw metrics per user from PostgreSQL and Elasticsearch, and every money figure from the
spend port. Ported from the prototype at users-leaderboard/v2/collector.py, adapted to use the
platform's async session and MetricsElasticRepository.

**Money is the one metric Elasticsearch does not supply here (AD-29).** It used to: a per-user ES
aggregation produced a total and a CLI split, a second ES aggregation produced a per-user CLI spend,
and the scorer added an ES web-spend aggregate to the first. Three aggregations for two quantities
is what `spend_service` exists to collapse, so the two figures now come from two port reads and are
never added to anything.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs.config import config
from codemie.enterprise.litellm.budget_categories import BudgetCategory, derive_category_from_user_id
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.repository.user_repository import user_repository
from codemie.service.monitoring.metrics_constants import INVOKED_BY_SCRIPT, MetricsAttributes
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.identity_bridge import aliases_for_user
from codemie.service.spend.ledger import ComposedScope, PersonTotalHolder, UserTerm
from codemie.service.spend.rendering import RenderedSpend, render_spend, time_range_or_none
from codemie.service.spend.spend_service import spend_service
from codemie.service.spend.traffic_class import TrafficClass

logger = logging.getLogger(__name__)

CLI_ASSISTANT_ID = "5a430368-9e91-4564-be20-989803bf4da2"

# Whitelist of JSONB column names allowed in dynamic SQL helper functions.
# Prevents SQL injection if callers pass untrusted column names.
_ALLOWED_COLUMNS: frozenset[str] = frozenset(
    {
        "a.created_by",
        "w.created_by",
        "we.created_by",
        "sk.created_by",
        "ii.created_by",
    }
)

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_VALID_SCHEMA_PATTERN = re.compile(r"^[a-zA-Z_]\w*$")
ATTR_USER_ID_KEYWORD = "attributes.user_id.keyword"
ATTR_ASSISTANT_ID_KEYWORD = "attributes.assistant_id.keyword"
ATTR_MCP_NAME_KEYWORD = "attributes.mcp_name.keyword"
ATTR_CLI_REQUEST = "attributes.cli_request"
ATTR_INVOKED_BY_KEYWORD = f"attributes.{MetricsAttributes.INVOKED_BY}.keyword"
METRIC_NAME_KEYWORD = "metric_name.keyword"


#: The no-figure state, shared because `RenderedSpend` is frozen. **Never a zero:** a person the
#: identity bridge could not resolve, a period the port cannot read, and a ledger that could not
#: answer all mean *we do not know what they spent*, which is a different fact from *they spent
#: nothing* and may not render as `$0.00` or feed a ranking.
NO_SPEND_FIGURE = render_spend(None)


@dataclass(frozen=True, slots=True)
class UserSpend:
    """One person's two money figures, both read through the spend port.

    **One CLI figure, not two.** The scorer needs a person's CLI money twice - once for the
    ranking's delivery-efficiency component and once for the summary it displays - and two
    aggregations for one quantity on one screen is exactly the divergence the port exists to
    remove, so both uses share this one read.

    Each figure keeps its `RenderedSpend` rather than a bare float: a truncated or
    dimension-incomplete total is a number *plus* an annotation, and dropping the annotation would
    leave it rendering indistinguishably from an exact figure.
    """

    total: RenderedSpend = NO_SPEND_FIGURE
    cli: RenderedSpend = NO_SPEND_FIGURE


@dataclass
class RawUserMetrics:
    """Raw metrics collected for a single user before scoring."""

    user_id: str
    user_name: str
    user_email: str | None = None
    projects: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    creation: dict = field(default_factory=dict)
    workflow_usage: dict = field(default_factory=dict)
    workflow_creation: dict = field(default_factory=dict)
    cli: dict = field(default_factory=dict)
    impact: dict = field(default_factory=dict)
    #: Money, and the only place it lives. Never Elasticsearch (AD-29) and never a dict of
    #: independently aggregated figures that a reader could add together.
    spend: UserSpend = field(default_factory=UserSpend)


def _validate_column(column: str) -> str:
    if column not in _ALLOWED_COLUMNS:
        raise ValueError(f"Column {column!r} is not in the allowed columns whitelist")
    return column


def _creator_user_id_sql(column: str) -> str:
    col = _validate_column(column)
    return f"COALESCE({col}->>'user_id', {col}->>'id', {col}->>'username')"


def _creator_name_sql(column: str) -> str:
    col = _validate_column(column)
    return f"NULLIF(COALESCE({col}->>'name', {col}->>'username', ''), '')"


def _creator_email_sql(column: str) -> str:
    col = _validate_column(column)
    return f"CASE WHEN COALESCE({col}->>'username', '') LIKE '%%@%%' THEN LOWER({col}->>'username') ELSE NULL END"


class LeaderboardCollector:
    """Collects raw metrics for all users from PG + ES for a given period."""

    def __init__(
        self,
        session: AsyncSession,
        es_repository: MetricsElasticRepository,
    ) -> None:
        self._session = session
        self._es = es_repository
        schema = config.DEFAULT_DB_SCHEMA
        if not _VALID_SCHEMA_PATTERN.match(schema):
            raise ValueError(f"Invalid DB schema name: {schema!r}")
        self._schema = schema

    async def collect(
        self,
        period_start: datetime,
        period_end: datetime,
    ) -> list[RawUserMetrics]:
        """Collect raw metrics for all users in the given period."""
        start_dt, end_exclusive, start_iso, end_iso = self._build_period_bounds(period_start, period_end)

        users = await self._discover_users_pg(start_dt, end_exclusive)
        logger.info(f"Leaderboard collector: discovered {len(users)} users from PG")

        pg_metrics, es_metrics = await asyncio.gather(
            self._collect_pg_metrics(start_dt, end_exclusive),
            self._collect_es_metrics(start_iso, end_iso),
            return_exceptions=True,
        )
        pg_metrics = self._normalize_metric_result(pg_metrics, "Leaderboard PG metrics collection failed", logger.error)
        es_metrics = self._normalize_metric_result(
            es_metrics,
            "Leaderboard ES metrics collection failed",
            logger.warning,
        )

        self._normalize_es_user_ids(users, es_metrics)
        self._merge_es_users(users, es_metrics)
        # Money is read after the identity merge rather than beside the two metric collections, so
        # the ES-only people the merge just added are asked about too. Read concurrently with them,
        # they would have been missing from the user set the port is asked about and would carry no
        # figure for a reason that has nothing to do with what they spent.
        raw_metrics = self._build_raw_metrics(
            users,
            pg_metrics,
            es_metrics,
            await self._collect_spend_or_nothing(set(users), time_range_or_none(start_dt, end_exclusive)),
        )

        logger.info(f"Leaderboard collector: assembled {len(raw_metrics)} user metric sets")
        return raw_metrics

    @staticmethod
    def _build_period_bounds(period_start: datetime, period_end: datetime) -> tuple[datetime, datetime, str, str]:
        from datetime import timedelta

        start_dt = period_start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_exclusive = (period_end + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start_dt, end_exclusive, period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")

    @staticmethod
    def _normalize_metric_result(
        result: dict[str, dict] | Exception,
        message: str,
        log_fn,
    ) -> dict[str, dict]:
        if isinstance(result, Exception):
            log_fn(f"{message}: {result}")
            return {}
        return result

    @staticmethod
    def _normalize_es_user_ids(users: dict[str, dict], es_metrics: dict[str, dict[str, dict]]) -> None:
        """Fold email-keyed ES buckets into their canonical UUID bucket.

        Some event sources (e.g. budget-limit metrics) emit the user's email as
        ``attributes.user_id`` instead of their UUID. This produces a spurious
        second bucket in ``es_metrics`` for the same person. We resolve it here —
        before identity merging — so the rest of the pipeline only sees UUID keys.
        """
        email_to_uuid: dict[str, str] = {v["user_email"]: k for k, v in users.items() if v.get("user_email")}
        # PG tables like conversations emit NULL for user_email, so the mapping above may be
        # incomplete. Enrich it from ES identity data on UUID-keyed buckets.
        for uid, es_data in es_metrics.items():
            if "@" in uid:
                continue
            es_email = es_data.get("identity", {}).get("user_email")
            if es_email and es_email not in email_to_uuid:
                email_to_uuid[es_email] = uid
        to_fold: list[tuple[str, str]] = []
        for uid in es_metrics:
            if "@" not in uid:
                continue
            category = derive_category_from_user_id(uid)
            base = uid if category == BudgetCategory.PLATFORM else uid[: -len(f"_codemie_{category.value}")]
            canonical_uid = email_to_uuid.get(base)
            if canonical_uid is not None:
                to_fold.append((uid, canonical_uid))
        for email_uid, canonical_uid in to_fold:
            LeaderboardCollector._fold_es_bucket(es_metrics.setdefault(canonical_uid, {}), es_metrics.pop(email_uid))

    @staticmethod
    def _fold_es_bucket(target: dict[str, dict], source: dict[str, dict]) -> None:
        """Merge *source* metric sections into *target*, summing numeric values."""
        for section, values in source.items():
            if section == "identity":
                continue
            if section not in target:
                target[section] = dict(values)
                continue
            for key, val in values.items():
                existing = target[section].get(key)
                if isinstance(val, (int, float)) and isinstance(existing, (int, float)):
                    target[section][key] = existing + val
                elif key not in target[section]:
                    target[section][key] = val

    @staticmethod
    def _merge_es_users(users: dict[str, dict], es_metrics: dict[str, dict[str, dict]]) -> None:
        """Add ES-only users to *users* and enrich identity of existing ones.

        By this point ``es_metrics`` has already been normalised (email keys
        folded into UUID keys by ``_normalize_es_user_ids``), so every key here
        is either a UUID or an email that has no PG counterpart.
        """
        for uid, es_data in es_metrics.items():
            identity = es_data.get("identity", {})
            if uid not in users:
                users[uid] = {
                    "user_id": uid,
                    "user_name": identity.get("user_name", uid),
                    "user_email": identity.get("user_email"),
                    "projects": identity.get("projects", []),
                }
                continue

            LeaderboardCollector._update_existing_user(users[uid], identity)

    @staticmethod
    def _update_existing_user(user: dict, identity: dict) -> None:
        # Allow ES name to overwrite if the current name is the UUID
        # fallback (set by _discover_users_pg when conversations.user_name
        # is null). A UUID string is truthy so the plain `not existing`
        # guard would silently keep the UUID as the display name.
        existing_name = user.get("user_name", "")
        es_name = identity.get("user_name", "")
        if es_name and (not existing_name or UUID_PATTERN.match(existing_name)):
            user["user_name"] = es_name
        if identity.get("user_email") and not user.get("user_email"):
            user["user_email"] = identity["user_email"]
        existing_projects = set(user.get("projects") or [])
        existing_projects.update(identity.get("projects") or [])
        user["projects"] = sorted(p for p in existing_projects if p)

    @staticmethod
    def _build_raw_metrics(
        users: dict[str, dict],
        pg_metrics: dict[str, dict],
        es_metrics: dict[str, dict[str, dict]],
        spend_metrics: dict[str, UserSpend],
    ) -> list[RawUserMetrics]:
        return [
            RawUserMetrics(
                user_id=uid,
                user_name=identity.get("user_name") or uid,
                user_email=identity.get("user_email"),
                projects=identity.get("projects", []),
                usage={
                    **pg_metrics.get(uid, {}).get("usage", {}),
                    **es_metrics.get(uid, {}).get("usage", {}),
                },
                creation={
                    **pg_metrics.get(uid, {}).get("creation", {}),
                    **es_metrics.get(uid, {}).get("creation", {}),
                },
                workflow_usage={
                    **pg_metrics.get(uid, {}).get("workflow_usage", {}),
                    **es_metrics.get(uid, {}).get("workflow_usage", {}),
                },
                workflow_creation=pg_metrics.get(uid, {}).get("workflow_creation", {}),
                cli={
                    **pg_metrics.get(uid, {}).get("cli", {}),
                    **es_metrics.get(uid, {}).get("cli", {}),
                },
                impact=pg_metrics.get(uid, {}).get("impact", {}),
                # A person absent from the spend read carries the no-figure default, never a zero.
                spend=spend_metrics.get(uid, UserSpend()),
            )
            for uid, identity in users.items()
        ]

    # ── PG: User Discovery ──────────────────────────────────────────

    async def _discover_users_pg(self, start_dt: datetime, end_exclusive: datetime) -> dict[str, dict]:
        s = self._schema
        sql = f"""
        WITH src AS (
            SELECT c.user_id::text AS user_id,
                   NULLIF(c.user_name, '') AS user_name,
                   NULL::text AS user_email,
                   c.project
            FROM {s}.conversations c
            WHERE c.user_id IS NOT NULL
              AND c.date >= :start AND c.date < :end_exclusive

            UNION ALL

            SELECT aui.user_id::text, NULL::text, NULL::text, aui.project
            FROM {s}.assistant_user_interaction aui
            WHERE aui.user_id IS NOT NULL
              AND aui.last_used_at >= :start
              AND aui.last_used_at < :end_exclusive

            UNION ALL

            SELECT {_creator_user_id_sql('a.created_by')}::text,
                   {_creator_name_sql('a.created_by')},
                   {_creator_email_sql('a.created_by')},
                   a.project
            FROM {s}.assistants a
            WHERE {_creator_user_id_sql('a.created_by')} IS NOT NULL
              AND (
                  (a.created_date >= :start AND a.created_date < :end_exclusive)
                  OR (a.updated_date >= :start AND a.updated_date < :end_exclusive)
              )

            UNION ALL

            SELECT {_creator_user_id_sql('w.created_by')}::text,
                   {_creator_name_sql('w.created_by')},
                   {_creator_email_sql('w.created_by')},
                   w.project
            FROM {s}.workflows w
            WHERE {_creator_user_id_sql('w.created_by')} IS NOT NULL
              AND (
                  (w.date >= :start AND w.date < :end_exclusive)
                  OR (w.update_date >= :start AND w.update_date < :end_exclusive)
              )

            UNION ALL

            SELECT {_creator_user_id_sql('we.created_by')}::text,
                   {_creator_name_sql('we.created_by')},
                   {_creator_email_sql('we.created_by')},
                   we.project
            FROM {s}.workflow_executions we
            WHERE {_creator_user_id_sql('we.created_by')} IS NOT NULL
              AND we.date >= :start AND we.date < :end_exclusive

            UNION ALL

            SELECT {_creator_user_id_sql('sk.created_by')}::text,
                   {_creator_name_sql('sk.created_by')},
                   {_creator_email_sql('sk.created_by')},
                   sk.project
            FROM {s}.skills sk
            WHERE {_creator_user_id_sql('sk.created_by')} IS NOT NULL
              AND sk.created_date >= :start
              AND sk.created_date < :end_exclusive

            UNION ALL

            SELECT {_creator_user_id_sql('ii.created_by')}::text,
                   {_creator_name_sql('ii.created_by')},
                   {_creator_email_sql('ii.created_by')},
                   ii.project_name AS project
            FROM {s}.index_info ii
            WHERE {_creator_user_id_sql('ii.created_by')} IS NOT NULL
              AND (
                  (ii.date >= :start AND ii.date < :end_exclusive)
                  OR (ii.update_date >= :start AND ii.update_date < :end_exclusive)
              )

            UNION ALL

            SELECT cm.user_id::text,
                   NULLIF(cm.user_name, ''),
                   NULL::text,
                   cm.project
            FROM {s}.conversation_metrics cm
            WHERE cm.user_id IS NOT NULL
              AND cm.date >= :start AND cm.date < :end_exclusive

            UNION ALL

            SELECT sc.shared_by_user_id::text,
                   NULLIF(sc.shared_by_user_name, ''),
                   NULL::text,
                   NULL::text
            FROM {s}.shared_conversations sc
            WHERE sc.shared_by_user_id IS NOT NULL
              AND sc.created_at >= :start
              AND sc.created_at < :end_exclusive
        )
        -- Resolve email-format user_ids to their canonical UUID via the users
        -- table. Some records (e.g. created_by on assistants/workflows) were
        -- written when the IdP used email as the JWT sub claim. Without this
        -- join those users appear twice: once under their UUID (from
        -- conversations) and once under their email, producing duplicate
        -- leaderboard entries.
        , normalized AS (
            SELECT
                COALESCE(u.id, src.user_id) AS user_id,
                COALESCE(src.user_name, u.name)  AS user_name,
                COALESCE(src.user_email, u.email) AS user_email,
                src.project
            FROM src
            LEFT JOIN {s}.users u
                   ON src.user_id LIKE '%%@%%'
                  AND LOWER(u.email) = LOWER(src.user_id)
        )
        SELECT
            user_id,
            COALESCE(
                MAX(user_name) FILTER (WHERE user_name ~ '^[A-Z]'),
                MAX(user_name) FILTER (WHERE user_name !~ '^[0-9a-f]'),
                MAX(user_name),
                user_id
            ) AS user_name,
            MAX(user_email) FILTER (WHERE user_email LIKE '%%@%%') AS user_email,
            array_remove(array_agg(DISTINCT project), NULL) AS projects
        FROM normalized
        WHERE user_id IS NOT NULL AND user_id != ''
        GROUP BY user_id
        """
        result = await self._session.execute(text(sql), {"start": start_dt, "end_exclusive": end_exclusive})
        rows = result.mappings().all()
        users: dict[str, dict] = {}
        for row in rows:
            user_name = row["user_name"] or row["user_id"]
            user_email = row.get("user_email")
            users[row["user_id"]] = {
                "user_id": row["user_id"],
                "user_name": user_name,
                "user_email": user_email,
                "projects": sorted(p for p in (row.get("projects") or []) if p),
            }
        return users

    # ── PG: Grouped Metrics ──────────────────────────────────────────

    async def _collect_pg_metrics(self, start_dt: datetime, end_exclusive: datetime) -> dict[str, dict[str, dict]]:
        s = self._schema
        data: dict[str, dict[str, dict]] = defaultdict(dict)
        params = {"start": start_dt, "end_exclusive": end_exclusive, "cli_id": CLI_ASSISTANT_ID}

        async def merge(section: str, sql: str, extra_params: dict | None = None) -> None:
            p = {**params, **(extra_params or {})}
            result = await self._session.execute(text(sql), p)
            for row in result.mappings().all():
                uid = row.get("user_id")
                if not uid:
                    continue
                payload = dict(row)
                payload.pop("user_id", None)
                data[uid][section] = {**data[uid].get(section, {}), **payload}

        # Platform active days
        await merge(
            "usage",
            f"""
            SELECT activity.user_id,
                   COUNT(DISTINCT activity.activity_day)::int AS platform_active_days
            FROM (
                SELECT c.user_id::text AS user_id, c.date::date AS activity_day
                FROM {s}.conversations c
                WHERE c.user_id IS NOT NULL
                  AND (c.is_workflow_conversation = FALSE OR c.is_workflow_conversation IS NULL)
                  AND c.initial_assistant_id IS DISTINCT FROM :cli_id
                  AND c.date >= :start AND c.date < :end_exclusive
                UNION
                SELECT aui.user_id::text, aui.last_used_at::date
                FROM {s}.assistant_user_interaction aui
                WHERE aui.user_id IS NOT NULL
                  AND aui.assistant_id IS DISTINCT FROM :cli_id
                  AND aui.last_used_at >= :start
                  AND aui.last_used_at < :end_exclusive
            ) activity
            GROUP BY activity.user_id
        """,
        )

        # Web conversations
        await merge(
            "usage",
            f"""
            SELECT c.user_id::text AS user_id,
                   COUNT(DISTINCT c.id)::int AS web_conversations,
                   COUNT(DISTINCT c.conversation_id)::int AS unique_conversations,
                   COALESCE(AVG(cm.number_of_messages), 0)::float AS avg_messages_per_conversation,
                   COALESCE(SUM(cm.number_of_messages), 0)::int AS total_messages,
                   COALESCE(SUM(cm.total_input_tokens), 0)::bigint AS web_input_tokens,
                   COALESCE(SUM(cm.total_output_tokens), 0)::bigint AS web_output_tokens,
                   COALESCE(SUM(cm.total_money_spent), 0)::float AS web_money_spent
            FROM {s}.conversations c
            LEFT JOIN {s}.conversation_metrics cm ON cm.conversation_id = c.id
            WHERE c.user_id IS NOT NULL
              AND (c.is_workflow_conversation = FALSE OR c.is_workflow_conversation IS NULL)
              AND c.initial_assistant_id IS DISTINCT FROM :cli_id
              AND c.date >= :start AND c.date < :end_exclusive
            GROUP BY c.user_id
        """,
        )

        # Assistants used
        await merge(
            "usage",
            f"""
            SELECT aui.user_id::text AS user_id,
                   COUNT(DISTINCT aui.assistant_id)::int AS assistants_used,
                   COALESCE(SUM(aui.usage_count), 0)::int AS assistant_usage_count
            FROM {s}.assistant_user_interaction aui
            WHERE aui.user_id IS NOT NULL
              AND aui.assistant_id IS DISTINCT FROM :cli_id
              AND aui.last_used_at >= :start
              AND aui.last_used_at < :end_exclusive
            GROUP BY aui.user_id
        """,
        )

        # Assistants created
        await merge(
            "creation",
            f"""
            SELECT {_creator_user_id_sql('a.created_by')}::text AS user_id,
                   COUNT(*)::int AS assistants_created,
                   COUNT(*) FILTER (WHERE a.shared)::int AS assistants_shared,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(a.toolkits), 0) > 0
                   )::int AS assistants_with_tools,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(a.mcp_servers), 0) > 0
                   )::int AS assistants_with_mcps,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(a.skill_ids), 0) > 0
                   )::int AS assistants_with_skills,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(a.assistant_ids), 0) > 0
                          OR COALESCE(jsonb_array_length(a.nested_assistants), 0) > 0
                   )::int AS assistants_with_nested,
                   COUNT(*) FILTER (
                       WHERE a.smart_tool_selection_enabled
                   )::int AS assistants_smart_tools,
                   COUNT(*) FILTER (
                       WHERE jsonb_typeof(a.context) = 'array'
                         AND COALESCE(jsonb_array_length(a.context), 0) > 0
                   )::int AS assistants_with_context,
                   COALESCE(AVG(length(COALESCE(a.system_prompt, ''))), 0)::float
                       AS avg_assistant_prompt_length,
                   COALESCE(SUM(a.unique_users_count), 0)::int AS assistant_unique_users_sum,
                   COALESCE(SUM(a.unique_likes_count), 0)::int AS assistant_likes_sum
            FROM {s}.assistants a
            WHERE {_creator_user_id_sql('a.created_by')} IS NOT NULL
              AND {_creator_user_id_sql('a.created_by')} <> 'system'
              AND (a.created_date >= :start OR a.updated_date >= :start)
              AND (a.created_date < :end_exclusive OR a.updated_date < :end_exclusive)
            GROUP BY {_creator_user_id_sql('a.created_by')}
        """,
        )

        # Assistant adoption by others
        await merge(
            "creation",
            f"""
            SELECT {_creator_user_id_sql('a.created_by')}::text AS user_id,
                   COUNT(DISTINCT aui.user_id)::int AS assistant_adopters,
                   COALESCE(SUM(aui.usage_count), 0)::int AS assistant_external_usage
            FROM {s}.assistants a
            JOIN {s}.assistant_user_interaction aui ON aui.assistant_id = a.id
            WHERE {_creator_user_id_sql('a.created_by')} IS NOT NULL
              AND aui.user_id IS NOT NULL
              AND aui.user_id <> {_creator_user_id_sql('a.created_by')}
              AND aui.last_used_at >= :start
              AND aui.last_used_at < :end_exclusive
            GROUP BY {_creator_user_id_sql('a.created_by')}
        """,
        )

        # Skills created
        await merge(
            "creation",
            f"""
            SELECT {_creator_user_id_sql('sk.created_by')}::text AS user_id,
                   COUNT(*)::int AS skills_created,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(sk.toolkits), 0) > 0
                   )::int AS skills_with_toolkits,
                   COALESCE(AVG(length(COALESCE(sk.content, ''))), 0)::float
                       AS avg_skill_content_length,
                   COALESCE(SUM(sk.unique_likes_count), 0)::int AS skill_likes_sum
            FROM {s}.skills sk
            WHERE {_creator_user_id_sql('sk.created_by')} IS NOT NULL
              AND sk.created_date >= :start
              AND sk.created_date < :end_exclusive
            GROUP BY {_creator_user_id_sql('sk.created_by')}
        """,
        )

        # Datasources created
        await merge(
            "creation",
            f"""
            SELECT {_creator_user_id_sql('ii.created_by')}::text AS user_id,
                   COUNT(*)::int AS datasources_created,
                   COUNT(DISTINCT ii.index_type)::int AS datasource_types_created,
                   COUNT(*) FILTER (WHERE ii.completed)::int AS datasources_completed,
                   COALESCE(SUM(
                       (ii.processing_info->>'total_documents')::int
                   ), 0)::int AS datasource_documents_total
            FROM {s}.index_info ii
            WHERE {_creator_user_id_sql('ii.created_by')} IS NOT NULL
              AND (ii.date >= :start OR ii.update_date >= :start)
              AND (ii.date < :end_exclusive OR ii.update_date < :end_exclusive)
            GROUP BY {_creator_user_id_sql('ii.created_by')}
        """,
        )

        # Workflows created
        await merge(
            "workflow_creation",
            f"""
            SELECT {_creator_user_id_sql('w.created_by')}::text AS user_id,
                   COUNT(*)::int AS workflows_created,
                   COUNT(*) FILTER (WHERE w.shared)::int AS workflows_shared,
                   COALESCE(AVG(COALESCE(jsonb_array_length(w.states), 0)), 0)::float
                       AS avg_workflow_states,
                   COALESCE(AVG(COALESCE(jsonb_array_length(w.assistants), 0)), 0)::float
                       AS avg_workflow_assistants,
                   COALESCE(AVG(COALESCE(jsonb_array_length(w.tools), 0)), 0)::float
                       AS avg_workflow_tools,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(w.tools), 0) > 0
                   )::int AS workflows_with_tools,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(w.custom_nodes), 0) > 0
                   )::int AS workflows_with_custom_nodes,
                   COUNT(*) FILTER (
                       WHERE COALESCE(jsonb_array_length(w.assistants), 0) > 0
                   )::int AS workflows_with_assistants,
                   COUNT(*) FILTER (
                       WHERE w.enable_summarization_node
                   )::int AS workflows_with_summarization,
                   COUNT(*) FILTER (
                       WHERE COALESCE(length(w.supervisor_prompt), 0) >= 300
                   )::int AS workflows_with_long_supervisor_prompt,
                   COALESCE(AVG(COALESCE(jsonb_array_length(w.custom_nodes), 0)), 0)::float
                       AS avg_workflow_custom_nodes,
                   COALESCE(AVG(COALESCE(length(w.yaml_config), 0)), 0)::float
                       AS avg_workflow_yaml_length
            FROM {s}.workflows w
            WHERE {_creator_user_id_sql('w.created_by')} IS NOT NULL
              AND w.date >= :start AND w.date < :end_exclusive
            GROUP BY {_creator_user_id_sql('w.created_by')}
        """,
        )

        # Workflow executions
        await merge(
            "workflow_usage",
            f"""
            SELECT {_creator_user_id_sql('we.created_by')}::text AS user_id,
                   COUNT(*)::int AS workflow_executions,
                   COUNT(*) FILTER (
                       WHERE we.overall_status::text = 'SUCCEEDED'
                   )::int AS workflow_successes,
                   COUNT(*) FILTER (
                       WHERE we.overall_status::text = 'FAILED'
                   )::int AS workflow_failures,
                   COUNT(DISTINCT we.workflow_id)::int AS workflows_executed_distinct,
                   COALESCE(SUM(
                       (we.tokens_usage->>'money_spent')::numeric
                   ), 0)::float AS workflow_total_money_spent
            FROM {s}.workflow_executions we
            WHERE {_creator_user_id_sql('we.created_by')} IS NOT NULL
              AND we.date >= :start AND we.date < :end_exclusive
            GROUP BY {_creator_user_id_sql('we.created_by')}
        """,
        )

        # Workflow adoption by others
        await merge(
            "workflow_creation",
            f"""
            SELECT {_creator_user_id_sql('w.created_by')}::text AS user_id,
                   COUNT(DISTINCT external_users.external_user)::int
                       AS workflow_external_users,
                   COUNT(*)::int AS workflow_external_executions
            FROM {s}.workflows w
            JOIN {s}.workflow_executions we ON we.workflow_id = w.id
            CROSS JOIN LATERAL (
                SELECT {_creator_user_id_sql('we.created_by')}::text AS external_user
            ) external_users
            WHERE {_creator_user_id_sql('w.created_by')} IS NOT NULL
              AND external_users.external_user IS NOT NULL
              AND external_users.external_user <> {_creator_user_id_sql('w.created_by')}
              AND we.date >= :start AND we.date < :end_exclusive
            GROUP BY {_creator_user_id_sql('w.created_by')}
        """,
        )

        # Shared conversations
        await merge(
            "impact",
            f"""
            SELECT sc.shared_by_user_id::text AS user_id,
                   COUNT(*)::int AS shared_conversations,
                   COALESCE(SUM(sc.access_count), 0)::int AS shared_conversation_access
            FROM {s}.shared_conversations sc
            WHERE sc.shared_by_user_id IS NOT NULL
              AND sc.created_at >= :start
              AND sc.created_at < :end_exclusive
            GROUP BY sc.shared_by_user_id
        """,
        )

        # Kata progress
        await merge(
            "impact",
            f"""
            SELECT user_id::text AS user_id,
                   COUNT(*)::int AS kata_completed
            FROM {s}.user_kata_progress
            WHERE user_id IS NOT NULL AND status::text = 'COMPLETED'
              AND completed_at >= :start
              AND completed_at < :end_exclusive
            GROUP BY user_id
        """,
        )

        # Feedback given
        await merge(
            "impact",
            f"""
            SELECT user_id::text AS user_id, COUNT(*)::int AS feedback_given
            FROM (
                SELECT aui.user_id
                FROM {s}.assistant_user_interaction aui
                WHERE aui.user_id IS NOT NULL AND aui.reaction IS NOT NULL
                  AND aui.reaction_at >= :start
                  AND aui.reaction_at < :end_exclusive
                UNION ALL
                SELECT sui.user_id
                FROM {s}.skill_user_interaction sui
                WHERE sui.user_id IS NOT NULL AND sui.reaction IS NOT NULL
                  AND sui.reaction_at >= :start
                  AND sui.reaction_at < :end_exclusive
            ) x
            GROUP BY user_id
        """,
        )

        return dict(data)

    # ── ES: Grouped Metrics ──────────────────────────────────────────

    async def _collect_es_metrics(self, start_iso: str, end_iso: str) -> dict[str, dict[str, dict]]:
        """Collect metrics from Elasticsearch: active days, tool usage, CLI metrics."""
        result: dict[str, dict[str, dict]] = defaultdict(dict)
        es_timeout = 60  # Leaderboard aggregations scan large date ranges

        range_filter = self._build_range_filter(start_iso, end_iso)
        await self._populate_es_activity_metrics(result, range_filter, es_timeout)
        await self._populate_es_tool_usage_metrics(result, range_filter, es_timeout)
        await self._populate_es_cli_metrics(result, range_filter, es_timeout)

        return dict(result)

    @staticmethod
    def _build_range_filter(start_iso: str, end_iso: str) -> dict:
        return {
            "range": {
                "@timestamp": {
                    "gte": start_iso,
                    "lte": end_iso,
                    "format": "yyyy-MM-dd",
                }
            }
        }

    @staticmethod
    def _by_user_terms() -> dict:
        return {
            "field": ATTR_USER_ID_KEYWORD,
            "size": 10000,
            "min_doc_count": 1,
        }

    @staticmethod
    def _metric_term(metric_name: str) -> dict:
        return {"term": {METRIC_NAME_KEYWORD: metric_name}}

    async def _populate_es_activity_metrics(
        self,
        result: dict[str, dict[str, dict]],
        range_filter: dict,
        es_timeout: int,
    ) -> None:
        try:
            activity_data = await self._es.execute_aggregation_query(
                self._build_activity_query(range_filter),
                request_timeout=es_timeout,
            )
            self._merge_activity_data(result, activity_data)
        except Exception as e:
            logger.warning(f"Leaderboard ES activity collection failed: {e}")

    async def _populate_es_tool_usage_metrics(
        self, result: dict[str, dict[str, dict]], range_filter: dict, es_timeout: int
    ) -> None:
        try:
            tool_data = await self._es.execute_aggregation_query(
                self._build_tool_usage_query(range_filter),
                request_timeout=es_timeout,
            )
            self._merge_tool_usage_data(result, tool_data)
        except Exception as e:
            logger.warning(f"Leaderboard ES tool usage collection failed: {e}")

    async def _populate_es_cli_metrics(
        self,
        result: dict[str, dict[str, dict]],
        range_filter: dict,
        es_timeout: int,
    ) -> None:
        try:
            cli_data = await self._es.execute_aggregation_query(
                self._build_cli_query(range_filter),
                request_timeout=es_timeout,
            )
            self._merge_cli_data(result, cli_data)
        except Exception as e:
            logger.warning(f"Leaderboard ES CLI metrics collection failed: {e}")

    def _build_activity_query(self, range_filter: dict) -> dict:
        return {
            "size": 0,
            "query": range_filter,
            "aggs": {
                "by_user": {
                    "terms": self._by_user_terms(),
                    "aggs": {
                        "user_name": {"terms": {"field": "attributes.user_name.keyword", "size": 1}},
                        "user_email": {"terms": {"field": "attributes.user_email.keyword", "size": 1}},
                        "projects": {"terms": {"field": "attributes.project.keyword", "size": 25}},
                        "active_days": {
                            "cardinality": {
                                "script": {
                                    "source": "doc['@timestamp'].value.toLocalDate().toString()",
                                    "lang": "painless",
                                }
                            }
                        },
                        "web_usage": {
                            "filter": {
                                "bool": {
                                    "must": [self._metric_term("conversation_assistant_usage")],
                                    "must_not": [{"term": {ATTR_ASSISTANT_ID_KEYWORD: CLI_ASSISTANT_ID}}],
                                }
                            },
                            "aggs": {
                                "assistants": {"cardinality": {"field": ATTR_ASSISTANT_ID_KEYWORD}},
                            },
                        },
                    },
                }
            },
        }

    def _build_tool_usage_query(self, range_filter: dict) -> dict:
        return {
            "size": 0,
            "query": {
                "bool": {
                    "must": [
                        range_filter,
                        {
                            "bool": {
                                "should": [
                                    self._metric_term("codemie_tools_usage_total"),
                                    self._metric_term("codemie_skill_tool_invoked"),
                                    {"exists": {"field": ATTR_MCP_NAME_KEYWORD}},
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                    ],
                    "must_not": [
                        {"term": {ATTR_CLI_REQUEST: True}},
                        {"term": {ATTR_ASSISTANT_ID_KEYWORD: CLI_ASSISTANT_ID}},
                    ],
                }
            },
            "aggs": {
                "by_user": {
                    "terms": self._by_user_terms(),
                    "aggs": {
                        "tool_usage": {
                            "filter": {
                                "bool": {
                                    "must": [self._metric_term("codemie_tools_usage_total")],
                                    # calls made from a workspace script are not distinct tools the user picked
                                    "must_not": [{"term": {ATTR_INVOKED_BY_KEYWORD: INVOKED_BY_SCRIPT}}],
                                }
                            },
                            "aggs": {
                                "unique_tools": {"cardinality": {"field": "attributes.tool_name.keyword"}},
                            },
                        },
                        "skill_usage": {
                            "filter": self._metric_term("codemie_skill_tool_invoked"),
                            "aggs": {"count": {"value_count": {"field": METRIC_NAME_KEYWORD}}},
                        },
                        "mcp_usage": {
                            "filter": {"exists": {"field": ATTR_MCP_NAME_KEYWORD}},
                            "aggs": {
                                "unique_mcps": {"cardinality": {"field": ATTR_MCP_NAME_KEYWORD}},
                            },
                        },
                    },
                }
            },
        }

    def _build_cli_query(self, range_filter: dict) -> dict:
        return {
            "size": 0,
            "query": {
                "bool": {
                    "must": [
                        range_filter,
                        {
                            "bool": {
                                "should": [
                                    self._metric_term("codemie_cli_session_total"),
                                    self._metric_term("codemie_cli_tool_usage_total"),
                                    self._metric_term("codemie_litellm_proxy_usage"),
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                    ]
                }
            },
            "aggs": {
                "by_user": {
                    "terms": self._by_user_terms(),
                    "aggs": {
                        "sessions": {
                            "filter": self._metric_term("codemie_cli_session_total"),
                            "aggs": {
                                "count": {"value_count": {"field": METRIC_NAME_KEYWORD}},
                            },
                        },
                        "tool_usage": {
                            "filter": self._metric_term("codemie_cli_tool_usage_total"),
                            "aggs": {
                                "repos": {"cardinality": {"field": "attributes.repository.keyword"}},
                                "lines_added": {"sum": {"field": "attributes.total_lines_added"}},
                                "lines_removed": {"sum": {"field": "attributes.total_lines_removed"}},
                                "files_created": {"sum": {"field": "attributes.files_created"}},
                                "files_modified": {"sum": {"field": "attributes.files_modified"}},
                                "files_deleted": {"sum": {"field": "attributes.files_deleted"}},
                                "input_tokens": {"sum": {"field": "attributes.input_tokens"}},
                                "output_tokens": {"sum": {"field": "attributes.output_tokens"}},
                                "cache_read_tokens": {"sum": {"field": "attributes.cache_read_input_tokens"}},
                            },
                        },
                        # No money sub-aggregation. A person's CLI spend is read through the spend
                        # port (AD-29: money never comes from Elasticsearch), and the copy that used
                        # to be summed here was one of two figures for that one quantity.
                    },
                }
            },
        }

    @staticmethod
    def _merge_activity_data(result: dict[str, dict[str, dict]], activity_data: dict) -> None:
        activity_buckets = activity_data.get("aggregations", {}).get("by_user", {})
        if activity_buckets.get("sum_other_doc_count", 0) > 0:
            logger.warning(
                f"Leaderboard ES activity aggregation truncated: "
                f"sum_other_doc_count={activity_buckets['sum_other_doc_count']}. "
                f"Consider increasing terms size."
            )

        for bucket in activity_buckets.get("buckets", []):
            uid = bucket["key"]
            name_buckets = bucket.get("user_name", {}).get("buckets", [])
            email_buckets = bucket.get("user_email", {}).get("buckets", [])
            result[uid]["identity"] = {
                "user_name": name_buckets[0]["key"] if name_buckets else uid,
                "user_email": email_buckets[0]["key"] if email_buckets else None,
                "projects": sorted(b["key"] for b in bucket.get("projects", {}).get("buckets", []) if b.get("key")),
            }
            result[uid]["usage"] = {
                "active_days": int(bucket.get("active_days", {}).get("value", 0)),
                "es_assistants_used": int(bucket.get("web_usage", {}).get("assistants", {}).get("value", 0) or 0),
            }

    @staticmethod
    def _merge_tool_usage_data(result: dict[str, dict[str, dict]], tool_data: dict) -> None:
        for bucket in tool_data.get("aggregations", {}).get("by_user", {}).get("buckets", []):
            uid = bucket["key"]
            existing = result[uid].get("usage", {})
            result[uid]["usage"] = {
                **existing,
                "unique_tools": int(bucket.get("tool_usage", {}).get("unique_tools", {}).get("value", 0) or 0),
                "skill_usage_events": int(bucket.get("skill_usage", {}).get("count", {}).get("value", 0) or 0),
                "unique_mcps_used": int(bucket.get("mcp_usage", {}).get("unique_mcps", {}).get("value", 0) or 0),
            }

    @staticmethod
    def _merge_cli_data(result: dict[str, dict[str, dict]], cli_data: dict) -> None:
        for bucket in cli_data.get("aggregations", {}).get("by_user", {}).get("buckets", []):
            uid = bucket["key"]
            sessions_agg = bucket.get("sessions", {})
            tool_agg = bucket.get("tool_usage", {})
            files_created = int(tool_agg.get("files_created", {}).get("value", 0) or 0)
            files_modified = int(tool_agg.get("files_modified", {}).get("value", 0) or 0)
            files_deleted = int(tool_agg.get("files_deleted", {}).get("value", 0) or 0)
            input_tok = int(tool_agg.get("input_tokens", {}).get("value", 0) or 0)
            output_tok = int(tool_agg.get("output_tokens", {}).get("value", 0) or 0)
            cache_tok = int(tool_agg.get("cache_read_tokens", {}).get("value", 0) or 0)
            result[uid]["cli"] = {
                "cli_sessions": int(sessions_agg.get("count", {}).get("value", 0) or 0),
                "cli_repos": int(tool_agg.get("repos", {}).get("value", 0) or 0),
                "cli_lines_added": int(tool_agg.get("lines_added", {}).get("value", 0) or 0),
                "cli_lines_removed": int(tool_agg.get("lines_removed", {}).get("value", 0) or 0),
                "cli_files_changed": files_created + files_modified + files_deleted,
                "cli_total_tokens": input_tok + output_tok + cache_tok,
                # No `cli_total_spend`: the person's CLI money is one spend-port read, carried on
                # `RawUserMetrics.spend.cli` and shared by the ranking and the summary alike.
            }

    # ── Money: the spend port, and nothing else ───────────────

    async def _collect_spend_or_nothing(self, identifiers: set[str], period: TimeRange | None) -> dict[str, UserSpend]:
        """`_collect_spend`, degrading every figure to absent if the ledger cannot answer.

        The port raises on failure and the reading path is what reports the absence - *"a ledger
        outage degrades a figure and never a request"*. So a snapshot still computes, every money
        figure in it says *unknown*, and nothing reports a zero anyone might read as measured.
        """
        try:
            return await self._collect_spend(identifiers, period)
        except Exception as exc:
            logger.warning(f"Leaderboard spend collection failed, so every money figure is absent: {exc}")
            return {}

    async def _collect_spend(self, identifiers: set[str], period: TimeRange | None) -> dict[str, UserSpend]:
        """Read every person's two money figures through the spend port.

        **Money never comes from Elasticsearch (AD-29).** This replaced a per-user ES money
        aggregation whose figures the scorer then added to a second ES aggregate; the two sums were
        two answers to one question, which is what the port exists to remove.

        A period the port cannot accept, an identifier the identity bridge cannot resolve, or a
        ledger that cannot answer all yield **no figure** for that person rather than a zero.
        """
        if period is None or not identifiers:
            logger.warning("Leaderboard spend: no readable period or no people, so every money figure is absent")
            return {}

        terms = await self._resolve_user_terms(identifiers)
        if not terms:
            return {}

        totals = await self._person_totals(terms, period)
        cli = await self._cli_figures(terms, period)
        return {
            identifier: UserSpend(total=render_spend(totals.get(identifier)), cli=render_spend(cli.get(identifier)))
            for identifier in terms
        }

    async def _resolve_user_terms(self, identifiers: set[str]) -> dict[str, UserTerm]:
        """Map each discovered identifier to the person the ledger knows, aliases included.

        Resolved in **one** batched lookup rather than one `resolve_identity` call per person: the
        leaderboard asks about every user in the tenant, and `aliases_for_user` exists for exactly
        this - *"a list screen has just loaded every user row it is about to show, and re-resolving
        each one would be a query per row for facts already in memory"*. The values are identical,
        because `resolve_identity` is that lookup followed by that alias rule.

        An identifier matching no user record is simply **absent** from the map, so that person is
        never asked about and carries no figure. That is the unresolvable state kept honest: there is
        no `user_id` to key a holder on, so there is nothing to sum - which is a different fact from
        having spent nothing, and it must not become an empty alias set on a fabricated holder.
        """
        records = await user_repository.afind_users_by_identifiers(self._session, identifiers)
        if len(records) < len(identifiers):
            logger.info(
                f"Leaderboard spend: the identity bridge resolved {len(records)}/{len(identifiers)} people; "
                f"the rest carry no money figure rather than a zero"
            )
        return {
            identifier: UserTerm(
                user_id=record.id,
                # The record is already in hand, so the alias set costs no further query.
                aliases=aliases_for_user(record.username, record.email),
            )
            for identifier, record in records.items()
        }

    @staticmethod
    async def _person_totals(terms: dict[str, UserTerm], period: TimeRange) -> dict[str, SpendResult]:
        """Each person's whole spend - **one** holder read, batched over everyone.

        `PersonTotalHolder` is the one holder spanning the personal *and* project pots, across every
        category, so this single figure already is the person's total across everything. Nothing is
        added to it - in particular no Elasticsearch web-spend aggregate, which is what the display
        copy used to be summed with.
        """
        holder_by_identifier = {
            identifier: PersonTotalHolder(user_id=term.user_id, aliases=term.aliases)
            for identifier, term in terms.items()
        }
        holders = list(dict.fromkeys(holder_by_identifier.values()))
        spend = await spend_service.consumed_for_holders(holders, period)
        return {
            identifier: result
            for identifier, holder in holder_by_identifier.items()
            if (result := spend.get(holder)) is not None
        }

    @staticmethod
    async def _cli_figures(terms: dict[str, UserTerm], period: TimeRange) -> dict[str, SpendResult]:
        """Each person's CLI money - the composed read, split by traffic class.

        **A composed scope, and emphatically not `PersonalHolder(user, BudgetCategory.CLI)`.** That
        holder looks like the answer and compiles, but it is what the person's own customer was
        charged, so it silently drops every CLI call the person made against a project budget.
        This figure is *all* of a person's CLI traffic, project-scoped rows included.
        `PersonTotalHolder` spans both pots but carries no category split, so neither holder has this
        shape and the class-split read is the one that does.

        One call per person because the port has no batched composed read: a class split is scoped by
        one `ComposedScope` at a time. A user-only scope is valid - a scope needs at least one term,
        not all of them.
        """
        figures: dict[str, SpendResult] = {}
        for identifier, term in terms.items():
            by_class = await spend_service.aggregate_composed_by_traffic_class(period, ComposedScope(user=term))
            figures[identifier] = by_class[TrafficClass.CLI]
        logger.info(f"Leaderboard spend: read the CLI class split for {len(figures)} people")
        return figures
