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

"""Shared fixtures for the spend widget tests: a fake cache, a fake port, and a ledger with a coverage floor."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers.ledger_group_spend import TableFilters
from codemie.service.spend.coverage import LedgerCoverage, SpendResult
from codemie.service.spend.ledger import AccessScope, ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.usage_attribution.spend_widgets import WidgetContext, base, helpers, widgets

FLOOR = datetime(2026, 10, 1, tzinfo=timezone.utc)
START = datetime(2026, 9, 13, tzinfo=timezone.utc)
END = datetime(2026, 10, 13, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 14, tzinfo=timezone.utc)
USER = SimpleNamespace(id="u-admin")
SCOPE = AccessScope(unrestricted=False, user_id="u-admin", plain_projects=("p",))
OTHER_SCOPE = AccessScope(unrestricted=False, user_id="u-other", plain_projects=("q",))


def coverage(floor=FLOOR, ready=True):
    """The ledger registered with this coverage."""
    return patch.object(base, "ledger_coverage", return_value=LedgerCoverage(coverage_floor=floor, ready=ready))


def groups(total=10.0, platform=4.0, cli=6.0, embedding=1.0):
    return {
        SpendGroup.TOTAL: SpendResult(Decimal(str(total))),
        SpendGroup.PLATFORM: SpendResult(Decimal(str(platform))),
        SpendGroup.CLI: SpendResult(Decimal(str(cli))),
        SpendGroup.EMBEDDING: SpendResult(Decimal(str(embedding))),
    }


def unavailable(window):
    return {group: SpendResult.no_figure(window) for group in SpendGroup}


def user_filters(uids):
    return TableFilters(ReportFilter(), [ReportFilter(users=(UserTerm(uid, frozenset()),)) for uid in uids])


def project_filters(names):
    return TableFilters(ReportFilter(), [ReportFilter(projects=(n,)) for n in names])


class FakeCache:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.ttls: list[int] = []

    async def get(self, key):
        return self.store.get(key)

    async def set_many(self, mapping, ttl):
        self.store.update(mapping)
        self.ttls.append(ttl)


def context(cache=None, access=SCOPE):
    """One caller's context; an explicit boundary, unless a test is about deriving it."""
    return WidgetContext(USER, cache if cache is not None else FakeCache(), access)


@pytest.fixture
def port():
    service = MagicMock()
    service.aggregate_by_spend_group = AsyncMock(return_value=groups())

    async def per_filter(period, *, access, common_filter, report_filters):
        return {f: groups(total=float(i + 1)) for i, f in enumerate(report_filters)}

    service.aggregate_by_spend_group_per_filter = AsyncMock(side_effect=per_filter)
    with patch.object(widgets, "spend_service", service), patch.object(helpers, "spend_service", service):
        yield service
