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

"""A fake reader's dashboard statements (`get_overview_facts`, `get_repository_groups`, `get_efficiency_facts`),
derived in Python from the per-session statements the test already stubs: the facts and the Python pricer
(`price_session_models`, `price_bucket_rows`, `buckets`) over the stubbed money rows. This is the aggregation and the
pricing the handler did before the database did them, kept as the oracle for the handler tests; the SQL itself is
checked against a real PostgreSQL (`test_live_dashboard_aggregates.py`)."""

from typing import Any
from unittest.mock import AsyncMock

from codemie.repository.cli_analytics.scope import FUNDING_ROUTES
from codemie.service.analytics.handlers.cli_analytics_handler import _float_sort_key, depth_bucket
from codemie.service.analytics.handlers.funding_buckets import price_bucket_rows
from codemie.service.analytics.handlers.session_money import price_session_models

_BUCKETS = ("1", "2-5", "6-10", "11-25", "26-50", "50+")


def _i(value: Any) -> int:
    return 0 if value is None else int(value)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _known_sum(values: list) -> int | None:
    known = [v for v in values if v is not None]
    return sum(known) if known else None


def _lists(rows: list[dict]) -> dict[str, list[str]]:
    return {
        "subscription_types": sorted({_s(v) or "unknown" for r in rows for v in r.get("subscription_types") or []}),
        "project_origins": sorted({_s(v) or "none" for r in rows for v in r.get("project_origins") or []}),
    }


def _bucket_row(key: str, rows: list[dict], **keys: Any) -> dict:
    unpriced = sorted({r["model"] for r in rows if r["missing"]})
    return {
        "key": key,
        **keys,
        "cost_usd": sum(r["cost_usd"] for r in rows),
        "session_count": len({r["session_id"] for r in rows}),
        "cost_incomplete": bool(unpriced),
        "unpriced_models": unpriced,
    }


def _estimate_rows(priced: list[dict], session_ids: Any = None, prefix: str = "", **keys: Any) -> list[dict]:
    """The funding-route and personal-billed rows of the priced estimate rows of `session_ids` (every row when None),
    as `grouped_cost_sql` returns them (a bucket without a priced session has no row)."""
    picked = [r for r in priced if session_ids is None or r["session_id"] in session_ids]
    rows = []
    for route in FUNDING_ROUTES:
        mine = [r for r in picked if r["funding_route"] == route]
        if mine:
            rows.append(_bucket_row(f"{prefix}route", mine, funding_route=route, **keys))
    for flag in (False, True):
        mine = [r for r in picked if r["personal_billed"] == flag]
        if mine:
            rows.append(_bucket_row(f"{prefix}billed", mine, personal_billed=flag, **keys))
    return rows


def aggregating(repo: Any) -> Any:  # noqa: C901 - one closure per statement, sharing the stubbed reader
    """Give `repo` the dashboard statements, read from its per-session ones at call time."""

    if not isinstance(getattr(repo, "get_cost_models", None), AsyncMock):
        repo.get_cost_models = AsyncMock(return_value=[])

    async def money(f, session_ids=None, spend=True):
        """(money of the spend rows, estimate rows) of `session_ids` (every selected session when None)."""
        spend_rows = await repo.get_session_model_costs(f) if spend else []
        funding_rows = await repo.get_session_funding_costs(f)
        if session_ids is not None:
            spend_rows = [r for r in spend_rows if _s(r.get("session_id")) in session_ids]
        return price_session_models(spend_rows), price_bucket_rows(funding_rows)

    async def class_rows(f) -> list[dict]:
        """The Overview's `class` rows (the cost by subscription class), priced from the funding rows, which list every
        session. A class without a session has no row; a funding row without a class reads unknown."""
        by_class: dict[str, list[dict]] = {}
        for row in await repo.get_session_funding_costs(f):
            by_class.setdefault(_s(row.get("subscription_type")) or "unknown", []).append(row)
        return [
            _bucket_row("class", price_bucket_rows(named), subscription_type=name)
            for name, named in sorted(by_class.items())
        ]

    def total_rows(spend, priced: list[dict], session_ids: Any = None, estimate_prefix: str = "") -> list[dict]:
        spend_row = {
            "key": "spend",
            "cost_usd": spend.total,
            "cache_read_cost_usd": spend.cache_read,
            "unpriced_models": spend.pricer.unpriced,
        }
        return [spend_row, *_estimate_rows(priced, session_ids, estimate_prefix)]

    async def overview_all(f, want):
        rows: list[dict] = []
        files = {_s(r.get("session_id")): r for r in await repo.get_file_facts_by_session(f)}
        if "facts" in want:
            turns = await repo.get_turns_by_session(f)
            tools = await repo.get_tool_success_by_session(f)
            durations = await repo.get_session_durations(f)
            rows.append(
                {
                    "part": "facts",
                    "turns": sum(_i(r.get("turns")) for r in turns),
                    "tool_calls": sum(_i(r.get("tool_calls")) for r in tools),
                    "tool_calls_success": sum(_i(r.get("tool_calls_success")) for r in tools),
                    "duration_ms": sum(_i(r.get("duration_ms")) for r in durations),
                    "files_changed": sum(_i(r.get("files_changed")) for r in files.values()),
                }
            )
        if "money" in want:
            spend, priced = await money(f)
            dead = [
                sid
                for sid, cost in spend.by_session.items()
                if cost > 0 and not _i(files.get(sid, {}).get("files_changed"))
            ]
            rows += [{"part": "money", **row} for row in total_rows(spend, priced)]
            rows += [{"part": "money", **row} for row in await class_rows(f)]
            rows.append({"part": "dead", "n": len(dead), "cost_usd": sum(spend.by_session[sid] for sid in dead)})
        return rows

    async def repository_all(
        f, include_branches=False, search=None, rates=(), page=0, per_page=None, priced=True, spend=True
    ):
        facts = await repo.get_session_cost_facts(f, search=search, include_costless=True)
        turns = {_s(r.get("session_id")): _i(r.get("turns")) for r in await repo.get_turns_by_session(f)}
        files = {_s(r.get("session_id")): r for r in await repo.get_file_facts_by_session(f)}
        tools = {_s(r.get("session_id")): r for r in await repo.get_tool_success_by_session(f)}
        lines = {_s(r.get("session_id")): r for r in await repo.get_lines_by_session(f)}
        returned = {_s(r.get("session_id")) for r in facts}
        spend, priced_rows = await money(f, returned, spend) if priced else (price_session_models([]), [])
        groups: dict[tuple, dict] = {}
        for row in facts:
            sid = _s(row.get("session_id"))
            repository, branch = _s(row.get("repository")) or None, _s(row.get("branch")) or None
            project = _s(row.get("project_name")) or None
            key = (project, repository, branch) if include_branches else (repository,)
            group = groups.setdefault(
                key,
                {
                    "project_name": project if include_branches else None,
                    "repository": repository,
                    "branch": branch if include_branches else None,
                    "n": 0,
                    "turns": 0,
                    "files_changed": 0,
                    "_added": [],
                    "_removed": [],
                    "tool_calls": 0,
                    "tool_calls_success": 0,
                    "subscription_types": set(),
                    "project_origins": set(),
                    "_projects": {},
                    "_sids": [],
                },
            )
            group["n"] += 1
            group["_sids"].append(sid)
            group["turns"] += turns.get(sid, 0)
            group["files_changed"] += _i(files.get(sid, {}).get("files_changed"))
            for name in ("added", "removed"):
                value = lines.get(sid, {}).get(f"lines_{name}")
                group[f"_{name}"].append(None if value is None else _i(value))
            group["tool_calls"] += _i(tools.get(sid, {}).get("tool_calls"))
            group["tool_calls_success"] += _i(tools.get(sid, {}).get("tool_calls_success"))
            group["subscription_types"].add(_s(row.get("subscription_type")) or "unknown")
            group["project_origins"].add(_s(row.get("project_origin")) or "none")
            if not include_branches and project:
                group["_projects"][project] = group["_projects"].get(project, 0) + 1
        out = []
        for group in groups.values():
            counts = group.pop("_projects")
            if not include_branches and counts:
                group["project_name"] = min(counts, key=lambda p: (-counts[p], p))
            group["lines_added"], group["lines_removed"] = (
                _known_sum(group.pop("_added")),
                _known_sum(group.pop("_removed")),
            )
            group["subscription_types"] = sorted(group["subscription_types"])
            group["project_origins"] = sorted(group["project_origins"])
            group["cost_usd"] = sum(spend.by_session.get(sid, 0.0) for sid in group["_sids"])
            out.append(group)
        out.sort(key=lambda r: (r["repository"] or "", r["project_name"] or ""))
        out.sort(key=lambda r: (r["n"], _float_sort_key(r["cost_usd"])), reverse=True)
        ranked = [{**g, "key": str(rank)} for rank, g in enumerate(out, 1)]
        shown = ranked if include_branches or per_page is None else ranked[page * per_page : (page + 1) * per_page]

        rows = [{"part": "group", **{k: v for k, v in g.items() if k != "_sids"}} for g in shown]
        rows.append({"part": "count", "n": len(out)})
        rows += [{"part": "total", **row} for row in total_rows(spend, priced_rows, returned)]
        # the funding and personal-billed estimates of the groups shown, by repository (by project and repository when
        # the branches roll up)
        keyed: dict[tuple, set] = {}
        for g in shown:
            key = (g["project_name"], g["repository"]) if include_branches else (g["repository"],)
            keyed.setdefault(key, set()).update(g["_sids"])
        for key, sids in keyed.items():
            names = (
                {"project_name": _s(key[0]), "repository": _s(key[1])}
                if include_branches
                else {"repository": _s(key[0])}
            )
            rows += [{"part": "bucket", **row} for row in _estimate_rows(priced_rows, sids, "b_", **names)]
        return rows

    async def efficiency_all(f, want):
        facts = await repo.get_session_cost_facts(f, include_costless=True)
        turns = {_s(r.get("session_id")): _i(r.get("turns")) for r in await repo.get_turns_by_session(f)}
        files = {_s(r.get("session_id")): r for r in await repo.get_file_facts_by_session(f)}
        lines = await repo.get_lines_by_session(f)
        totals = (await repo.get_lines_totals(f) or [{}])[0]
        classes = {_s(r.get("key")): r for r in await repo.get_class_lists(f, "session")}
        spend, priced = await money(f) if "money" in want else (None, None)

        def union(session_ids) -> dict:
            return _lists([classes[sid] for sid in session_ids if sid in classes])

        selected = [_s(r.get("session_id")) for r in facts]
        worst = None
        buckets: dict[str, list[str]] = {b: [] for b in _BUCKETS}
        for row in facts:
            sid = _s(row.get("session_id"))
            calls = _i(row.get("api_call_count"))
            ratio = (_i(row.get("cache_read_tokens")) / calls) if calls else 0.0
            if calls > 0 and (
                worst is None or ratio > worst["ratio"] or (ratio == worst["ratio"] and sid < worst["session_id"])
            ):
                worst = {
                    "part": "worst",
                    "ratio": ratio,
                    "session_id": sid,
                    "session_title": row.get("session_title"),
                    "prompt": row.get("prompt"),
                    "repository": row.get("repository"),
                    "branch": row.get("branch"),
                }
            buckets[depth_bucket(turns.get(sid, 0))].append(sid)
        selected_files = {sid: r for sid, r in files.items() if sid in set(selected)}
        code_ids = {sid for sid, r in selected_files.items() if _i(r.get("files_changed"))}
        code_ids |= {_s(r.get("session_id")) for r in lines if _i(r.get("lines_added")) or _i(r.get("lines_removed"))}
        rows: list[dict] = []
        if "money" in want:
            dead = [
                sid
                for sid, cost in spend.by_session.items()
                if cost > 0 and not _i(files.get(sid, {}).get("files_changed"))
            ]
            rows += [{"part": "money", **row} for row in total_rows(spend, priced)]
            rows.append(
                {"part": "dead", "n": len(dead), "cost_usd": sum(spend.by_session[sid] for sid in dead), **union(dead)}
            )
        if "facts" not in want:
            return rows
        rows += [worst] if worst else []
        rows += [{"part": "depth", "key": b, "n": len(ids), **union(ids)} for b, ids in buckets.items() if ids]
        rows.append({"part": "all", "n": len(selected), **union(selected)})
        rows.append(
            {
                "part": "code",
                "files_changed": sum(_i(r.get("files_changed")) for r in selected_files.values()),
                "files_written": sum(_i(r.get("files_written")) for r in selected_files.values()),
                "files_edited": sum(_i(r.get("files_edited")) for r in selected_files.values()),
                "lines_added": totals.get("lines_added"),
                "lines_removed": totals.get("lines_removed"),
                **union(code_ids),
            }
        )
        return rows

    def only(parts):
        return lambda rows: [r for r in rows if r["part"] in parts]

    async def overview_facts(f):
        return await overview_all(f, {"facts"})

    async def overview_money(f, rates=()):
        return await overview_all(f, {"money"})

    async def efficiency_facts(f):
        return await efficiency_all(f, {"facts"})

    async def efficiency_money(f, rates=()):
        return await efficiency_all(f, {"money"})

    async def repository_ranking(f, include_branches=False, search=None, rates=(), page=0, per_page=None):
        rows = await repository_all(f, include_branches, search, rates, page, per_page)
        ranked = ("key", "n", "project_name", "repository", "branch", "cost_usd")
        return [
            {"part": "group", **{k: r.get(k) for k in ranked}} if r["part"] == "group" else r
            for r in rows
            if include_branches or r["part"] != "bucket"  # without branches the page's estimates are their own read
        ]

    async def repository_buckets(f, search=None, rates=(), repositories=(), unattributed=False):
        rows = await repository_all(f, False, search, rates, 0, None, spend=False)
        named = set(repositories)
        return [
            r
            for r in rows
            if r["part"] == "bucket" and (r["repository"] in named or (unattributed and r["repository"] == ""))
        ]

    async def repository_facts(f, include_branches=False, search=None, repositories=(), unattributed=False):
        rows = await repository_all(f, include_branches, search, (), 0, None, priced=False)
        keep = [
            r
            for r in rows
            if r["part"] == "group"
            and (include_branches or r["repository"] in set(repositories) or (unattributed and r["repository"] is None))
        ]
        return [
            {
                **{k: v for k, v in r.items() if k not in ("key", "cost_usd")},
                **({} if include_branches else {"project_name": None}),
            }
            for r in keep
        ]

    async def overview_models(f):
        by_model = {_s(r.get("key")): r for r in await repo.get_class_lists(f, "model")}
        return [
            {
                "part": "model",
                "key": _s(r.get("model_name")),
                "n": _i(r.get("session_count")),
                **_lists([by_model.get(_s(r.get("model_name")), {})]),
            }
            for r in await repo.get_model_breakdown(f)
        ]

    async def overview_lines(f):
        by_day = {_s(r.get("key")): r for r in await repo.get_class_lists(f, "day")}
        total = (await repo.get_lines_totals(f) or [{}])[0]
        days = [
            {
                "part": "day",
                "key": r["day"].isoformat() if hasattr(r["day"], "isoformat") else _s(r["day"]),
                "net_lines": r.get("net_lines"),
                "nonzero": r.get("nonzero"),
                **_lists([by_day.get(r["day"].isoformat() if hasattr(r["day"], "isoformat") else _s(r["day"]), {})]),
            }
            for r in await repo.get_lines_daily(f)
        ]
        return [
            {"part": "total", "lines_added": total.get("lines_added"), "lines_removed": total.get("lines_removed")},
            *days,
        ]

    async def dashboard_kpis(f):
        return await repo.get_cost_kpis(f)

    repo.get_dashboard_kpis = AsyncMock(side_effect=dashboard_kpis)
    repo.get_overview_models = AsyncMock(side_effect=overview_models)
    repo.get_overview_lines = AsyncMock(side_effect=overview_lines)
    repo.get_overview_facts = AsyncMock(side_effect=overview_facts)
    repo.get_overview_money = AsyncMock(side_effect=overview_money)
    repo.get_repository_ranking = AsyncMock(side_effect=repository_ranking)
    repo.get_repository_facts = AsyncMock(side_effect=repository_facts)
    repo.get_repository_buckets = AsyncMock(side_effect=repository_buckets)
    repo.get_efficiency_facts = AsyncMock(side_effect=efficiency_facts)
    repo.get_efficiency_money = AsyncMock(side_effect=efficiency_money)
    return repo
