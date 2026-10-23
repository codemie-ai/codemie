# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""App DB facts of the analysis: shared projects, budgets, Git datasources, members, people profiles, and the dated
platform budget funding of shared projects (funding.py).

Read-only queries. The worker reads them synchronously in its own process; the API process reads the small subset it
needs for the fast endpoints (shared projects and the funding) asynchronously and keeps it for a few minutes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import text

from codemie.service.usage_attribution import funding
from codemie.service.usage_attribution.identity import normalize_link

logger = logging.getLogger(__name__)

_SQL_PROJECTS = text(
    """
    select a.name, a.deleted_at is not null, coalesce(a.chargeback_enabled, false),
           coalesce(a.chargeback_attribution, ''), coalesce(cc.name, '')
    from applications a left join cost_centers cc on cc.id = a.cost_center_id
    where a.project_type = 'shared'
    """
)
_SQL_BUDGETS = text(
    """
    select pba.project_name, pba.budget_category, coalesce(pba.allocation_mode, ''), pba.assigned_at,
           coalesce(b.max_budget, 0)
    from project_budget_assignments pba
    join budgets b on b.budget_id = pba.budget_id and b.deleted_at is null and b.is_active
    where pba.deleted_at is null
    """
)


def datasource_rows(rows: list) -> list[list[str]]:
    """(project, link) rows as the analysis keeps them: the project and the normalized owner/repo (or _git/<repo>)
    only, since links may carry credentials and the inputs are cached. The pipeline uses these keys as they are."""
    return [[r[0], key] for r in rows if (key := normalize_link(r[1]))]


_SQL_DATASOURCES = text(
    """
    select a.name, r.link from repositories r join applications a on a.id = r.app_id
    where a.deleted_at is null and a.project_type = 'shared' and coalesce(r.link, '') <> ''
    union all
    select i.project_name, i.link from index_info i join applications a on a.name = i.project_name
    where a.deleted_at is null and a.project_type = 'shared' and i.index_type = 'code'
      and coalesce(i.link, '') <> ''
    """
)
_SQL_MEMBERS = text(
    """
    select up.user_id, up.project_name from user_projects up
    join applications a on a.name = up.project_name
    where a.project_type = 'shared' and a.deleted_at is null
    """
)
_SQL_PEOPLE = text(
    """
    select u.id, coalesce(u.name, ''), coalesce(ue.first_name, ''), coalesce(ue.last_name, ''),
           coalesce(ue.country, ''), coalesce(ue.city, ''), coalesce(ue.job_title_group, ''),
           coalesce(ue.primary_skill, ''), coalesce(u.email, '')
    from users u left join user_enrichment ue on ue.user_id = u.id
    where u.id = any(:ids)
    """
)
# Without user enrichment (features:userEnrichmentEnabled off): no names, location or job from its table.
_SQL_PEOPLE_BASIC = text(
    """
    select u.id, coalesce(u.name, ''), '', '', '', '', '', '', coalesce(u.email, '')
    from users u
    where u.id = any(:ids)
    """
)
_SQL_SHARED_NAMES = text("select a.name from applications a where a.project_type = 'shared'")
# Web Platform funding (funding.py), with the dates of each part. History older than the longest range is not needed.
_FUNDING_HISTORY_DAYS = 100
_SQL_FUNDING_ASSIGNMENTS = text(
    """
    select pba.project_name, pba.budget_id, pba.assigned_at, pba.deleted_at, b.is_active, b.deleted_at
    from project_budget_assignments pba
    join budgets b on b.budget_id = pba.budget_id
    join applications a on a.name = pba.project_name and a.project_type = 'shared'
    where pba.budget_category = 'platform'
      and (pba.deleted_at is null or pba.deleted_at > now() - make_interval(days => :days))
    """
)
_SQL_FUNDING_ALLOCATIONS = text(
    """
    select pmba.project_name, pmba.user_id, pmba.assigned_at, pmba.pmba_deleted_at
    from project_member_budget_assignments pmba
    where pmba.budget_category = 'platform'
      and (pmba.pmba_deleted_at is null or pmba.pmba_deleted_at > now() - make_interval(days => :days))
    """
)
_SQL_FUNDING_STOPS = text(
    """
    select e.entity_id, e.event_type, e.created_at
    from activity_events e
    where e.entity_type = :entity_type and e.event_type in (:stopped, :restored)
      and e.created_at > now() - make_interval(days => :days)
    """
)

_PEOPLE_CHUNK = 5000


def _timed(session: Any, name: str, statement: Any, params: dict | None, timings: list[dict]) -> list:
    t0 = time.perf_counter()
    rows = session.execute(statement, params or {}).fetchall()
    timings.append({"query": name, "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000.0, 1)})
    return rows


def load_static(session: Any, timings: list[dict]) -> dict[str, Any]:
    """Facts that do not depend on the period."""
    projects = _timed(session, "projects", _SQL_PROJECTS, None, timings)
    budgets = _timed(session, "budgets", _SQL_BUDGETS, None, timings)
    datasources = _timed(session, "datasources", _SQL_DATASOURCES, None, timings)
    members = _timed(session, "members", _SQL_MEMBERS, None, timings)
    return {
        "shared": sorted({r[0] for r in projects}),
        "projects": [[r[0], bool(r[1]), bool(r[2]), r[3], r[4]] for r in projects],
        "budgets": [[r[0], r[1], r[2], r[3].isoformat() if r[3] else None, float(r[4] or 0), ""] for r in budgets],
        "datasources": datasource_rows(datasources),
        "members": [[r[0], r[1]] for r in members],
    }


def _funding_params() -> dict[str, Any]:
    from codemie.service.activity.activity_models import ActivityEntityType, BudgetManagementEvent

    return {
        "days": _FUNDING_HISTORY_DAYS,
        "entity_type": ActivityEntityType.BUDGET,
        "stopped": BudgetManagementEvent.PROJECT_BUDGET_STOPPED,
        "restored": BudgetManagementEvent.PROJECT_BUDGET_RESTORED,
    }


def _funding_facts(assignments: list, allocations: list, stops: list) -> dict[str, list[list]]:
    from codemie.service.activity.activity_models import BudgetManagementEvent

    kinds = {
        BudgetManagementEvent.PROJECT_BUDGET_STOPPED: funding.STOPPED,
        BudgetManagementEvent.PROJECT_BUDGET_RESTORED: funding.RESTORED,
    }
    return {
        "assignments": [[r[0], r[1], r[2], r[3], bool(r[4]), r[5]] for r in assignments],
        "allocations": [[r[0], r[1], r[2], r[3]] for r in allocations],
        "stops": [[r[0], kinds.get(r[1], ""), r[2]] for r in stops],
    }


def load_funding(session: Any, timings: list[dict]) -> dict[str, list[list]]:
    """Platform budget assignments of shared projects, member allocations and budget stops, with their dates."""
    params = _funding_params()
    return _funding_facts(
        _timed(session, "funding_assignments", _SQL_FUNDING_ASSIGNMENTS, params, timings),
        _timed(session, "funding_allocations", _SQL_FUNDING_ALLOCATIONS, params, timings),
        _timed(session, "funding_stops", _SQL_FUNDING_STOPS, params, timings),
    )


def load_people(session: Any, uids: list[str], timings: list[dict], enrichment: bool = True) -> list[list]:
    """Names and emails of the people in the period, with location and job title group from user enrichment."""
    statement = _SQL_PEOPLE if enrichment else _SQL_PEOPLE_BASIC
    rows: list[list] = []
    ordered = sorted(uids)
    t0 = time.perf_counter()
    for i in range(0, len(ordered), _PEOPLE_CHUNK):
        chunk = ordered[i : i + _PEOPLE_CHUNK]
        rows.extend(list(r) for r in session.execute(statement, {"ids": chunk}).fetchall())
    timings.append({"query": "people", "rows": len(rows), "ms": round((time.perf_counter() - t0) * 1000.0, 1)})
    return rows


# ---------------------------------------------------------------------------------------------------------------------
# API process: shared projects and platform budgets for the fast endpoints, kept for a few minutes
# ---------------------------------------------------------------------------------------------------------------------
_FACTS_TTL_S = 300.0
_facts: dict[str, Any] = {"at": 0.0, "value": None}
_facts_lock: asyncio.Lock | None = None


async def project_facts() -> dict[str, Any]:
    """``{"shared": [...], "funding": {...}}`` (funding.py), cached in-process for five minutes."""
    global _facts_lock
    now = time.monotonic()
    if _facts["value"] is not None and now - _facts["at"] < _FACTS_TTL_S:
        return _facts["value"]
    if _facts_lock is None:
        _facts_lock = asyncio.Lock()
    async with _facts_lock:
        if _facts["value"] is not None and time.monotonic() - _facts["at"] < _FACTS_TTL_S:
            return _facts["value"]
        from codemie.clients.postgres import get_async_session

        params = _funding_params()
        async with get_async_session() as session:
            shared = (await session.execute(_SQL_SHARED_NAMES)).scalars().all()
            assignments = (await session.execute(_SQL_FUNDING_ASSIGNMENTS, params)).fetchall()
            allocations = (await session.execute(_SQL_FUNDING_ALLOCATIONS, params)).fetchall()
            stops = (await session.execute(_SQL_FUNDING_STOPS, params)).fetchall()
        value = {"shared": sorted(set(shared)), "funding": _funding_facts(assignments, allocations, stops)}
        _facts.update(at=time.monotonic(), value=value)
        return value


def reset_project_facts_cache() -> None:
    _facts.update(at=0.0, value=None)
