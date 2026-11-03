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

"""The model dimension, and the two ways it differs from every other one.

It is the only dimension whose column the **provider** populates, so it has no pre-rollout NULL era and
the metric store contributes nothing to it. Both facts are asserted rather than assumed, because each has
a tempting shortcut: skipping the union machinery (which would stop a genuinely NULL row being labelled)
and adding a metric-store lookup (which would resolve nothing and widen a window for no reason).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from codemie.repository.spend_logs_repository import (
    _UNION_DIMENSIONS,
    _dimension_is_null,
    _ledger_branch,
    _require_union_dimension,
)
from codemie.service.spend.coverage import SpendDimension, TimeRange
from codemie.service.spend.ledger import DimensionScope

PERIOD = TimeRange(datetime(2026, 8, 1, tzinfo=timezone.utc), datetime(2026, 9, 1, tzinfo=timezone.utc))


def sql(clause) -> str:
    return str(clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})).replace("\n", " ")


class TestItIsALedgerNativeDimension:
    def test_it_is_a_union_dimension_and_not_rejected(self) -> None:
        assert SpendDimension.MODEL in _UNION_DIMENSIONS
        _require_union_dimension(DimensionScope(SpendDimension.MODEL))

    def test_it_buckets_on_model_group_and_not_on_model(self) -> None:
        """The schema's own comment: grouping by the fully qualified `model` splits or merges models."""
        rendered = sql(_ledger_branch(DimensionScope(SpendDimension.MODEL, "gpt-4.1")))
        assert "model_group" in rendered
        # `model_group` contains the substring `model`, so assert the bare column is not compared
        # on its own rather than asserting `model` is absent.
        assert "litellm_spend_logs.model =" not in rendered
        assert "gpt-4.1" in rendered

    def test_a_grouped_scope_selects_every_populated_row(self) -> None:
        rendered = sql(_ledger_branch(DimensionScope(SpendDimension.MODEL, None)))
        assert "model_group IS NOT NULL" in rendered


class TestTheUnionMachineryStillApplies:
    def test_a_null_row_is_still_reachable_as_residue(self) -> None:
        """The column is nullable. A special case here would stop a NULL row ever being labelled."""
        assert _dimension_is_null(DimensionScope(SpendDimension.MODEL)) is not None
        assert "model_group IS NULL" in sql(_dimension_is_null(DimensionScope(SpendDimension.MODEL)))


class TestTheMetricStoreContributesNothing:
    @pytest.mark.asyncio
    async def test_the_resolver_returns_no_ids_rather_than_widening_a_window(self) -> None:
        """No lookup exists for this dimension, and that is the right answer, not a gap."""
        from codemie.service.spend.dimension_scope import resolve_metric_store_ids

        class _Repo:
            def __init__(self) -> None:
                self.calls = 0

            async def execute_aggregation_query(self, body):  # pragma: no cover - must not be reached
                self.calls += 1
                return {}

        repo = _Repo()
        ids = await resolve_metric_store_ids(repo, DimensionScope(SpendDimension.MODEL, "gpt-4.1"), PERIOD)

        assert ids == ()
        assert repo.calls == 0
