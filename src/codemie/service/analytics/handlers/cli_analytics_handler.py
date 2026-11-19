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
import copy
import functools
import logging
import math
import weakref
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone, tzinfo
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

from codemie.configs.config import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import (
    G_MODEL,
    G_MODEL_PERSONAL,
    G_MODEL_ROUTE,
    G_PERSONAL,
    G_ROUTE,
    G_TOTAL,
    G_USER,
    G_USER_PERSONAL,
    G_USER_ROUTE,
    CliAnalyticsReader,
    CostGroup,
    GroupedMoney,
    ModelRates,
    ProjectPage,
    RequestBudgetExceeded,
    RollupCost,
    RollupOverview,
    RollupUsers,
    UsersMoneyPage,
)
from codemie.repository.cli_analytics.postgres.settings import dashboard_concurrency_limit
from codemie.repository.cli_analytics.scope import API_SUBSCRIPTION_TYPE
from codemie.repository.cli_analytics.vocabulary import BLANK_CHARS, humanize_harness_name, strip_pasted_content
from codemie.service.analytics.cli_analytics_project_gate import (
    NOTE_FOLDER_NOT_LINKED,
    NOTE_LINK_UNAVAILABLE,
    NOTE_REGISTERED_INACCESSIBLE,
    NOTE_REGISTRATION_AMBIGUOUS,
    ORIGIN_DEFAULT,
)
from codemie.service.analytics.delivery_framework import classify_delivery_framework
from codemie.service.analytics.dashboard_cache import (
    dashboard_key,
    get_ranking_cache,
    mark_rollup_used,
    rollup_key,
    rollup_max_age_s,
)
from codemie.service.analytics.handlers.coding_agent_pricing import (
    CatalogPricer,
    model_rates_for,
    normalize_model,
    unpriced_display_names,
)
from codemie.service.analytics.handlers.funding_buckets import (
    EVIDENCE,
    buckets,
    buckets_from_groups,
    drop_evidence,
    group_pricing,
    merge_pricing,
    price_bucket_rows,
    pricing_of_rows,
    row_pricing,
    rows_of,
    session_index,
    without_evidence,
)
from codemie.service.analytics.handlers.session_money import (
    cost_available,
    dispatch_money,
    price_session_models,
    token_counts,
    unpriced_session_ids,
)

_DEPTH_BUCKETS = ("1", "2-5", "6-10", "11-25", "26-50", "50+")
_MAX_INVOCATIONS = 10
_PROMPT_PREVIEW_LEN = 50

logger = logging.getLogger(__name__)

T = TypeVar("T")


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


# Claude Desktop (the Code tab, local Cowork and cloud-hosted Cowork) auto-generates a chat name and sends
# it as `session_title`: an explicit rename everywhere else, but not here. `entrypoint` is a controlled
# identifier set by client code, never user-authored free text, but is still compared
# trimmed with BLANK_CHARS and lower-cased: a client sending stray whitespace or different casing than
# these literals must not bypass the exclusion. `claude-desktop-3p` and `remote_cowork` were
# confirmed live, directly off a running Desktop build, not merely inferred — the Code tab's own
# `CLAUDE_CODE_ENTRYPOINT` and a cloud-hosted Cowork session's own entrypoint, respectively.
_DESKTOP_ENTRYPOINTS = frozenset({"claude-desktop", "claude-desktop-3p", "local-agent", "remote_cowork"})


def _title_is_eligible(entrypoint: object) -> bool:
    """Whether a session's `session_title` may be served/searched at all: false only when `entrypoint` is a
    known Desktop value once trimmed and lowercased. A blank or missing `entrypoint` is never a member, so
    it is always eligible, unchanged from before this rule existed — the exclusion only narrows what
    it has positive evidence for, never widens onto it."""
    return _s(entrypoint).strip(BLANK_CHARS).lower() not in _DESKTOP_ENTRYPOINTS


def _served_prompt(title: object, first_prompt: object, repository: object, branch: object, entrypoint: object) -> str:
    """The session's name when it has one and is eligible, else its first prompt without paste markup.

    Rows stored before the rollup stripped the markup are cleaned here too. A first prompt that cleans to
    nothing (an old-CLI prompt cut inside the open tag) is served as the repository, else the branch. A
    Desktop-hosted session's title is its own auto-name, not an explicit rename, so it is skipped entirely.
    """
    effective_title = title if _title_is_eligible(entrypoint) else None
    return (
        _s(effective_title).strip(BLANK_CHARS) or strip_pasted_content(_s(first_prompt)) or _s(repository) or _s(branch)
    )


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


def _by_group_key(groups: list[CostGroup], key: str) -> dict[str, list[CostGroup]]:
    """The rows of a two-key grouping, collected under the value of its first key."""
    by_key: dict[str, list[CostGroup]] = {}
    for group in groups:
        by_key.setdefault(_s(group.keys.get(key)), []).append(group)
    return by_key


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
        "_sids": set(),  # kept past finalizing: the handler prices the row's buckets from it
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


def _build_session_repository_rows(
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
        bucket["_sids"].add(sid)
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


def _repository_bucket(group: dict, include_branches: bool) -> dict:
    """One response bucket from a `get_repository_groups` group row: its counts and its spend, as the reader summed
    them."""
    added, removed = _ni(group.get("lines_added")), _ni(group.get("lines_removed"))
    tool_calls, tool_ok = _i(group.get("tool_calls")), _i(group.get("tool_calls_success"))
    return {
        "repository": _s(group.get("repository")) or None,
        "branch": (_s(group.get("branch")) or None) if include_branches else None,
        "project_name": _s(group.get("project_name")) or None,
        "session_count": _i(group.get("n")),
        "turns": _i(group.get("turns")),
        "cost_usd": _f(group.get("cost_usd")),
        "files_changed": _i(group.get("files_changed")),
        "lines_added": added,
        "lines_removed": removed,
        "tool_calls": tool_calls,
        "tool_calls_success": tool_ok,
        "tool_success_rate": _pct(tool_ok, tool_calls),
        "subscription_types": sorted({_s(v) or "unknown" for v in group.get("subscription_types") or []}),
        "project_origins": sorted({_s(v) or "none" for v in group.get("project_origins") or []}),
        "net_lines": _nnet(added, removed),
        **row_pricing(group),
    }


_PRICING_FIELDS = ("cost_incomplete", "unpriced_models", "cost_unknown")
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


def _new_rollup_group(row: dict, new_group: Callable[[dict], dict], children: str) -> dict:
    return {
        **new_group(row),
        **{field: None if field in _NULLABLE_SUM_FIELDS else 0 for field in _REPO_SUM_FIELDS},
        "cost_usd": 0.0,
        children: [],
        "subscription_types": [],
        "project_origins": [],
    }


def _add_to_rollup_group(group: dict, row: dict) -> None:
    """Adds a row's counts and class lists to its group; the session ids only when the row carries them (the
    session-based rows do, the reader's groups do not)."""
    if "_sids" in row:
        group.setdefault("_sids", set()).update(row["_sids"])
    for field in _REPO_SUM_FIELDS:
        if field in _NULLABLE_SUM_FIELDS:
            group[field] = _nsum(group[field], row[field])
        else:
            group[field] += row[field]
    for field in ("subscription_types", "project_origins"):
        group[field] = sorted({*group[field], *row[field]})


def _roll_up(
    rows: list[dict],
    key_of: Callable[[dict], Hashable],
    new_group: Callable[[dict], dict],
    children: str,
    child_of: Callable[[dict], dict],
) -> list[dict]:
    """Rolls rows up into one group per `key_of`, each row nested under `children` as `child_of` shapes it. Sums the
    raw counts first and calls _pct once per group, so a row's percentage is never averaged into the group's
    (EPMCDME-15585) -- a zero-call row contributes 0/0 to the sums, not a 0% vote weighted by session count. The one
    rollup of both levels (branches into repositories, repositories into projects): a summed field goes here."""
    groups: dict[Hashable, dict] = {}
    for row in rows:
        key = key_of(row)
        group = groups.get(key)
        if group is None:
            group = groups[key] = _new_rollup_group(row, new_group, children)
        _add_to_rollup_group(group, row)
        group[children].append(child_of(row))

    out = list(groups.values())
    for group in out:
        group["net_lines"] = _nnet(group["lines_added"], group["lines_removed"])
        group["tool_success_rate"] = _pct(group["tool_calls_success"], group["tool_calls"])
        # The amount is the sum of its children: priced rows carry their pricing up, session-priced ones are set later.
        if group[children] and all("cost_incomplete" in child for child in group[children]):
            group.update(merge_pricing(group[children]))
    return out


def _group_branches_into_repositories(branch_rows: list[dict]) -> list[dict]:
    """Rolls finalized per-branch rows up into (project, repository) groups (see `_roll_up`)."""
    out = _roll_up(
        branch_rows,
        key_of=lambda row: (row["project_name"], row["repository"]),
        new_group=lambda row: {"repository": row["repository"], "project_name": row["project_name"]},
        children="branches",
        child_of=lambda row: {
            **{field: row[field] for field in _BRANCH_ROW_FIELDS},
            **{field: row[field] for field in (*_PRICING_FIELDS, EVIDENCE, "_sids") if field in row},
        },
    )
    for repo in out:
        repo["branches"].sort(key=lambda b: b["branch"] or "")
    out.sort(key=lambda r: (r["repository"] or "", r["project_name"] or ""))
    out.sort(key=lambda r: (r["session_count"], _float_sort_key(r["cost_usd"])), reverse=True)
    return out


def _build_repository_rows(groups: list[dict], include_branches: bool) -> list[dict]:
    """Response rows of the reader's groups: (project, repository, branch) groups with branches, else repository only
    (already ranked and paged by the reader). include_branches=True rolls the branch groups up into (project,
    repository) groups, each carrying its own backend-computed tool_success_rate and its branches nested underneath,
    so the UI never recomputes or re-averages the percentage (EPMCDME-15585)."""
    rows = [_repository_bucket(group, include_branches) for group in groups]
    return _group_branches_into_repositories(rows) if include_branches else rows


def _parts(rows: list[dict]) -> dict[str, list[dict]]:
    """The rows of a dashboard statement by their `part`."""
    parts: dict[str, list[dict]] = {}
    for row in rows:
        parts.setdefault(_s(row.get("part")), []).append(row)
    return parts


def _cost_kpis(total: CostGroup | None, kpi_rows: list[dict], all_classes: list[dict]) -> dict[str, Any]:
    kpi = _first(kpi_rows)
    total_cost = total.cost_usd if total is not None else 0.0
    spend_sessions = _i(kpi.get("spend_sessions"))
    total_tokens = (
        _i(kpi.get("total_input_tokens"))
        + _i(kpi.get("total_output_tokens"))
        + _i(kpi.get("total_cache_read_tokens"))
        + _i(kpi.get("total_cache_creation_tokens"))
    )
    return {
        "total_sessions": _i(kpi.get("total_sessions")),
        "total_cost_usd": total_cost,
        "total_tokens": total_tokens,
        "avg_cost_per_session": (total_cost / spend_sessions) if spend_sessions else 0.0,
        **group_pricing(total),
        **_lists(_first(all_classes)),
    }


def _cost_by_user(groups: dict[str, list[CostGroup]], by_user: list[dict], include_buckets: bool) -> list[dict]:
    user_cost = {g.keys["user"]: g.cost_usd for g in groups.get(G_USER, [])}
    user_money = {g.keys["user"]: g for g in groups.get(G_USER, [])}
    user_routes = _by_group_key(groups.get(G_USER_ROUTE, []), "user")
    user_personal = _by_group_key(groups.get(G_USER_PERSONAL, []), "user")
    rows = [
        {
            "developer_name": _s(r.get("developer_name")),
            "user_email": _s(r.get("developer_name")),
            "cost_usd": user_cost.get(_s(r.get("developer_name")), 0.0),
            **group_pricing(user_money.get(_s(r.get("developer_name")))),
            **_lists(r),
            **(
                buckets_from_groups(
                    user_routes.get(_s(r.get("developer_name")), []),
                    user_personal.get(_s(r.get("developer_name")), []),
                )
                if include_buckets
                else {}
            ),
        }
        for r in by_user
    ]
    rows.sort(key=lambda r: r["developer_name"])
    rows.sort(key=lambda r: _float_sort_key(r["cost_usd"]), reverse=True)
    return rows


def _cost_by_model(groups: dict[str, list[CostGroup]], model_classes: list[dict], include_buckets: bool) -> list[dict]:
    """The models with cost, each with its buckets when asked. The per-model buckets cover the same sessions as
    `G_MODEL` (every session, Personal-paid included, since EPMCDME-17260), so no model has buckets without a row."""
    by_model_classes = _by_key(model_classes)
    model_routes = _by_group_key(groups.get(G_MODEL_ROUTE, []), "model")
    model_personal = _by_group_key(groups.get(G_MODEL_PERSONAL, []), "model")

    def model_row(name: str, cost: float, money: CostGroup) -> dict[str, Any]:
        return {
            "model_name": name,
            "cost_usd": cost,
            **group_pricing(money),
            **_lists(by_model_classes.get(name)),
            **(
                buckets_from_groups(model_routes.get(name, []), model_personal.get(name, [])) if include_buckets else {}
            ),
        }

    return sorted(
        (model_row(g.keys["model"], g.cost_usd, g) for g in groups.get(G_MODEL, [])),
        key=lambda r: (-_float_sort_key(r["cost_usd"]), r["model_name"]),
    )


def _cost_response(
    money: GroupedMoney,
    by_user: list[dict],
    kpi_rows: list[dict],
    all_classes: list[dict],
    model_classes: list[dict],
    include_buckets: bool,
) -> tuple[dict[str, Any], CatalogPricer]:
    """The Cost page's data from the rows of its statements (or of the rollup)."""
    groups = money.groups
    pricer = CatalogPricer()
    total = next(iter(groups.get(G_TOTAL, [])), None)
    if total is not None:
        pricer._unpriced.update(total.unpriced_models)  # the spend scope only; estimate buckets never feed it
    data = {
        "kpis": _cost_kpis(total, kpi_rows, all_classes),
        "cost_by_user": _cost_by_user(groups, by_user, include_buckets),
        "cost_by_model": _cost_by_model(groups, model_classes, include_buckets),
        "models_without_spend": [],  # kept in the contract; always empty since EPMCDME-17260
        **(buckets_from_groups(groups.get(G_ROUTE, []), groups.get(G_PERSONAL, [])) if include_buckets else {}),
    }
    return data, pricer


async def _users_ranking(repo: Any, f: LocalAnalyticsFilter) -> UsersMoneyPage:
    """The money of every user, the same for every page of a window: a fresh one is reused (and shared with
    /user-charts); the page is cut from the whole ranking by the caller, as the chart needs the whole ranking anyway."""
    ranking_cache = get_ranking_cache()
    ranking_key = dashboard_key("users-ranking", f) if ranking_cache.enabled else None
    everyone = ranking_cache.get(ranking_key) if ranking_key else None
    if everyone is None:
        rates = [model_rates_for(model) for model in await repo.get_cost_models(f)]
        everyone = await repo.get_users_money_page(f, rates, None, 0)
        if ranking_key:
            ranking_cache.put(ranking_key, everyone)
    return everyone


async def _users_detail(
    repo: Any,
    f: LocalAnalyticsFilter,
    ranked: UsersMoneyPage,
    limit: int | None,
    charts_only: bool,
    include_daily_activity: bool,
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    """The fact rows of the ranked page's developers: user rows, session facts, lines, last active, daily activity."""
    user_rows = developer_facts = lines_rows = last_active_rows = daily_rows = []
    if not ranked.developers:  # an empty page must not read as "no developer restriction"
        return user_rows, developer_facts, lines_rows, last_active_rows, daily_rows
    scoped = copy.copy(f)
    if limit is not None:  # /user-charts takes every developer: no restriction to send
        scoped.developers = list(ranked.developers)
    if not charts_only:
        user_rows = await repo.get_users(scoped)
        developer_facts = await repo.get_developer_session_facts(scoped)
        lines_rows = await repo.get_lines_by_user(scoped)
        last_active_rows = await repo.get_users_last_active(scoped)
        if include_daily_activity:
            daily_rows = await repo.get_users_daily_activity(scoped)
    return user_rows, developer_facts, lines_rows, last_active_rows, daily_rows


def _dashboard_buckets(route_rows: list[dict], billed_rows: list[dict]) -> dict[str, Any]:
    def group(row: dict, key: str) -> CostGroup:
        return CostGroup(
            keys={key: row.get(key)},
            cost_usd=_f(row.get("cost_usd")),
            cache_read_cost_usd=_f(row.get("cache_read_cost_usd")),
            session_count=_i(row.get("session_count")),
            unpriced_models=tuple(row.get("unpriced_models") or []),
            cost_incomplete=bool(row.get("cost_incomplete")),
        )

    return buckets_from_groups(
        [group(row, "funding_route") for row in route_rows],
        [group(row, "personal_billed") for row in billed_rows],
    )


def _money(rows: list[dict]) -> tuple[dict, dict[str, Any], CatalogPricer]:
    """The request's spend row (cost, cache-read cost, unpriced models), its funding and personal-billed estimate
    buckets and the pricer that carries the spend's unpriced models, from the money rows of a dashboard statement
    (`key` is the grouping)."""
    by_grouping = {name: [r for r in rows if _s(r.get("key")) == name] for name in ("spend", "route", "billed")}
    spend = _first(by_grouping["spend"])
    pricer = CatalogPricer()
    pricer.record_unpriced(unpriced_display_names(spend.get("unpriced_models") or []))
    return spend, _dashboard_buckets(by_grouping["route"], by_grouping["billed"]), pricer


def _cost_split(total_cost: float, rows: list[dict]) -> tuple[float, float]:
    """The Overview's (API cost, Subscription cost) of Est. Cost `total_cost` from the `class` money rows (the cost by
    subscription class): API is the CodeMie proxy class, Subscription what API leaves of Est. Cost, so every other
    class, Personal-paid included."""
    by_class = {_s(r.get("subscription_type")): _f(r.get("cost_usd")) for r in rows if _s(r.get("key")) == "class"}
    api_cost = by_class.get(API_SUBSCRIPTION_TYPE, 0.0)
    return api_cost, max(0.0, total_cost - api_cost)


def _group_repositories_into_projects(
    repo_rows: list[dict], project_order: list[str | None], include_repositories: bool = True
) -> list[dict]:
    """Rolls (project, repository) rows with nested branches up into one row per project (see `_roll_up`), each with
    its repositories nested by cost, or, without `include_repositories`, with only its `repository_count`. Which
    sessions belong in the rows (the search) and the order of the projects (`project_order`, by spend) were decided
    by the database (`get_project_page`)."""
    out = _roll_up(
        repo_rows,
        key_of=lambda repo: repo["project_name"],
        new_group=lambda repo: {"project_name": repo["project_name"]},
        children="repositories",
        child_of=lambda repo: repo,
    )
    for project in out:
        project["repository_count"] = len(project["repositories"])
        if include_repositories:
            project["repositories"].sort(key=lambda r: (-_float_sort_key(r["cost_usd"]), r["repository"] or ""))
        else:
            del project["repositories"]
    position = {name: index for index, name in enumerate(project_order)}
    out.sort(key=lambda p: position.get(p["project_name"], len(position)))
    return out


def _project_total_row(row: dict) -> dict:
    """One drill-down project row from a `get_project_totals` row: the fields the nested rollup of the default response
    gives a project (`_group_repositories_into_projects` without `include_repositories`), with the project's session
    ids (`_sids`) for the handler to price the estimate buckets from."""
    added, removed = _ni(row.get("lines_added")), _ni(row.get("lines_removed"))
    tool_calls, tool_ok = _i(row.get("tool_calls")), _i(row.get("tool_calls_success"))
    return {
        "project_name": _s(row.get("project_name")) or None,
        "session_count": _i(row.get("session_count")),
        "turns": _i(row.get("turns")),
        "cost_usd": _f(row.get("cost_usd")),
        "files_changed": _i(row.get("files_changed")),
        "lines_added": added,
        "lines_removed": removed,
        "tool_calls": tool_calls,
        "tool_calls_success": tool_ok,
        "subscription_types": sorted({_s(v) or "unknown" for v in row.get("subscription_types") or []}),
        "project_origins": sorted({_s(v) or "none" for v in row.get("project_origins") or []}),
        "net_lines": _nnet(added, removed),
        "tool_success_rate": _pct(tool_ok, tool_calls),
        "repository_count": _i(row.get("repository_count")),
        "_sids": {_s(sid) for sid in row.get("session_ids") or []},
    }


def _pagination(page: int, per_page: int, total: int) -> dict[str, Any]:
    return {"page": page, "per_page": per_page, "total_count": total, "has_more": (page + 1) * per_page < total}


def with_unattributed(rows: list[T], null_row: T | None, pagination: dict[str, Any]) -> list[T]:
    """Appends the null-named row once, as the last row of the last page (outside `per_page`; `total_count` and
    `has_more` count named rows only). Never on another page, and never on a page beyond the last (no rows there)."""
    if null_row is None or pagination["has_more"]:
        return rows
    return [*rows, null_row] if rows or pagination["page"] == 0 else rows


def _unattributed_repository_last(
    page_rows: list[dict], search: str | None, pagination: dict[str, Any], *, enabled: bool
) -> list[dict]:
    """With `unattributed_last` (paged, no branches) the group without a repository leaves the ranked rows and is
    appended as the last row of the last page. A search names a repository, so it never keeps that group."""
    if not enabled:
        return page_rows
    named_rows = [r for r in page_rows if r["repository"] is not None]
    null_row = None if search else next((r for r in page_rows if r["repository"] is None), None)
    return with_unattributed(named_rows, null_row, pagination)


def _page(rows: list[dict], page: int, per_page: int) -> tuple[list[dict], dict[str, Any]]:
    offset = page * per_page
    return rows[offset : offset + per_page], _pagination(page, per_page, len(rows))


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
        # A skill is a skill however it was started; the reader flags slash-started rows only to order them.
        return f"skill · {skill}", "skill"
    return None


# One FIFO semaphore per event loop (created on first use, so none binds to the wrong loop), sized from the config.
_dashboard_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = (
    weakref.WeakKeyDictionary()
)


@asynccontextmanager
async def dashboard_slot():
    """One of this pod's slots for a dashboard request, so ingestion keeps pooled connections.
    Requests over the limit queue in arrival order (a light one can wait behind the heavy ones; a gate of its own
    would give up the pool-minus-four connections ingestion counts on). Enter it inside the request's
    `asyncio.timeout` (`dashboard_request` does): the wait then counts against the budget, and a timeout or cancel
    while waiting or running gives the slot back."""
    limit = dashboard_concurrency_limit(
        config.CLI_ANALYTICS_DASHBOARD_MAX_CONCURRENT, config.CLI_ANALYTICS_PG_POOL_SIZE
    )
    loop = asyncio.get_running_loop()
    held = _dashboard_gates.get(loop)
    if held is None or held[0] != limit:
        held = _dashboard_gates[loop] = (limit, asyncio.Semaphore(limit))
    gate = held[1]
    async with gate:
        yield


# Stored in the ranking cache for a window the rollup could not answer, so its next requests go straight to the live
# statements for the cache's TTL.
_ROLLUP_REFUSED: Any = object()

# The moment (loop time) the request in this context runs out of budget, while a `one_request_budget` method runs.
_request_deadline: ContextVar[float | None] = ContextVar("cli_analytics_request_deadline", default=None)


def one_request_budget(method):
    """One budget for everything the handler method does: a request that reads the rollup and then, when that cannot
    answer, the live statements enters `dashboard_request` twice, and each entry must not start a budget of its own
    (twice 540 s is past the proxy's read timeout, with a slot and a connection held for a client that has gone)."""

    @functools.wraps(method)
    async def within_one_budget(*args, **kwargs):
        deadline = asyncio.get_running_loop().time() + config.CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S
        token = _request_deadline.set(deadline)
        try:
            return await method(*args, **kwargs)
        finally:
            _request_deadline.reset(token)

    return within_one_budget


@asynccontextmanager
async def dashboard_request():
    """The request budget, then a slot of the pod (the wait for it counts against the budget). The budget running out
    is a `RequestBudgetExceeded`, so the route can tell it from a pool or connect timeout of a store that is down.
    Inside a `one_request_budget` method every entry shares that method's budget."""
    deadline = _request_deadline.get()
    budget = (
        asyncio.timeout_at(deadline)
        if deadline is not None
        else asyncio.timeout(config.CLI_ANALYTICS_DASHBOARD_REQUEST_BUDGET_S)
    )
    try:
        async with budget, dashboard_slot():
            yield
    except TimeoutError as exc:
        if budget.expired():
            raise RequestBudgetExceeded("the dashboard request outran its budget") from exc
        raise


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

    @one_request_budget
    async def get_overview(
        self, f: LocalAnalyticsFilter, *, include_class_lists: bool = True, include_buckets: bool = True
    ) -> tuple[dict[str, Any], CatalogPricer, str | None]:
        """The Overview tab. `include_class_lists` and `include_buckets` (the tab reads neither; a caller that does not
        read them passes false): without the class lists the subscription and origin lists stay empty, without the
        buckets the funding-route and personal-billed estimates stay zero. With both off the per-user start-day rollup
        answers when it can (`_rollup_overview`), with the same values; otherwise, and on a refusal, the live statements
        run on one connection, one after another, and the response is built after the block."""
        rollup = None if include_class_lists or include_buckets else await self._rollup_overview(f)
        if rollup is not None:
            money_rows, fact_rows, model_rows = rollup.money_rows, rollup.fact_rows, rollup.model_rows
            kpi_rows, line_rows, all_classes = rollup.kpi_rows, rollup.line_rows, []
        else:
            async with dashboard_request(), self._repo.bounded() as repo:
                await repo.materialize_sel(f)
                await repo.materialize_files(f)
                rates = [model_rates_for(model) for model in await repo.get_cost_models(f)]
                money_rows = await repo.get_overview_money(f, rates)
                fact_rows = await repo.get_overview_facts(f)
                model_rows = await repo.get_overview_models(f)
                kpi_rows = await repo.get_dashboard_kpis(f)
                line_rows = await repo.get_overview_lines(f)
                all_classes = await repo.get_class_lists(f, "all") if include_class_lists else []
        parts = _parts(money_rows)
        spend, estimate, pricer = _money(parts.get("money", []))
        if not include_buckets:
            estimate = {}

        kpi = _first(kpi_rows)
        total_sessions = _i(kpi.get("total_sessions"))
        total_cost, cache_read_cost_usd = _f(spend.get("cost_usd")), _f(spend.get("cache_read_cost_usd"))
        api_cost, subscription_cost = _cost_split(total_cost, parts.get("money", []))
        input_tokens = _i(kpi.get("total_input_tokens"))
        output_tokens = _i(kpi.get("total_output_tokens"))
        cache_read_tokens = _i(kpi.get("total_cache_read_tokens"))
        cache_creation_tokens = _i(kpi.get("total_cache_creation_tokens"))
        total_api_calls = _i(kpi.get("total_api_calls"))

        # The facts are already summed over `sel` in the reader: the same costless-inclusive universe
        # `total_sessions` above counts.
        facts = _first(fact_rows)
        total_turns = _i(facts.get("turns"))
        total_files_changed = _i(facts.get("files_changed"))
        tool_calls = _i(facts.get("tool_calls"))
        tool_success = _i(facts.get("tool_calls_success"))
        duration_ms = _i(facts.get("duration_ms"))

        line_parts = _parts(line_rows)
        lines = _first(line_parts.get("total", []))
        net_lines = _nnet(_ni(lines.get("lines_added")), _ni(lines.get("lines_removed")))
        days = sorted(line_parts.get("day", []), key=lambda r: _s(r.get("key")))

        dead_sessions = _i(_first(parts.get("dead", [])).get("n"))

        total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens

        kpis = {
            **without_evidence(row_pricing({**spend, "cost_incomplete": bool(pricer.unpriced)})),
            "total_sessions": total_sessions,
            "total_cost_usd": total_cost,
            "api_cost_usd": api_cost,
            "subscription_cost_usd": subscription_cost,
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

        # Without the class lists every list is empty, also those the day and model statements return with their rows.
        daily_buckets = [
            {
                "day": _s(r.get("key")),
                "net_lines": _ni(r.get("net_lines")),
                **_lists(r if include_class_lists else None),
            }
            for r in days
        ]
        model_breakdown = [
            {
                "model_name": _s(r.get("key")),
                "session_count": _i(r.get("n")),
                **_lists(r if include_class_lists else None),
            }
            for r in model_rows
        ]

        data_as_of = max((_s(r.get("key")) for r in days if r.get("nonzero")), default=None)
        return (
            {"kpis": kpis, "daily_buckets": daily_buckets, "model_breakdown": model_breakdown, **estimate},
            pricer,
            data_as_of,
        )

    # ── Cost ─────────────────────────────────────────────────────────────────

    @one_request_budget
    async def get_cost(
        self, f: LocalAnalyticsFilter, *, include_class_lists: bool = True, include_buckets: bool = True
    ) -> tuple[dict[str, Any], CatalogPricer]:
        """The Cost page from one held connection: rates, one grouped money statement, then the facts, run in order
        under the request budget. The rows are built after the block, once the connection is back in the pool.

        `include_class_lists` and `include_buckets` (the Cost tab reads neither; the route turns them off unless
        asked): without the class lists the subscription and origin lists of the totals, users and models stay empty
        and their statements do not run; without the buckets the funding-route and personal-billed buckets stay
        empty (zero) and the six groupings behind them are not built. `models_without_spend` is always empty."""
        rollup = None if include_class_lists or include_buckets else await self._rollup_cost(f)
        if rollup is not None:
            money, by_user, all_classes, model_classes = (
                rollup.money,
                [{"developer_name": name} for name in rollup.developers],
                [],
                [],
            )
            kpi_rows = [
                {
                    "total_sessions": rollup.total_sessions,
                    "spend_sessions": rollup.spend_sessions,
                    "total_input_tokens": rollup.total_tokens,  # the four token totals are only ever summed
                }
            ]
        else:
            async with dashboard_request(), self._repo.bounded() as repo:
                await repo.materialize_sel(f)  # `sel` once for every statement of the request
                rates = [model_rates_for(model) for model in await repo.get_cost_models(f)]
                money = await repo.get_cost_money(f, rates, buckets=include_buckets)
                kpi_rows = await repo.get_cost_kpis(f)
                all_classes = await repo.get_class_lists(f, "all") if include_class_lists else []
                model_classes = await repo.get_class_lists(f, "model") if include_class_lists else []
                by_user = await repo.get_cost_by_user(f, with_classes=include_class_lists)
        return _cost_response(money, by_user, kpi_rows, all_classes, model_classes, include_buckets)

    async def _rollup_overview(self, f: LocalAnalyticsFilter) -> RollupOverview | None:
        """/overview's cards and charts from the per-user start-day rollup, or None when it cannot answer (see
        `PostgresCliAnalyticsReader.rollup_overview`): the live statements then run. Cached and refused as `/cost`."""
        read = getattr(self._repo, "rollup_overview", None)
        if read is None or not config.CLI_ANALYTICS_USER_ROLLUP_ENABLED:
            return None
        cache = get_ranking_cache()
        key = rollup_key(f, "overview-rollup") if cache.enabled else None
        if key is not None and (cached := cache.get(key)) is not None:
            if cached is _ROLLUP_REFUSED:
                return None
            mark_rollup_used()
            return cached
        async with dashboard_request():
            served = await read(f, model_rates_for, rollup_max_age_s())
        if served is not None:
            mark_rollup_used()  # only a request the rollup answers keeps the warm-up job going
        if key is not None:
            # A refusal is kept too: the next request of the window would repeat the same reads to be refused again.
            cache.put(key, served if served is not None else _ROLLUP_REFUSED)
        return served

    async def _rollup_cost(self, f: LocalAnalyticsFilter) -> RollupCost | None:
        """/cost's totals and money from the per-user start-day rollup, or None when it cannot answer (see
        `PostgresCliAnalyticsReader.rollup_cost`): the live statements then run. A fresh answer is reused for the
        cache's TTL, as for the Users page."""
        read = getattr(self._repo, "rollup_cost", None)
        if read is None or not config.CLI_ANALYTICS_USER_ROLLUP_ENABLED:
            return None
        cache = get_ranking_cache()
        key = rollup_key(f, "cost-rollup") if cache.enabled else None
        if key is not None and (cached := cache.get(key)) is not None:
            if cached is _ROLLUP_REFUSED:
                return None
            mark_rollup_used()
            return cached
        async with dashboard_request():
            served = await read(f, model_rates_for, rollup_max_age_s())
        if served is not None:
            mark_rollup_used()  # only a request the rollup answers keeps the warm-up job going
        if key is not None:
            # A refusal is kept too: the next request of the window would repeat the same reads to be refused again.
            cache.put(key, served if served is not None else _ROLLUP_REFUSED)
        return served

    # ── Users ────────────────────────────────────────────────────────────────

    @one_request_budget
    async def get_users(
        self,
        f: LocalAnalyticsFilter,
        page: int | None,
        per_page: int | None,
        *,
        charts_only: bool = False,
        include_chart: bool = False,
        include_daily_activity: bool = True,
        include_avg_duration: bool = True,
    ) -> tuple[dict[str, Any], CatalogPricer]:
        """The Users page (`/user-charts`: every user) from one held connection: rates, the ranked money page, then
        the facts of the page's developers, run in order under the request budget. The rows are built after it.

        `charts_only` (`/user-charts`, whose consumer reads only each user's name and money): every read after the
        money page is skipped, so the session and token counts, the class lists, the turns, tool, lines, last-active
        and daily-activity fields keep their model defaults (0, empty or None) and `avg_session_duration_ms` is None.

        `include_chart` (`/users?include_chart=true`, a page request): the money statement runs for every user, the
        page is cut from its ranking here, and `chart` lists every user's name and cost in rank order, so the
        cost-share chart needs no `/user-charts` request. The page's rows are the ones the page statement gives.

        `include_daily_activity` and `include_avg_duration` (both read the whole window and nothing in the Users tab
        shows them; the route turns them off unless asked): without them `daily_activity` stays empty and
        `avg_session_duration_ms` is None."""
        limit, offset = (per_page, page * per_page) if page is not None and per_page is not None else (None, 0)
        with_chart = include_chart and limit is not None
        rollup = await self._rollup_users(f, charts_only, include_daily_activity, include_avg_duration)
        if rollup is not None:
            everyone = rollup.money_page
            ranked = everyone
            if limit is not None:
                page_names = everyone.developers[offset : offset + limit]
                ranked = UsersMoneyPage(page_names, everyone.total_count, everyone.money)
            wanted = set(ranked.developers)
            user_rows, developer_facts, lines_rows, last_active_rows = (
                ([], [], [], [])
                if charts_only
                else (
                    [r for r in rollup.user_rows if r["developer_name"] in wanted],
                    [r for r in rollup.developer_facts if r["developer"] in wanted],
                    [r for r in rollup.lines_rows if r["developer_name"] in wanted],
                    [r for r in rollup.last_active_rows if r["developer_name"] in wanted],
                )
            )
            avg_duration, daily_rows = None, []
        else:
            (
                everyone,
                ranked,
                avg_duration,
                user_rows,
                developer_facts,
                lines_rows,
                last_active_rows,
                daily_rows,
            ) = await self._users_live(f, limit, offset, charts_only, include_daily_activity, include_avg_duration)
        # The rows of every user (8,000 and their daily activity) are built off the event loop: ingest runs on it.
        data, pricer = await asyncio.to_thread(
            self._users_response,
            ranked,
            avg_duration,
            user_rows,
            developer_facts,
            lines_rows,
            last_active_rows,
            daily_rows,
        )
        if with_chart:
            user_money = {g.keys["user"]: g for g in everyone.money.groups.get(G_USER, [])}
            data["chart"] = [
                {
                    "developer_name": name,
                    "cost_usd": user_money[name].cost_usd if name in user_money else 0.0,
                    **group_pricing(user_money.get(name)),
                }
                for name in everyone.developers
            ]
        return data, pricer

    async def _rollup_users(
        self, f: LocalAnalyticsFilter, charts_only: bool, include_daily_activity: bool, include_avg_duration: bool
    ) -> RollupUsers | None:
        """The Users page's data from the per-user start-day rollup, or None when it cannot answer (see
        `PostgresCliAnalyticsReader.rollup_users`) or when the request asks for a read over the whole window that the
        rollup does not hold (daily activity, the average duration): the live statements then run."""
        if not charts_only and (include_daily_activity or include_avg_duration):
            return None
        read = getattr(self._repo, "rollup_users", None)
        if read is None or not config.CLI_ANALYTICS_USER_ROLLUP_ENABLED:
            return None
        # Every page of a window and /user-charts read the same data: a fresh answer is reused for the cache's TTL, and
        # the warm-up job keeps the answer of the usual windows fresh while the rollup is in use.
        cache = get_ranking_cache()
        key = rollup_key(f) if cache.enabled else None
        if key is not None and (cached := cache.get(key)) is not None:
            if cached is _ROLLUP_REFUSED:
                return None
            mark_rollup_used()
            return cached
        async with dashboard_request():
            served = await read(f, model_rates_for, rollup_max_age_s())
        if served is not None:
            mark_rollup_used()  # only a request the rollup answers keeps the warm-up job going
        if key is not None:
            # A refusal is kept too: the next request of the window would repeat the same reads to be refused again.
            cache.put(key, served if served is not None else _ROLLUP_REFUSED)
        return served

    async def _users_live(
        self,
        f: LocalAnalyticsFilter,
        limit: int | None,
        offset: int,
        charts_only: bool,
        include_daily_activity: bool,
        include_avg_duration: bool,
    ) -> tuple[UsersMoneyPage, UsersMoneyPage, int | None, list[dict], list[dict], list[dict], list[dict], list[dict]]:
        """The live statements of the Users page, on one held connection, in order, under the request budget."""
        async with dashboard_request(), self._repo.bounded() as repo:
            await repo.materialize_sel(f)  # `sel` once for every statement of the request
            everyone = await _users_ranking(repo, f)
            ranked = everyone
            if limit is not None:
                ranked = UsersMoneyPage(
                    everyone.developers[offset : offset + limit], everyone.total_count, everyone.money
                )
            avg_duration = (
                await repo.get_avg_session_duration_ms(f) if include_avg_duration and not charts_only else None
            )
            detail = await _users_detail(repo, f, ranked, limit, charts_only, include_daily_activity)
        return (everyone, ranked, avg_duration, *detail)

    @staticmethod
    def _users_response(
        ranked: UsersMoneyPage,
        avg_duration: int | None,
        user_rows: list[dict],
        developer_facts: list[dict],
        lines_rows: list[dict],
        last_active_rows: list[dict],
        daily_rows: list[dict],
    ) -> tuple[dict[str, Any], CatalogPricer]:
        """The Users response from what the held connection read (pure Python: no database access)."""
        groups = ranked.money.groups
        pricer = CatalogPricer()
        total = next(iter(groups.get(G_TOTAL, [])), None)
        if total is not None:
            pricer._unpriced.update(total.unpriced_models)  # the spend scope only; estimate buckets never feed it
        user_cost = {g.keys["user"]: g.cost_usd for g in groups.get(G_USER, [])}
        user_money = {g.keys["user"]: g for g in groups.get(G_USER, [])}
        user_routes = _by_group_key(groups.get(G_USER_ROUTE, []), "user")
        user_personal = _by_group_key(groups.get(G_USER_PERSONAL, []), "user")
        facts_by_user = {_s(r.get("developer")): r for r in developer_facts}
        users_by_name = {_s(r.get("developer_name")): r for r in user_rows}
        last_active = {_s(r.get("developer_name")): r.get("last_active") for r in last_active_rows}
        net_lines_by_user = {_s(r.get("developer_name")): _ni(r.get("net_lines")) for r in lines_rows}

        daily_by_user: dict[str, list[dict]] = {}
        for row in daily_rows:
            daily_by_user.setdefault(_s(row.get("developer_name")), []).append(
                {"day": _iso(row.get("day")), "session_count": _i(row.get("session_count")), **_lists(row)}
            )

        rows: list[dict] = []
        for name in ranked.developers:  # the page, in rank order
            row = users_by_name.get(name, {})
            facts = facts_by_user.get(name, {})
            tool_calls = _i(facts.get("tool_calls"))
            rows.append(
                {
                    "developer_name": name,
                    "user_email": name,
                    "session_count": _i(row.get("session_count")),
                    "input_tokens": _i(row.get("input_tokens")),
                    "output_tokens": _i(row.get("output_tokens")),
                    "cache_read_tokens": _i(row.get("cache_read_tokens")),
                    "cache_creation_tokens": _i(row.get("cache_creation_tokens")),
                    "cost_usd": user_cost.get(name, 0.0),
                    **group_pricing(user_money.get(name)),
                    "turns": _i(facts.get("turns")),
                    "tool_calls": tool_calls,
                    "tool_success_rate": _pct(_i(facts.get("tool_successes")), tool_calls),
                    "top_model": _s(row.get("top_model")) or None,
                    "net_lines": net_lines_by_user.get(name),
                    "last_active": _iso(last_active[name]) if last_active.get(name) else None,
                    "user_id": None,  # filled in by the router via UserIdentityResolver
                    "daily_activity": daily_by_user.get(name, []),
                    **_lists(row),
                    **buckets_from_groups(user_routes.get(name, []), user_personal.get(name, [])),
                }
            )

        return {
            "rows": rows,
            "avg_session_duration_ms": avg_duration,
            "total_count": ranked.total_count,
            **buckets_from_groups(groups.get(G_ROUTE, []), groups.get(G_PERSONAL, [])),
        }, pricer

    # ── Repositories ─────────────────────────────────────────────────────────

    async def _repository_rows(
        self, f: LocalAnalyticsFilter, include_branches: bool, search: str | None
    ) -> tuple[list[dict], set[str], CatalogPricer]:
        """Repository rows (with `_sids` still attached), the selected session ids and the pricer. Shared by the
        repository and project breakdowns; the funding rows are read by the caller over the scope it reports."""
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
            self._repo.get_session_model_costs(f),
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

        rows = _build_session_repository_rows(
            cost_facts,
            money.by_session,
            turns_by_session,
            files_by_session,
            success_by_session,
            lines_by_session,
            include_branches,
        )
        return rows, returned, money.pricer

    async def get_repositories(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        include_branches: bool,
        search: str | None = None,
        unattributed_last: bool = False,
    ) -> tuple[dict[str, Any], CatalogPricer]:
        # With `unattributed_last` (paged, without branches) the group without a repository is not ranked or counted:
        # `total_count`/`has_more` count named repositories and that group is the last row of the last page. By
        # default it ranks and counts like any row.
        # The first statement ranks, pages and prices the groups of the sessions the search keeps (the whole selection,
        # before the page is cut); the totals, the unpriced models and the estimates are those of that selection. The
        # second reads the counts of the groups on the page only.
        async with dashboard_request(), self._repo.bounded() as repo:
            rates = [model_rates_for(model) for model in await repo.get_cost_models(f)]
            rows = await repo.get_repository_ranking(
                f, include_branches, search, rates, page, per_page, unattributed_last=unattributed_last
            )
            parts = _parts(rows)
            ranked = sorted(parts.get("group", []), key=lambda r: _i(r.get("key")))  # the reader's rank
            # The counts of the groups, for the groups of the page only (every group with branches).
            named = [_s(r.get("repository")) for r in ranked if _s(r.get("repository"))]
            fact_rows = (
                await repo.get_repository_facts(
                    f, include_branches, search, named, unattributed=len(named) < len(ranked)
                )
                if ranked
                else []
            )
            # Without branches the estimates of the page are priced from the page's sessions alone.
            bucket_rows = (
                await repo.get_repository_buckets(f, search, rates, named, unattributed=len(named) < len(ranked))
                if ranked and not include_branches
                else parts.get("bucket", [])
            )
        _spend, estimate, pricer = _money(parts.get("total", []))

        def group_key(row: dict) -> tuple:
            names = (row.get("project_name"), row.get("repository"), row.get("branch"))
            return tuple(_s(n) for n in names) if include_branches else (_s(row.get("repository")),)

        facts = {group_key(r): r for r in fact_rows}
        groups = [
            {
                **facts.get(group_key(g), {}),
                **{
                    k: g.get(k)
                    for k in (
                        "key",
                        "n",
                        "project_name",
                        "repository",
                        "branch",
                        "cost_usd",
                        "unpriced_models",
                        "cost_incomplete",
                        "cost_priced",
                    )
                },
            }
            for g in ranked
        ]
        page_rows = _build_repository_rows(groups, include_branches)
        drop_evidence(page_rows)

        if include_branches:
            total_count = len(page_rows)
            pagination = {"page": 0, "per_page": total_count, "total_count": total_count, "has_more": False}
        else:
            # `count` is the named repositories only with `unattributed_last`, else every group.
            pagination = _pagination(page, per_page, _i(_first(parts.get("count", [])).get("n")))

        def row_key(project: Any, repository: Any) -> tuple:
            return (_s(project), _s(repository)) if include_branches else (_s(repository),)

        bucket_rows_by_key: dict[tuple, dict[str, list[dict]]] = {}
        for bucket in bucket_rows:
            kind = "route" if _s(bucket.get("key")) == "b_route" else "billed"
            key = row_key(bucket.get("project_name"), bucket.get("repository"))
            bucket_rows_by_key.setdefault(key, {"route": [], "billed": []})[kind].append(bucket)
        for row in page_rows:
            mine = bucket_rows_by_key.get(row_key(row["project_name"], row["repository"]), {})
            row.update(_dashboard_buckets(mine.get("route", []), mine.get("billed", [])))
        page_rows = _unattributed_repository_last(
            page_rows, search, pagination, enabled=unattributed_last and not include_branches
        )
        return {"rows": page_rows, "pagination": pagination, **estimate}, pricer

    async def get_branches(
        self, f: LocalAnalyticsFilter, repository: str, page: int, per_page: int
    ) -> tuple[dict[str, Any], CatalogPricer]:
        """One page of the branches of `repository` (ANDed with the filter's repositories) by session count, spend,
        then name, paged in SQL. The sessions without a branch are the last row of the last page, outside `per_page`
        and `total_count`."""
        if f.repositories and repository not in f.repositories:
            return {"rows": [], "pagination": _pagination(page, per_page, 0)}, CatalogPricer()
        scoped = copy.copy(f)
        scoped.repositories = [repository]
        async with dashboard_request(), self._repo.bounded() as repo:
            rates = [model_rates_for(model) for model in await repo.get_cost_models(scoped)]
            rows = await repo.get_branch_ranking(scoped, rates, page, per_page)
        parts = _parts(rows)
        _spend, _estimate, pricer = _money(parts.get("total", []))
        pagination = _pagination(page, per_page, _i(_first(parts.get("count", [])).get("n")))
        ranked = sorted(parts.get("group", []), key=lambda r: _i(r.get("key")))  # the reader's rank
        branch_rows = [
            {field: row[field] for field in _BRANCH_ROW_FIELDS} for row in (_repository_bucket(g, True) for g in ranked)
        ]
        named = [r for r in branch_rows if r["branch"] is not None]
        null_row = next((r for r in branch_rows if r["branch"] is None), None)
        return {"rows": with_unattributed(named, null_row, pagination), "pagination": pagination}, pricer

    async def get_projects(
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        search: str | None = None,
        include_repositories: bool = True,
    ) -> tuple[dict[str, Any], CatalogPricer]:
        """One page of projects by spend.

        The database matches the search, groups the sessions by project, orders and cuts the page (`get_project_page`,
        priced with the catalog rates of the window's models); only the page's sessions are then read in detail and
        priced here, so the response cost is the catalog price, as everywhere.

        By default each project carries its repositories and their branches nested, the search matches a project or
        repository name and the unattributed project is ranked last and counted. Without `include_repositories`
        (drill-down) the rows are totals only (`repository_count`, no repositories), the search matches project names
        only, and the unattributed project is the last row of the last page, outside `per_page` and `total_count`,
        and never under a search."""
        search = search.strip() if search and search.strip() else None
        rates = [model_rates_for(model) for model in await self._repo.get_cost_models(f)]
        result = await self._repo.get_project_page(f, page, per_page, rates, search, drilldown=not include_repositories)
        if not include_repositories:
            return await self._drilldown_projects(f, result, page, per_page, rates)
        # The window buckets cover every matching session: the whole window without a search.
        window = f if result.matched_session_ids is None else f.restricted_to(result.matched_session_ids)
        pagination = _pagination(page, per_page, result.total)
        page_session_ids = [*result.session_ids, *result.unattributed_session_ids]
        rows: list[dict] = []
        pricer = CatalogPricer()
        if page_session_ids:
            rows, _, pricer = await self._repository_rows(
                f.restricted_to(page_session_ids), include_branches=True, search=None
            )
        # metadata.unpriced_models covers the window's spend models, not only the page's.
        pricer.record_unpriced_from_flags(result.model_flags)
        priced = price_bucket_rows(await self._repo.get_session_funding_costs(window))

        page_rows = _group_repositories_into_projects(rows, result.project_names, include_repositories)
        drop_evidence(page_rows)
        # Each entity reads only its own sessions' rows through one index of the window's rows, not the whole window.
        index = session_index(priced)
        for project in page_rows:
            scoped = rows_of(priced, index, project.pop("_sids"))
            project.update(buckets(scoped), **pricing_of_rows(scoped))
            # Not `repo`: that name is the reader yielded by `bounded()` (test_ports).
            for repo_row in project.get("repositories", ()):
                scoped = rows_of(priced, index, repo_row.pop("_sids"))
                repo_row.update(buckets(scoped), **pricing_of_rows(scoped))
                for branch_row in repo_row["branches"]:
                    branch_row.update(pricing_of_rows(rows_of(priced, index, branch_row.pop("_sids"))))
        return {"rows": page_rows, "pagination": pagination, **buckets(priced, result.matched_session_ids)}, pricer

    async def _drilldown_projects(
        self,
        f: LocalAnalyticsFilter,
        result: ProjectPage,
        page: int,
        per_page: int,
        rates: list[ModelRates],
    ) -> tuple[dict[str, Any], CatalogPricer]:
        """The drill-down page (`include_repositories=false`): the totals of the page's projects come from one database
        statement (`get_project_totals`, grouped by project over the page's sessions), priced with the catalog rates
        in SQL. No per-session read and no nested repository or branch row is built; the estimate buckets of a project
        are priced from its session ids, which the same statement returns."""
        # The window buckets cover every matching session: the whole window without a search.
        window = f if result.matched_session_ids is None else f.restricted_to(result.matched_session_ids)
        pagination = _pagination(page, per_page, result.total)
        page_session_ids = [*result.session_ids, *result.unattributed_session_ids]
        project_rows = []
        if page_session_ids:
            totals = await self._repo.get_project_totals(f.restricted_to(page_session_ids), rates)
            project_rows = [_project_total_row(row) for row in totals]
        position = {name: index for index, name in enumerate(result.project_names)}
        project_rows.sort(key=lambda p: position.get(p["project_name"], len(position)))
        # The pricer holds the window's unpriced spend models (the flags), not only the page's.
        pricer = CatalogPricer()
        pricer.record_unpriced_from_flags(result.model_flags)
        priced = price_bucket_rows(await self._repo.get_session_funding_costs(window))
        index = session_index(priced)
        for project in project_rows:
            scoped = rows_of(priced, index, project.pop("_sids"))
            project.update(buckets(scoped), **pricing_of_rows(scoped))
        named = [p for p in project_rows if p["project_name"] is not None]
        null_row = next((p for p in project_rows if p["project_name"] is None), None)
        page_rows = with_unattributed(named, null_row, pagination)
        return {"rows": page_rows, "pagination": pagination, **buckets(priced, result.matched_session_ids)}, pricer

    # ── Sessions ─────────────────────────────────────────────────────────────

    @staticmethod
    def _log_unavailable_cost(session_ids: list[str], unpriced_sessions: set[str]) -> None:
        unpriced_models = sum(1 for sid in session_ids if sid in unpriced_sessions)
        if unpriced_models:
            logger.info("cli_analytics: %d sessions listed with cost_available=false: unpriced models", unpriced_models)

    @staticmethod
    def _session_row(  # noqa: PLR0913 - one argument per per-session fact read, merged here
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
                row.get("session_title"),
                row.get("prompt"),
                row.get("repository"),
                row.get("branch"),
                row.get("entrypoint"),
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
    async def _read_window(
        repo: CliAnalyticsReader,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        sort_by: str,
        search: str | None,
        framework: str | None,
        is_unattributed: bool,
    ) -> tuple[list[tuple[str, tuple[bool, ...]]] | None, int, list[str]]:
        """The window's reads, chosen by the database for every sort: (model flags, total, page ids).
        The flags (whole filtered window) exist only for the cost sort and the framework filter; the other sorts
        report unpriced models from the page's own rows, as before, and issue no window-wide token statement.

        The cost sort needs the catalog rates of the window's models first (`get_cost_models`). The framework filter
        is the customer's regex config, so the reader hands it the distinct classifier name sets of the window and
        pages the sessions of the sets it accepts. Runs statements one after another.
        """
        rates: list[ModelRates] | None = None
        if sort_by == "cost_usd":
            rates = [model_rates_for(model) for model in await repo.get_cost_models(f)]
        if framework:
            total, page_ids, flags = await repo.get_framework_page(
                f,
                sort_by,
                page,
                per_page,
                lambda sets: [names for names in sets if classify_delivery_framework(names) == framework],
                rates=rates,
                search=search,
                is_unattributed=is_unattributed,
            )
            return flags, total, page_ids
        if sort_by == "cost_usd":
            # One statement builds the (session, model) groups once for the key, the page, the count and the flags.
            total, page_ids, flags = await repo.get_cost_page(f, page, per_page, rates or [], search, is_unattributed)
            return flags, total, page_ids
        total, page_ids = await repo.get_session_page(f, sort_by, page, per_page, search, is_unattributed, rates=rates)
        return None, total, page_ids

    @staticmethod
    async def _read_details(repo: CliAnalyticsReader, f: LocalAnalyticsFilter, page_ids: list[str], unattributed: bool):
        """The page's detail statements, run together: the bounded reader runs at most two of them at a time.

        A task group, not `gather`: when one statement fails the others are cancelled and awaited before this returns,
        so the bounded block never releases a connection while a sibling statement is still running on it. The first
        failure is re-raised as itself (not as an ExceptionGroup), so callers see the same exception types as before.
        """
        try:
            async with asyncio.TaskGroup() as group:
                tasks = (
                    group.create_task(
                        repo.get_session_cost_facts(f, is_unattributed=unattributed, include_costless=True)
                    ),
                    group.create_task(repo.get_turns_by_session(f)),
                    group.create_task(repo.get_tool_success_by_session(f)),
                    group.create_task(repo.get_lines_by_session(f)),
                    group.create_task(repo.get_session_model_costs(f)),
                    group.create_task(repo.get_skill_names_by_session(page_ids, scope=f.scope)),
                )
        except ExceptionGroup as failure:
            raise failure.exceptions[0] from None
        return tuple(task.result() for task in tasks)

    async def get_sessions(  # noqa: PLR0913 - mirrors the Sessions route's query parameters
        self,
        f: LocalAnalyticsFilter,
        page: int,
        per_page: int,
        sort_by: str,
        search: str | None,
        framework: str | None = None,
        is_unattributed: bool = False,
        branch: str | None = None,
        is_branch_unattributed: bool = False,
    ) -> tuple[dict[str, Any], CatalogPricer, str | None]:
        f.branch = branch or None
        f.branch_unattributed = is_branch_unattributed
        # A blank search filters nothing: it must not route the request through the searched statements.
        search = search if search and search.strip() else None
        pricer = CatalogPricer()
        # One analytics connection for the window statements and a second one only for the details, held only while
        # statements run: pricing and the row building happen outside them. (The framework filter's classification of
        # the window's few name sets runs inside the hold.)
        async with self._repo.bounded() as repo:
            flags, total, page_ids = await self._read_window(
                repo, f, page, per_page, sort_by, search, framework, is_unattributed
            )
            details = None
            if page_ids:
                # The window statements ran on the one connection; only the detail statements use a second one.
                async with repo.widened() as wide:
                    details = await self._read_details(wide, f.restricted_to(page_ids), page_ids, is_unattributed)
        if flags is not None:
            # Whole filtered window, only where the request already read it (the cost sort, the framework filter).
            pricer.record_unpriced_from_flags(flags)
        if not page_ids:
            return {"sessions": [], "page": page, "per_page": per_page, "total": total}, pricer, None
        cost_facts, turns_rows, success_rows, lines_rows, session_model_rows, skill_rows = details
        skills_by_session = {_s(r["session_id"]): r["skill_names"] for r in skill_rows}

        turns_by_session = {_s(r.get("session_id")): r for r in turns_rows}
        success_by_session = {_s(r.get("session_id")): r for r in success_rows}
        lines_by_session = {_s(r.get("session_id")): r for r in lines_rows}
        model_rows_by_session = self._group_by_session(session_model_rows)
        unpriced_sessions = unpriced_session_ids(session_model_rows, pricer)

        rows_by_session = {_s(r.get("session_id")): r for r in cost_facts}
        # The page order is the response order; a page id without a fact row is not listed.
        page_rows = [
            self._session_row(
                rows_by_session[sid],
                turns_by_session,
                success_by_session,
                lines_by_session,
                skills_by_session,
                model_rows_by_session,
            )
            for sid in page_ids
            if sid in rows_by_session
        ]

        listed = {s["trace_id"] for s in page_rows}
        money = price_session_models([r for r in session_model_rows if _s(r.get("session_id")) in listed], pricer)
        for s in page_rows:
            s["cost_usd"] = money.by_session.get(s["trace_id"], 0.0)
            s["cost_available"] = s["trace_id"] not in unpriced_sessions
            s.update(money.session_pricing(s["trace_id"]))
            s["bloat_pct"] = _pct(money.cache_read_by_session.get(s["trace_id"], 0.0), s["cost_usd"])
        self._log_unavailable_cost([s["trace_id"] for s in page_rows], unpriced_sessions)

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
                meta.get("session_title"),
                meta.get("prompt"),
                meta.get("repository"),
                meta.get("branch"),
                meta.get("entrypoint"),
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
            "cost_available": cost_available(model_cost_rows, pricer),
            **money.session_pricing(),  # the detail's rows are this one session's
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
        return detail, unpriced_display_names(pricer.unpriced), pricer.catalog_source

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
            "reasoning_tokens": _i(row.get("reasoning_tokens")) if row.get("reasoning_tokens") is not None else None,
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

    async def get_activity(self, f: LocalAnalyticsFilter, tz: tzinfo = timezone.utc) -> dict[str, Any]:
        """Weekday/hour buckets in `tz`. Each session is converted with its own UTC offset, so sessions
        recorded under another DST offset than today's still land on their local hour and weekday."""
        rows = await self._repo.get_session_start_times(f)
        heat = [[0] * 24 for _ in range(7)]
        by_hour = [0] * 24
        by_weekday = [0] * 7
        for row in rows:
            started = row.get("started_at")
            if not isinstance(started, datetime):
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            started = started.astimezone(tz)
            weekday = started.weekday()  # Monday=0 .. Sunday=6, matching the contract
            hour = started.hour
            heat[weekday][hour] += 1
            by_hour[hour] += 1
            by_weekday[weekday] += 1
        return {"heat": heat, "by_hour": by_hour, "by_weekday": by_weekday}

    # ── Tools ────────────────────────────────────────────────────────────────

    async def get_tools(self, f: LocalAnalyticsFilter) -> dict:
        # One held connection and `sel` built once, as the other dashboards: six statements at once took six of the
        # pool's eight connections and each evaluated the window's selection again.
        async with dashboard_request(), self._repo.bounded() as repo:
            await repo.materialize_sel(f)
            tool_rows = await repo.get_tool_usage(f)
            model_rows = await repo.get_model_breakdown(f)
            invocation_rows = await repo.get_invocations(f)
            tool_classes = await repo.get_class_lists(f, "tool")
            model_classes = await repo.get_class_lists(f, "model")
            invocation_classes = await repo.get_class_lists(f, "invocation")
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
        async with dashboard_request(), self._repo.bounded() as repo:
            await repo.materialize_sel(f)
            await repo.materialize_files(f)
            rates = [model_rates_for(model) for model in await repo.get_cost_models(f)]
            fact_rows = await repo.get_efficiency_facts(f)
            money_rows = await repo.get_efficiency_money(f, rates)
            kpi_rows = await repo.get_dashboard_kpis(f)
        parts = {**_parts(fact_rows), **_parts(money_rows)}
        everything, code = (_first(parts.get(part, [])) for part in ("all", "code"))
        spend, estimate, pricer = _money(parts.get("money", []))

        kpi = _first(kpi_rows)
        total_cost = _f(spend.get("cost_usd"))
        cache_read_tokens = _i(kpi.get("total_cache_read_tokens"))
        total_api_calls = _i(kpi.get("total_api_calls"))
        cache_read_cost_usd = _f(spend.get("cache_read_cost_usd"))

        worst = _first(parts.get("worst", []))
        worst_prompt = None
        if worst:
            worst_prompt = (
                _served_prompt(
                    worst.get("session_title"),
                    worst.get("prompt"),
                    worst.get("repository"),
                    worst.get("branch"),
                    worst.get("entrypoint"),
                )
                or None
            )
            if worst_prompt:
                worst_prompt = worst_prompt[:_PROMPT_PREVIEW_LEN]
        depth = {_s(r.get("key")): r for r in parts.get("depth", [])}

        # A session that cost money and changed no file is dead; the reader counts them and lists their classes.
        dead = _first(parts.get("dead", []))
        dead_count, wasted = _i(dead.get("n")), _f(dead.get("cost_usd"))

        data = {
            "kpis": {
                "avg_context_per_call": _ctx_per_call(cache_read_tokens, total_api_calls),
                "worst_session_ctx_per_call": _f(worst.get("ratio")) if worst else None,
                "worst_session_prompt": worst_prompt,
                "worst_session_trace_id": _s(worst.get("session_id")) if worst else None,
                "cache_read_cost_usd": cache_read_cost_usd,
                "bloat_pct": _pct(cache_read_cost_usd, total_cost),
                "total_cost_usd": total_cost,
                **_lists(everything),
            },
            "dead_sessions": {
                "count": dead_count,
                "pct_of_sessions": _pct(dead_count, _i(kpi.get("spend_sessions"))),
                "wasted_cost_usd": wasted,
                "avg_cost_per_dead": (wasted / dead_count) if dead_count else None,
                **_lists(dead),
            },
            "session_depth": [
                {"bucket": b, "count": _i(depth.get(b, {}).get("n")), **_lists(depth.get(b))} for b in _DEPTH_BUCKETS
            ],
            "code_changes": {
                "files_changed": _i(code.get("files_changed")),
                "files_written": _i(code.get("files_written")),
                "files_edited": _i(code.get("files_edited")),
                "net_lines": _nnet(_ni(code.get("lines_added")), _ni(code.get("lines_removed"))),
                **_lists(code),
            },
            **estimate,
        }
        return data, pricer


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
