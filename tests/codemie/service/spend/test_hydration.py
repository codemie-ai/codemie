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

"""A pending cost settles from the ledger on read, and only a settled figure is ever written back.

AD-28 rule 7: hydrate on read, never re-price, by the id matching the record's own grain; rule 3b:
pending and unavailable are never collapsed into each other.
"""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.spend.hydration import hydrate_request_cost
from codemie.service.spend.ledger import RequestCost


@pytest.fixture
def ledger():
    """The spend facade as the hydrator sees it; `cost_for_request` answers pending unless told otherwise."""
    facade = MagicMock()
    facade.cost_for_request = AsyncMock(return_value=RequestCost.pending())
    with patch("codemie.service.spend.hydration.spend_service", facade):
        yield facade.cost_for_request


def hydrate(*, request_id="req-1", money_spent=0.0, pending=True, unavailable=False):
    return asyncio.run(
        hydrate_request_cost(request_id=request_id, money_spent=money_spent, pending=pending, unavailable=unavailable)
    )


class TestASettledAnswerIsWrittenBack:
    def test_the_ledgers_figure_replaces_the_subtotal_and_clears_both_flags(self, ledger):
        ledger.return_value = RequestCost.settled(Decimal("0.0010465"))

        hydrated = hydrate(money_spent=0.0)

        assert hydrated.settled_now is True
        assert hydrated.money_spent == pytest.approx(0.0010465)
        assert hydrated.pending is False
        assert hydrated.unavailable is False

    def test_a_settled_zero_is_a_measured_zero_and_is_written_too(self, ledger):
        # A failure row costs zero (AD-20): a real figure, never skipped and never flagged.
        ledger.return_value = RequestCost.settled(Decimal("0"))

        hydrated = hydrate(money_spent=0.0)

        assert hydrated.settled_now is True
        assert hydrated.money_spent == 0.0
        assert (hydrated.pending, hydrated.unavailable) == (False, False)

    def test_a_partly_settled_subtotal_is_replaced_not_added_to(self, ledger):
        # The ledger sums every row of the request, the settled runs' rows included; adding the
        # stored part on top would count them twice.
        ledger.return_value = RequestCost.settled(Decimal("1.5"))

        assert hydrate(money_spent=0.4).money_spent == 1.5


class TestAnUnsettledAnswerChangesNothingStored:
    def test_a_pending_answer_leaves_the_record_as_it_was(self, ledger):
        ledger.return_value = RequestCost.pending()

        hydrated = hydrate(money_spent=0.4, pending=True)

        assert hydrated.settled_now is False
        assert hydrated.money_spent == 0.4
        assert (hydrated.pending, hydrated.unavailable) == (True, False)

    def test_an_unavailable_answer_is_presented_and_not_persisted(self, ledger):
        """Unavailable comes back both from an unactivated ledger and from a failed read (AD-11);
        storing it would silence every later hydration after one outage."""
        ledger.return_value = RequestCost.unavailable()

        hydrated = hydrate(money_spent=0.4)

        assert hydrated.settled_now is False, "nothing is written back"
        assert hydrated.unavailable is True
        assert hydrated.pending is False, "the reader renders pending first; both would still say 'awaiting'"
        assert hydrated.money_spent == 0.4, "the settled part is still shown"


class TestOnlyAPendingKeyedRecordIsAsked:
    def test_a_settled_record_is_returned_untouched_and_never_asked(self, ledger):
        hydrated = hydrate(money_spent=1.25, pending=False)

        assert (hydrated.money_spent, hydrated.pending, hydrated.unavailable, hydrated.settled_now) == (
            1.25,
            False,
            False,
            False,
        )
        ledger.assert_not_awaited()

    def test_a_record_already_unavailable_is_never_asked(self, ledger):
        # Rule 7: hydration is suppressed while `money_spent_unavailable` is true.
        hydrated = hydrate(money_spent=0.0, pending=True, unavailable=True)

        assert hydrated.settled_now is False
        assert hydrated.unavailable is True
        ledger.assert_not_awaited()

    def test_a_pending_record_with_no_key_is_presented_unavailable_and_never_asked(self, ledger):
        # A turn persisted before turns carried their request id can never settle: no figure is
        # coming for it, which is what unavailable means - and nothing is stored for it.
        hydrated = hydrate(request_id=None, money_spent=0.0)

        assert hydrated.settled_now is False
        assert (hydrated.pending, hydrated.unavailable) == (False, True)
        ledger.assert_not_awaited()

    def test_the_ledger_is_asked_at_the_request_grain_and_never_by_run(self, ledger):
        # Asking by run id here would write one call's cost into every call of the request.
        hydrate(request_id="req-7")

        ledger.assert_awaited_once_with(request_id="req-7")
        assert "run_id" not in ledger.await_args.kwargs
