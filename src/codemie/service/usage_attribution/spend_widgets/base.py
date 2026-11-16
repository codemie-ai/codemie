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

"""The base every spend widget shares: its typed outputs, the caller's context, and the coverage-floor window rule."""

from abc import ABC, abstractmethod
from dataclasses import field
from datetime import datetime
from typing import Any, ClassVar, Protocol, Self

from pydantic.dataclasses import dataclass

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.service.analytics.query_pipeline import AnalyticsQueryPipeline
from codemie.service.spend.coverage import TimeRange
from codemie.service.spend.ledger import AccessScope
from codemie.service.spend.ledger_registry import ledger_coverage
from codemie.service.spend.spend_group import SpendGroup


@dataclass(frozen=True)
class SpendFigures:
    """The four summary figures. No figure is None, never a zero.

    ``complete`` is internal: False says the port could not give every figure, so the answer must not be cached. It
    takes no part in equality and is never laid into a response.
    """

    total: float | None = None
    platform: float | None = None
    cli: float | None = None
    embedding: float | None = None
    complete: bool = field(default=True, compare=False)

    @classmethod
    def from_groups(cls, figures: dict[SpendGroup, float | None], complete: bool = True) -> Self:
        return cls(**{group.value: figures.get(group) for group in SpendGroup}, complete=complete)

    def of(self, group: SpendGroup) -> float | None:
        return getattr(self, group.value)


@dataclass(frozen=True)
class SpendTable:
    """Figures per key (user id or project name); ``complete`` is False when any row had an unavailable figure."""

    rows: dict[str, SpendFigures] = field(default_factory=dict)
    complete: bool = field(default=True, compare=False)

    def of(self, key: str) -> SpendFigures:
        """The key's figures; a key the table does not hold has no figures."""
        return self.rows.get(key) or SpendFigures()


@dataclass(frozen=True)
class CliCategorySpend:
    category: str
    spend: float | None = None
    classified: float | None = None
    allocated: float | None = None
    cb: float | None = None
    sh: float | None = None
    pe: float | None = None
    users: int = 0


@dataclass(frozen=True)
class CliReasonSpend:
    reason: str
    spend: float | None = None
    allocated: float | None = None
    remaining: float | None = None
    repositories: int = 0


@dataclass(frozen=True)
class CliUsageFigures:
    """Classified CLI ledger figures in cents; absent money remains None."""

    total: float | None = None
    people: int | None = None
    production: float | None = None
    production_charged: float | None = None
    production_allocated: float | None = None
    experiments: float | None = None
    experiments_allocated: float | None = None
    experiments_parts: dict[str, float | None] = field(default_factory=dict)
    allocated: float | None = None
    unclassified: float | None = None
    likely_production: float | None = None
    unclassified_other: float | None = None
    unclassified_parts: dict[str, float | None] = field(default_factory=dict)
    categories: tuple[CliCategorySpend, ...] = ()
    reasons: tuple[CliReasonSpend, ...] = ()
    coverage_share: float | None = None
    analysed: float | None = None
    outside_limits: float | None = None
    complete: bool = field(default=True, compare=False)


class WidgetCache(Protocol):
    async def get(self, key: str) -> bytes | None: ...

    async def set_many(self, mapping: dict[str, bytes], ttl: int) -> None: ...


class WidgetContext:
    """One caller: who they are, the shared cache, and their access boundary, derived once for every widget."""

    def __init__(self, user: Any, cache: WidgetCache, access: AccessScope | None = None):
        self.user = user
        self.cache = cache
        self._access = access

    @property
    def scope(self) -> AccessScope:
        """The caller's boundary, derived from the one place analytics derives it."""
        if self._access is None:
            self._access = AnalyticsQueryPipeline(self.user, MetricsElasticRepository()).access_scope
        return self._access


class SpendWidget[SpendOutput: (SpendFigures, SpendTable, CliUsageFigures)](ABC):
    """One entity's spend, read from the hourly ledger and clipped to the coverage floor.

    A subclass names its cache ``view`` and its ``output`` type, and implements ``read``, its one public method.
    Every read is clipped to ``[max(start, F), end)`` before the port is asked, so a window wholly below the floor, or
    a ledger that is not activated, gives no figure - never a zero - and the port's legacy leg is never reached.
    """

    view: ClassVar[str]
    output: ClassVar[type]

    def __init__(self, context: WidgetContext):
        self._context = context

    @abstractmethod
    async def read(self, start: datetime, end: datetime, *names: Any) -> SpendOutput: ...

    @staticmethod
    def _window(start: datetime, end: datetime) -> TimeRange | None:
        """The part of ``[start, end)`` the ledger has a figure for, or None when that is nothing."""
        return ledger_coverage().admitted(TimeRange(start, end))
