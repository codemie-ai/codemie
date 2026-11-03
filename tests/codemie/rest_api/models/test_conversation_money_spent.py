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

"""How a chat turn and its conversation publish money: settled, pending, or absent.

AD-23/AD-28: a settled subtotal published without its qualification reads as a final cost, and an
absent figure is never a zero.
"""

import pytest

from codemie.core.models import TokensUsage
from codemie.rest_api.models.conversation import (
    ChatTurnData,
    Conversation,
    ConversationMetrics,
    GeneratedMessage,
)


def a_chat_turn(**overrides) -> ChatTurnData:
    fields = {
        "user_query": "User Msg",
        "user_query_raw": "User Msg",
        "assistant_id": "assistant-id",
        "assistant_response": "AI Msg",
        "thoughts": [],
        "history_index": 0,
        "time_elapsed": 0.0,
        "input_tokens": 1,
        "output_tokens": 2,
        "file_names": [],
        "money_spent": 0.0,
    }
    fields.update(overrides)
    return ChatTurnData(**fields)


def wire_assistant_message(turn: ChatTurnData) -> dict:
    """The assistant message of this turn exactly as it leaves GET /v1/conversations/{id}."""
    conversation = Conversation(conversation_id="test_id", history=[])
    conversation.update_chat_history(turn, project="test-project")
    return conversation.history[-1].model_dump()


def an_assistant_message(money_spent, pending=None, unavailable=None) -> GeneratedMessage:
    """One persisted turn. The flags default to None - the shape of a row nobody qualified."""
    return GeneratedMessage(
        role="Assistant",
        money_spent=money_spent,
        money_spent_pending=pending,
        money_spent_unavailable=unavailable,
    )


def a_conversation_of(*messages: GeneratedMessage) -> Conversation:
    return Conversation(conversation_id="test_id", history=list(messages))


def a_conversation(*money_spent_values) -> Conversation:
    return a_conversation_of(*(an_assistant_message(value) for value in money_spent_values))


def wire_metrics(conversation: Conversation) -> dict:
    """This conversation's metrics exactly as GET /v1/assistants/metrics/{id} emits them.

    The endpoint declares `response_model_exclude_none=True`, so a field left None is absent from the
    payload rather than sent as `null` - which is how "no figure" is expressed on the wire.
    """
    metrics = ConversationMetrics(conversation_id="test_id")
    metrics.calculate_metrics(conversation)
    return metrics.model_dump(exclude_none=True)


def test_pending_chat_turn_reaches_the_wire_qualified():
    """A streamed LiteLLM turn whose cost has not landed is the normal case, not an edge case."""
    usage = TokensUsage(input_tokens=1, output_tokens=2, money_spent=0.0, money_spent_pending=True)

    wire = wire_assistant_message(
        a_chat_turn(
            money_spent=usage.money_spent,
            money_spent_pending=usage.money_spent_pending,
            money_spent_unavailable=usage.money_spent_unavailable,
        )
    )

    assert wire["money_spent"] == 0.0, "the settled subtotal still travels; the flag qualifies it"
    assert wire["money_spent_pending"] is True
    assert wire["money_spent_unavailable"] is False


def test_unavailable_chat_turn_reaches_the_wire_qualified():
    usage = TokensUsage(input_tokens=1, output_tokens=2, money_spent=0.0, money_spent_unavailable=True)

    wire = wire_assistant_message(
        a_chat_turn(
            money_spent=usage.money_spent,
            money_spent_pending=usage.money_spent_pending,
            money_spent_unavailable=usage.money_spent_unavailable,
        )
    )

    assert wire["money_spent"] == 0.0
    assert wire["money_spent_pending"] is False
    assert wire["money_spent_unavailable"] is True


def test_settled_chat_turn_reaches_the_wire_with_its_real_figure():
    """The mirror case: a genuinely settled turn reports its cost, qualified by nothing."""
    usage = TokensUsage(input_tokens=1, output_tokens=2, money_spent=1.25)

    wire = wire_assistant_message(
        a_chat_turn(
            money_spent=usage.money_spent,
            money_spent_pending=usage.money_spent_pending,
            money_spent_unavailable=usage.money_spent_unavailable,
        )
    )

    assert wire["money_spent"] == 1.25
    assert wire["money_spent_pending"] is False
    assert wire["money_spent_unavailable"] is False


def test_get_total_money_spent_counts_real_zero():
    # A FAILED call genuinely costs zero; it must be counted, not dropped by truthiness.
    conversation = a_conversation(0.0)

    assert conversation.get_total_money_spent() == 0


def test_get_total_money_spent_returns_none_when_nothing_settled():
    """Re-baselined: this used to assert the fabricated 0 its own comment argued against.

    None means NOT SETTLED. With no settled message there is no total to report, and 0 would say
    "this conversation was free" - AD-23: an absent figure is never a zero.
    """
    conversation = a_conversation(None)

    assert conversation.get_total_money_spent() is None


def test_get_total_money_spent_sums_mix_of_zero_value_and_none():
    conversation = a_conversation(0.0, 1.5, None, 2.25)

    assert conversation.get_total_money_spent() == 3.75


def test_get_total_money_spent_returns_none_for_empty_history():
    """Re-baselined with the case above: an empty conversation has nothing settled either."""
    conversation = Conversation(conversation_id="test_id", history=[])

    assert conversation.get_total_money_spent() is None


def test_conversation_metrics_do_not_persist_an_exact_zero_when_nothing_settled():
    metrics = ConversationMetrics(conversation_id="test_id")

    metrics.calculate_metrics(a_conversation(None, None))

    assert metrics.total_money_spent is None


def test_conversation_metrics_persist_the_settled_total():
    metrics = ConversationMetrics(conversation_id="test_id")

    metrics.calculate_metrics(a_conversation(1.5, 2.25))

    assert metrics.total_money_spent == 3.75


class TestTheConversationTotalIsQualifiedOrAbsent:
    """The four states a conversation's money can be in, and how each one is published.

    Every one of these turns is what `RequestSummary.calculate` actually produces: a settled subtotal
    plus a flag. The bug this class pins is that `money_spent is not None` treats the flagged `0.0` of
    a streamed turn as a measurement, so the endpoint answered `$0.0000` for every conversation on a
    deployment where chat streams through LiteLLM.
    """

    def test_no_turn_settled_reports_no_figure_at_all(self):
        conversation = a_conversation_of(
            an_assistant_message(0.0, pending=True),
            an_assistant_message(0.0, pending=True),
        )

        money = conversation.get_money_spent()

        assert money.total is None, "a flagged zero is an absence wearing a number, not $0.00"
        assert money.pending is True
        assert money.unavailable is False

    def test_no_turn_settled_reaches_the_wire_as_a_flag_with_no_figure_beside_it(self):
        wire = wire_metrics(a_conversation_of(an_assistant_message(0.0, pending=True)))

        assert "total_money_spent" not in wire, "an omitted field is how the reader renders '-'"
        assert wire["money_spent_pending"] is True
        assert wire["money_spent_unavailable"] is False

    def test_nothing_settled_and_nothing_coming_is_published_as_unavailable(self):
        conversation = a_conversation_of(an_assistant_message(0.0, unavailable=True))

        wire = wire_metrics(conversation)

        assert "total_money_spent" not in wire
        assert wire["money_spent_unavailable"] is True
        assert wire["money_spent_pending"] is False

    def test_some_settled_and_some_pending_publishes_the_subtotal_marked_as_one(self):
        conversation = a_conversation_of(
            an_assistant_message(0.02),
            an_assistant_message(0.02),
            an_assistant_message(0.02),
            an_assistant_message(0.0, pending=True),
            an_assistant_message(0.0, pending=True),
        )

        wire = wire_metrics(conversation)

        assert wire["total_money_spent"] == pytest.approx(0.06)
        assert wire["money_spent_pending"] is True, "$0.0600 published bare reads as the whole cost"

    def test_a_partly_settled_turn_keeps_the_part_it_measured(self):
        # One run of the turn priced, another not: the figure is real and short, not absent.
        conversation = a_conversation_of(an_assistant_message(0.03, pending=True))

        money = conversation.get_money_spent()

        assert money.total == 0.03
        assert money.pending is True

    def test_every_turn_settled_publishes_the_exact_total_unqualified(self):
        conversation = a_conversation_of(an_assistant_message(1.5), an_assistant_message(2.25))

        wire = wire_metrics(conversation)

        assert wire["total_money_spent"] == 3.75
        assert wire["money_spent_pending"] is False
        assert wire["money_spent_unavailable"] is False

    def test_a_measured_zero_stays_a_zero_and_stays_unqualified(self):
        # A turn that really cost nothing, both flags explicitly false.
        conversation = a_conversation_of(an_assistant_message(0.0, pending=False, unavailable=False))

        wire = wire_metrics(conversation)

        assert wire["total_money_spent"] == 0.0
        assert wire["money_spent_pending"] is False
        assert wire["money_spent_unavailable"] is False

    def test_a_measured_zero_and_an_unsettled_zero_stay_distinguishable(self):
        assert a_conversation_of(an_assistant_message(0.0, pending=False)).get_money_spent().total == 0.0
        assert a_conversation_of(an_assistant_message(0.0, pending=True)).get_money_spent().total is None

    def test_a_message_with_no_figure_at_all_is_not_mistaken_for_an_unsettled_one(self):
        """Every user message carries `money_spent=None`, as does every pre-costing assistant message.

        Flagging those would stamp "cost unavailable" on conversations that were simply never priced
        this way, so a missing figure is skipped and the settled turn beside it reports unqualified.
        """
        conversation = a_conversation_of(an_assistant_message(None), an_assistant_message(1.25))

        money = conversation.get_money_spent()

        assert money.total == 1.25
        assert money.pending is False
        assert money.unavailable is False


class TestATurnCarriesTheKeyItSettlesBy:
    """AD-28 rule 7 needs the request id on the record; the `history` JSON column takes it without a migration."""

    def test_the_assistant_message_carries_the_turn_request_id_and_the_user_message_does_not(self):
        conversation = Conversation(conversation_id="test_id", history=[])
        conversation.update_chat_history(a_chat_turn(request_id="req-1"), project="test-project")

        user_message, assistant_message = conversation.history
        assert assistant_message.request_id == "req-1"
        assert user_message.request_id is None, "a user message carries no cost and no key"

    def test_the_key_reaches_the_wire(self):
        assert wire_assistant_message(a_chat_turn(request_id="req-1"))["request_id"] == "req-1"

    def test_a_turn_written_without_a_key_round_trips_as_none(self):
        assert wire_assistant_message(a_chat_turn())["request_id"] is None

    def test_a_persisted_message_without_the_field_still_loads(self):
        # Every turn written before this field existed.
        assert GeneratedMessage(role="Assistant", money_spent=0.0, money_spent_pending=True).request_id is None
