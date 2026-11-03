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

"""A workflow conversation's money total is the settled part, or nothing at all - never a zero."""

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.core.models import TokensUsage
from codemie.rest_api.routers.assistant.execution import _calculate_workflow_metrics
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.spend.ledger import RequestCost

OWNER = User(id="owner", name="Owner")


@pytest.fixture(autouse=True)
def the_caller_may_view_the_conversation():
    """These tests are about the figures; who may read them is `test_conversation_metrics_access.py`'s concern."""
    with patch("codemie.rest_api.routers.assistant.execution.Ability") as ability:
        ability.return_value.can.return_value = True
        yield


@pytest.fixture(autouse=True)
def ledger():
    """The read behind AD-28 rule 7, answering pending unless a test says otherwise.

    Every read of a pending record now asks the ledger, so the stored state a test sets up is what
    the endpoint publishes only while the ledger still says "a figure is coming".
    """
    facade = MagicMock()
    facade.cost_for_request = AsyncMock(return_value=RequestCost.pending())
    with patch("codemie.service.spend.hydration.spend_service", facade):
        yield facade.cost_for_request


def _conversation(*execution_ids):
    conversation = MagicMock()
    conversation.history = [MagicMock(execution_id=execution_id) for execution_id in execution_ids]
    return conversation


def _execution(tokens_usage):
    execution = MagicMock()
    execution.tokens_usage = tokens_usage
    return execution


def _executions_by_id(mapping):
    """Patch `WorkflowExecution.get_by_execution_id` to answer from `mapping`."""
    return patch(
        "codemie.core.workflow_models.WorkflowExecution.get_by_execution_id",
        side_effect=lambda execution_id: mapping.get(execution_id, []),
    )


class TestASettledSubtotalIsNeverPresentedAsATotal:
    def test_settled_subtotals_add_up(self):
        mapping = {
            "e-1": [_execution(TokensUsage(input_tokens=10, output_tokens=1, money_spent=1.5))],
            "e-2": [_execution(TokensUsage(input_tokens=20, output_tokens=2, money_spent=2.5))],
        }
        with _executions_by_id(mapping):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1", "e-2")))

        assert metrics.total_money_spent == 4.0
        assert metrics.total_input_tokens == 30
        assert metrics.total_output_tokens == 3

    def test_a_conversation_with_nothing_settled_reports_no_total_rather_than_zero(self):
        # No usage record at all on either execution: there is no figure, and `0.0` would publish
        # "this conversation was free".
        mapping = {"e-1": [_execution(None)], "e-2": [_execution(None)]}
        with _executions_by_id(mapping):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1", "e-2")))

        assert metrics.total_money_spent is None

    def test_a_measured_zero_survives(self):
        # A failed call that genuinely cost nothing is a figure, and must not be dropped with the
        # absences.
        mapping = {"e-1": [_execution(TokensUsage(input_tokens=1, output_tokens=1, money_spent=0.0))]}
        with _executions_by_id(mapping):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent == 0.0

    def test_an_execution_with_no_usage_record_does_not_contribute_a_zero(self):
        mapping = {
            "e-1": [_execution(TokensUsage(input_tokens=10, output_tokens=1, money_spent=3.0))],
            "e-2": [_execution(None)],
        }
        with _executions_by_id(mapping):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1", "e-2")))

        # The settled part is reported as-is; the missing execution is not counted as $0 spend, and
        # its absent token counts are not invented either.
        assert metrics.total_money_spent == 3.0
        assert metrics.total_input_tokens == 10

    def test_a_pending_subtotal_is_still_summed_and_flagged_in_the_log(self):
        pending = TokensUsage(input_tokens=5, output_tokens=1, money_spent=1.0, money_spent_pending=True)
        mapping = {"e-1": [_execution(pending)]}
        with _executions_by_id(mapping), patch("codemie.rest_api.routers.assistant.execution.logger") as logger:
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent == 1.0
        assert "money_spent_is_subtotal=True" in logger.debug.call_args.args[0]

    def test_a_conversation_with_no_executions_reports_no_total(self):
        with _executions_by_id({}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation()))

        assert metrics.total_money_spent is None
        assert metrics.total_input_tokens == 0


class TestAConversationWithNoMetricsRowAtAll:
    """The other absence on this endpoint: not "nothing settled" but "nobody has priced it".

    `get_conversation_metrics` returned `ConversationMetrics(total_money_spent=0, ...)` when no row
    existed, which is a claim that the conversation was free. The column is already `Optional[float]`
    because a conversation with nothing settled has no figure either, so absence is representable.
    """

    def test_a_missing_metrics_row_reports_no_money_rather_than_zero(self):
        from codemie.rest_api.routers.assistant import execution as assistant_router

        # `Conversation` is imported inside the endpoint, so it is patched where it is defined.
        with (
            patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=None),
            patch.object(assistant_router.ConversationMetrics, "get_by_fields", return_value=None),
        ):
            metrics = asyncio.run(assistant_router.conversation_metrics(conversation_id="conv-missing"))

        assert metrics.total_money_spent is None
        # The token counts stay zero: no row means no messages were recorded, which is a real count,
        # unlike a price nobody computed.
        assert metrics.total_input_tokens == 0
        assert metrics.total_output_tokens == 0


class TestTheWorkflowQualificationReachesTheResponse:
    """`_calculate_workflow_metrics` always computed this signal; it could only log it."""

    def test_a_pending_execution_publishes_the_pending_flag(self):
        pending = TokensUsage(input_tokens=5, output_tokens=1, money_spent=1.0, money_spent_pending=True)
        with _executions_by_id({"e-1": [_execution(pending)]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent == 1.0
        assert metrics.money_spent_pending is True
        assert metrics.money_spent_unavailable is False

    def test_an_execution_whose_cost_will_never_arrive_publishes_the_unavailable_flag(self):
        gone = TokensUsage(input_tokens=5, output_tokens=1, money_spent=1.0, money_spent_unavailable=True)
        with _executions_by_id({"e-1": [_execution(gone)]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.money_spent_unavailable is True
        assert metrics.money_spent_pending is False

    def test_an_execution_with_no_usage_record_does_not_promise_a_later_figure(self):
        # Nothing here says a cost is coming, so the total is marked unavailable rather than
        # pending: "awaiting final cost" would send the reader back for something never arriving.
        with _executions_by_id({"e-1": [_execution(None)]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.money_spent_unavailable is True
        assert metrics.money_spent_pending is False

    def test_a_fully_settled_workflow_conversation_publishes_no_qualification(self):
        settled = TokensUsage(input_tokens=5, output_tokens=1, money_spent=1.0)
        with _executions_by_id({"e-1": [_execution(settled)]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent == 1.0
        assert metrics.money_spent_pending is False
        assert metrics.money_spent_unavailable is False


class TestAFlaggedZeroIsAnAbsenceAndNotAFigure:
    """The zero the LiteLLM streaming path actually persists on a workflow execution.

    `RequestSummary.calculate` writes `TokensUsage(money_spent=0.0, money_spent_pending=True)` when no
    run has settled, and `workflow_execution_service` stores that object verbatim, so this is the
    normal state of a streamed workflow conversation rather than an edge case. Gating the total on
    `money_spent is not None` let the zero into the sum and sent `total_money_spent: 0.0` beside
    `money_spent_pending: true` - rendered as `$0.0000 (awaiting final cost)`, where the
    assistant-conversation twin in the same state renders `- (awaiting final cost)`.
    """

    @staticmethod
    def _flagged_zero(pending=True, unavailable=False):
        return TokensUsage(
            input_tokens=5,
            output_tokens=1,
            money_spent=0.0,
            money_spent_pending=pending,
            money_spent_unavailable=unavailable,
        )

    def test_a_single_flagged_zero_reports_no_total(self):
        with _executions_by_id({"e-1": [_execution(self._flagged_zero())]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent is None
        assert metrics.money_spent_pending is True
        assert metrics.money_spent_unavailable is False
        # The token counts are measurements and survive the absent figure.
        assert metrics.total_input_tokens == 5

    def test_several_flagged_zeros_still_add_up_to_no_total(self):
        mapping = {
            "e-1": [_execution(self._flagged_zero())],
            "e-2": [_execution(self._flagged_zero())],
        }
        with _executions_by_id(mapping):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1", "e-2")))

        assert metrics.total_money_spent is None
        assert metrics.money_spent_pending is True

    def test_a_flagged_zero_beside_an_execution_with_no_usage_record_reports_both_flags_and_no_total(self):
        mapping = {
            "e-1": [_execution(self._flagged_zero())],
            "e-2": [_execution(None)],
        }
        with _executions_by_id(mapping):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1", "e-2")))

        assert metrics.total_money_spent is None
        assert metrics.money_spent_pending is True
        assert metrics.money_spent_unavailable is True

    def test_a_zero_whose_cost_is_never_coming_reports_no_total_either(self):
        flagged = self._flagged_zero(pending=False, unavailable=True)
        with _executions_by_id({"e-1": [_execution(flagged)]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent is None
        assert metrics.money_spent_unavailable is True
        assert metrics.money_spent_pending is False

    def test_a_flagged_zero_beside_a_settled_figure_leaves_that_figure_alone(self):
        mapping = {
            "e-1": [_execution(TokensUsage(input_tokens=10, output_tokens=1, money_spent=0.02))],
            "e-2": [_execution(self._flagged_zero())],
        }
        with _executions_by_id(mapping):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1", "e-2")))

        # Something was measured, so the subtotal is published - flagged, but published.
        assert metrics.total_money_spent == 0.02
        assert metrics.money_spent_pending is True

    def test_a_measured_zero_is_not_swept_up_with_the_flagged_ones(self):
        """The counterpart the fix must not break: both flags false means the turn really was free."""
        measured = TokensUsage(input_tokens=5, output_tokens=1, money_spent=0.0)
        with _executions_by_id({"e-1": [_execution(measured)]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent == 0.0
        assert metrics.money_spent_pending is False
        assert metrics.money_spent_unavailable is False

    def test_the_wire_omits_the_figure_for_an_all_flagged_zero_workflow_conversation(self):
        """End of the chain: what the chat header is handed, through the endpoint's own response model."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from codemie.rest_api.routers.assistant import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[authenticate] = lambda: OWNER
        conversation = MagicMock(is_workflow_conversation=True)
        conversation.history = [MagicMock(execution_id="e-1")]
        with (
            patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
            _executions_by_id({"e-1": [_execution(self._flagged_zero())]}),
        ):
            response = TestClient(app).get("/v1/assistants/metrics/conv-1")

        assert response.status_code == 200
        payload = response.json()
        assert "total_money_spent" not in payload, "$0.0000 (awaiting final cost) is what the header showed"
        assert payload["money_spent_pending"] is True
        assert payload["money_spent_unavailable"] is False


class TestTheFlagLeavesTheEndpointOverHttp:
    """The end of the chain: FastAPI can only emit fields the response model declares.

    The two flags existed on every message and on `TokensUsage` for the whole of this refactor, and
    the UI has rendered them since - but `ConversationMetrics` declared neither, so the suffix code
    in `ChatHeader` was unreachable and a streamed conversation went out as `total_money_spent: 0.0`.
    """

    @staticmethod
    def _client():
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from codemie.rest_api.routers.assistant import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[authenticate] = lambda: OWNER
        return TestClient(app)

    @staticmethod
    def _metrics_of(*messages):
        from codemie.rest_api.models.conversation import Conversation, ConversationMetrics

        conversation = Conversation(conversation_id="conv-1", history=list(messages))
        metrics = ConversationMetrics(conversation_id="conv-1")
        metrics.calculate_metrics(conversation)
        return conversation, metrics

    @staticmethod
    def _an_assistant_message(money_spent, pending=False, unavailable=False):
        from codemie.rest_api.models.conversation import GeneratedMessage

        # Keyed: a pending turn with a request id is re-asked and, with the ledger still saying
        # pending, stays pending - the stored state is what the wire carries.
        return GeneratedMessage(
            role="Assistant",
            request_id="req-1",
            money_spent=money_spent,
            money_spent_pending=pending,
            money_spent_unavailable=unavailable,
        )

    def _get(self, stored):
        # The conversation the row was computed from: the endpoint re-reads it to hydrate.
        conversation, metrics = stored
        with (
            patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
            patch(
                "codemie.rest_api.models.conversation.ConversationMetrics.get_by_fields",
                return_value=metrics,
            ),
        ):
            response = self._client().get("/v1/assistants/metrics/conv-1")

        assert response.status_code == 200
        return response.json()

    def test_an_all_pending_conversation_sends_the_flag_and_no_figure(self):
        payload = self._get(self._metrics_of(self._an_assistant_message(0.0, pending=True)))

        assert "total_money_spent" not in payload, "$0.0000 is what the usage card used to show"
        assert payload["money_spent_pending"] is True
        assert payload["money_spent_unavailable"] is False

    def test_a_mixed_conversation_sends_the_subtotal_with_its_flag(self):
        payload = self._get(
            self._metrics_of(
                self._an_assistant_message(0.02),
                self._an_assistant_message(0.0, pending=True),
            )
        )

        assert payload["total_money_spent"] == 0.02
        assert payload["money_spent_pending"] is True

    def test_a_measured_zero_is_sent_as_a_zero_with_no_qualification(self):
        payload = self._get(self._metrics_of(self._an_assistant_message(0.0)))

        assert payload["total_money_spent"] == 0.0
        assert payload["money_spent_pending"] is False
        assert payload["money_spent_unavailable"] is False


class TestAPendingExecutionSettlesFromTheLedgerOnRead:
    """AD-28 rule 7 for the workflow twin: the execution row hydrates by its own execution id."""

    @staticmethod
    def _pending_execution(execution_id="e-1"):
        execution = _execution(TokensUsage(input_tokens=5, output_tokens=1, money_spent=0.0, money_spent_pending=True))
        execution.execution_id = execution_id
        return execution

    def test_an_execution_the_ledger_has_settled_is_returned_settled_and_saved_once(self, ledger):
        ledger.return_value = RequestCost.settled(Decimal("0.75"))
        execution = self._pending_execution()

        with _executions_by_id({"e-1": [execution]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent == 0.75
        assert metrics.money_spent_pending is False
        assert metrics.money_spent_unavailable is False
        assert execution.tokens_usage.money_spent == 0.75
        execution.update.assert_called_once_with(touch_timestamp=False)
        ledger.assert_awaited_once_with(request_id="e-1")

    def test_an_execution_the_ledger_still_lacks_stays_awaiting(self, ledger):
        execution = self._pending_execution()

        with _executions_by_id({"e-1": [execution]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.total_money_spent is None
        assert metrics.money_spent_pending is True
        execution.update.assert_not_called()

    def test_an_execution_with_no_usage_record_is_still_unavailable_and_never_asked(self, ledger):
        with _executions_by_id({"e-1": [_execution(None)]}):
            metrics = asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        assert metrics.money_spent_unavailable is True
        ledger.assert_not_awaited()

    def test_a_settled_execution_is_not_re_asked(self, ledger):
        settled = _execution(TokensUsage(input_tokens=5, output_tokens=1, money_spent=1.0))

        with _executions_by_id({"e-1": [settled]}):
            asyncio.run(_calculate_workflow_metrics("conv-1", _conversation("e-1")))

        ledger.assert_not_awaited()
        settled.update.assert_not_called()


class TestTheAssistantPathHydratesThroughTheConversationService:
    """The endpoint delegates; the rules live in `ConversationService.hydrate_conversation_metrics`.

    The wire shape is `ConversationMetricsOut`, built from whichever row the service handed back, so
    the money is read through the response rather than by identity with the row.
    """

    @staticmethod
    def _get(conversation, metrics, hydrated):
        from codemie.rest_api.routers.assistant import execution as assistant_router

        with (
            patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
            patch.object(assistant_router.ConversationMetrics, "get_by_fields", return_value=metrics),
            patch.object(
                assistant_router.ConversationService, "hydrate_conversation_metrics", new=AsyncMock()
            ) as hydrate,
        ):
            hydrate.return_value = hydrated
            result = asyncio.run(assistant_router.conversation_metrics(conversation_id="conv-1", user=OWNER))
        return result, hydrate

    def test_a_stored_row_is_hydrated_against_its_conversation(self):
        from codemie.rest_api.models.conversation import ConversationMetrics, ConversationMetricsOut

        conversation = MagicMock(is_workflow_conversation=False)
        conversation.get_total_classifier_cost_usd.return_value = 0.0
        metrics = ConversationMetrics(conversation_id="conv-1", money_spent_pending=True)
        hydrated = ConversationMetrics(
            conversation_id="conv-1",
            total_input_tokens=3,
            total_output_tokens=4,
            total_money_spent=0.5,
            money_spent_pending=False,
            money_spent_unavailable=False,
        )

        result, hydrate = self._get(conversation, metrics, hydrated)

        hydrate.assert_awaited_once_with(conversation, metrics)
        assert isinstance(result, ConversationMetricsOut)
        assert (result.total_input_tokens, result.total_output_tokens) == (3, 4)
        assert (result.total_money_spent, result.money_spent_pending, result.money_spent_unavailable) == (
            0.5,
            False,
            False,
        )

    def test_a_row_whose_conversation_is_gone_is_not_served(self):
        """Deleting a conversation leaves its metrics row; with no conversation to check the caller against, its
        totals would be readable by anyone signed in - so the read answers as for a conversation never priced."""
        from codemie.rest_api.models.conversation import ConversationMetrics

        metrics = ConversationMetrics(conversation_id="conv-1", total_money_spent=0.25, money_spent_pending=True)

        result, hydrate = self._get(None, metrics, hydrated=None)

        hydrate.assert_not_awaited()
        assert (result.total_money_spent, result.total_input_tokens, result.total_output_tokens) == (None, 0, 0)
