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

"""Assembles Local Analytics responses from this backend's ClickHouse facts.

Per-session facts are merged in Python rather than in one wide SQL statement.
At current volume (single-digit thousands of spans per week) this costs nothing
measurable and keeps each ClickHouse query independently verifiable. See
ANALYTICS_DISCOVERY/local_analytics_implementation_plan.md §9 item F1 for the
threshold at which this should move into a materialized fact table.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any

from codemie.repository.cli_analytics_repository import (
    LocalAnalyticsFilter,
    LocalAnalyticsRepository,
    depth_bucket,
)
from codemie.service.analytics.delivery_framework import classify_delivery_framework
from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost, lookup_cost_config

logger = logging.getLogger(__name__)

_DEPTH_BUCKETS = ("1", "2-5", "6-10", "11-25", "26-50", "50+")
_MAX_INVOCATIONS = 10
_PROMPT_PREVIEW_LEN = 50


# ── Coercion helpers (ClickHouse returns Decimal / None / UInt64 variously) ───


def _i(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _f(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _iso(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return _s(value)


def _pct(numerator: float, denominator: float) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _first(rows: list[dict]) -> dict:
    return rows[0] if rows else {}


def _count_dispatch_subtypes(rows: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for row in rows:
        subtype = _s(row.get("subagent_type"))
        if subtype:
            totals[subtype] = totals.get(subtype, 0) + 1
    return totals


def _token_dict(
    cost_usd: float, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_creation_tokens: int
) -> dict:
    return {
        "cost_usd": cost_usd or None,
        "input_tokens": input_tokens or None,
        "output_tokens": output_tokens or None,
        "cache_read_tokens": cache_read_tokens or None,
        "cache_creation_tokens": cache_creation_tokens or None,
    }


def _dispatch_label_and_kind(
    row: dict, type_totals: dict[str, int], counters: dict[str, int]
) -> tuple[str, str] | None:
    subtype = _s(row.get("subagent_type"))
    if subtype:
        if type_totals[subtype] == 1:
            label = f"agent · {subtype}"
        else:
            counters[subtype] = counters.get(subtype, 0) + 1
            label = f"agent · {subtype} #{counters[subtype]}"
        return label, "agent"
    skill = _s(row.get("skill_name"))
    if skill:
        if row.get("is_slash_command"):
            return f"command · {skill}", "command"
        return f"skill · {skill}", "skill"
    return None


class LocalAnalyticsHandler:
    def __init__(self, repo: LocalAnalyticsRepository) -> None:
        self._repo = repo

    # ── Pricing ──────────────────────────────────────────────────────────────

    @staticmethod
    def _price_cache_reads(model_rows: list[dict]) -> tuple[float, list[str]]:
        """Total USD cost of cache-read tokens, plus the models we could not price.

        cache_read_cost() returns 0.0 for any model absent from the active LLM cost
        config, so an unpriced model silently understates the total. The names are
        returned for the response metadata and a server-side warning instead.
        """
        total = 0.0
        unpriced: list[str] = []
        for row in model_rows:
            model = _s(row.get("model_name"))
            tokens = _i(row.get("cache_read_tokens"))
            if not model:
                continue
            total += cache_read_cost(model, tokens)
            if tokens > 0 and lookup_cost_config(model) is None:
                unpriced.append(model)
        if unpriced:
            logger.warning(
                "cli_analytics: no cache-read pricing for models %s - "
                "cache_read_cost_usd and bloat_pct are understated",
                sorted(set(unpriced)),
            )
        return total, sorted(set(unpriced))

    # ── Overview ─────────────────────────────────────────────────────────────

    async def get_overview(self, f: LocalAnalyticsFilter) -> tuple[dict, list[str], str | None]:
        (
            kpi_rows,
            model_rows,
            lines_totals,
            lines_daily,
            turns_rows,
            file_rows,
            success_rows,
            duration_rows,
        ) = await asyncio.gather(
            self._repo.get_cost_kpis(f),
            self._repo.get_model_breakdown(f),
            self._repo.get_lines_totals(f),
            self._repo.get_lines_daily(f),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_session_durations(f),
        )

        kpi = _first(kpi_rows)
        total_sessions = _i(kpi.get("total_sessions"))
        total_cost = _f(kpi.get("total_cost_usd"))
        input_tokens = _i(kpi.get("total_input_tokens"))
        output_tokens = _i(kpi.get("total_output_tokens"))
        cache_read_tokens = _i(kpi.get("total_cache_read_tokens"))
        cache_creation_tokens = _i(kpi.get("total_cache_creation_tokens"))

        # D1: the session universe is the priced (cost_daily) one, so every
        # per-session roll-up below is restricted to those sessions.
        cost_facts = await self._repo.get_session_cost_facts(f)
        cost_sessions = {_s(r.get("session_id")) for r in cost_facts}

        total_turns = sum(_i(r.get("turns")) for r in turns_rows if _s(r.get("session_id")) in cost_sessions)
        files_by_session = {_s(r.get("session_id")): r for r in file_rows}
        total_files_changed = sum(
            _i(r.get("files_changed")) for sid, r in files_by_session.items() if sid in cost_sessions
        )
        tool_calls = sum(_i(r.get("tool_calls")) for r in success_rows if _s(r.get("session_id")) in cost_sessions)
        tool_success = sum(
            _i(r.get("tool_calls_success")) for r in success_rows if _s(r.get("session_id")) in cost_sessions
        )
        duration_ms = sum(_i(r.get("duration_ms")) for r in duration_rows if _s(r.get("session_id")) in cost_sessions)

        lines = _first(lines_totals)
        net_lines = _i(lines.get("lines_added")) - _i(lines.get("lines_removed"))

        dead_sessions, _wasted = self._dead_sessions(cost_facts, files_by_session)

        cache_read_cost_usd, unpriced = self._price_cache_reads(model_rows)
        total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens

        kpis = {
            "total_sessions": total_sessions,
            "total_cost_usd": total_cost,
            "duration_ms": duration_ms,
            "total_turns": total_turns,
            "total_tool_calls": tool_calls,
            "tool_call_success_rate": _pct(tool_success, tool_calls),
            "total_files_changed": total_files_changed,
            "net_lines": net_lines,
            "dead_sessions": dead_sessions,
            "total_input_tokens": input_tokens,
            "total_output_tokens": output_tokens,
            "total_cache_creation_tokens": cache_creation_tokens,
            "total_cache_read_tokens": cache_read_tokens,
            "total_tokens": total_tokens,
            "cache_read_cost_usd": cache_read_cost_usd,
            "bloat_pct": _pct(cache_read_cost_usd, total_cost),
            "avg_context_per_call": (cache_read_tokens / total_turns) if total_turns else 0.0,
        }

        daily_buckets = [{"day": _iso(r.get("day")), "net_lines": _i(r.get("net_lines"))} for r in lines_daily]
        model_breakdown = [
            {"model_name": _s(r.get("model_name")), "session_count": _i(r.get("session_count"))} for r in model_rows
        ]

        data_as_of = max((_iso(r.get("day")) for r in lines_daily), default=None)
        return (
            {"kpis": kpis, "daily_buckets": daily_buckets, "model_breakdown": model_breakdown},
            unpriced,
            data_as_of,
        )

    @staticmethod
    def _dead_sessions(cost_facts: list[dict], files_by_session: dict[str, dict]) -> tuple[int, float]:
        """A session that cost money but touched no file. Returns (count, wasted_usd)."""
        dead = 0
        wasted = 0.0
        for row in cost_facts:
            cost = _f(row.get("cost_usd"))
            if cost <= 0:
                continue
            sid = _s(row.get("session_id"))
            if _i(files_by_session.get(sid, {}).get("files_changed")) == 0:
                dead += 1
                wasted += cost
        return dead, wasted

    # ── Cost ─────────────────────────────────────────────────────────────────

    async def get_cost(self, f: LocalAnalyticsFilter) -> tuple[dict, list[str]]:
        kpi_rows, by_user, model_rows = await asyncio.gather(
            self._repo.get_cost_kpis(f),
            self._repo.get_cost_by_user(f),
            self._repo.get_model_breakdown(f),
        )
        kpi = _first(kpi_rows)
        total_sessions = _i(kpi.get("total_sessions"))
        total_cost = _f(kpi.get("total_cost_usd"))
        total_tokens = (
            _i(kpi.get("total_input_tokens"))
            + _i(kpi.get("total_output_tokens"))
            + _i(kpi.get("total_cache_read_tokens"))
            + _i(kpi.get("total_cache_creation_tokens"))
        )
        _cost, unpriced = self._price_cache_reads(model_rows)
        data = {
            "kpis": {
                "total_sessions": total_sessions,
                "total_cost_usd": total_cost,
                "total_tokens": total_tokens,
                "avg_cost_per_session": (total_cost / total_sessions) if total_sessions else 0.0,
            },
            "cost_by_user": [
                {"developer_name": _s(r.get("developer_name")), "cost_usd": _f(r.get("cost_usd"))} for r in by_user
            ],
            "cost_by_model": [
                {"model_name": _s(r.get("model_name")), "cost_usd": _f(r.get("cost_usd"))}
                for r in sorted(model_rows, key=lambda x: _f(x.get("cost_usd")), reverse=True)
            ],
        }
        return data, unpriced

    # ── Users ────────────────────────────────────────────────────────────────

    async def get_users(self, f: LocalAnalyticsFilter, page: int | None, per_page: int | None) -> dict:
        (
            user_rows,
            daily_rows,
            last_active_rows,
            lines_rows,
            turns_rows,
            file_rows,
            success_rows,
            duration_rows,
        ) = await asyncio.gather(
            self._repo.get_users(f),
            self._repo.get_users_daily_activity(f),
            self._repo.get_users_last_active(f),
            self._repo.get_lines_by_user(f),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_session_durations(f),
        )

        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        success_by_session = {_s(r.get("session_id")): r for r in success_rows}
        last_active = {_s(r.get("developer_name")): r.get("last_active") for r in last_active_rows}
        net_lines_by_user = {_s(r.get("developer_name")): _i(r.get("net_lines")) for r in lines_rows}

        daily_by_user: dict[str, list[dict]] = {}
        for row in daily_rows:
            daily_by_user.setdefault(_s(row.get("developer_name")), []).append(
                {"day": _iso(row.get("day")), "session_count": _i(row.get("session_count"))}
            )

        rows: list[dict] = []
        for row in user_rows:
            name = _s(row.get("developer_name"))
            session_ids = [_s(s) for s in (row.get("session_ids") or [])]
            turns = sum(turns_by_session.get(sid, 0) for sid in session_ids)
            tool_calls = sum(_i(success_by_session.get(sid, {}).get("tool_calls")) for sid in session_ids)
            tool_ok = sum(_i(success_by_session.get(sid, {}).get("tool_calls_success")) for sid in session_ids)
            rows.append(
                {
                    "developer_name": name,
                    "session_count": _i(row.get("session_count")),
                    "input_tokens": _i(row.get("input_tokens")),
                    "output_tokens": _i(row.get("output_tokens")),
                    "cache_read_tokens": _i(row.get("cache_read_tokens")),
                    "cache_creation_tokens": _i(row.get("cache_creation_tokens")),
                    "cost_usd": _f(row.get("cost_usd")),
                    "turns": turns,
                    "tool_calls": tool_calls,
                    "tool_success_rate": _pct(tool_ok, tool_calls),
                    "top_model": _s(row.get("top_model")) or None,
                    "net_lines": net_lines_by_user.get(name, 0),
                    "last_active": _iso(last_active[name]) if last_active.get(name) else None,
                    "user_id": None,  # filled in by the router via UserIdentityResolver
                    "daily_activity": daily_by_user.get(name, []),
                }
            )

        durations = [_i(r.get("duration_ms")) for r in duration_rows]
        avg_duration = int(sum(durations) / len(durations)) if durations else None

        total_count = len(rows)
        if page is not None and per_page is not None:
            offset = page * per_page
            rows = rows[offset : offset + per_page]

        return {
            "rows": rows,
            "avg_session_duration_ms": avg_duration,
            "total_count": total_count,
        }

    # ── Repositories ─────────────────────────────────────────────────────────

    async def get_repositories(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        include_branches: bool,
        search: str | None = None,
    ) -> dict:
        (
            cost_facts,
            turns_rows,
            file_rows,
            success_rows,
            lines_rows,
        ) = await asyncio.gather(
            self._repo.get_session_cost_facts(f, search=search),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_lines_by_session(f),
        )

        # cost_facts already includes repository/branch/project_name via LEFT JOIN with
        # v_session_dimensions (NULL for plugin-less sessions that have no dimensions row).
        # Driving the bucket loop from cost_facts instead of a separate get_repository_sessions
        # call ensures plugin-less sessions appear in the unattributed bucket (CR-010).
        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        files_by_session = {_s(r.get("session_id")): r for r in file_rows}
        success_by_session = {_s(r.get("session_id")): r for r in success_rows}
        lines_by_session = {_s(r.get("session_id")): r for r in lines_rows}

        buckets: dict[tuple, dict] = {}
        for row in cost_facts:
            sid = _s(row.get("session_id"))
            repository = _s(row.get("repository")) or None
            branch = _s(row.get("branch")) or None
            key = (repository, branch) if include_branches else (repository,)
            bucket = buckets.setdefault(
                key,
                {
                    "repository": repository,
                    "branch": branch if include_branches else None,
                    "project_name": _s(row.get("project_name")) or None,
                    "session_count": 0,
                    "turns": 0,
                    "cost_usd": 0.0,
                    "files_changed": 0,
                    "lines_added": 0,
                    "lines_removed": 0,
                    "_tool_calls": 0,
                    "_tool_ok": 0,
                },
            )
            bucket["session_count"] += 1
            bucket["turns"] += turns_by_session.get(sid, 0)
            bucket["cost_usd"] += _f(row.get("cost_usd"))
            bucket["files_changed"] += _i(files_by_session.get(sid, {}).get("files_changed"))
            bucket["lines_added"] += _i(lines_by_session.get(sid, {}).get("lines_added"))
            bucket["lines_removed"] += _i(lines_by_session.get(sid, {}).get("lines_removed"))
            bucket["_tool_calls"] += _i(success_by_session.get(sid, {}).get("tool_calls"))
            bucket["_tool_ok"] += _i(success_by_session.get(sid, {}).get("tool_calls_success"))
            if not bucket["project_name"]:
                bucket["project_name"] = _s(row.get("project_name")) or None

        rows = []
        for bucket in buckets.values():
            tool_calls = bucket.pop("_tool_calls")
            tool_ok = bucket.pop("_tool_ok")
            bucket["tool_success_rate"] = _pct(tool_ok, tool_calls)
            bucket["net_lines"] = bucket["lines_added"] - bucket["lines_removed"]
            rows.append(bucket)

        rows.sort(key=lambda r: (r["session_count"], r["cost_usd"]), reverse=True)
        total_count = len(rows)

        if include_branches:
            page_rows = rows
            pagination = {
                "page": 0,
                "per_page": len(rows),
                "total_count": total_count,
                "has_more": False,
            }
        else:
            offset = page * per_page
            page_rows = rows[offset : offset + per_page]
            pagination = {
                "page": page,
                "per_page": per_page,
                "total_count": total_count,
                "has_more": (page + 1) * per_page < total_count,
            }

        return {"rows": page_rows, "pagination": pagination}

    # ── Sessions ─────────────────────────────────────────────────────────────

    async def get_sessions(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        sort_by: str,
        search: str | None,
        framework: str | None = None,
        is_unattributed: bool = False,
        branch: str | None = None,
    ) -> tuple[dict, list[str], str | None]:
        f.branch = branch or None
        cost_facts, turns_rows, success_rows, lines_rows, model_rows = await asyncio.gather(
            self._repo.get_session_cost_facts(f, is_unattributed=is_unattributed),
            self._repo.get_turns_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_lines_by_session(f),
            self._repo.get_model_breakdown(f),
        )

        session_ids = [_s(r.get("session_id")) for r in cost_facts]
        skill_rows = await self._repo.get_skill_names_by_session(session_ids)
        skills_by_session = {_s(r["session_id"]): r["skill_names"] for r in skill_rows}

        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        success_by_session = {_s(r.get("session_id")): r for r in success_rows}
        lines_by_session = {_s(r.get("session_id")): r for r in lines_rows}
        _cost, unpriced = self._price_cache_reads(model_rows)

        sessions: list[dict] = []
        for row in cost_facts:
            sid = _s(row.get("session_id"))
            model = _s(row.get("model_name"))
            cache_read = _i(row.get("cache_read_tokens"))
            cost = _f(row.get("cost_usd"))
            turns = turns_by_session.get(sid, 0)
            lines = lines_by_session.get(sid, {})
            started_at = row.get("started_at")
            last_event = row.get("last_event_at")
            duration_ms = 0
            if isinstance(started_at, datetime) and isinstance(last_event, datetime):
                duration_ms = max(0, int((last_event - started_at).total_seconds() * 1000))
            sessions.append(
                {
                    "trace_id": sid,
                    "developer_name": _s(row.get("developer_name")),
                    "repository": _s(row.get("repository")) or None,
                    "branch": _s(row.get("branch")) or None,
                    "prompt": _s(row.get("prompt")),
                    "start_time": _iso(started_at),
                    "duration_ms": duration_ms,
                    "model_name": model,
                    "turns": turns,
                    "tool_call_count": _i(success_by_session.get(sid, {}).get("tool_calls")),
                    "input_tokens": _i(row.get("input_tokens")),
                    "output_tokens": _i(row.get("output_tokens")),
                    "cache_read_tokens": cache_read,
                    "cache_creation_tokens": _i(row.get("cache_creation_tokens")),
                    "cost_usd": cost,
                    "net_lines": _i(lines.get("lines_added")) - _i(lines.get("lines_removed")),
                    "bloat_pct": _pct(cache_read_cost(model, cache_read), cost),
                    "delivery_framework": classify_delivery_framework(skills_by_session.get(sid, [])),
                    "_ctx_per_call": (cache_read / turns) if turns else 0.0,
                }
            )

        if search:
            term = search.strip().lower()
            sessions = [
                s
                for s in sessions
                if term in s["prompt"].lower()
                or term in (s["repository"] or "").lower()
                or term in (s["branch"] or "").lower()
            ]

        if framework:
            sessions = [s for s in sessions if s.get("delivery_framework") == framework]

        sort_key = {
            "start_time": lambda s: s["start_time"],
            "cost_usd": lambda s: s["cost_usd"],
            "ctx_per_call": lambda s: s["_ctx_per_call"],
        }.get(sort_by, lambda s: s["start_time"])
        sessions.sort(key=sort_key, reverse=True)

        total = len(sessions)
        offset = page * per_page
        page_rows = sessions[offset : offset + per_page]
        for row in page_rows:
            row.pop("_ctx_per_call", None)

        data_as_of = max((s["start_time"] for s in page_rows), default=None)
        return {"sessions": page_rows, "page": page, "per_page": per_page, "total": total}, unpriced, data_as_of

    # ── Session detail ───────────────────────────────────────────────────────

    async def get_session_detail(self, session_id: str) -> tuple[dict | None, list[str]]:
        meta_rows, cost_rows, scalar_rows, tool_rows, event_rows, dispatch_rows, skill_rows = await asyncio.gather(
            self._repo.get_session_detail_meta(session_id),
            self._repo.get_session_detail_cost(session_id),
            self._repo.get_session_detail_scalars(session_id),
            self._repo.get_session_detail_tools(session_id),
            self._repo.get_session_detail_events(session_id),
            self._repo.get_session_detail_dispatches(session_id),
            self._repo.get_skill_names_by_session([session_id]),
        )
        meta = _first(meta_rows)
        if not meta or not _s(meta.get("session_id")):
            return None, []

        cost = _first(cost_rows)
        scalars = _first(scalar_rows)
        model = _s(cost.get("model_name"))
        cache_read = _i(cost.get("cache_read_tokens"))
        cost_usd = _f(cost.get("cost_usd"))
        crc = cache_read_cost(model, cache_read)
        unpriced = [model] if (cache_read > 0 and model and lookup_cost_config(model) is None) else []

        tools = []
        tool_ok_total = 0
        for row in tool_rows:
            calls = _i(row.get("call_count"))
            ok = _i(row.get("success_count"))
            tool_ok_total += ok
            tools.append(
                {
                    "tool_name": _s(row.get("tool_name")),
                    "call_count": calls,
                    "success_count": ok,
                    "success_rate": _pct(ok, calls),
                }
            )

        events = [
            {
                "timestamp": _iso(r.get("timestamp")),
                "event_type": _s(r.get("event_type")),
                "model_name": _s(r.get("model_name")) or None,
                "input_tokens": _i(r.get("input_tokens")) if _s(r.get("event_type")) == "api_request" else None,
                "output_tokens": _i(r.get("output_tokens")) if _s(r.get("event_type")) == "api_request" else None,
                "cache_read_tokens": _i(r.get("cache_read_tokens"))
                if _s(r.get("event_type")) == "api_request"
                else None,
                "cost_usd": _f(r.get("cost_usd")) if _s(r.get("event_type")) == "api_request" else None,
                "tool_name": _s(r.get("tool_name")) or None,
            }
            for r in event_rows
        ]

        started_at = meta.get("started_at")
        duration_ms = _i(meta.get("duration_ms"))
        dispatches = self._build_dispatches(
            dispatch_rows,
            started_at,
            duration_ms,
            cost_usd,
            _i(cost.get("input_tokens")),
            _i(cost.get("output_tokens")),
            cache_read,
            _i(cost.get("cache_creation_tokens")),
        )

        lines_added = _i(scalars.get("lines_added"))
        lines_removed = _i(scalars.get("lines_removed"))

        detail = {
            "trace_id": _s(meta.get("session_id")),
            "developer_name": _s(meta.get("developer_name")),
            "repository": _s(meta.get("repository")) or None,
            "branch": _s(meta.get("branch")) or None,
            "prompt": _s(meta.get("prompt")),
            "start_time": _iso(started_at),
            "duration_ms": duration_ms,
            "model_name": model,
            "turns": _i(scalars.get("turns")),
            "tool_call_count": _i(scalars.get("tool_call_count")),
            "input_tokens": _i(cost.get("input_tokens")),
            "output_tokens": _i(cost.get("output_tokens")),
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": _i(cost.get("cache_creation_tokens")),
            "cost_usd": cost_usd,
            "net_lines": lines_added - lines_removed,
            "bloat_pct": _pct(crc, cost_usd),
            "events": events,
            "active_ms": _i(scalars.get("active_ms")),
            "agent_count": _i(scalars.get("agent_count")),
            "skill_count": _i(scalars.get("skill_count")),
            "cache_read_cost_usd": crc,
            "tool_calls_success": tool_ok_total,
            "tools": tools,
            "dispatches": dispatches,
        }
        skill_names = _first(skill_rows).get("skill_names") or []
        detail["delivery_framework"] = classify_delivery_framework(skill_names)
        return detail, unpriced

    @staticmethod
    def _build_dispatches(
        rows: list[dict],
        session_start: Any,
        session_duration_ms: int,
        session_cost: float,
        session_input_tokens: int = 0,
        session_output_tokens: int = 0,
        session_cache_read_tokens: int = 0,
        session_cache_creation_tokens: int = 0,
    ) -> list[dict]:
        """Row 0 is always the synthetic session lane, then agents/skills in start order."""
        dispatches: list[dict] = [
            {
                "label": "session",
                "kind": "session",
                "start_offset_ms": 0,
                "duration_ms": session_duration_ms,
                **_token_dict(
                    session_cost,
                    session_input_tokens,
                    session_output_tokens,
                    session_cache_read_tokens,
                    session_cache_creation_tokens,
                ),
            }
        ]
        if not rows or not isinstance(session_start, datetime):
            return dispatches

        type_totals = _count_dispatch_subtypes(rows)
        counters: dict[str, int] = {}
        for row in rows:
            entry = _dispatch_label_and_kind(row, type_totals, counters)
            if entry is None:
                continue
            label, kind = entry
            span_start = row.get("span_start")
            offset = int((span_start - session_start).total_seconds() * 1000) if isinstance(span_start, datetime) else 0
            dispatches.append(
                {
                    "label": label,
                    "kind": kind,
                    "start_offset_ms": offset,
                    "duration_ms": _i(row.get("real_duration_ms"))
                    if kind in ("skill", "command")
                    else _i(row.get("duration_ms")),
                    **_token_dict(
                        _f(row.get("cost_usd")),
                        _i(row.get("input_tokens")),
                        _i(row.get("output_tokens")),
                        _i(row.get("cache_read_tokens")),
                        _i(row.get("cache_creation_tokens")),
                    ),
                }
            )
        return dispatches

    # ── Activity ─────────────────────────────────────────────────────────────

    async def get_activity(self, f: LocalAnalyticsFilter) -> dict:
        rows = await self._repo.get_session_start_times(f)
        heat = [[0] * 24 for _ in range(7)]
        by_hour = [0] * 24
        by_weekday = [0] * 7
        for row in rows:
            started = row.get("started_at")
            if not isinstance(started, datetime):
                continue
            weekday = started.weekday()  # Monday=0 .. Sunday=6, matching the contract
            hour = started.hour
            heat[weekday][hour] += 1
            by_hour[hour] += 1
            by_weekday[weekday] += 1
        return {"heat": heat, "by_hour": by_hour, "by_weekday": by_weekday}

    # ── Tools ────────────────────────────────────────────────────────────────

    async def get_tools(self, f: LocalAnalyticsFilter) -> dict:
        tool_rows, model_rows, invocation_rows = await asyncio.gather(
            self._repo.get_tool_usage(f),
            self._repo.get_model_breakdown(f),
            self._repo.get_invocations(f),
        )
        tool_usage = []
        for row in tool_rows:
            calls = _i(row.get("call_count"))
            ok = _i(row.get("success_count"))
            tool_usage.append(
                {
                    "tool_name": _s(row.get("tool_name")),
                    "call_count": calls,
                    "success_count": ok,
                    "success_rate": _pct(ok, calls),
                }
            )
        tokens_by_model = sorted(
            (
                {"model_name": _s(r.get("model_name")), "total_tokens": _i(r.get("total_tokens"))}
                for r in model_rows
                if _i(r.get("total_tokens")) > 0
            ),
            key=lambda r: r["total_tokens"],
            reverse=True,
        )[:20]
        return {
            "tool_usage": tool_usage,
            "tokens_by_model": tokens_by_model,
            "skills_invoked": self._top_invocations(invocation_rows, "skill"),
            "agent_subtypes": self._top_invocations(invocation_rows, "agent"),
            "slash_commands": self._top_invocations(invocation_rows, "command"),
        }

    @staticmethod
    def _top_invocations(rows: list[dict], kind: str) -> list[dict]:
        items = [
            {"name": _s(r.get("name")), "count": _i(r.get("count"))}
            for r in rows
            if _s(r.get("kind")) == kind and _s(r.get("name"))
        ]
        items.sort(key=lambda r: r["count"], reverse=True)
        return items[:_MAX_INVOCATIONS]

    # ── Efficiency ───────────────────────────────────────────────────────────

    async def get_efficiency(self, f: LocalAnalyticsFilter) -> tuple[dict, list[str]]:
        kpi_rows, model_rows, cost_facts, turns_rows, file_rows, lines_totals = await asyncio.gather(
            self._repo.get_cost_kpis(f),
            self._repo.get_model_breakdown(f),
            self._repo.get_session_cost_facts(f),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_lines_totals(f),
        )

        kpi = _first(kpi_rows)
        total_sessions = _i(kpi.get("total_sessions"))
        total_cost = _f(kpi.get("total_cost_usd"))
        cache_read_tokens = _i(kpi.get("total_cache_read_tokens"))

        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        files_by_session = {_s(r.get("session_id")): r for r in file_rows}

        total_turns = sum(turns_by_session.get(_s(r.get("session_id")), 0) for r in cost_facts)
        cache_read_cost_usd, unpriced = self._price_cache_reads(model_rows)

        worst_ratio: float | None = None
        worst_prompt: str | None = None
        worst_trace: str | None = None
        depth_counts = dict.fromkeys(_DEPTH_BUCKETS, 0)
        for row in cost_facts:
            sid = _s(row.get("session_id"))
            turns = turns_by_session.get(sid, 0)
            ratio = (_i(row.get("cache_read_tokens")) / turns) if turns else 0.0
            if worst_ratio is None or ratio > worst_ratio:
                worst_ratio = ratio
                worst_prompt = _s(row.get("prompt")) or None
                worst_trace = sid
            depth_counts[depth_bucket(turns)] += 1

        if worst_prompt:
            worst_prompt = worst_prompt[:_PROMPT_PREVIEW_LEN]

        dead_count, wasted = self._dead_sessions(cost_facts, files_by_session)

        cost_session_ids = {_s(r.get("session_id")) for r in cost_facts}
        priced_files = [r for sid, r in files_by_session.items() if sid in cost_session_ids]
        files_changed = sum(_i(r.get("files_changed")) for r in priced_files)
        files_written = sum(_i(r.get("files_written")) for r in priced_files)
        files_edited = sum(_i(r.get("files_edited")) for r in priced_files)
        lines = _first(lines_totals)

        data = {
            "kpis": {
                "avg_context_per_call": (cache_read_tokens / total_turns) if total_turns else 0.0,
                "worst_session_ctx_per_call": worst_ratio,
                "worst_session_prompt": worst_prompt,
                "worst_session_trace_id": worst_trace,
                "cache_read_cost_usd": cache_read_cost_usd,
                "bloat_pct": _pct(cache_read_cost_usd, total_cost),
            },
            "dead_sessions": {
                "count": dead_count,
                "pct_of_sessions": _pct(dead_count, total_sessions),
                "wasted_cost_usd": wasted,
                "avg_cost_per_dead": (wasted / dead_count) if dead_count else None,
            },
            "session_depth": [{"bucket": b, "count": depth_counts[b]} for b in _DEPTH_BUCKETS],
            "code_changes": {
                "files_changed": files_changed,
                "files_written": files_written,
                "files_edited": files_edited,
                "net_lines": _i(lines.get("lines_added")) - _i(lines.get("lines_removed")),
            },
        }
        return data, unpriced


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
