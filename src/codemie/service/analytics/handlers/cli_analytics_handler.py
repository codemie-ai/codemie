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
from codemie.repository.cli_analytics.ports import SESSION_PAGE_SORTS, CliAnalyticsReader
from codemie.repository.cli_analytics.vocabulary import BLANK_CHARS, humanize_harness_name, strip_pasted_content
from codemie.service.analytics.cli_analytics_project_gate import (
    NOTE_FOLDER_NOT_LINKED,
    NOTE_LINK_UNAVAILABLE,
    NOTE_REGISTERED_INACCESSIBLE,
    NOTE_REGISTRATION_AMBIGUOUS,
    ORIGIN_DEFAULT,
)
from codemie.service.analytics.delivery_framework import classify_delivery_framework
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, normalize_model
from codemie.service.analytics.handlers.session_money import (
    dispatch_money,
    has_priced_model,
    price_session_models,
    priced_session_ids,
    token_counts,
)

_DEPTH_BUCKETS = ("1", "2-5", "6-10", "11-25", "26-50", "50+")
_MAX_INVOCATIONS = 10
_PROMPT_PREVIEW_LEN = 50

logger = logging.getLogger(__name__)


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


def _opt_int(value: Any) -> int | None:
    """A count that may be unknown: None stays None, a real zero stays 0."""
    return None if value is None else _i(value)


def _summary_fields(row: dict[str, Any]) -> dict[str, Any]:
    """The latest summary's state and the metrics it reports (turns, tool_calls, agents, skills): a metric is complete
    only when it is listed here and the state is final; observed counts alone say nothing about completeness."""
    return {
        "summary_state": _s(row.get("summary_state")) or "none",
        "summary_metrics": sorted(row.get("summary_metrics") or []),
    }


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
    """Skills and commands: the interaction span, or a forked skill's run. Agents: the subagent's run time.
    None when unknown (a fork or an agent that has not stopped)."""
    if kind in ("skill", "command"):
        value = row.get("real_duration_ms")
        return None if value is None or _i(value) < 0 else _i(value)
    if kind == "agent":
        value = row.get("subagent_duration_ms")
        return None if value is None else _i(value)
    return _i(row.get("duration_ms"))


def _bar_position(row: dict, kind: str, session_start: datetime, session_duration_ms: int) -> tuple[int, int | None]:
    """A dispatch bar's start offset and duration in ms from the session start.

    An agent bar with no known start, or a start before the session, cannot be placed: offset 0 and no duration.
    A skill or command bar the session cannot clamp (before its start, at or after its end) keeps its duration.
    Otherwise a run is clamped to the session end.
    """
    dispatch_start = row.get("dispatch_start")
    placed = isinstance(dispatch_start, datetime)
    offset = int((dispatch_start - session_start).total_seconds() * 1000) if placed else 0
    duration = _duration_for_kind(row, kind)
    if kind != "agent":
        if offset < 0 or offset >= session_duration_ms:
            return offset, duration
        return offset, _clamp_to_session(duration, offset, session_duration_ms)
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


def _served_prompt(title: object, first_prompt: object, repository: object, branch: object) -> str:
    """The session's name when it has one, else its first prompt without paste markup.

    Rows stored before the rollup stripped the markup are cleaned here too. A first prompt that cleans to
    nothing (an old-CLI prompt cut inside the open tag) is served as the repository, else the branch.
    """
    return _s(title).strip(BLANK_CHARS) or strip_pasted_content(_s(first_prompt)) or _s(repository) or _s(branch)


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _s(value)


def _ni(value: Any) -> int | None:
    """`_i` that keeps unknown (None) as None: a lines figure is null when unreported, 0 only for a real 0."""
    return None if value is None else _i(value)


def _nsum(*values: int | None) -> int | None:
    """Sum of the known values; None only when every input is unknown (never null-if-any-unknown)."""
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _nnet(added: int | None, removed: int | None) -> int | None:
    """added - removed, None when both are unknown; one known side treats the other as 0."""
    if added is None and removed is None:
        return None
    return (added or 0) - (removed or 0)


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


FOLDER_NOT_LINKED_LABEL = "folder not linked to a project"
_DEFAULT_FALLBACK_NOTES = frozenset(
    {NOTE_FOLDER_NOT_LINKED, NOTE_LINK_UNAVAILABLE, NOTE_REGISTERED_INACCESSIBLE, NOTE_REGISTRATION_AMBIGUOUS}
)


def _routing(row: dict) -> dict[str, str | None]:
    """The stored `route_note` as is (None when missing or blank, never guessed) plus the public label, set only for a
    default-routed session whose note says the folder had no usable project link (including an inaccessible link)."""
    note = _s(row.get("route_note")).strip() or None
    default_fallback = _s(row.get("project_origin")) == ORIGIN_DEFAULT and note in _DEFAULT_FALLBACK_NOTES
    return {"route_note": note, "routing_label": FOLDER_NOT_LINKED_LABEL if default_fallback else None}


def _lists(row: dict | None) -> dict[str, list[str]]:
    """Sorted distinct classes of an entity row (`subscription_types`/`project_origins` arrays). The reader already
    returns the response vocabulary (scope.sql_*; `none` is not re-normalisable), so values are only de-duplicated
    and sorted; an entity without a class row lists nothing."""
    row = row or {}
    return {
        "subscription_types": sorted({_s(v) or "unknown" for v in row.get("subscription_types") or []}),
        "project_origins": sorted({_s(v) or "none" for v in row.get("project_origins") or []}),
    }


def _union_lists(by_session: dict[str, dict], session_ids: Any) -> dict[str, list[str]]:
    """Class lists of exactly the given contributing sessions (`by_session` = get_class_lists(f, "session"))."""
    rows = [by_session[sid] for sid in session_ids if sid in by_session]
    return {
        "subscription_types": sorted({v for lists in map(_lists, rows) for v in lists["subscription_types"]}),
        "project_origins": sorted({v for lists in map(_lists, rows) for v in lists["project_origins"]}),
    }


def _by_key(rows: list[dict]) -> dict[str, dict]:
    return {_s(r.get("key")): r for r in rows}


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
        "lines_added": None,
        "lines_removed": None,
        "_tool_calls": 0,
        "_tool_ok": 0,
        "_projects": {},
        "_subs": set(),
        "_origins": set(),
    }


def _finalize_repository_bucket(bucket: dict, include_branches: bool) -> dict:
    bucket["tool_calls"] = bucket.pop("_tool_calls")
    bucket["tool_calls_success"] = bucket.pop("_tool_ok")
    bucket["tool_success_rate"] = _pct(bucket["tool_calls_success"], bucket["tool_calls"])
    projects = bucket.pop("_projects")
    bucket["subscription_types"] = sorted(bucket.pop("_subs"))
    bucket["project_origins"] = sorted(bucket.pop("_origins"))
    if not include_branches:
        # A repository can be worked on from several projects: label it with the one
        # most of its sessions belong to, not whichever session came first.
        bucket["project_name"] = _most_frequent(projects)
    bucket["net_lines"] = _nnet(bucket["lines_added"], bucket["lines_removed"])
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
    "subscription_types",
    "project_origins",
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


_NULLABLE_SUM_FIELDS = frozenset({"lines_added", "lines_removed"})


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
                "lines_added": None,
                "lines_removed": None,
                "tool_calls": 0,
                "tool_calls_success": 0,
                "branches": [],
                "subscription_types": [],
                "project_origins": [],
            },
        )
        for field in _REPO_SUM_FIELDS:
            if field in _NULLABLE_SUM_FIELDS:
                repo[field] = _nsum(repo[field], row[field])
            else:
                repo[field] += row[field]
        for field in ("subscription_types", "project_origins"):
            repo[field] = sorted({*repo[field], *row[field]})
        repo["branches"].append({field: row[field] for field in _BRANCH_ROW_FIELDS})

    out = list(repos.values())
    for repo in out:
        repo["net_lines"] = _nnet(repo["lines_added"], repo["lines_removed"])
        repo["tool_success_rate"] = _pct(repo["tool_calls_success"], repo["tool_calls"])
        repo["branches"].sort(key=lambda b: b["branch"] or "")
    out.sort(key=lambda r: (r["repository"] or "", r["project_name"] or ""))
    out.sort(key=lambda r: (r["session_count"], _float_sort_key(r["cost_usd"])), reverse=True)
    return out


def _build_repository_rows(
    cost_facts: list[dict],
    session_money: dict[str, float],
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
        bucket["cost_usd"] += session_money.get(sid, 0.0)
        bucket["files_changed"] += _i(files_by_session.get(sid, {}).get("files_changed"))
        bucket["lines_added"] = _nsum(bucket["lines_added"], _ni(lines_by_session.get(sid, {}).get("lines_added")))
        bucket["lines_removed"] = _nsum(
            bucket["lines_removed"], _ni(lines_by_session.get(sid, {}).get("lines_removed"))
        )
        bucket["_tool_calls"] += _i(success_by_session.get(sid, {}).get("tool_calls"))
        bucket["_tool_ok"] += _i(success_by_session.get(sid, {}).get("tool_calls_success"))
        bucket["_subs"].add(_s(row.get("subscription_type")) or "unknown")
        bucket["_origins"].add(_s(row.get("project_origin")) or "none")
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
    def _display_models(model_cost_rows: list[dict], fallback: str) -> str:
        """All of a session's models, comma-separated, in alphabetical order.

        Falls back to `fallback` (the session's most-called model) when there are
        no per-model rows, keeping single-model sessions unchanged.
        """
        names = sorted({normalize_model(_s(row.get("model_name"))) for row in model_cost_rows} - {""})
        if not names:
            return fallback
        return ", ".join(names)

    @staticmethod
    def _group_by_session(rows: list[dict]) -> dict[str, list[dict]]:
        """Group per-model cost rows (one row per session+model) by session_id."""
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            grouped.setdefault(_s(row.get("session_id")), []).append(row)
        return grouped

    # ── Overview ─────────────────────────────────────────────────────────────

    async def get_overview(self, f: LocalAnalyticsFilter) -> tuple[dict[str, Any], CatalogPricer, str | None]:
        (
            kpi_rows,
            model_rows,
            lines_totals,
            lines_daily,
            turns_rows,
            file_rows,
            success_rows,
            duration_rows,
            session_model_rows,
            all_classes,
            model_classes,
            day_classes,
        ) = await asyncio.gather(
            self._repo.get_cost_kpis(f),
            self._repo.get_model_breakdown(f),
            self._repo.get_lines_totals(f),
            self._repo.get_lines_daily(f),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_session_durations(f),
            self._repo.get_session_model_costs(f, spend_only=True),
            self._repo.get_class_lists(f, "all"),
            self._repo.get_class_lists(f, "model"),
            self._repo.get_class_lists(f, "day"),
        )

        money = price_session_models(session_model_rows)
        kpi = _first(kpi_rows)
        total_sessions = _i(kpi.get("total_sessions"))
        total_cost = money.total
        input_tokens = _i(kpi.get("total_input_tokens"))
        output_tokens = _i(kpi.get("total_output_tokens"))
        cache_read_tokens = _i(kpi.get("total_cache_read_tokens"))
        cache_creation_tokens = _i(kpi.get("total_cache_creation_tokens"))
        total_api_calls = _i(kpi.get("total_api_calls"))

        # Every roll-up below is already scoped to `sel` in its own reader query: the same
        # costless-inclusive universe `total_sessions` above now counts.
        total_turns = sum(_i(r.get("turns")) for r in turns_rows)
        files_by_session = {_s(r.get("session_id")): r for r in file_rows}
        total_files_changed = sum(_i(r.get("files_changed")) for r in files_by_session.values())
        tool_calls = sum(_i(r.get("tool_calls")) for r in success_rows)
        tool_success = sum(_i(r.get("tool_calls_success")) for r in success_rows)
        duration_ms = sum(_i(r.get("duration_ms")) for r in duration_rows)

        lines = _first(lines_totals)
        net_lines = _nnet(_ni(lines.get("lines_added")), _ni(lines.get("lines_removed")))

        dead_sessions, _wasted = self._dead_sessions(money.by_session, files_by_session)

        cache_read_cost_usd = money.cache_read
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
            **_lists(_first(all_classes)),
        }

        by_day, by_model = _by_key(day_classes), _by_key(model_classes)
        daily_buckets = [
            {"day": _iso(r.get("day")), "net_lines": _ni(r.get("net_lines")), **_lists(by_day.get(_iso(r.get("day"))))}
            for r in lines_daily
        ]
        model_breakdown = [
            {
                "model_name": _s(r.get("model_name")),
                "session_count": _i(r.get("session_count")),
                **_lists(by_model.get(_s(r.get("model_name")))),
            }
            for r in model_rows
        ]

        data_as_of = max((_iso(r.get("day")) for r in lines_daily if r.get("nonzero")), default=None)
        return (
            {"kpis": kpis, "daily_buckets": daily_buckets, "model_breakdown": model_breakdown},
            money.pricer,
            data_as_of,
        )

    @staticmethod
    def _dead_sessions(
        session_money: dict[str, float], files_by_session: dict[str, dict[str, Any]]
    ) -> tuple[int, float]:
        """A session that cost money but touched no file. Returns (count, wasted_usd)."""
        ids = LocalAnalyticsHandler._dead_ids(session_money, files_by_session)
        return len(ids), sum(session_money[sid] for sid in ids)

    @staticmethod
    def _dead_ids(session_money: dict[str, float], files_by_session: dict[str, dict[str, Any]]) -> list[str]:
        return [
            sid
            for sid, cost in session_money.items()
            if cost > 0 and _i(files_by_session.get(sid, {}).get("files_changed")) == 0
        ]

    # ── Cost ─────────────────────────────────────────────────────────────────

    async def get_cost(self, f: LocalAnalyticsFilter) -> tuple[dict[str, Any], CatalogPricer]:
        kpi_rows, by_user, session_model_rows, all_classes, model_classes = await asyncio.gather(
            self._repo.get_cost_kpis(f),
            self._repo.get_cost_by_user(f),
            self._repo.get_session_model_costs(f, spend_only=True),
            self._repo.get_class_lists(f, "all"),
            self._repo.get_class_lists(f, "model"),
        )
        by_model = _by_key(model_classes)
        money = price_session_models(session_model_rows)
        kpi = _first(kpi_rows)
        total_sessions = _i(kpi.get("total_sessions"))
        total_cost = money.total
        spend_sessions = _i(kpi.get("spend_sessions"))
        total_tokens = (
            _i(kpi.get("total_input_tokens"))
            + _i(kpi.get("total_output_tokens"))
            + _i(kpi.get("total_cache_read_tokens"))
            + _i(kpi.get("total_cache_creation_tokens"))
        )
        cost_by_user = [
            {
                "developer_name": _s(r.get("developer_name")),
                "user_email": _s(r.get("developer_name")),
                "cost_usd": money.of_sessions([_s(sid) for sid in (r.get("session_ids") or [])]),
                **_lists(r),
            }
            for r in by_user
        ]
        cost_by_user.sort(key=lambda r: r["developer_name"])
        cost_by_user.sort(key=lambda r: _float_sort_key(r["cost_usd"]), reverse=True)
        cost_by_model = sorted(
            (
                {"model_name": name, "cost_usd": cost, **_lists(by_model.get(name))}
                for name, cost in money.by_model.items()
            ),
            key=lambda r: (-_float_sort_key(r["cost_usd"]), r["model_name"]),
        )
        data = {
            "kpis": {
                "total_sessions": total_sessions,
                "total_cost_usd": total_cost,
                "total_tokens": total_tokens,
                "avg_cost_per_session": (total_cost / spend_sessions) if spend_sessions else 0.0,
                **_lists(_first(all_classes)),
            },
            "cost_by_user": cost_by_user,
            "cost_by_model": cost_by_model,
        }
        return data, money.pricer

    # ── Users ────────────────────────────────────────────────────────────────

    async def get_users(
        self, f: LocalAnalyticsFilter, page: int | None, per_page: int | None
    ) -> tuple[dict[str, Any], CatalogPricer]:
        (
            user_rows,
            daily_rows,
            last_active_rows,
            lines_rows,
            turns_rows,
            file_rows,
            success_rows,
            duration_rows,
            session_model_rows,
        ) = await asyncio.gather(
            self._repo.get_users(f),
            self._repo.get_users_daily_activity(f),
            self._repo.get_users_last_active(f),
            self._repo.get_lines_by_user(f),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_session_durations(f),
            self._repo.get_session_model_costs(f, spend_only=True),
        )

        money = price_session_models(session_model_rows)
        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        success_by_session = {_s(r.get("session_id")): r for r in success_rows}
        last_active = {_s(r.get("developer_name")): r.get("last_active") for r in last_active_rows}
        net_lines_by_user = {_s(r.get("developer_name")): _ni(r.get("net_lines")) for r in lines_rows}

        daily_by_user: dict[str, list[dict]] = {}
        for row in daily_rows:
            daily_by_user.setdefault(_s(row.get("developer_name")), []).append(
                {"day": _iso(row.get("day")), "session_count": _i(row.get("session_count")), **_lists(row)}
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
                    "cost_usd": money.of_sessions(session_ids),
                    "turns": turns,
                    "tool_calls": tool_calls,
                    "tool_success_rate": _pct(tool_ok, tool_calls),
                    "top_model": _s(row.get("top_model")) or None,
                    "net_lines": net_lines_by_user.get(name),
                    "last_active": _iso(last_active[name]) if last_active.get(name) else None,
                    "user_id": None,  # filled in by the router via UserIdentityResolver
                    "daily_activity": daily_by_user.get(name, []),
                    **_lists(row),
                }
            )

        # Average duration over every session get_users returned: the same costless-inclusive
        # universe as the overview, not the priced-only one D1 used to restrict to.
        listed_sessions = {_s(s) for row in user_rows for s in (row.get("session_ids") or [])}
        # A session whose activity end is unknown (NULL, legacy data) is not a 0 ms session: skip it, keep real zeros.
        durations = [
            _i(r["duration_ms"])
            for r in duration_rows
            if _s(r.get("session_id")) in listed_sessions and r.get("duration_ms") is not None
        ]
        avg_duration = int(sum(durations) / len(durations)) if durations else None

        rows.sort(key=lambda r: r["developer_name"])
        rows.sort(key=lambda r: _float_sort_key(r["cost_usd"]), reverse=True)
        total_count = len(rows)
        if page is not None and per_page is not None:
            offset = page * per_page
            rows = rows[offset : offset + per_page]

        return {
            "rows": rows,
            "avg_session_duration_ms": avg_duration,
            "total_count": total_count,
        }, money.pricer

    # ── Repositories ─────────────────────────────────────────────────────────

    async def get_repositories(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        include_branches: bool,
        search: str | None = None,
    ) -> tuple[dict[str, Any], CatalogPricer]:
        (
            cost_facts,
            turns_rows,
            file_rows,
            success_rows,
            lines_rows,
            session_model_rows,
        ) = await asyncio.gather(
            self._repo.get_session_cost_facts(f, search=search, include_costless=True),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_lines_by_session(f),
            self._repo.get_session_model_costs(f, spend_only=True),
        )
        returned = {_s(r.get("session_id")) for r in cost_facts}
        money = price_session_models([r for r in session_model_rows if _s(r.get("session_id")) in returned])

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
            money.by_session,
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

        return {"rows": page_rows, "pagination": pagination}, money.pricer

    # ── Sessions ─────────────────────────────────────────────────────────────

    @staticmethod
    def _log_unavailable_cost(
        session_ids: list[str], model_rows_by_session: dict[str, list[dict]], priced_sessions: set[str]
    ) -> None:
        no_token_rows = sum(1 for sid in session_ids if sid not in model_rows_by_session)
        unpriced_models = sum(1 for sid in session_ids if sid in model_rows_by_session and sid not in priced_sessions)
        if no_token_rows or unpriced_models:
            logger.info(
                "cli_analytics: %d sessions listed with cost_available=false: no_token_rows=%d unpriced_models=%d",
                no_token_rows + unpriced_models,
                no_token_rows,
                unpriced_models,
            )

    @staticmethod
    def _session_row(
        row: dict,
        turns_by_session: dict[str, dict],
        success_by_session: dict[str, dict],
        lines_by_session: dict[str, dict],
        skills_by_session: dict[str, Any],
        model_rows_by_session: dict[str, list[dict]],
    ) -> dict:
        sid = _s(row.get("session_id"))
        model = normalize_model(_s(row.get("model_name")))
        cache_read = _i(row.get("cache_read_tokens"))
        summary = turns_by_session.get(sid, {})
        observed = success_by_session.get(sid, {})
        lines = lines_by_session.get(sid, {})
        started_at = row.get("started_at")
        # The reader's activity-range duration: the one Session Detail and Users Avg report too.
        duration_ms = max(0, _i(row.get("duration_ms")))
        model_cost_rows = model_rows_by_session.get(sid, [])
        return {
            "trace_id": sid,
            "developer_name": _s(row.get("developer_name")),
            "user_email": _s(row.get("developer_name")),
            "repository": _s(row.get("repository")) or None,
            "branch": _s(row.get("branch")) or None,
            "prompt": _served_prompt(
                row.get("session_title"), row.get("prompt"), row.get("repository"), row.get("branch")
            ),
            "start_time": _iso(started_at),
            "duration_ms": duration_ms,
            "model_name": LocalAnalyticsHandler._display_models(model_cost_rows, model),
            # None is unknown (no summary reports it, no observed activity); 0 is a reported zero
            "turns": _opt_int(summary.get("turns")),
            **_summary_fields(summary),
            # observed calls pair with the success rate; the summary's whole-session count is the optional total
            "tool_call_count": _i(observed.get("tool_calls")),
            "tool_calls_total": _opt_int(observed.get("tool_calls_total")),
            "api_call_count": _i(row.get("api_call_count")),
            "input_tokens": _i(row.get("input_tokens")),
            "output_tokens": _i(row.get("output_tokens")),
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": _i(row.get("cache_creation_tokens")),
            "net_lines": _nnet(_ni(lines.get("lines_added")), _ni(lines.get("lines_removed"))),
            "delivery_framework": classify_delivery_framework(skills_by_session.get(sid, [])),
            "ctx_per_call": _ctx_per_call(row.get("cache_read_tokens"), row.get("api_call_count")),
            "subscription_type": _s(row.get("subscription_type")) or "unknown",
            "project_origin": _s(row.get("project_origin")) or "none",
            **_routing(row),
        }

    @staticmethod
    def _filter_sessions(
        sessions: list[dict],
        search: str | None,
        framework: str | None,
        named_first_prompt: dict[str, str],
    ) -> list[dict]:
        if search:
            term = search.strip().lower()
            sessions = [
                s
                for s in sessions
                if term in s["prompt"].lower()
                or term in named_first_prompt.get(s["trace_id"], "").lower()
                or term in (s["repository"] or "").lower()
                or term in (s["branch"] or "").lower()
            ]

        if framework:
            sessions = [s for s in sessions if s.get("delivery_framework") == framework]
        return sessions

    @staticmethod
    def _page_sessions(
        sessions: list[dict],
        in_sql: bool,
        sql_total: int,
        page_ids: list[str],
        sort_by: str,
        page: int,
        per_page: int,
    ) -> tuple[list[dict], int]:
        if in_sql:
            # The reader already ordered, searched and paged; its page order is the response order.
            position = {sid: i for i, sid in enumerate(page_ids)}
            return sorted(sessions, key=lambda s: position[s["trace_id"]]), sql_total

        sort_key = {
            "start_time": lambda s: s["start_time"],
            "cost_usd": lambda s: _float_sort_key(s["cost_usd"]),
            "ctx_per_call": lambda s: _float_sort_key(s["ctx_per_call"]),
        }.get(sort_by, lambda s: s["start_time"])
        # Equal sort values (e.g. sessions without plugin data share one start time) keep
        # a stable trace_id order, so pages neither repeat nor skip sessions.
        sessions.sort(key=lambda s: s["trace_id"])
        sessions.sort(key=sort_key, reverse=True)

        offset = page * per_page
        return sessions[offset : offset + per_page], len(sessions)

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
    ) -> tuple[dict[str, Any], CatalogPricer, str | None]:
        f.branch = branch or None
        # cost_usd sorts by catalog prices and framework filters by customer regex config: neither is
        # expressible in SQL, so those requests are filtered, sorted and paged in Python over the whole window.
        in_sql = sort_by in SESSION_PAGE_SORTS and not framework
        sql_total = 0
        page_ids: list[str] = []
        if in_sql:
            sql_total, page_ids = await self._repo.get_session_page(f, sort_by, page, per_page, search, is_unattributed)
            if not page_ids:
                return {"sessions": [], "page": page, "per_page": per_page, "total": sql_total}, CatalogPricer(), None
            f = f.restricted_to(page_ids)

        cost_facts, turns_rows, success_rows, lines_rows, session_model_rows = await asyncio.gather(
            self._repo.get_session_cost_facts(f, is_unattributed=is_unattributed, include_costless=True),
            self._repo.get_turns_by_session(f),
            self._repo.get_tool_success_by_session(f),
            self._repo.get_lines_by_session(f),
            self._repo.get_session_model_costs(f),
        )

        session_ids = [_s(r.get("session_id")) for r in cost_facts]
        skill_rows = await self._repo.get_skill_names_by_session(session_ids, scope=f.scope)
        skills_by_session = {_s(r["session_id"]): r["skill_names"] for r in skill_rows}

        turns_by_session = {_s(r.get("session_id")): r for r in turns_rows}
        success_by_session = {_s(r.get("session_id")): r for r in success_rows}
        lines_by_session = {_s(r.get("session_id")): r for r in lines_rows}

        model_rows_by_session = self._group_by_session(session_model_rows)

        pricer = CatalogPricer()
        priced_sessions = priced_session_ids(session_model_rows, pricer)

        sessions = [
            self._session_row(
                row,
                turns_by_session,
                success_by_session,
                lines_by_session,
                skills_by_session,
                model_rows_by_session,
            )
            for row in cost_facts
        ]

        if not in_sql:
            # A named session is also found by its first prompt; an unnamed one is served as that prompt already.
            named_first_prompt = {
                _s(r.get("session_id")): strip_pasted_content(_s(r.get("prompt")))
                for r in cost_facts
                if _s(r.get("session_title")).strip(BLANK_CHARS)
            }
            sessions = self._filter_sessions(sessions, search, framework, named_first_prompt)

        returned = {s["trace_id"] for s in sessions}
        money = price_session_models([r for r in session_model_rows if _s(r.get("session_id")) in returned], pricer)
        for s in sessions:
            s["cost_usd"] = money.by_session.get(s["trace_id"], 0.0)
            s["cost_available"] = s["trace_id"] in priced_sessions
            s["bloat_pct"] = _pct(money.cache_read_by_session.get(s["trace_id"], 0.0), s["cost_usd"])

        page_rows, total = self._page_sessions(sessions, in_sql, sql_total, page_ids, sort_by, page, per_page)
        self._log_unavailable_cost([s["trace_id"] for s in page_rows], model_rows_by_session, priced_sessions)

        data_as_of = max((s["start_time"] for s in page_rows), default=None)
        return {"sessions": page_rows, "page": page, "per_page": per_page, "total": total}, money.pricer, data_as_of

    # ── Session detail ───────────────────────────────────────────────────────

    async def get_session_detail(self, session_id: str, *, scope: Any) -> tuple[dict[str, Any] | None, list[str], str]:
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
            self._repo.get_session_detail_meta(session_id, scope=scope),
            self._repo.get_session_detail_cost(session_id, scope=scope),
            self._repo.get_session_detail_scalars(session_id, scope=scope),
            self._repo.get_session_detail_tools(session_id, scope=scope),
            self._repo.get_session_detail_events(session_id, scope=scope),
            self._repo.get_session_detail_dispatches(session_id, scope=scope),
            self._repo.get_skill_names_by_session([session_id], scope=scope),
            self._repo.get_session_detail_model_costs(session_id, scope=scope),
        )
        pricer = CatalogPricer()
        meta = _first(meta_rows)
        if not meta or not _s(meta.get("session_id")):
            return None, [], pricer.catalog_source

        cost = _first(cost_rows)
        scalars = _first(scalar_rows)
        model = normalize_model(_s(cost.get("model_name")))
        # Token columns come from the per-model rows, which add the totals-only subagent rows, as the list does.
        input_tokens = sum(_i(row.get("input_tokens")) for row in model_cost_rows)
        output_tokens = sum(_i(row.get("output_tokens")) for row in model_cost_rows)
        cache_read = sum(_i(row.get("cache_read_tokens")) for row in model_cost_rows)
        cache_creation = sum(_i(row.get("cache_creation_tokens")) for row in model_cost_rows)
        display_model = self._display_models(model_cost_rows, model)

        tools = []
        for row in tool_rows:
            calls = _i(row.get("call_count"))
            ok = _i(row.get("success_count"))
            tools.append(
                {
                    "tool_name": _s(row.get("tool_name")),
                    "call_count": calls,
                    "success_count": ok,
                    "success_rate": _pct(ok, calls),
                }
            )

        events = [self._session_event(r, pricer) for r in event_rows]

        dispatch_rows = [{**row, "cost_usd": dispatch_money(pricer, row)} for row in dispatch_rows]
        money = price_session_models(model_cost_rows, pricer)
        cost_usd = money.total
        crc = money.cache_read

        started_at = meta.get("started_at")
        duration_ms = _i(meta.get("duration_ms"))
        dispatches = self._build_dispatches(
            dispatch_rows,
            started_at,
            duration_ms,
            cost_usd,
            input_tokens,
            output_tokens,
            cache_read,
            cache_creation,
        )

        lines_added = _ni(scalars.get("lines_added"))
        lines_removed = _ni(scalars.get("lines_removed"))
        # Successes of every tool, not only the listed top 20, as the aggregates read them.
        tool_ok_total = _i(scalars.get("tool_calls_success"))

        detail = {
            "trace_id": _s(meta.get("session_id")),
            "developer_name": _s(meta.get("developer_name")),
            "user_email": _s(meta.get("developer_name")),
            "repository": _s(meta.get("repository")) or None,
            "branch": _s(meta.get("branch")) or None,
            "prompt": _served_prompt(
                meta.get("session_title"), meta.get("prompt"), meta.get("repository"), meta.get("branch")
            ),
            "start_time": _iso(started_at),
            "duration_ms": duration_ms,
            "model_name": display_model,
            "turns": _opt_int(scalars.get("turns")),
            **_summary_fields(scalars),
            "tool_call_count": _i(scalars.get("tool_call_count")),
            "tool_calls_total": _opt_int(scalars.get("tool_calls_total")),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read,
            "cache_creation_tokens": cache_creation,
            "cost_usd": cost_usd,
            "cost_available": has_priced_model((_s(row.get("model_name")) for row in model_cost_rows), pricer),
            "net_lines": _nnet(lines_added, lines_removed),
            "bloat_pct": _pct(crc, cost_usd),
            "events": events,
            "active_ms": _i(scalars.get("active_ms")),
            "agent_count": _opt_int(scalars.get("agent_count")),
            "skill_count": _opt_int(scalars.get("skill_count")),
            "cache_read_cost_usd": crc,
            "tool_calls_success": tool_ok_total,
            "tool_success_rate": _pct(tool_ok_total, _i(scalars.get("tool_call_count"))),
            "tools": tools,
            "dispatches": dispatches,
            "subscription_type": _s(meta.get("subscription_type")) or "unknown",
            "project_origin": _s(meta.get("project_origin")) or "none",
            **_routing(meta),
        }
        skill_names = _first(skill_rows).get("skill_names") or []
        detail["delivery_framework"] = classify_delivery_framework(skill_names)
        detail["harness_name"] = humanize_harness_name(meta.get("harness_service_name"))
        return detail, pricer.unpriced, pricer.catalog_source

    @staticmethod
    def _session_event(row: dict[str, Any], pricer: CatalogPricer) -> dict[str, Any]:
        """One timeline event. An `api_request` and a `subagent_totals` event (the totals of a subagent that has no
        request of its own) carry tokens and catalog money; a `tool_call` carries none."""
        event_type = _s(row.get("event_type"))
        has_usage = event_type in ("api_request", "subagent_totals")
        return {
            "timestamp": _iso(row.get("timestamp")),
            "event_type": event_type,
            "model_name": normalize_model(_s(row.get("model_name"))) or None,
            "input_tokens": _i(row.get("input_tokens")) if has_usage else None,
            "output_tokens": _i(row.get("output_tokens")) if has_usage else None,
            "cache_read_tokens": _i(row.get("cache_read_tokens")) if has_usage else None,
            "cache_creation_tokens": _i(row.get("cache_creation_tokens")) if has_usage else None,
            "cost_usd": pricer.price(_s(row.get("model_name")), token_counts(row)) if has_usage else None,
            "tool_name": _s(row.get("tool_name")) or None,
        }

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
        tool_rows, model_rows, invocation_rows, tool_classes, model_classes, invocation_classes = await asyncio.gather(
            self._repo.get_tool_usage(f),
            self._repo.get_model_breakdown(f),
            self._repo.get_invocations(f),
            self._repo.get_class_lists(f, "tool"),
            self._repo.get_class_lists(f, "model"),
            self._repo.get_class_lists(f, "invocation"),
        )
        by_tool, by_model = _by_key(tool_classes), _by_key(model_classes)
        by_invocation = {(_s(r.get("kind")), _s(r.get("key"))): r for r in invocation_classes}
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
                    **_lists(by_tool.get(_s(row.get("tool_name")))),
                }
            )
        tokens_by_model = sorted(
            (
                {
                    "model_name": _s(r.get("model_name")),
                    "total_tokens": _i(r.get("total_tokens")),
                    **_lists(by_model.get(_s(r.get("model_name")))),
                }
                for r in model_rows
                if _i(r.get("total_tokens")) > 0
            ),
            key=lambda r: r["total_tokens"],
            reverse=True,
        )[:20]
        return {
            "tool_usage": tool_usage,
            "tokens_by_model": tokens_by_model,
            "skills_invoked": self._top_invocations(invocation_rows, "skill", by_invocation),
            "agent_subtypes": self._top_invocations(invocation_rows, "agent", by_invocation),
            "slash_commands": self._top_invocations(invocation_rows, "command", by_invocation),
        }

    @staticmethod
    def _top_invocations(rows: list[dict], kind: str, classes: dict[tuple[str, str], dict] | None = None) -> list[dict]:
        classes = classes or {}
        items = [
            {
                "name": _s(r.get("name")),
                "count": _i(r.get("count")),
                **_lists(classes.get((kind, _s(r.get("name"))))),
            }
            for r in rows
            if _s(r.get("kind")) == kind and _s(r.get("name"))
        ]
        items.sort(key=lambda r: (-r["count"], r["name"]))
        return items[:_MAX_INVOCATIONS]

    # ── Efficiency ───────────────────────────────────────────────────────────

    async def get_efficiency(self, f: LocalAnalyticsFilter) -> tuple[dict[str, Any], CatalogPricer]:
        (
            kpi_rows,
            session_model_rows,
            cost_facts,
            turns_rows,
            file_rows,
            lines_totals,
            lines_rows,
            session_classes,
        ) = await asyncio.gather(
            self._repo.get_cost_kpis(f),
            self._repo.get_session_model_costs(f, spend_only=True),
            self._repo.get_session_cost_facts(f, include_costless=True),
            self._repo.get_turns_by_session(f),
            self._repo.get_file_facts_by_session(f),
            self._repo.get_lines_totals(f),
            self._repo.get_lines_by_session(f),
            self._repo.get_class_lists(f, "session"),
        )
        classes = _by_key(session_classes)

        money = price_session_models(session_model_rows)
        kpi = _first(kpi_rows)
        total_cost = money.total
        cache_read_tokens = _i(kpi.get("total_cache_read_tokens"))
        total_api_calls = _i(kpi.get("total_api_calls"))

        turns_by_session = {_s(r.get("session_id")): _i(r.get("turns")) for r in turns_rows}
        files_by_session = {_s(r.get("session_id")): r for r in file_rows}

        cache_read_cost_usd = money.cache_read

        worst_ratio: float | None = None
        worst_prompt: str | None = None
        worst_trace: str | None = None
        depth_counts = dict.fromkeys(_DEPTH_BUCKETS, 0)
        depth_sessions: dict[str, list[str]] = {b: [] for b in _DEPTH_BUCKETS}
        for row in cost_facts:
            sid = _s(row.get("session_id"))
            turns = turns_by_session.get(sid, 0)
            ratio = _ctx_per_call(row.get("cache_read_tokens"), row.get("api_call_count"))
            # Among sessions with API calls, highest ratio wins; a tie goes to the lowest session id.
            if _i(row.get("api_call_count")) > 0 and (
                worst_ratio is None or ratio > worst_ratio or (ratio == worst_ratio and sid < worst_trace)
            ):
                worst_ratio = ratio
                worst_prompt = (
                    _served_prompt(
                        row.get("session_title"), row.get("prompt"), row.get("repository"), row.get("branch")
                    )
                    or None
                )
                worst_trace = sid
            bucket = depth_bucket(turns)
            depth_counts[bucket] += 1
            depth_sessions[bucket].append(sid)

        if worst_prompt:
            worst_prompt = worst_prompt[:_PROMPT_PREVIEW_LEN]

        dead_ids = self._dead_ids(money.by_session, files_by_session)
        dead_count, wasted = len(dead_ids), sum(money.by_session[sid] for sid in dead_ids)

        selected_session_ids = {_s(r.get("session_id")) for r in cost_facts}
        selected_files = [r for sid, r in files_by_session.items() if sid in selected_session_ids]
        # Sessions that contributed to code_changes: a changed file, or a non-zero line delta.
        code_ids = {
            sid for sid, r in files_by_session.items() if sid in selected_session_ids and _i(r.get("files_changed"))
        }
        code_ids |= {
            _s(r.get("session_id")) for r in lines_rows if _i(r.get("lines_added")) or _i(r.get("lines_removed"))
        }
        files_changed = sum(_i(r.get("files_changed")) for r in selected_files)
        files_written = sum(_i(r.get("files_written")) for r in selected_files)
        files_edited = sum(_i(r.get("files_edited")) for r in selected_files)
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
                **_union_lists(classes, selected_session_ids),
            },
            "dead_sessions": {
                "count": dead_count,
                "pct_of_sessions": _pct(dead_count, _i(kpi.get("spend_sessions"))),
                "wasted_cost_usd": wasted,
                "avg_cost_per_dead": (wasted / dead_count) if dead_count else None,
                **_union_lists(classes, dead_ids),
            },
            "session_depth": [
                {"bucket": b, "count": depth_counts[b], **_union_lists(classes, depth_sessions[b])}
                for b in _DEPTH_BUCKETS
            ],
            "code_changes": {
                "files_changed": files_changed,
                "files_written": files_written,
                "files_edited": files_edited,
                "net_lines": _nnet(_ni(lines.get("lines_added")), _ni(lines.get("lines_removed"))),
                **_union_lists(classes, code_ids),
            },
        }
        return data, money.pricer


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
