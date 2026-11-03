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

"""The two budget limit dashboards, computed from the ledger against the configured limits.

Both tables used to be read from Elasticsearch. LiteLLM's own spend counter was copied into a
`BUDGET_SOFT_LIMIT_WARNING` / `BUDGET_HARD_LIMIT_VIOLATION` metric document at enforcement time, and
this handler aggregated `max(attributes.spent)` over those documents. Three facts retire that source
rather than let it be repointed: that counter is an enforcement input and never a reported figure
(AD-10); the emissions are retired together with the threshold check that produced them, and this
handler was their only reader; and a document existed only for a limit crossed *while* the emitter
happened to be running, so the table could never answer about a past period.

So the question is answered the way every other spend report is (AD-1, AD-14): the configured limits
come from the budget rows, the money comes from the spend port, and *which holders reached a limit in
this period* is computed at query time. Computed from the ledger it is exact, historical and
reconcilable; the endpoints and their response shape are unchanged.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import get_async_session
from codemie.repository.budget_repository import budget_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User
from codemie.service.analytics.access_filter import AccessFilter
from codemie.service.analytics.query_pipeline import AnalyticsQueryFilters, tabular_filters_applied
from codemie.service.analytics.response_formatter import ResponseFormatter
from codemie.service.analytics.time_parser import TimeParser
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.budget_models import Budget
from codemie.service.spend.identity_bridge import aliases_for_user
from codemie.service.spend.ledger import PersonalHolder
from codemie.service.spend.rendering import (
    RenderedSpend,
    apply_spend_cells,
    render_spend,
    spend_footer,
    time_range_or_none,
)
from codemie.service.spend.spend_service import spend_service

if TYPE_CHECKING:
    from codemie.repository.user_repository import _UserFields

logger = logging.getLogger(__name__)

#: The money column both dashboards carry.
MONEY_COLUMN = "max_spent"


def _footer_annotation_only(footer: dict[str, object]) -> dict[str, object]:
    """The footer's qualification without its sum.

    Separate from `spend_footer` rather than a flag on it: every other tabular surface *wants* the
    money total, and a parameter would invite a caller to drop it for convenience. Here it is dropped
    because the column is excluded from totals platform-wide, and that reason does not generalise.
    """
    return {key: value for key, value in footer.items() if key != MONEY_COLUMN}


#: The same candidate cap `AnalyticsQueryPipeline.execute_tabular_query` applies to a terms
#: aggregation, kept so neither table can grow unbounded when every user has a budget.
CANDIDATE_LIMIT = 10_000

#: Built from the enum so an unrecognised stored category cannot reach a holder.
_CATEGORY_BY_VALUE = {category.value: category for category in BudgetCategory}


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One holder that has a limit: whose spend to read, what to compare it to, what the row shows."""

    holder: PersonalHolder
    limit: float
    user_email: str


def _stayed_under(rendered: RenderedSpend, limit: float) -> bool:
    """True only where the ledger *proves* the holder stayed under the limit.

    A holder is listed unless this returns True, which is AD-33's direction-of-resolution rule:
    resolve the unknowable in the direction that surfaces it rather than the direction that hides
    it. The three cases are the whole of the rule:

    - **no figure** — the comparison cannot be made at all. Excluding the holder would assert they
      did not breach, and listing them with a number would assert they did; so they are listed with
      no number and the `spend_unavailable` annotation set.
    - **figure at or over the limit** — listed. A qualified figure only ever understates, so this
      branch is certain either way.
    - **figure under the limit but qualified** (`unavailable` set, or `partial` non-empty) — the
      figure is a *floor*, and the missing portion could carry it over the limit. Undetermined, so
      listed with its number and its annotation.

    Do not "simplify" this to `figure is None or figure >= limit`: that silently drops every
    partially-resolved holder, and partial is the expected state for a window reaching below the
    explicit-identity floor rather than an edge case.
    """
    return rendered.is_exact and rendered.figure < limit


def _row_identity(user_id: str, record: _UserFields | None) -> str:
    """What the `user_email` cell shows: the email, else the username, else the raw id.

    The Elasticsearch bucket key was a raw user id and a resolver turned it into an email in a
    second pass. The user record is already in hand here, so the cell is filled directly. The
    fallbacks keep a holder on the table instead of blanking the only column that identifies them.
    """
    if record is None:
        return user_id
    return record.email or record.username or user_id


class BudgetHandler:
    """Handler for budget analytics."""

    def __init__(self, user: User):
        """Initialize budget handler. The user decides what the caller is allowed to see."""
        self._user = user

    async def get_budget_soft_limit(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get the personal budget holders whose spend in the window reached their soft limit."""
        return await self._holders_at_limit(
            limit_of=lambda budget: budget.soft_budget,
            limit_name="soft",
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def get_budget_hard_limit(
        self,
        time_period: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        users: list[str] | None = None,
        projects: list[str] | None = None,
        page: int = 0,
        per_page: int = 20,
    ) -> dict:
        """Get the personal budget holders whose spend in the window reached their hard limit."""
        return await self._holders_at_limit(
            limit_of=lambda budget: budget.max_budget,
            limit_name="hard",
            time_period=time_period,
            start_date=start_date,
            end_date=end_date,
            users=users,
            projects=projects,
            page=page,
            per_page=per_page,
        )

    async def _holders_at_limit(
        self,
        *,
        limit_of: Callable[[Budget], float],
        limit_name: str,
        time_period: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
        users: list[str] | None,
        projects: list[str] | None,
        page: int,
        per_page: int,
    ) -> dict:
        """Both dashboards. They differ only in which limit column they compare the spend against.

        The period is the **requested analytics window**, parsed exactly as the Elasticsearch
        pipeline parses it: the question is "who reached a limit in this period", and the window is
        the only period the endpoint receives. A current-budget-period figure would leave the four
        window parameters dead and could not answer about the past.
        """
        started = time.time()
        logger.info(f"Requesting budget-{limit_name}-limit analytics")

        start_dt, end_dt = TimeParser.parse(time_period, start_date, end_date)
        rows, rendered = await self._rows_at_limit(limit_of, limit_name, start_dt, end_dt, users, projects)

        total_count = len(rows)
        offset = page * per_page
        response = ResponseFormatter.format_tabular_response(
            rows=rows[offset : offset + per_page],
            columns=self._get_budget_limit_columns(),
            filters_applied=tabular_filters_applied(
                AnalyticsQueryFilters(time_period=time_period, users=users, projects=projects), start_dt, end_dt
            ),
            execution_time_ms=(time.time() - started) * 1000,
            # The footer carries the annotation triple and **no money key**, which is not an
            # oversight. `TotalsCalculator.EXCLUSION_PATTERNS` matches `^max_.*` ("already an
            # aggregate"), so this table's totals were `{}` before the rewire and adding a sum here
            # would be a gained key on a response whose shape the contract keeps. The triple still
            # belongs: on a *who reached their limit* list it says the list itself may be short,
            # because a holder whose figure is unavailable might belong on it.
            totals=_footer_annotation_only(spend_footer(MONEY_COLUMN, rendered)),
        )
        response["pagination"] = {
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "has_more": (page + 1) * per_page < total_count,
        }
        return response

    async def _rows_at_limit(
        self,
        limit_of: Callable[[Budget], float],
        limit_name: str,
        start_dt: datetime,
        end_dt: datetime,
        users: list[str] | None,
        projects: list[str] | None,
    ) -> tuple[list[dict], list[RenderedSpend]]:
        """Every holder the ledger cannot prove stayed under its limit, ordered, unsliced.

        Returns the rows and their rendered figures in step, because the footer qualifies the same
        figures the rows carry and re-deriving them from the cells would lose the annotations.
        """
        if not self._may_read_every_holder(limit_name):
            return [], []
        if projects:
            # A personal budget has no project to scope by, so a project filter selects no holder.
            # The Elasticsearch source behaved identically — the limit metric documents carried no
            # project attribute — and reading the filter as "users who are members of these
            # projects" would invent a meaning the endpoint never had.
            logger.info(
                f"budget-{limit_name}-limit: personal budgets carry no project, so the projects "
                f"filter {projects} lists no holders"
            )
            return [], []

        async with get_async_session() as session:
            candidates = await self._candidates(session, limit_of, limit_name, users)

        # One window for every holder here, so one batched read; the port splits it into holder
        # batches itself. No window means no period the port can answer about, and every figure is
        # then absent rather than a fabricated zero.
        window = time_range_or_none(start_dt, end_dt)
        consumed = (
            await spend_service.consumed_for_holders([c.holder for c in candidates], window)
            if window is not None and candidates
            else {}
        )

        kept: list[tuple[dict, RenderedSpend]] = []
        for candidate in candidates:
            spend = render_spend(consumed.get(candidate.holder))
            if _stayed_under(spend, candidate.limit):
                continue
            kept.append(({"user_email": candidate.user_email}, spend))

        # The ledger figure decides the order and an absent one sorts last rather than as a zero:
        # the first key separates the two outright, so no absent row is ranked among the figures.
        kept.sort(key=lambda pair: (pair[1].figure is not None, pair[1].figure or 0.0), reverse=True)

        rows = [row for row, _ in kept]
        rendered = [spend for _, spend in kept]
        apply_spend_cells(rows, rendered, MONEY_COLUMN)
        logger.debug(f"budget-{limit_name}-limit: candidates={len(candidates)} listed={len(rows)}")
        return rows, rendered

    def _may_read_every_holder(self, limit_name: str) -> bool:
        """Whether this caller may see the whole tenant's budget holders.

        The Elasticsearch source was scoped by `SecureQueryBuilder`, which injects a project filter
        for every caller except an admin or an auditor. The limit metric documents carried no
        project attribute, so that filter matched nothing and a non-admin, non-auditor caller saw an
        empty table. A personal budget has no project to scope by either, so the boundary stays
        exactly where it was instead of being widened by the move to the database: admins and
        auditors see every holder, everyone else sees none.

        `AccessFilter` decides rather than `user.is_admin`, so who counts as unrestricted cannot
        drift between this table and every Elasticsearch-backed one — an auditor counts.
        """
        if AccessFilter(self._user).get_project_access_context().is_admin:
            return True
        logger.info(
            f"budget-{limit_name}-limit: caller has no unrestricted analytics access, so no budget "
            f"holders are listed (user_id={self._user.id})"
        )
        return False

    async def _candidates(
        self,
        session: AsyncSession,
        limit_of: Callable[[Budget], float],
        limit_name: str,
        users: list[str] | None,
    ) -> list[_Candidate]:
        """The holders that have a limit, each with its limit and the name its row shows.

        `user_budget_assignments` *is* the candidate set: one row per (user, category) that has a
        budget, which is far smaller than every user times every category.
        """
        user_ids = await self._requested_user_ids(session, users, limit_name)
        if user_ids is not None and not user_ids:
            return []

        assignments = await budget_repository.list_personal_assignments_with_budgets(
            session, user_ids=user_ids, limit=CANDIDATE_LIMIT
        )
        if len(assignments) == CANDIDATE_LIMIT:
            # A silent cap reads downstream as "this covered everything".
            logger.warning(
                f"budget-{limit_name}-limit: candidate holders truncated at the {CANDIDATE_LIMIT} cap, "
                f"so holders beyond it are absent from this table"
            )

        records = await user_repository.afind_users_by_identifiers(
            session, {assignment.user_id for assignment, _ in assignments}
        )

        candidates: list[_Candidate] = []
        for assignment, budget in assignments:
            category = _CATEGORY_BY_VALUE.get(assignment.category)
            if category is None:
                logger.warning(
                    f"budget-{limit_name}-limit: skipping assignment with unrecognised category "
                    f"{assignment.category!r} for user_id={assignment.user_id}"
                )
                continue
            record = records.get(assignment.user_id)
            candidates.append(
                _Candidate(
                    holder=PersonalHolder(
                        user_id=assignment.user_id,
                        category=category,
                        # `None`, never an empty set: an unresolved record means the person's
                        # fallback rows cannot be *identified*, which the port reports as a portion
                        # whose amount is not computable. An empty set would claim they have no
                        # further identifiers.
                        aliases=aliases_for_user(record.username, record.email) if record else None,
                    ),
                    limit=limit_of(budget),
                    user_email=_row_identity(assignment.user_id, record),
                )
            )
        return candidates

    async def _requested_user_ids(
        self,
        session: AsyncSession,
        users: list[str] | None,
        limit_name: str,
    ) -> list[str] | None:
        """The user ids the `users` filter admits, or None when there is no filter.

        The filter arrives as whatever the screen collected — an id, a username, a display name or
        an email — so it is resolved the same way every other analytics filter is. An identifier
        matching no user is simply absent from the mapping and narrows the candidate set rather than
        widening it.
        """
        if not users:
            return None
        found = await user_repository.afind_users_by_identifiers(session, set(users))
        unresolved = sorted(set(users) - set(found))
        if unresolved:
            logger.info(f"budget-{limit_name}-limit: no user matches {unresolved}, so they list no holders")
        return sorted({record.id for record in found.values()})

    def _get_budget_limit_columns(self) -> list[dict]:
        """Get column definitions for budget limit (used by both soft and hard limit)."""
        return [
            {"id": "user_email", "label": "User", "type": "string"},
            {"id": MONEY_COLUMN, "label": "Max Spent ($)", "type": "number", "format": "currency"},
        ]
