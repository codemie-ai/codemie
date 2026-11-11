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

**Each row is asked as one filter, and the whole table in one batched read.** A row is the port's
`aggregate_by_spend_group` for a filter naming that row's person (or project) under the caller's
boundary, and `aggregate_by_spend_group_per_filter` answers every such filter of a table at once, so
"which ledger rows belong to whom" stays the port's one attribution predicate. The alternatives are each
wrong for a CLI or platform column: `PersonalHolder(user, CLI)` is what the person's own customer was
charged and drops every call made against a project budget, `PersonTotalHolder` has no category split,
and the composed reads take no boundary - so a project admin would be shown a member's spend from
projects they cannot see, beside rows Elasticsearch had already narrowed to what they can.

**The caller's boundary and the dashboard's own filter both reach every row.** A per-user filter
replaces the dashboard's users term - the row is one of those users already - and keeps its projects
term; a per-project filter replaces the projects term and keeps the users term. Dropping the surviving
term would un-filter a filtered dashboard on exactly the rows it was narrowed to. That surviving term is
the table's common filter, which the batch narrows its one scan by.

**A row with no identity gets no figure, never an unfiltered read.** A filter naming nobody means
*everything* to the port, so a row whose person or project cannot be named is answered without asking.

**A failed batch costs every row its figure, never the table.** One statement answers the whole table,
so a ledger that fails - a `SPEND_LEDGER_STATEMENT_TIMEOUT_MS` timeout on the dedicated pool
(`SPEND_LEDGER_POOL_SIZE` > 0) among the causes - renders each of its rows as no figure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import NamedTuple

from codemie.clients.postgres import get_async_session
from codemie.configs import logger
from codemie.repository.application_repository import application_repository
from codemie.service.analytics.handlers.report_filter import report_filter_for
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import AccessScope, ReportFilter
from codemie.service.spend.rendering import RenderedSpend, render_spend
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.spend_service import spend_service


class TableFilters(NamedTuple):
    """A table's per-row filters, in row order, and the dashboard term every one of them carries."""

    common: ReportFilter
    rows: list[ReportFilter | None]

    @classmethod
    def naming_nobody(cls, row_count: int) -> TableFilters:
        return cls(ReportFilter(), [None] * row_count)


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


async def user_report_filters(identifiers: Sequence[str], projects: Sequence[str] | None) -> TableFilters:
    """One filter per identifier, in order: that person, inside the dashboard's projects filter.

    `None` for an empty identifier, which names nobody. An identifier matching no user record is still
    a term, filtered on the id alone and labelled by the port as a bridge hole.
    """
    named = list(dict.fromkeys(identifier for identifier in identifiers if identifier))
    if not named:
        return TableFilters.naming_nobody(len(identifiers))
    base = await report_filter_for(named, projects)
    term_by_identifier = dict(zip(named, base.users, strict=True))
    return TableFilters(
        common=replace(base, users=()),
        rows=[
            replace(base, users=(term_by_identifier[identifier],)) if identifier in term_by_identifier else None
            for identifier in identifiers
        ],
    )


async def project_report_filters(names: Sequence[str], users: Sequence[str] | None) -> TableFilters:
    """One filter per project name, in order: that project, inside the dashboard's users filter.

    A personal project's calls reach the ledger with no project at all, so each filter also carries that
    project's own owners - the port matches such a project through them. `None` for an empty name.
    """
    if not any(names):
        return TableFilters.naming_nobody(len(names))
    return await project_filters_within(await report_filter_for(users, None), names)


async def project_filters_within(base: ReportFilter, names: Sequence[str]) -> TableFilters:
    """`project_report_filters` over a filter the caller already built, so one request filters on one set of people.

    `base` keeps its users term and loses its projects term: each row is one of the dashboard's projects
    already. `None` for an empty name - a filter naming no project means every project to the port.
    """
    named = list(dict.fromkeys(name for name in names if name))
    if not named:
        return TableFilters.naming_nobody(len(names))
    owners = await _personal_owners_by_name(named)
    return TableFilters(
        common=replace(base, projects=(), personal_project_owners=()),
        rows=[
            replace(base, projects=(name,), personal_project_owners=owners[name]) if name in owners else None
            for name in names
        ],
    )


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
    common_filter: ReportFilter,
    report_filters: Sequence[ReportFilter | None],
    group: SpendGroup,
) -> list[RenderedSpend]:
    """The group's figure for each filter, in order, from one batched read - no figure for an unreadable one.

    **Every filter must carry `common_filter`'s terms unchanged**, because the batch narrows its one scan
    by them: a row filtered on anything else would be answered from rows its own filter never reached,
    and read short without a label. Such a filter is a caller defect and raises before anything is read.

    A ledger that fails is absorbed by the port as an unavailable figure for every filter it was asked,
    so a failed batch costs every row its figure and never the table. One cause is the batch's single
    statement exceeding `SPEND_LEDGER_STATEMENT_TIMEOUT_MS`, which applies only on the dedicated pool
    (`SPEND_LEDGER_POOL_SIZE` > 0).
    """
    named = list(dict.fromkeys(report_filter for report_filter in report_filters if report_filter is not None))
    if period is None or not named:
        return [render_spend(None) for _ in report_filters]
    stray = [report_filter for report_filter in named if not _carries_common_term(report_filter, common_filter)]
    if stray:
        raise ValueError(f"{len(stray)} row filter(s) do not carry the table's common filter")
    answers = await spend_service.aggregate_by_spend_group_per_filter(
        period, access=access, common_filter=common_filter, report_filters=named
    )
    return [
        render_spend(None) if report_filter is None else render_spend(answers[report_filter][group])
        for report_filter in report_filters
    ]


def _carries_common_term(report_filter: ReportFilter, common_filter: ReportFilter) -> bool:
    carries_users = not common_filter.users or report_filter.users == common_filter.users
    carries_projects = not common_filter.projects or (
        report_filter.projects == common_filter.projects
        and report_filter.personal_project_owners == common_filter.personal_project_owners
    )
    return carries_users and carries_projects
