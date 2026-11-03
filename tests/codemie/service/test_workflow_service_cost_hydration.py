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

"""A workflow execution's pending cost settles from the ledger by its own execution id (AD-28 rule 7)."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.models import TokensUsage
from codemie.service.spend.ledger import RequestCost
from codemie.service.workflow_service import WorkflowService


@pytest.fixture
def ledger():
    facade = MagicMock()
    facade.cost_for_request = AsyncMock(return_value=RequestCost.pending())
    with patch("codemie.service.spend.hydration.spend_service", facade):
        yield facade.cost_for_request


def an_execution(tokens_usage, execution_id="exec-1"):
    execution = MagicMock()
    execution.execution_id = execution_id
    execution.tokens_usage = tokens_usage
    return execution


PENDING = TokensUsage(input_tokens=5, output_tokens=1, money_spent=0.0, money_spent_pending=True)


def hydrate(execution):
    return asyncio.run(WorkflowService.hydrate_execution_cost(execution))


class TestASettledExecutionIsSavedOnce:
    def test_the_figure_lands_on_the_row_and_the_row_is_updated(self, ledger):
        ledger.return_value = RequestCost.settled(Decimal("0.75"))
        execution = an_execution(PENDING)

        usage = hydrate(execution)

        assert usage.money_spent == 0.75
        assert (usage.money_spent_pending, usage.money_spent_unavailable) == (False, False)
        assert execution.tokens_usage is usage, "the row carries the settled usage"
        # Settling a cost is not a run: the execution's "Finished" (its update_date) must not move.
        execution.update.assert_called_once_with(touch_timestamp=False)

    def test_the_tokens_travel_with_the_settled_figure(self, ledger):
        ledger.return_value = RequestCost.settled(Decimal("0.75"))

        usage = hydrate(an_execution(PENDING))

        assert (usage.input_tokens, usage.output_tokens) == (5, 1)

    def test_the_ledger_is_asked_by_the_executions_own_id(self, ledger):
        hydrate(an_execution(PENDING, execution_id="exec-42"))

        ledger.assert_awaited_once_with(request_id="exec-42")


class TestAnUnsettledExecutionIsNotSaved:
    def test_a_pending_answer_keeps_the_row_awaiting(self, ledger):
        execution = an_execution(PENDING)

        usage = hydrate(execution)

        assert usage.money_spent_pending is True
        execution.update.assert_not_called()

    def test_an_unavailable_answer_is_shown_but_the_row_keeps_its_pending_flag(self, ledger):
        ledger.return_value = RequestCost.unavailable()
        execution = an_execution(PENDING)

        usage = hydrate(execution)

        assert (usage.money_spent_pending, usage.money_spent_unavailable) == (False, True)
        assert execution.tokens_usage.money_spent_pending is True, "stored untouched: the next read asks again"
        execution.update.assert_not_called()


class TestOnlyAPendingExecutionIsAsked:
    def test_a_settled_execution_is_returned_as_stored(self, ledger):
        settled = TokensUsage(input_tokens=5, output_tokens=1, money_spent=1.0)

        usage = hydrate(an_execution(settled))

        assert usage == settled
        ledger.assert_not_awaited()

    def test_an_execution_with_no_usage_record_has_nothing_to_hydrate(self, ledger):
        assert hydrate(an_execution(None)) is None
        ledger.assert_not_awaited()
