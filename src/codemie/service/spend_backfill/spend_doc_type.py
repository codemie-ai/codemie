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

"""What the spend backfill job needs from each kind of spend document it backfills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from codemie.repository.spend_backfill_metrics_repository import MetricDoc
from codemie.service.spend.coverage import TimeRange


@dataclass(frozen=True)
class DocUpdate:
    """One document's settlement: the attributes to write, or None to leave it, plus figures for the run log."""

    status: str
    reason: str | None
    attributes: dict[str, Any] | None
    log_sums: Mapping[str, float] = field(default_factory=dict)
    log_maxima: Mapping[str, float] = field(default_factory=dict)


class SpendDocType(ABC):
    """One subclass per kind of spend document the job backfills."""

    metric_name: ClassVar[str]
    settlement_version: ClassVar[int]
    source_fields: ClassVar[tuple[str, ...]]

    @abstractmethod
    async def build_updates(
        self, docs: Sequence[MetricDoc], ledger_period: TimeRange, now: datetime, timeout: float
    ) -> list[DocUpdate]:
        """Returns one update per document, in order; `timeout` is the seconds left for its ledger reads."""
