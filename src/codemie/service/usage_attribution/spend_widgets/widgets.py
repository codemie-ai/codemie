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

"""The spend widgets: one class per entity, each with one public ``read``."""

import asyncio
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from codemie.service.analytics.handlers.ledger_group_spend import project_report_filters, user_report_filters
from codemie.service.spend.spend_service import spend_service
from codemie.service.spend.ledger import CliSpendRow
from codemie.service.usage_attribution.pipeline import Analysis
from codemie.service.usage_attribution.constants import (
    CATEGORIES,
    EXPERIMENTS,
    LIKELY_PRODUCTION,
    NO_REPOSITORY,
    OUTSIDE_LIMITS,
    PRODUCTION,
    TOO_LITTLE_EVIDENCE_REASONS,
    UNCLASSIFIED,
)
from codemie.service.usage_attribution.spend_widgets import helpers
from codemie.service.usage_attribution.spend_widgets.base import (
    CliCategorySpend,
    CliReasonSpend,
    CliUsageFigures,
    SpendFigures,
    SpendTable,
    SpendWidget,
    WidgetContext,
)
from codemie.service.usage_attribution.spend_widgets.caching import cached_read


class PlatformSpend(SpendWidget[SpendFigures]):
    """The platform's Total, Web Platform, CLI and datasource figures."""

    view = "platform"
    output = SpendFigures

    @cached_read
    async def read(self, start: datetime, end: datetime) -> SpendFigures:
        """The platform's figures for the part of ``[start, end)`` the ledger covers."""
        window = self._window(start, end)

        if window is None:
            return SpendFigures()

        answer = await spend_service.aggregate_by_spend_group(window, access=self._context.scope, report_filter=None)
        return helpers.rendered(answer)


class PeopleSpend(SpendWidget[SpendTable]):
    """Each person's figures, by user id."""

    view = "people"
    output = SpendTable

    @cached_read
    async def read(self, start: datetime, end: datetime, uids: list[str]) -> SpendTable:
        """Each person's figures by user id, matched by id or alias, for the part of ``[start, end)`` covered."""
        window = self._window(start, end)

        if window is None or not uids:
            return helpers.no_rows(uids)

        filters = await user_report_filters(list(uids), None)
        return await helpers.read_per_row(window, self._context.scope, uids, filters)


class ProjectSpend(SpendWidget[SpendTable]):
    """Each project's figures, by project name."""

    view = "projects"
    output = SpendTable

    @cached_read
    async def read(self, start: datetime, end: datetime, names: list[str]) -> SpendTable:
        """Each project's figures by name (and a personal project's owners'), for the part of the range covered."""
        window = self._window(start, end)

        if window is None or not names:
            return helpers.no_rows(names)

        filters = await project_report_filters(list(names), None)
        return await helpers.read_per_row(window, self._context.scope, names, filters)


class CliUsageSpend(SpendWidget[CliUsageFigures]):
    """CLI categories and allocation, using ledger money and the analysis's classification evidence."""

    _EMAIL_INDEX = 8

    view = "cli_usage"
    output = CliUsageFigures

    def __init__(self, context: WidgetContext, *, evidence: dict[str, Any], settings: dict[str, Any]):
        super().__init__(context)
        self._evidence = evidence
        self._settings = settings

    @cached_read
    async def read(
        self,
        start: datetime,
        end: datetime,
        evidence_key: str,  # noqa: ARG002 - consumed by cached_read
    ) -> CliUsageFigures:
        """Classified figures for the covered window, scoped and cached like every other spend widget."""
        window = self._window(start, end)
        if window is None:
            return CliUsageFigures()
        rows = await spend_service.cli_attribution_rows(window, access=self._context.scope)
        if rows is None:
            return CliUsageFigures(complete=False)
        return await asyncio.to_thread(self._classify, rows)

    def _aliases(self) -> dict[str, str]:
        aliases = {p["name"]: uid for uid, p in self._evidence["persons"].items()}
        for row in self._evidence.get("app_db", {}).get("people", []):
            for alias in (row[1], row[self._EMAIL_INDEX] if len(row) > self._EMAIL_INDEX else None):
                if alias:
                    aliases[alias] = row[0]
        return aliases

    def _money_inputs(self, rows: tuple[CliSpendRow, ...]) -> tuple[dict, dict]:
        aliases = self._aliases()
        persons: dict[str, dict] = {}
        projects: dict[str, dict] = {}
        for row in rows:
            uid = row.user_id or aliases.get(row.principal) or row.principal or "__unattributed__"
            person = persons.setdefault(
                uid,
                {
                    "name": row.principal or self._evidence["persons"].get(uid, {}).get("name", uid),
                    "spend": 0.0,
                    "cb": 0.0,
                    "sh": 0.0,
                    "nc": 0.0,
                    "pairs": defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]),
                    "projects": defaultdict(lambda: [0.0, 0.0, 0.0]),
                    "fallback": {},
                },
            )
            amount = float(row.spend)
            charged = amount if row.form == "project_scoped" else 0.0
            person["spend"] += amount
            person["cb"] += charged
            pair = person["pairs"][row.repository or ""]
            pair[0] += amount
            pair[1] += charged
            project = person["projects"][row.project or ""]
            project[0] += amount
            project[1] += charged
            if row.project and charged:
                data = projects.setdefault(
                    row.project,
                    {
                        "spend": 0.0,
                        "cb": 0.0,
                        "sh": 0.0,
                        "people": {},
                        "repos": {},
                    },
                )
                data["spend"] += amount
                data["cb"] += charged
                data["people"].setdefault(uid, [0.0, person["name"]])[0] += amount
                data["repos"].setdefault(row.repository or "", [0.0, 0.0])[0] += amount
        return persons, projects

    def _classification_inputs(self, rows: tuple[CliSpendRow, ...], persons: dict) -> dict:
        # Preserve the evidence window. Newly observed ledger repositories can still use names/branches/client.
        evidence_people = {
            uid: {**person, "pairs": dict(person["pairs"])} for uid, person in self._evidence["persons"].items()
        }
        for uid, person in persons.items():
            evidence = evidence_people.setdefault(uid, {**person, "pairs": {}})
            for value, pair in person["pairs"].items():
                evidence["pairs"].setdefault(value, pair)
        values = dict(self._evidence.get("values", {}))
        new_values: dict[str, dict] = {}
        for row in rows:
            value = row.repository or ""
            if value in values:
                continue
            evidence = new_values.setdefault(
                value,
                {
                    "spend": 0.0,
                    "people": 0,
                    "sessions": 0,
                    "personal": 0.0,
                    "branches": {},
                    "clients": {},
                    "models": {},
                    "projects": {},
                    "projects_cb": {},
                },
            )
            self._add_value_context(evidence, row)
        return {"ev_persons": evidence_people, "values": {**values, **new_values}}

    @staticmethod
    def _add_value_context(evidence: dict[str, Any], row: CliSpendRow) -> None:
        """Accumulate the context carried by ledger rows for a newly observed repository."""
        amount = float(row.spend)
        evidence["spend"] += amount
        if row.form != "project_scoped":
            evidence["personal"] += amount
        for field, key in (("branches", row.branch), ("clients", row.client), ("projects", row.project)):
            if key:
                evidence[field][key] = evidence[field].get(key, 0.0) + amount
        if row.project and row.form == "project_scoped":
            evidence["projects_cb"][row.project] = evidence["projects_cb"].get(row.project, 0.0) + amount

    def _classify(self, rows: tuple[CliSpendRow, ...]) -> CliUsageFigures:
        persons, projects = self._money_inputs(rows)
        inputs = {
            **self._evidence,
            **self._classification_inputs(rows, persons),
            "persons": persons,
            "shared_cli": projects,
        }
        return self._figures(Analysis(inputs, self._settings).run())

    @staticmethod
    def _categories(analysis: Analysis) -> tuple[CliCategorySpend, ...]:
        moved: Counter = Counter()
        for allocation in analysis.allocated.values():
            moved.update(allocation)
        rows = []
        for category in CATEGORIES:
            money = analysis.cat_money[category]
            allocated = analysis.allocated.get(category, Counter())
            if category == UNCLASSIFIED:
                allocated = Counter({key: -value for key, value in moved.items()})
            rows.append(
                CliCategorySpend(
                    category=category,
                    spend=helpers.round_cents(money["spend"] + allocated["spend"]),
                    classified=helpers.round_cents(money["spend"]),
                    allocated=helpers.round_cents(allocated["spend"]),
                    cb=helpers.round_cents(money["cb"] + allocated["cb"]),
                    sh=helpers.round_cents(money["sh"] + allocated["sh"]),
                    pe=helpers.round_cents(money["pe"] + allocated["pe"]),
                    users=len(analysis.cat_users.get(category, set()) | analysis.allocated_users.get(category, set())),
                )
            )
        return tuple(rows)

    @staticmethod
    def _reasons(analysis: Analysis) -> tuple[CliReasonSpend, ...]:
        counts = Counter(key["reason"] for key in analysis.K.values() if key["cat"] == UNCLASSIFIED)
        return tuple(
            CliReasonSpend(
                reason=reason,
                repositories=counts[reason],
                spend=helpers.round_cents(spend),
                allocated=helpers.round_cents(analysis.allocated_reason.get(reason, 0.0)),
                remaining=helpers.round_cents(max(0.0, spend - analysis.allocated_reason.get(reason, 0.0))),
            )
            for reason, spend in analysis.unclassified.most_common()
        )

    def _figures(self, analysis: Analysis) -> CliUsageFigures:
        allocated = {category: money["spend"] for category, money in analysis.allocated.items()}
        total_allocated = sum(allocated.values())
        unclassified = max(0.0, analysis.cat_money[UNCLASSIFIED]["spend"] - total_allocated)
        remaining = {
            reason: max(0.0, spend - analysis.allocated_reason.get(reason, 0.0))
            for reason, spend in analysis.unclassified.items()
        }
        likely = min(unclassified, remaining.get(LIKELY_PRODUCTION, 0.0))
        experiments = {
            category: analysis.cat_money[category]["spend"] + allocated.get(category, 0.0) for category in EXPERIMENTS
        }
        outside = analysis.unclassified.get(OUTSIDE_LIMITS, 0.0)
        total = analysis.cli["billed"]
        analysed = max(0.0, total - outside)
        return CliUsageFigures(
            total=helpers.round_cents(total),
            people=len(analysis.persons),
            production=helpers.round_cents(analysis.cat_money[PRODUCTION]["spend"] + allocated.get(PRODUCTION, 0.0)),
            production_charged=helpers.round_cents(
                analysis.cat_money[PRODUCTION]["cb"] + analysis.allocated[PRODUCTION]["cb"]
            ),
            production_allocated=helpers.round_cents(allocated.get(PRODUCTION, 0.0)),
            experiments=helpers.round_cents(sum(experiments.values())),
            experiments_allocated=helpers.round_cents(sum(allocated.get(category, 0.0) for category in EXPERIMENTS)),
            experiments_parts={category: helpers.round_cents(spend) for category, spend in experiments.items()},
            allocated=helpers.round_cents(total_allocated),
            unclassified=helpers.round_cents(unclassified),
            likely_production=helpers.round_cents(likely),
            unclassified_other=helpers.round_cents(max(0.0, unclassified - likely)),
            unclassified_parts={
                LIKELY_PRODUCTION: helpers.round_cents(likely),
                NO_REPOSITORY: helpers.round_cents(remaining.get(NO_REPOSITORY, 0.0)),
                "too_little_evidence": helpers.round_cents(
                    sum(remaining.get(reason, 0.0) for reason in TOO_LITTLE_EVIDENCE_REASONS)
                ),
                OUTSIDE_LIMITS: helpers.round_cents(remaining.get(OUTSIDE_LIMITS, 0.0)),
            },
            categories=self._categories(analysis),
            reasons=self._reasons(analysis),
            coverage_share=round(analysed / total * 100.0, 2) if total > 0 else 100.0,
            analysed=helpers.round_cents(analysed),
            outside_limits=helpers.round_cents(outside),
        )
