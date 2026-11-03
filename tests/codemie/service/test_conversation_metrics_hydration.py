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

"""A conversation's pending turns settle from the ledger when its metrics are read (AD-28 rule 7)."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.rest_api.models.conversation import Conversation, ConversationMetrics, GeneratedMessage
from codemie.service.conversation_service import ConversationService
from codemie.service.spend.ledger import RequestCost


@pytest.fixture
def ledger():
    """`cost_for_request` answering per request id from a dict the test fills in."""
    answers: dict[str, RequestCost] = {}
    facade = MagicMock()
    facade.cost_for_request = AsyncMock(side_effect=lambda *, request_id: answers[request_id])
    with patch("codemie.service.spend.hydration.spend_service", facade):
        yield answers, facade.cost_for_request


@pytest.fixture
def persistence():
    with (
        patch.object(Conversation, "update") as conversation_update,
        patch.object(ConversationMetrics, "save") as metrics_save,
    ):
        yield conversation_update, metrics_save


def a_turn(request_id, money_spent=0.0, pending=True, unavailable=False) -> GeneratedMessage:
    return GeneratedMessage(
        role="Assistant",
        request_id=request_id,
        money_spent=money_spent,
        money_spent_pending=pending,
        money_spent_unavailable=unavailable,
    )


def a_conversation(*turns) -> Conversation:
    return Conversation(id="conv-1", conversation_id="conv-1", history=list(turns))


def stored_metrics(conversation) -> ConversationMetrics:
    """The row exactly as `upsert_chat_history` left it."""
    metrics = ConversationMetrics(conversation_id="conv-1")
    metrics.calculate_metrics(conversation)
    return metrics


def hydrate(conversation, metrics):
    return asyncio.run(ConversationService.hydrate_conversation_metrics(conversation, metrics))


class TestSettledTurnsAreWrittenBack:
    def test_one_of_two_pending_turns_settles_and_the_total_stays_a_subtotal(self, ledger, persistence):
        answers, _ = ledger
        answers["req-1"] = RequestCost.settled(Decimal("0.0010465"))
        answers["req-2"] = RequestCost.pending()
        conversation = a_conversation(a_turn("req-1"), a_turn("req-2"))
        conversation_update, metrics_save = persistence

        metrics = hydrate(conversation, stored_metrics(conversation))

        assert metrics.total_money_spent == pytest.approx(0.0010465)
        assert metrics.money_spent_pending is True, "one figure is still coming"
        assert metrics.money_spent_unavailable is False
        settled, still_pending = conversation.history
        assert (settled.money_spent, settled.money_spent_pending, settled.money_spent_unavailable) == (
            pytest.approx(0.0010465),
            False,
            False,
        )
        assert still_pending.money_spent_pending is True
        # A read must not reorder the recency-sorted chat list.
        conversation_update.assert_called_once_with(columns=["history"], touch_timestamp=False)
        metrics_save.assert_called_once()

    def test_both_turns_settled_publishes_the_exact_total_unqualified(self, ledger, persistence):
        answers, _ = ledger
        answers["req-1"] = RequestCost.settled(Decimal("0.0010465"))
        answers["req-2"] = RequestCost.settled(Decimal("0.00087325"))
        conversation = a_conversation(a_turn("req-1"), a_turn("req-2"))

        metrics = hydrate(conversation, stored_metrics(conversation))

        assert metrics.total_money_spent == pytest.approx(0.0010465 + 0.00087325)
        assert metrics.money_spent_pending is False
        assert metrics.money_spent_unavailable is False

    def test_the_persisted_row_is_computed_before_any_presentation_flag_is_applied(self, ledger, persistence):
        """The row keeps only what the ledger settled; a keyless turn stays pending in storage."""
        answers, _ = ledger
        answers["req-1"] = RequestCost.settled(Decimal("2"))
        conversation = a_conversation(a_turn("req-1"), a_turn(None))
        _, metrics_save = persistence
        persisted = {}
        metrics_save.side_effect = lambda self=None, **_: persisted.update(
            pending=metrics.money_spent_pending, unavailable=metrics.money_spent_unavailable
        )
        metrics = stored_metrics(conversation)

        returned = hydrate(conversation, metrics)

        assert persisted == {"pending": True, "unavailable": False}, "stored: a figure is still coming"
        assert returned.money_spent_unavailable is True, "shown: none is coming for the keyless turn"
        assert returned.money_spent_pending is False


class TestNothingIsWrittenForATurnThatDidNotSettle:
    def test_a_pending_answer_leaves_the_row_and_the_conversation_alone(self, ledger, persistence):
        answers, _ = ledger
        answers["req-1"] = RequestCost.pending()
        conversation = a_conversation(a_turn("req-1"))
        conversation_update, metrics_save = persistence

        metrics = hydrate(conversation, stored_metrics(conversation))

        assert metrics.total_money_spent is None
        assert metrics.money_spent_pending is True
        conversation_update.assert_not_called()
        metrics_save.assert_not_called()

    def test_an_unavailable_answer_is_presented_on_the_response_only(self, ledger, persistence):
        answers, _ = ledger
        answers["req-1"] = RequestCost.unavailable()
        conversation = a_conversation(a_turn("req-1"))
        conversation_update, metrics_save = persistence

        metrics = hydrate(conversation, stored_metrics(conversation))

        assert metrics.money_spent_unavailable is True
        assert metrics.money_spent_pending is False
        conversation_update.assert_not_called()
        metrics_save.assert_not_called()

    def test_a_turn_without_a_key_is_presented_unavailable_and_never_asked(self, ledger, persistence):
        _, cost_for_request = ledger
        conversation = a_conversation(a_turn(None))
        conversation_update, metrics_save = persistence

        metrics = hydrate(conversation, stored_metrics(conversation))

        assert metrics.money_spent_unavailable is True
        assert metrics.money_spent_pending is False
        cost_for_request.assert_not_awaited()
        conversation_update.assert_not_called()
        metrics_save.assert_not_called()


class TestOnlyPendingRecordsAreAsked:
    def test_a_settled_row_is_returned_as_stored_without_a_ledger_read(self, ledger, persistence):
        _, cost_for_request = ledger
        conversation = a_conversation(a_turn("req-1", money_spent=1.25, pending=False))
        metrics = stored_metrics(conversation)

        assert hydrate(conversation, metrics) is metrics
        cost_for_request.assert_not_awaited()

    def test_settled_turns_beside_a_pending_one_are_not_re_asked(self, ledger, persistence):
        answers, cost_for_request = ledger
        answers["req-2"] = RequestCost.pending()
        conversation = a_conversation(
            a_turn("req-1", money_spent=1.25, pending=False),
            a_turn("req-2"),
            a_turn("req-3", money_spent=0.5, pending=False, unavailable=True),
        )

        hydrate(conversation, stored_metrics(conversation))

        cost_for_request.assert_awaited_once_with(request_id="req-2")

    def test_a_turn_still_generating_is_not_asked(self, ledger, persistence):
        """Its completing upsert replaces it; a ledger answer now would settle a partial cost."""
        answers, cost_for_request = ledger
        answers["req-1"] = RequestCost.settled(Decimal("0.5"))
        answers["req-2"] = RequestCost.pending()
        generating = a_turn("req-1")
        generating.in_progress = True
        conversation = a_conversation(a_turn("req-2"), generating)

        hydrate(conversation, stored_metrics(conversation))

        cost_for_request.assert_awaited_once_with(request_id="req-2")
        assert (generating.money_spent, generating.money_spent_pending) == (0.0, True)

    def test_exactly_one_read_per_pending_turn(self, ledger, persistence):
        answers, cost_for_request = ledger
        answers.update({"req-1": RequestCost.pending(), "req-2": RequestCost.pending()})
        conversation = a_conversation(a_turn("req-1"), a_turn("req-2"))

        hydrate(conversation, stored_metrics(conversation))

        assert cost_for_request.await_count == 2
