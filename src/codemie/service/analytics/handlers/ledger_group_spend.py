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

"""One spend group's ledger figure per user or per project, for the tables that used to sum `money_spent`.

**The port has no batched per-person (or per-project) group read, and this is how the gap is closed
without a second attribution predicate.** `aggregate_by_spend_group` answers one filter's scope in one
scan and takes both the caller's boundary and a `ReportFilter`, so a table row is answered by asking it
once with that row's person (or project) as the filter. The alternatives are each wrong for a CLI or
platform column: `PersonalHolder(user, CLI)` is what the person's own customer was charged and drops
every call made against a project budget, `PersonTotalHolder` has no category split, and the composed
reads take no boundary - so a project admin would be shown a member's spend from projects they cannot
see, beside rows Elasticsearch had already narrowed to what they can.

**The caller's boundary and the dashboard's own filter both reach every read.** A per-user filter
replaces the dashboard's users term - the row is one of those users already - and keeps its projects
term; a per-project filter replaces the projects term and keeps the users term. Dropping the surviving
term would un-filter a filtered dashboard on exactly the rows it was narrowed to.

**A row with no identity gets no figure, never an unfiltered read.** A filter naming nobody means
*everything* to the port, so a row whose person or project cannot be named is answered without asking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import replace

from codemie.clients.postgres import get_async_session
from codemie.configs import logger
from codemie.repository.application_repository import application_repository
from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import AccessScope, ReportFilter
from codemie.service.spend.rendering import RenderedSpend, render_spend
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.spend_service import spend_service

#: How many ledger scans one table keeps in flight. Each scan is a single statement on its own session,
#: so this bounds the connections a wide table (every CLI user of a tenant) can take from the pool.
READ_CONCURRENCY = 8


async def dashboard_group_spend(
    period: TimeRange | None,
    access: AccessScope,
    users: Sequence[str] | None,
    projects: Sequence[str] | None,
    group: SpendGroup,
) -> RenderedSpend:
    """One group's figure for the whole dashboard scope - the read a summary card makes."""
    if period is None:
        return render_spend(None)
    report_filter = await report_filter_for(users, projects)
    groups = await spend_service.aggregate_by_spend_group(period, access=access, report_filter=report_filter)
    return render_spend(groups[group])


async def user_report_filters(identifiers: Sequence[str], projects: Sequence[str] | None) -> list[ReportFilter | None]:
    """One filter per identifier, in order: that person, inside the dashboard's projects filter.

    `None` for an empty identifier, which names nobody. An identifier matching no user record is still
    a term, filtered on the id alone and labelled by the port as a bridge hole.
    """
    named = list(dict.fromkeys(identifier for identifier in identifiers if identifier))
    if not named:
        return [None for _ in identifiers]
    base = await report_filter_for(named, projects)
    term_by_identifier = dict(zip(named, base.users, strict=True))
    return [
        replace(base, users=(term_by_identifier[identifier],)) if identifier in term_by_identifier else None
        for identifier in identifiers
    ]


async def project_report_filters(names: Sequence[str], users: Sequence[str] | None) -> list[ReportFilter | None]:
    """One filter per project name, in order: that project, inside the dashboard's users filter.

    A personal project's calls reach the ledger with no project at all, so each filter also carries that
    project's own owners - the port matches such a project through them. `None` for an empty name.
    """
    if not any(names):
        return [None for _ in names]
    return await project_filters_within(await report_filter_for(users, None), names)


async def project_filters_within(base: ReportFilter, names: Sequence[str]) -> list[ReportFilter | None]:
    """`project_report_filters` over a filter the caller already built, so one request filters on one set of people.

    `base` keeps its users term and loses its projects term: each row is one of the dashboard's projects
    already. `None` for an empty name - a filter naming no project means every project to the port.
    """
    named = list(dict.fromkeys(name for name in names if name))
    if not named:
        return [None for _ in names]
    owners = await _personal_owners_by_name(named)
    return [
        replace(base, projects=(name,), personal_project_owners=owners[name]) if name in owners else None
        for name in names
    ]


async def _personal_owners_by_name(names: list[str]) -> dict[str, tuple[str, ...] | None]:
    """Each project's personal-project owners: empty when it is not personal, `None` when nobody can say.

    `None` is the port's own convention for a personal project with no recorded owner and for a failed
    lookup, and the ledger labels the figure rather than reading the name alone as complete.
    """
    try:
        async with get_async_session() as session:
            found = await application_repository.aget_personal_project_owners_by_name(session, names)
    except Exception as error:
        logger.warning(
            f"ledger_group_spend: the personal-project lookup for {len(names)} project(s) failed; each is matched "
            f"by its name alone and the figure is labelled: {error}",
            exc_info=True,
        )
        return {name: None for name in names}
    return {name: _owners(found.get(name)) for name in names}


def _owners(found: set[str | None] | None) -> tuple[str, ...] | None:
    if found is None:
        return ()
    return None if None in found else tuple(sorted(owner for owner in found if owner is not None))


async def read_group_spend(
    period: TimeRange | None,
    access: AccessScope,
    report_filters: Sequence[ReportFilter | None],
    group: SpendGroup,
) -> list[RenderedSpend]:
    """The group's figure for each filter, in order - an unreadable filter or window is no figure.

    A ledger that fails is already absorbed by the port as an unavailable figure, so one bad read
    costs its row's figure and never the table.
    """
    gate = asyncio.Semaphore(READ_CONCURRENCY)

    async def read(report_filter: ReportFilter | None) -> RenderedSpend:
        if period is None or report_filter is None:
            return render_spend(None)
        async with gate:
            groups = await spend_service.aggregate_by_spend_group(period, access=access, report_filter=report_filter)
        return render_spend(groups[group])

    return list(await asyncio.gather(*(read(report_filter) for report_filter in report_filters)))
