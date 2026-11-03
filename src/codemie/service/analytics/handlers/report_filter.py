# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""The dashboard's users/projects filter as the spend port's `ReportFilter`, resolved once per request.

Two whole-window surfaces - the summary cards and the weekly chart - accept the same `users` and
`projects` request filter their Elasticsearch query narrows on, and both now read money through the
port. The port's filter needs more than the identifiers the screen collected: a person is not one
identifier on the rows the fallback parser attributed, so each user term carries the alias set the
identity bridge resolves for them. Resolved here, in one place and one round trip, so the two surfaces
cannot come to filter on different people for the same request.
"""

from __future__ import annotations

from collections.abc import Sequence

from codemie.clients.postgres import get_async_session
from codemie.configs import logger
from codemie.repository.application_repository import application_repository
from codemie.repository.user_repository import user_repository
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import SPEND_LEDGER_READ_FAILURES_METRIC
from codemie.service.spend.identity_bridge import aliases_for_user
from codemie.service.spend.ledger import ReportFilter, UserTerm


async def report_filter_for(users: Sequence[str] | None, projects: Sequence[str] | None) -> ReportFilter:
    """Build the port's filter from the request's identifiers, keeping every identifier a term.

    **An identifier matching no user record stays in the filter as a term with no alias set.** It is
    still what the dashboard asked about - the Elasticsearch query narrows on the very same
    identifier - so dropping it would widen the money to everyone while the rows beside it stayed
    narrowed. `aliases=None` is the state the port labels as a bridge hole, which is the honest
    answer: that person's fallback rows cannot be identified, and the figure says so rather than
    quietly reading only the rows keyed on the id.

    One bulk lookup for the whole set, as the users table's own enricher does, rather than one
    resolution per identifier: the record is what the alias set is built from, and a dashboard
    naming twenty people would otherwise pay twenty queries for facts one query returns.

    **A lookup that fails degrades the figure and never the request.** The dashboards this feeds
    needed only Elasticsearch before the port; a database outage during this one round trip must
    not turn them into an error. Every identifier is then a term with no alias set - the state the
    ledger labels as a bridge hole - and the failure is logged and counted, exactly as a failed
    ledger read is.

    **Projects cost one lookup too: which of them are personal, and whose.** A personal project's
    money is matched through its owner (`ReportFilter.personal_project_owners`), and a failed lookup
    degrades the same way - the owners are unknown, and the ledger labels the figure.
    """
    identifiers = list(dict.fromkeys(identifier for identifier in users or () if identifier))
    names = tuple(dict.fromkeys(project for project in projects or () if project))
    terms: tuple[UserTerm, ...] = ()
    if identifiers:
        records = await _records_for(identifiers)
        terms = tuple(_user_term(identifier, records.get(identifier)) for identifier in identifiers)
    owners = await _personal_project_owners(names) if names else ()
    return ReportFilter(users=terms, projects=names, personal_project_owners=owners)


async def _personal_project_owners(projects: tuple[str, ...]) -> tuple[str, ...] | None:
    """The `users.id`s owning the personal projects among `projects` - `None` when nobody can say.

    A personal project's calls reach the ledger with no project at all, so the port can match them only
    through its owner (see `ReportFilter`). `None` - a failed lookup, or a personal project with no
    recorded owner - is the state the port labels rather than reads as complete.
    """
    try:
        async with get_async_session() as session:
            owners = await application_repository.aget_personal_project_owners(session, list(projects))
    except Exception as error:
        _count_lookup_failure(
            f"the personal-project lookup for {len(projects)} project(s) failed; each is matched by its name "
            f"alone and the figure is labelled: {error}"
        )
        return None
    return None if None in owners else tuple(sorted(owners))


async def _records_for(identifiers: list[str]) -> dict:
    try:
        async with get_async_session() as session:
            return await user_repository.afind_users_by_identifiers(session, set(identifiers))
    except Exception as error:
        _count_lookup_failure(
            f"the identity lookup for {len(identifiers)} user id(s) failed; every term is filtered on its id "
            f"alone and the figure is labelled: {error}"
        )
        return {}


def _count_lookup_failure(message: str) -> None:
    logger.warning(f"report_filter: {message}", exc_info=True)
    # A fresh dict per call: the helper mutates the attributes it is handed.
    BaseMonitoringService.send_count_metric(
        name=SPEND_LEDGER_READ_FAILURES_METRIC, attributes={"ledger": "identity_bridge", "read": "report_filter"}
    )


def _user_term(identifier: str, record) -> UserTerm:
    if record is None:
        return UserTerm(user_id=identifier, aliases=None)
    return UserTerm(user_id=record.id, aliases=aliases_for_user(record.username, record.email))
