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

"""Assembles Local Analytics responses from the CLI Analytics storage facts.

Works on any `CliAnalyticsReader`: it only calls the fact
queries and coerces values, so pricing, dead-session, depth and
delivery-framework logic live here. No response may depend on the row order of a query without a
total ORDER BY, or the same data could be answered differently.

Per-session facts are merged in Python rather than in one wide SQL statement.
At current volume (single-digit thousands of spans per week) this costs nothing
measurable and keeps each query independently verifiable. See
ANALYTICS_DISCOVERY/local_analytics_implementation_plan.md §9 item F1 for the
threshold at which this should move into a materialized fact table.
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timezone
from typing import Any

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import CliAnalyticsReader
from codemie.repository.cli_analytics.vocabulary import humanize_harness_name
from codemie.service.analytics.delivery_framework import classify_delivery_framework
from codemie.service.analytics.handlers.coding_agent_pricing import cache_read_cost, lookup_cost_config

logger = logging.getLogger(__name__)

_DEPTH_BUCKETS = ("1", "2-5", "6-10", "11-25", "26-50", "50+")
_MAX_INVOCATIONS = 10
_PROMPT_PREVIEW_LEN = 50


def depth_bucket(turns: int) -> str:
    if turns <= 1:
        return "1"
    if turns <= 5:
        return "2-5"
    if turns <= 10:
        return "6-10"
    if turns <= 25:
        return "11-25"
    if turns <= 50:
        return "26-50"
    return "50+"


# ── Coercion helpers (the reader may return Decimal, None or int) ───────────────


def _i(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _clamp_to_session(duration_ms: int | None, start_offset_ms: int, session_duration_ms: int) -> int | None:
    """Keep start + duration within the session: a subagent that stopped after the session ended is cut at its end."""
    if duration_ms is None or start_offset_ms >= session_duration_ms:
        return None
    return min(duration_ms, session_duration_ms - start_offset_ms)


def _duration_for_kind(row: dict, kind: str) -> int | None:
    """Skills and commands: the interaction span. Agents: the subagent's call-to-stop run time, None if unknown."""
    if kind in ("skill", "command"):
        return _i(row.get("real_duration_ms"))
    if kind == "agent":
        value = row.get("subagent_duration_ms")
        return None if value is None else _i(value)
    return _i(row.get("duration_ms"))


def _bar_position(row: dict, kind: str, session_start: datetime, session_duration_ms: int) -> tuple[int, int | None]:
    """A dispatch bar's start offset and duration in ms from the session start.

    An agent bar with no known start, or a start before the session, cannot be placed: offset 0 and no duration.
    Otherwise an agent's run is clamped to the session end.
    """
    dispatch_start = row.get("dispatch_start")
    placed = isinstance(dispatch_start, datetime)
    offset = int((dispatch_start - session_start).total_seconds() * 1000) if placed else 0
    duration = _duration_for_kind(row, kind)
    if kind != "agent":
        return offset, duration
    if not placed or offset < 0:
        return 0, None
    return offset, _clamp_to_session(duration, offset, session_duration_ms)


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
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _s(value)


def _pct(numerator: float, denominator: float) -> float:
    """Percent to one decimal using half-up rounding for nonnegative counts.

    Keep one backend rounding rule for analytics percentages, including repository
    and branch rollups: 1/16 is 6.3%, not 6.2%. Clients display the server value.
    """
    if not denominator:
        return 0.0
    return math.floor(numerator / denominator * 1000 + 0.5) / 10


def _ctx_per_call(cache_read_tokens: Any, api_call_count: Any) -> float:
    """Cache-read tokens per model API call, zero-guarded. The single shared
    computation reused by get_overview, get_sessions and get_efficiency —
    none of them may reimplement this division inline."""
    calls = _i(api_call_count)
    return (_i(cache_read_tokens) / calls) if calls else 0.0


def _first(rows: list[dict]) -> dict:
    return rows[0] if rows else {}


def _float_sort_key(value: float) -> float:
    """Sort key for a float computed from sums: the same values added in another order
    can differ in the last bits, which must not reorder otherwise equal rows."""
    return round(value, 9)


def _most_frequent(counts: dict[str, int]) -> str | None:
    """The most frequent value; ties go to the alphabetically first."""
    return min(counts, key=lambda value: (-counts[value], value)) if counts else None


def _new_repository_bucket(repository: str | None, branch: str | None, project: str | None) -> dict:
    return {
        "repository": repository,
        "branch": branch,
        "project_name": project,
        "session_count": 0,
        "turns": 0,
        "cost_usd": 0.0,
        "files_changed": 0,
        "lines_added": 0,
        "lines_removed": 0,
        "_tool_calls": 0,
        "_tool_ok": 0,
        "_projects": {},
    }


def _finalize_repository_bucket(bucket: dict, include_branches: bool) -> dict:
    bucket["tool_calls"] = bucket.pop("_tool_calls")
    bucket["tool_calls_success"] = bucket.pop("_tool_ok")
    bucket["tool_success_rate"] = _pct(bucket["tool_calls_success"], bucket["tool_calls"])
    projects = bucket.pop("_projects")
    if not include_branches:
        # A repository can be worked on from several projects: label it with the one
        # most of its sessions belong to, not whichever session came first.
        bucket["project_name"] = _most_frequent(projects)
    bucket["net_lines"] = bucket["lines_added"] - bucket["lines_removed"]
    return bucket


_BRANCH_ROW_FIELDS = (
    "branch",
    "session_count",
    "turns",
    "cost_usd",
    "files_changed",
    "lines_added",
    "lines_removed",
    "net_lines",
    "tool_calls",
    "tool_calls_success",
    "tool_success_rate",
)
_REPO_SUM_FIELDS = (
    "session_count",
    "turns",
    "cost_usd",
    "files_changed",
    "lines_added",
    "lines_removed",
    "tool_calls",
    "tool_calls_success",
)


def _group_branches_into_repositories(branch_rows: list[dict]) -> list[dict]:
    """Rolls finalized per-branch rows up into (project, repository) groups: sums raw counts
    first and calls _pct once per group, so a branch's percentage is never averaged into the
    repository's (EPMCDME-15585) -- a zero-call branch contributes 0/0 to the sums, not a 0%
    vote weighted by session count."""
    repos: dict[tuple, dict] = {}
    for row in branch_rows:
        key = (row["project_name"], row["repository"])
        repo = repos.setdefault(
            key,
            {
                "repository": row["repository"],
                "project_name": row["project_name"],
                "session_count": 0,
                "turns": 0,
                "cost_usd": 0.0,
                "files_changed": 0,
                "lines_added": 0,
                "lines_removed": 0,
                "tool_calls": 0,
                "tool_calls_success": 0,
                "branches": [],
            },
        )
        for field in _REPO_SUM_FIELDS:
            repo[field] += row[field]
        repo["branches"].append({field: row[field] for field in _BRANCH_ROW_FIELDS})

    out = list(repos.values())
    for repo in out:
        repo["net_lines"] = repo["lines_added"] - repo["lines_removed"]
        repo["tool_success_rate"] = _pct(repo["tool_calls_success"], repo["tool_calls"])
        repo["branches"].sort(key=lambda b: b["branch"] or "")
    out.sort(key=lambda r: (r["repository"] or "", r["project_name"] or ""))
    out.sort(key=lambda r: (r["session_count"], _float_sort_key(r["cost_usd"])), reverse=True)
    return out


def _build_repository_rows(
    cost_facts: list[dict],
    turns_by_session: dict[str, int],
    files_by_session: dict[str, dict],
    success_by_session: dict[str, dict],
    lines_by_session: dict[str, dict],
    include_branches: bool,
) -> list[dict]:
    """Bucket sessions by (project, repository, branch) with branches, else by repository only.
    include_branches=True rolls the branch buckets up into (project, repository) groups, each
    carrying its own backend-computed tool_success_rate and its branches nested underneath, so
    the UI never recomputes or re-averages the percentage (EPMCDME-15585)."""
    buckets: dict[tuple, dict] = {}
    for row in cost_facts:
        sid = _s(row.get("session_id"))
        repository = _s(row.get("repository")) or None
        branch = _s(row.get("branch")) or None
        project = _s(row.get("project_name")) or None
        if include_branches:
            key, bucket_branch, bucket_project = (project, repository, branch), branch, project
        else:
            key, bucket_branch, bucket_project = (repository,), None, None
        bucket = buckets.setdefault(key, _new_repository_bucket(repository, bucket_branch, bucket_project))
        bucket["session_count"] += 1
        bucket["turns"] += turns_by_session.get(sid, 0)
        bucket["cost_usd"] += _f(row.get("cost_usd"))
        bucket["files_changed"] += _i(files_by_session.get(sid, {}).get("files_changed"))
        bucket["lines_added"] += _i(lines_by_session.get(sid, {}).get("lines_added"))
        bucket["lines_removed"] += _i(lines_by_session.get(sid, {}).get("lines_removed"))
        bucket["_tool_calls"] += _i(success_by_session.get(sid, {}).get("tool_calls"))
        bucket["_tool_ok"] += _i(success_by_session.get(sid, {}).get("tool_calls_success"))
        if not include_branches and project:
            bucket["_projects"][project] = bucket["_projects"].get(project, 0) + 1

    rows = [_finalize_repository_bucket(bucket, include_branches) for bucket in buckets.values()]
    if include_branches:
        return _group_branches_into_repositories(rows)
    rows.sort(key=lambda r: (r["repository"] or "", r["project_name"] or ""))
    rows.sort(key=lambda r: (r["session_count"], _float_sort_key(r["cost_usd"])), reverse=True)
    return rows


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
    def __init__(self, repo: CliAnalyticsReader) -> None:
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

    @staticmethod
    def _display_models(model_cost_rows: list[dict], fallback: str) -> str:
        """All of a session's models, comma-separated, in alphabetical order.

        Falls back to `fallback` (the session's most-called model) when there are
        no per-model rows, keeping single-model sessions unchanged.
        """
        names = sorted(_s(row.get("model_name")) for row in model_cost_rows if _s(row.get("model_name")))
        if not names:
            return fallback
        return ", ".join(names)

    @classmethod
    def _session_cache_read_cost(
        cls, model_cost_rows: list[dict], model: str, cache_read: int
    ) -> tuple[float, list[str]]:
        """Cache-read cost + unpriced models for one session.

        Per-model rows (when available) price each model's own cache-read tokens at its own
        rate; otherwise falls back to pricing the whole total at the single most-called model,
        keeping single-model sessions byte-identical to the pre-per-model calculation.
        """
        if model_cost_rows:
            return cls._price_cache_reads(model_cost_rows)
        unpriced = [model] if (cache_read > 0 and model and lookup_cost_config(model) is None) else []
        return cache_read_cost(model, cache_read), unpriced

    @staticmethod
    def _group_by_session(rows: list[dict]) -> dict[str, list[dict]]:
        """Group per-model cost rows (one row per session+model) by session_id."""
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(_s(row.get("session_id")), []).append(row)
        return grouped

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
        total_api_calls = _i(kpi.get("total_api_calls"))

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
            "avg_context_per_call": _ctx_per_call(cache_read_tokens, total_api_calls),
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
                {
                    "developer_name": _s(r.get("developer_name")),
                    "user_email": _s(r.get("developer_name")),
                    "cost_usd": _f(r.get("cost_usd")),
                }
                for r in by_user
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
                    "user_email": name,
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

        # D1 rule: average duration over priced sessions only (same universe as the overview)
        priced_sessions = {_s(s) for row in user_rows for s in (row.get("session_ids") or [])}
        durations = [_i(r.get("duration_ms")) for r in duration_rows if _s(r.get("session_id")) in priced_sessions]
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

        rows = _build_repository_rows(
            cost_facts,
            turns_by_session,
            files_by_session,
            success_by_session,
            lines_by_session,
            include_branches,
        )
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
        cost_facts, turns_rows, success_rows, lines_rows, model_rows, session_model_rows = await asyncio.gather(
            self._repo.get_session_cost_facts(f, is_unattributed=is_unattributed),
            self._repo.get_turns_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_lines_by_session(f),
            self._repo.get_model_breakdown(f),
            self._repo.get_session_model_costs(f),
        )

        session_ids = [_s(r.get("session_id")) for r in cost_facts]
        skill_rows = await self._repo.get_skill_names_by_session(session_ids)
        skills_by_session = {_s(r["session_id"]): r["skill_names"] for r in skill_rows}

        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        success_by_session = {_s(r.get("session_id")): r for r in success_rows}
        lines_by_session = {_s(r.get("session_id")): r for r in lines_rows}
        _cost, unpriced = self._price_cache_reads(model_rows)

        model_rows_by_session = self._group_by_session(session_model_rows)

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
            model_cost_rows = model_rows_by_session.get(sid, [])
            crc = self._session_cache_read_cost(model_cost_rows, model, cache_read)[0]
            sessions.append(
                {
                    "trace_id": sid,
                    "developer_name": _s(row.get("developer_name")),
                    "user_email": _s(row.get("developer_name")),
                    "repository": _s(row.get("repository")) or None,
                    "branch": _s(row.get("branch")) or None,
                    "prompt": _s(row.get("prompt")),
                    "start_time": _iso(started_at),
                    "duration_ms": duration_ms,
                    "model_name": self._display_models(model_cost_rows, model),
                    "turns": turns,
                    "tool_call_count": _i(success_by_session.get(sid, {}).get("tool_calls")),
                    "api_call_count": _i(row.get("api_call_count")),
                    "input_tokens": _i(row.get("input_tokens")),
                    "output_tokens": _i(row.get("output_tokens")),
                    "cache_read_tokens": cache_read,
                    "cache_creation_tokens": _i(row.get("cache_creation_tokens")),
                    "cost_usd": cost,
                    "net_lines": _i(lines.get("lines_added")) - _i(lines.get("lines_removed")),
                    "bloat_pct": _pct(crc, cost),
                    "delivery_framework": classify_delivery_framework(skills_by_session.get(sid, [])),
                    "ctx_per_call": _ctx_per_call(row.get("cache_read_tokens"), row.get("api_call_count")),
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
            "cost_usd": lambda s: _float_sort_key(s["cost_usd"]),
            "ctx_per_call": lambda s: _float_sort_key(s["ctx_per_call"]),
        }.get(sort_by, lambda s: s["start_time"])
        # Equal sort values (e.g. sessions without plugin data share one start time) keep
        # a stable trace_id order, so pages neither repeat nor skip sessions.
        sessions.sort(key=lambda s: s["trace_id"])
        sessions.sort(key=sort_key, reverse=True)

        total = len(sessions)
        offset = page * per_page
        page_rows = sessions[offset : offset + per_page]

        data_as_of = max((s["start_time"] for s in page_rows), default=None)
        return {"sessions": page_rows, "page": page, "per_page": per_page, "total": total}, unpriced, data_as_of

    # ── Session detail ───────────────────────────────────────────────────────

    async def get_session_detail(self, session_id: str) -> tuple[dict | None, list[str]]:
        (
            meta_rows,
            cost_rows,
            scalar_rows,
            tool_rows,
            event_rows,
            dispatch_rows,
            skill_rows,
            model_cost_rows,
        ) = await asyncio.gather(
            self._repo.get_session_detail_meta(session_id),
            self._repo.get_session_detail_cost(session_id),
            self._repo.get_session_detail_scalars(session_id),
            self._repo.get_session_detail_tools(session_id),
            self._repo.get_session_detail_events(session_id),
            self._repo.get_session_detail_dispatches(session_id),
            self._repo.get_skill_names_by_session([session_id]),
            self._repo.get_session_detail_model_costs(session_id),
        )
        meta = _first(meta_rows)
        if not meta or not _s(meta.get("session_id")):
            return None, []

        cost = _first(cost_rows)
        scalars = _first(scalar_rows)
        model = _s(cost.get("model_name"))
        cache_read = _i(cost.get("cache_read_tokens"))
        cost_usd = _f(cost.get("cost_usd"))
        crc, unpriced = self._session_cache_read_cost(model_cost_rows, model, cache_read)
        display_model = self._display_models(model_cost_rows, model)

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
            "user_email": _s(meta.get("developer_name")),
            "repository": _s(meta.get("repository")) or None,
            "branch": _s(meta.get("branch")) or None,
            "prompt": _s(meta.get("prompt")),
            "start_time": _iso(started_at),
            "duration_ms": duration_ms,
            "model_name": display_model,
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
            "tool_success_rate": _pct(tool_ok_total, _i(scalars.get("tool_call_count"))),
            "tools": tools,
            "dispatches": dispatches,
        }
        skill_names = _first(skill_rows).get("skill_names") or []
        detail["delivery_framework"] = classify_delivery_framework(skill_names)
        detail["harness_name"] = humanize_harness_name(meta.get("harness_service_name"))
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
            offset, duration = _bar_position(row, kind, session_start, session_duration_ms)
            dispatches.append(
                {
                    "label": label,
                    "kind": kind,
                    "start_offset_ms": offset,
                    "duration_ms": duration,
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
        items.sort(key=lambda r: (-r["count"], r["name"]))
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
        total_api_calls = _i(kpi.get("total_api_calls"))

        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        files_by_session = {_s(r.get("session_id")): r for r in file_rows}

        cache_read_cost_usd, unpriced = self._price_cache_reads(model_rows)

        worst_ratio: float | None = None
        worst_prompt: str | None = None
        worst_trace: str | None = None
        depth_counts = dict.fromkeys(_DEPTH_BUCKETS, 0)
        for row in cost_facts:
            sid = _s(row.get("session_id"))
            turns = turns_by_session.get(sid, 0)
            ratio = _ctx_per_call(row.get("cache_read_tokens"), row.get("api_call_count"))
            # Highest ratio wins; a tie goes to the lowest session id, not to row order.
            if worst_ratio is None or ratio > worst_ratio or (ratio == worst_ratio and sid < worst_trace):
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
                "avg_context_per_call": _ctx_per_call(cache_read_tokens, total_api_calls),
                "worst_session_ctx_per_call": worst_ratio,
                "worst_session_prompt": worst_prompt,
                "worst_session_trace_id": worst_trace,
                "cache_read_cost_usd": cache_read_cost_usd,
                "bloat_pct": _pct(cache_read_cost_usd, total_cost),
                "total_cost_usd": total_cost,
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
