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

from datetime import datetime

from codemie.service.analytics.handlers.ledger_group_spend import project_report_filters, user_report_filters
from codemie.service.spend.spend_service import spend_service
from codemie.service.usage_attribution.spend_widgets import helpers
from codemie.service.usage_attribution.spend_widgets.base import SpendFigures, SpendTable, SpendWidget
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
