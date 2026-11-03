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

"""Unit tests for platform tool implementations."""

import asyncio
import json
import unittest
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, MagicMock, patch

import pytest

from codemie.configs import config as config_module
from codemie.agents.tools.platform import platform_tool
from codemie.agents.tools.platform.platform_tool import (
    GetKeySpendingTool,
    GetSpendingTool,
    _parse_date_filters,
    _extract_tools_invoked,
)
from codemie.core.exceptions import InvalidFilterCombinationError, UnauthorizedPlatformAccessError
from codemie.enterprise.loader import KeySpendingInfo, HAS_LITELLM
from codemie.rest_api.security.user import User
from codemie.service.conversation_service import SpendingAnalyticsResult
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.identity_bridge import ResolvedIdentity


# ==================== Helper Function Tests ====================


class TestParseDateFilters(unittest.TestCase):
    """Test suite for _parse_date_filters helper function"""

    def test_parse_date_filters_since_date(self):
        """Test _parse_date_filters with since_date parameter"""
        # Arrange
        since_date = "2024-01-01T00:00:00Z"
        last_n_days = None

        # Act
        result = _parse_date_filters(since_date, last_n_days)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(result.year, 2024)
        self.assertEqual(result.month, 1)
        self.assertEqual(result.day, 1)

    def test_parse_date_filters_last_n_days(self):
        """Test _parse_date_filters with last_n_days parameter"""
        # Arrange
        since_date = None
        last_n_days = 7

        # Act
        result = _parse_date_filters(since_date, last_n_days)

        # Assert
        self.assertIsNotNone(result)
        expected_date = datetime.now(timezone.utc) - timedelta(days=7)
        # Allow 1 second difference for test execution time
        self.assertAlmostEqual(result.timestamp(), expected_date.timestamp(), delta=1)
        # Ensure result is timezone-aware (UTC)
        self.assertIsNotNone(result.tzinfo)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_parse_date_filters_no_filter(self):
        """Test _parse_date_filters with no filters"""
        # Arrange
        since_date = None
        last_n_days = None

        # Act
        result = _parse_date_filters(since_date, last_n_days)

        # Assert
        self.assertIsNone(result)

    def test_parse_date_filters_both_raises_exception(self):
        """Test _parse_date_filters raises exception when both filters provided"""
        # Arrange
        since_date = "2024-01-01T00:00:00Z"
        last_n_days = 7

        # Act & Assert
        with self.assertRaises(InvalidFilterCombinationError):
            _parse_date_filters(since_date, last_n_days)


class TestExtractToolsInvoked(unittest.TestCase):
    """Test suite for _extract_tools_invoked helper function"""

    def test_extract_tools_invoked_full_mode_with_thoughts(self):
        """Test _extract_tools_invoked in full_mode with thoughts"""
        # Arrange
        msg = Mock()
        thought1 = Mock()
        thought1.author_name = "search_tool"
        thought1.input_text = "search query"

        thought2 = Mock()
        thought2.author_name = "calculator_tool"
        thought2.input_text = "2 + 2"

        msg.thoughts = [thought1, thought2]

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["tool_name"], "search_tool")
        self.assertEqual(result[0]["tool_input"], "search query")
        self.assertEqual(result[1]["tool_name"], "calculator_tool")
        self.assertEqual(result[1]["tool_input"], "2 + 2")

    def test_extract_tools_invoked_not_full_mode(self):
        """Test _extract_tools_invoked returns None when not in full_mode"""
        # Arrange
        msg = Mock()
        msg.thoughts = [Mock()]

        # Act
        result = _extract_tools_invoked(msg, full_mode=False)

        # Assert
        self.assertIsNone(result)

    def test_extract_tools_invoked_no_thoughts(self):
        """Test _extract_tools_invoked with no thoughts"""
        # Arrange
        msg = Mock()
        msg.thoughts = None

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNone(result)

    def test_extract_tools_invoked_empty_thoughts(self):
        """Test _extract_tools_invoked with empty thoughts list"""
        # Arrange
        msg = Mock()
        msg.thoughts = []

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNone(result)

    def test_extract_tools_invoked_thought_without_author_name(self):
        """Test _extract_tools_invoked skips thoughts without author_name"""
        # Arrange
        msg = Mock()
        thought1 = Mock()
        thought1.author_name = "valid_tool"
        thought1.input_text = "input"

        thought2 = Mock()
        thought2.author_name = None  # No author_name

        msg.thoughts = [thought1, thought2]

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_name"], "valid_tool")

    def test_extract_tools_invoked_thought_without_input_text(self):
        """Test _extract_tools_invoked handles thought without input_text attribute"""
        # Arrange
        msg = Mock()
        thought = Mock()
        thought.author_name = "tool_name"
        # Don't set input_text attribute
        del thought.input_text

        msg.thoughts = [thought]

        # Act
        result = _extract_tools_invoked(msg, full_mode=True)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["tool_name"], "tool_name")
        self.assertIsNone(result[0]["tool_input"])


# ==================== get_spending: one composed port read ====================

MODULE = "codemie.agents.tools.platform.platform_tool"

#: A window the port accepts, used wherever a test needs to name one.
PERIOD = TimeRange(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc))

RESOLVED = ResolvedIdentity(user_id="user-1", aliases=frozenset({"alice", "alice@example.com"}))
UNRESOLVABLE = ResolvedIdentity(user_id=None, aliases=None)


def _run_now(coro, *, timeout=30.0, on_timeout="operation"):
    """Stand-in for `run_coroutine_blocking` that runs the coroutine here and now."""
    return asyncio.run(coro)


def _timed_out(coro, *, timeout=30.0, on_timeout="operation"):
    """Stand-in for a `run_coroutine_blocking` that timed out: no value, and it does **not** raise."""
    coro.close()
    return None


def _money_read_timed_out(coro, *, timeout=30.0, on_timeout="operation"):
    """Every lookup answers and every money read times out."""
    if "spend read" in on_timeout:
        return _timed_out(coro)
    return _run_now(coro)


def _owner_lookup_timed_out(coro, *, timeout=30.0, on_timeout="operation"):
    """The personal-project owner lookup times out and everything else answers."""
    if "owner lookup" in on_timeout:
        return _timed_out(coro)
    return _run_now(coro)


@asynccontextmanager
async def _fake_async_session():
    yield object()


def _activity(total_conversations=3, total_workflow_executions=2, breakdown=None):
    """The two services' activity answers, with no money total on either - there is none to give."""
    conversation_result = SpendingAnalyticsResult(
        total_input_tokens=1000,
        total_output_tokens=500,
        total_conversations=total_conversations,
        spending_breakdown=breakdown or [],
    )
    workflow_data = {
        "total_input_tokens": 200,
        "total_output_tokens": 100,
        "total_workflow_executions": total_workflow_executions,
        "breakdown": [],
    }
    return conversation_result, workflow_data


class _SpendHarness:
    """Patches every seam `get_spending` reaches outside the tool itself."""

    def __init__(self, aggregate_composed, identity=RESOLVED, bridge=_run_now, project_owners=frozenset()):
        self.service = MagicMock()
        self.service.aggregate_composed = aggregate_composed
        # The platform total is never the answer to a scoped question, so an `aggregate` call is a
        # test failure rather than a different result.
        self.service.aggregate = AsyncMock(side_effect=AssertionError("aggregate(period, None) must not be called"))
        self.service.aggregate_by_dimension = AsyncMock(
            side_effect=AssertionError("aggregate_by_dimension drops the report's own filters")
        )
        self.conversation_result, self.workflow_data = _activity()
        self._patches = [
            patch(f"{MODULE}.spend_service", self.service),
            patch(f"{MODULE}.run_coroutine_blocking", bridge),
            patch(f"{MODULE}.get_async_session", _fake_async_session),
            patch(f"{MODULE}.resolve_identity", AsyncMock(return_value=identity)),
            # The owners of the personal projects the call names: none for a shared project.
            patch(f"{MODULE}._personal_project_owners", AsyncMock(return_value=project_owners)),
        ]
        self._readers = {
            "conversation_reader": patch(
                "codemie.service.conversation_service.ConversationService.get_spending_analytics",
                return_value=self.conversation_result,
            ),
            "workflow_reader": patch(
                "codemie.service.workflow_service.WorkflowService.get_workflow_spending_analytics",
                return_value=self.workflow_data,
            ),
        }

    def __enter__(self):
        for item in self._patches:
            item.start()
        for name, item in self._readers.items():
            setattr(self, name, item.start())
        self.resolve_identity = platform_tool.resolve_identity
        return self

    def __exit__(self, *exc):
        for item in reversed([*self._patches, *self._readers.values()]):
            item.stop()
        return False


#: An administrator for `_validate_user_permissions`, admin under **every** config branch.
#:
#: `is_admin=True` alone is not enough and the failure is invisible until the suite is run in full:
#: `User.resolve_is_admin` runs at construction and, when `ENABLE_USER_MANAGEMENT` is off and `ENV` is
#: not `local`, **recomputes** the flag from `ADMIN_USER_ID`/roles - overwriting the `True` passed here
#: with `False` for a user holding no roles. Alone, these tests passed only because `config.ENV` is
#: `local` on a dev machine; in a full run an earlier test sets `ENV` elsewhere and every one of them
#: failed with `UnauthorizedPlatformAccessError`. Carrying the admin role satisfies the legacy branch
#: too, so all three paths resolve to admin and the fixture no longer depends on ambient config.
ADMIN = User(id="admin-user", name="Admin", username="admin", is_admin=True, roles=[config_module.ADMIN_ROLE_NAME])


class TestTheSpendingTotalIsOneComposedReadAndNotTwoFiguresAdded(unittest.TestCase):
    """AD-29: the conversation figure was added to the workflow figure, on the surface an assistant
    quotes to a person. A sum of two independently floored figures is not a figure."""

    def test_the_addition_helper_no_longer_exists(self):
        # The shape itself is gone, not merely repointed: nothing can add two spend figures here.
        self.assertFalse(hasattr(platform_tool, "_calculate_total_spending"))
        self.assertTrue(hasattr(platform_tool, "_calculate_total_tokens"))

    def test_neither_service_returns_a_money_total_to_add(self):
        from codemie.service.workflow_service import WorkflowService

        self.assertNotIn("total_money_spent", SpendingAnalyticsResult.model_fields)
        with (
            patch.object(WorkflowService, "_build_workflow_filters", return_value=[]),
            patch("codemie.service.workflow_service.Session"),
            patch.object(
                WorkflowService,
                "_get_workflow_totals",
                return_value={"total_input_tokens": 1, "total_output_tokens": 2, "total_workflow_executions": 3},
            ),
        ):
            answer = WorkflowService.get_workflow_spending_analytics()
        self.assertNotIn("total_money_spent", answer)

    def test_one_composed_read_answers_the_whole_conjunction(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("42.5")))
        with _SpendHarness(composed) as harness:
            parsed = json.loads(
                GetSpendingTool(user=ADMIN).execute(
                    user_name="Alice", workflow_id="wf-1", project="acme", last_n_days=7
                )
            )

        # Exactly one money read for the total, over one scope carrying every term at once.
        composed.assert_awaited_once()
        _period, scope = composed.await_args.args
        self.assertEqual(scope.user.user_id, "user-1")
        self.assertEqual(scope.user.aliases, RESOLVED.aliases)
        self.assertEqual(scope.project, "acme")
        self.assertEqual(scope.dimension.dimension, SpendDimension.WORKFLOW)
        self.assertEqual(scope.dimension.value, "wf-1")
        self.assertEqual(parsed["total_money_spent"], 42.5)
        # Tokens still add - they are counts, not money.
        self.assertEqual(parsed["total_input_tokens"], 1200)
        self.assertEqual(parsed["total_output_tokens"], 600)
        harness.service.aggregate.assert_not_awaited()

    def test_an_assistant_id_becomes_the_assistant_dimension(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))
        with _SpendHarness(composed):
            GetSpendingTool(user=ADMIN).execute(user_name="Alice", assistant_id="as-1")

        _period, scope = composed.await_args.args
        self.assertEqual(scope.dimension.dimension, SpendDimension.ASSISTANT)
        self.assertEqual(scope.dimension.value, "as-1")

    def test_two_dimension_filters_at_once_are_refused_rather_than_added(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))
        with _SpendHarness(composed):
            with self.assertRaises(InvalidFilterCombinationError):
                GetSpendingTool(user=ADMIN).execute(user_name="Alice", assistant_id="as-1", workflow_id="wf-1")
        composed.assert_not_awaited()


class TestASpendQuestionWithNoSubjectIsNotAnsweredWithThePlatformTotal(unittest.TestCase):
    """`ComposedScope` refuses an all-absent scope for this exact call site: four `Optional[str]`s an
    agent may omit, and a scope built from four `None`s would return the platform total in place of
    one person's spend, silently, with an assistant quoting it to that person."""

    def test_an_all_absent_filter_set_is_refused(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("999999")))
        with _SpendHarness(composed) as harness:
            with self.assertRaises(InvalidFilterCombinationError):
                GetSpendingTool(user=ADMIN).execute()

            # Neither the composed read nor the platform total is issued: the question is not
            # answerable as asked, and "everyone" is not a reading of it.
            composed.assert_not_awaited()
            harness.service.aggregate.assert_not_awaited()

    def test_a_date_filter_alone_is_still_no_subject(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("999999")))
        with _SpendHarness(composed):
            with self.assertRaises(InvalidFilterCombinationError):
                GetSpendingTool(user=ADMIN).execute(last_n_days=7)
        composed.assert_not_awaited()

    def test_the_refusal_names_the_filters_that_would_answer_it(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))
        with _SpendHarness(composed):
            with self.assertRaises(InvalidFilterCombinationError) as caught:
                GetSpendingTool(user=ADMIN).execute()
        message = str(caught.exception)
        for filter_name in ("user_name", "project", "assistant_id", "workflow_id"):
            self.assertIn(filter_name, message)

    def test_a_project_alone_is_a_subject_and_is_answered(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("7")))
        with _SpendHarness(composed):
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(project="acme"))
        _period, scope = composed.await_args.args
        self.assertEqual(scope.project, "acme")
        self.assertIsNone(scope.user)
        self.assertEqual(parsed["total_money_spent"], 7.0)


class TestAnAbsentFigureReachesTheAgentQualifiedRatherThanAsZero(unittest.TestCase):
    """AD-23/AD-33: an absent figure is never a zero, and a qualified figure carries its
    qualification all the way onto the wire the assistant reads."""

    def test_no_figure_is_null_and_flagged_unavailable(self):
        composed = AsyncMock(return_value=SpendResult.no_figure(PERIOD))
        with _SpendHarness(composed):
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="Alice"))

        self.assertIsNone(parsed["total_money_spent"])
        self.assertNotEqual(parsed["total_money_spent"], 0.0)
        self.assertTrue(parsed["spend_unavailable"])

    def test_a_dimension_hole_is_named_but_not_sized(self):
        # The residue is the platform's unbucketed money for the dimension, not narrowed to the caller,
        # so sizing it would publish other people's spend.
        composed = AsyncMock(
            return_value=SpendResult(
                figure=Decimal("10"),
                partial={SpendDimension.ASSISTANT: Portion(spend=Decimal("2.5"), rows=4)},
            )
        )
        with _SpendHarness(composed):
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="Alice", assistant_id="as-1"))

        self.assertEqual(parsed["total_money_spent"], 10.0)
        self.assertTrue(parsed["spend_partial"])
        self.assertIsNone(parsed["spend_missing_amount"])

    def test_a_project_hole_is_named_but_not_sized(self):
        # E2E pass H: a person's spend in a project created that day read "$136,040.59 unattributed" -
        # the platform's project-less money below the identity floor, which is no one project's
        # (EPMCDME-15237). The figure still says it is partial; it no longer sizes that hole.
        composed = AsyncMock(
            return_value=SpendResult(
                figure=Decimal("0.0015684"),
                partial={SpendDimension.PROJECT: Portion(spend=Decimal("136040.59"), rows=9)},
            )
        )
        with _SpendHarness(composed):
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="Alice", project="acme"))

        self.assertEqual(parsed["total_money_spent"], 0.0015684)
        self.assertTrue(parsed["spend_partial"])
        self.assertIsNone(parsed["spend_missing_amount"])

    def test_a_timeout_degrades_the_figure_instead_of_raising(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("10")))
        with _SpendHarness(composed, bridge=_money_read_timed_out):
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="Alice", project="acme"))

        self.assertIsNone(parsed["total_money_spent"])
        self.assertTrue(parsed["spend_unavailable"])
        # The rest of the response still answers: a slow ledger degrades a figure, not a request.
        self.assertEqual(parsed["total_input_tokens"], 1200)

    def test_an_unidentifiable_user_name_is_absent_and_never_widened(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("500")))
        with _SpendHarness(composed, identity=UNRESOLVABLE):
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="ghost", project="acme"))

        # Dropping the unresolvable user term would have answered for the whole of `acme`.
        composed.assert_not_awaited()
        self.assertIsNone(parsed["total_money_spent"])
        self.assertTrue(parsed["spend_unavailable"])

    def test_a_window_the_port_cannot_read_yields_no_figure(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("500")))
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        with _SpendHarness(composed):
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="Alice", since_date=future))

        composed.assert_not_awaited()
        self.assertIsNone(parsed["total_money_spent"])
        self.assertTrue(parsed["spend_unavailable"])


# ==================== Tool Tests ====================


class TestTheActivityNamesTheSamePersonAsTheMoney(unittest.TestCase):
    """UA-H-D1 (pass H): the money was read for the resolved person while the counts matched the raw
    `user_name` against stored display names, so one answer described two different populations."""

    def test_both_activity_reads_get_the_resolved_person_and_no_raw_name(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("36")))
        with _SpendHarness(composed) as harness:
            GetSpendingTool(user=ADMIN).execute(user_name="alice@example.com", project="acme")

        for reader in (harness.conversation_reader, harness.workflow_reader):
            reader.assert_called_once()
            self.assertNotIn("user_name", reader.call_args.kwargs)
            self.assertEqual(reader.call_args.kwargs["user_term"].user_id, "user-1")
            self.assertEqual(reader.call_args.kwargs["project"], "acme")

    def test_an_unidentifiable_user_name_reads_no_activity_and_reports_every_figure_absent(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("500")))
        with _SpendHarness(composed, identity=UNRESOLVABLE) as harness:
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="ghost", project="acme"))

        # Reading the activity without the user term would have counted everybody in `acme`.
        harness.conversation_reader.assert_not_called()
        harness.workflow_reader.assert_not_called()
        composed.assert_not_awaited()
        self.assertIsNone(parsed["total_money_spent"])
        self.assertTrue(parsed["spend_unavailable"])
        for count in ("total_input_tokens", "total_output_tokens", "total_conversations", "total_workflow_executions"):
            self.assertIsNone(parsed[count], count)
        self.assertEqual(parsed["spending_breakdown"], [])

    def test_a_timed_out_identity_lookup_is_absent_and_never_a_zero(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("500")))
        with _SpendHarness(composed, bridge=_timed_out) as harness:
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="Alice"))

        harness.conversation_reader.assert_not_called()
        harness.workflow_reader.assert_not_called()
        self.assertIsNone(parsed["total_conversations"])
        self.assertTrue(parsed["spend_unavailable"])

    def test_the_callers_own_name_is_their_account_without_a_lookup(self):
        # In any case and by any of the three identifiers: the session already holds the account, so no
        # lookup can land on another account sharing the display name.
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))
        for own_name in ("ADMIN", "admin", "Admin"):
            with self.subTest(own_name=own_name), _SpendHarness(composed) as harness:
                GetSpendingTool(user=ADMIN).execute(user_name=own_name)

                harness.resolve_identity.assert_not_awaited()
                self.assertEqual(harness.conversation_reader.call_args.kwargs["user_term"].user_id, "admin-user")
                _period, scope = composed.await_args.args
                self.assertEqual(scope.user.user_id, "admin-user")

    def test_the_callers_own_name_is_answered_where_no_users_row_exists(self):
        # Legacy-JWT mode writes no `users` rows, so a lookup finds nobody; the caller's own question is
        # still answered from their session's account rather than reported absent.
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("3")))
        with _SpendHarness(composed, identity=UNRESOLVABLE) as harness:
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(user_name="Admin"))

        harness.workflow_reader.assert_called_once()
        self.assertEqual(parsed["total_money_spent"], 3.0)
        self.assertEqual(parsed["total_conversations"], 3)


@contextmanager
def _user_managed_config():
    """A config under which a `User` keeps the `is_admin` it is given.

    Validation makes every user an admin when `ENV` is `local` (see `ADMIN`), and the tool's model validates
    its user again, so a non-admin caller exists only while `ENV` is not `local` and users are managed.
    """
    with (
        patch.object(config_module, "ENV", "dev"),
        patch.object(config_module, "ENABLE_USER_MANAGEMENT", True),
    ):
        yield


class TestAPersonalProjectIsReadThroughItsOwner(unittest.TestCase):
    """UA-H-D3 (pass H): a personal project's calls carry no project in the ledger, so the project term
    alone answered a measured $0.00 for every personal project while its activity was counted."""

    def test_the_total_and_both_activity_reads_get_the_projects_owners(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("13")))
        with _SpendHarness(composed, project_owners={"owner-1"}) as harness:
            parsed = json.loads(GetSpendingTool(user=ADMIN).execute(project="bob@example.com"))

        for reader in (harness.conversation_reader, harness.workflow_reader):
            self.assertEqual(reader.call_args.kwargs["project_owners"], ("owner-1",))
        _period, scope = composed.await_args.args
        self.assertEqual((scope.project, scope.project_owners), ("bob@example.com", ("owner-1",)))
        self.assertEqual(parsed["total_money_spent"], 13.0)

    def test_a_shared_project_carries_no_owner(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("2")))
        with _SpendHarness(composed) as harness:
            GetSpendingTool(user=ADMIN).execute(project="acme")

        self.assertEqual(harness.conversation_reader.call_args.kwargs["project_owners"], ())
        _period, scope = composed.await_args.args
        self.assertEqual(scope.project_owners, ())

    def test_an_owner_nobody_can_name_reports_every_figure_absent(self):
        # Read by its name alone, a personal project would answer a measured zero; unknown is absent.
        for case, harness_args in (
            ("no recorded owner", {"project_owners": {None}}),
            ("timed-out lookup", {"bridge": _owner_lookup_timed_out}),
        ):
            composed = AsyncMock(return_value=SpendResult(figure=Decimal("2")))
            with self.subTest(case), _SpendHarness(composed, **harness_args) as harness:
                parsed = json.loads(GetSpendingTool(user=ADMIN).execute(project="bob@example.com"))

            harness.conversation_reader.assert_not_called()
            composed.assert_not_awaited()
            self.assertIsNone(parsed["total_money_spent"])
            self.assertTrue(parsed["spend_unavailable"])
            self.assertIsNone(parsed["total_conversations"])

    def test_no_project_asks_no_owner_lookup(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))
        with _SpendHarness(composed):
            GetSpendingTool(user=ADMIN).execute(user_name="Admin")

            platform_tool._personal_project_owners.assert_not_awaited()

    def test_another_users_personal_project_is_refused_to_a_non_admin(self):
        """A project named like the caller's username passes `_validate_user_permissions`, and it is
        another person's personal project wherever their email is that username."""
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("13")))
        with _user_managed_config(), _SpendHarness(composed, project_owners={"owner-1"}):
            caller = User(id="zed", name="Zed", username="bob@example.com")
            with self.assertRaises(UnauthorizedPlatformAccessError):
                GetSpendingTool(user=caller).execute(project="bob@example.com")

        composed.assert_not_awaited()

    def test_the_owner_reads_their_own_personal_project(self):
        composed = AsyncMock(return_value=SpendResult(figure=Decimal("13")))
        with _user_managed_config(), _SpendHarness(composed, project_owners={"owner-1"}):
            caller = User(id="owner-1", name="Bob", username="bob@example.com")
            parsed = json.loads(GetSpendingTool(user=caller).execute(project="bob@example.com"))

        self.assertEqual(parsed["total_money_spent"], 13.0)


@pytest.mark.skipif(not HAS_LITELLM, reason="Enterprise package not installed - LiteLLM not available")
class TestGetKeySpendingTool(unittest.TestCase):
    """Test suite for GetKeySpendingTool"""

    def setUp(self):
        """Set up test fixtures"""
        self.admin_user = User(id="admin-user", name="Admin", username="admin", roles=["admin"])
        self.regular_user = User(id="regular-user", name="Regular", username="regular", roles=[])

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_admin_all_keys(self):
        """Test GetKeySpendingTool execute for admin user requesting all keys"""
        # Arrange
        tool = GetKeySpendingTool(user=self.admin_user)

        mock_keys = [
            KeySpendingInfo(spend=100.0, key_alias="key1"),
            KeySpendingInfo(spend=200.0, key_alias="key2"),
        ]

        with patch("codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_available.return_value = True
            mock_provider.get_all_keys_spending.return_value = mock_keys
            mock_get_provider.return_value = mock_provider

            # Act
            result = tool.execute(key_aliases=None, include_details=True, page=1, size=100)

            # Assert
            # After the fix, we always pass include_details=True to fetch full data
            mock_provider.get_all_keys_spending.assert_called_once_with(include_details=True, page=1, size=100)
            self.assertIsNotNone(result)
            # Result is JSON string, parse to verify
            import json

            parsed = json.loads(result)
            self.assertEqual(parsed["total_keys"], 2)
            self.assertEqual(parsed["total_spend_across_keys"], 300.0)
            self.assertEqual(len(parsed["keys"]), 2)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_admin_specific_keys(self):
        """Test GetKeySpendingTool execute for admin user requesting specific keys"""
        # Arrange
        tool = GetKeySpendingTool(user=self.admin_user)

        mock_keys = [
            KeySpendingInfo(spend=100.0, key_alias="key1"),
        ]

        with patch("codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_available.return_value = True
            mock_provider.get_keys_info_by_alias.return_value = mock_keys
            mock_get_provider.return_value = mock_provider

            # Act
            result = tool.execute(key_aliases=["key1"], include_details=False, page=1, size=50)

            # Assert
            # After the fix, we always pass include_details=True to fetch full data
            mock_provider.get_keys_info_by_alias.assert_called_once_with(
                ["key1"], include_details=True, page=1, size=50
            )
            self.assertIsNotNone(result)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_non_admin_raises_exception(self):
        """Test GetKeySpendingTool execute raises exception for non-admin user"""
        # Arrange
        tool = GetKeySpendingTool(user=self.regular_user)

        # Act & Assert
        with self.assertRaises(UnauthorizedPlatformAccessError):
            tool.execute(key_aliases=None)

    @patch("codemie.rest_api.security.user.config.ENV", "production")
    def test_execute_with_custom_pagination(self):
        """Test GetKeySpendingTool execute with custom pagination parameters"""
        # Arrange
        tool = GetKeySpendingTool(user=self.admin_user)

        mock_keys = []

        with patch("codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.is_available.return_value = True
            mock_provider.get_all_keys_spending.return_value = mock_keys
            mock_get_provider.return_value = mock_provider

            # Act
            result = tool.execute(key_aliases=None, include_details=False, page=2, size=25)

            # Assert
            # After the fix, we always pass include_details=True to fetch full data
            mock_provider.get_all_keys_spending.assert_called_once_with(include_details=True, page=2, size=25)
            self.assertIsNotNone(result)

            import json

            parsed = json.loads(result)
            self.assertEqual(parsed["total_keys"], 0)
            self.assertEqual(parsed["total_spend_across_keys"], 0.0)


class TestConversationMetricsSummaryQualifiesItsTotal:
    """AD-23/AD-33 on the summary an assistant quotes: a subtotal must not look like a total.

    `total_money_spent += metric.total_money_spent or 0.0` made a conversation whose cost has not
    settled contribute a **zero**, so the summary of ten conversations of which two had settled looked
    exactly like the summary of ten that had all settled and cost that much.
    """

    @staticmethod
    def _item(money):
        metric = MagicMock()
        metric.number_of_messages = 1
        metric.total_input_tokens = 10
        metric.total_output_tokens = 5
        metric.total_money_spent = money
        metric.avg_response_time = 0.0
        metric.project = "p"
        metric.conversation_id = "conv-1"
        metric.user_name = "someone"
        metric.update_date = None
        item = MagicMock()
        item.metric = metric
        item.assistant_ids = []
        return item

    def _summary(self, monies):
        result = MagicMock()
        result.total_count = len(monies)
        result.metrics_with_assistants = [self._item(one) for one in monies]
        summary, _ = platform_tool._calculate_metrics_summary(result)
        return summary

    def test_nothing_settled_is_no_figure_and_never_a_zero(self):
        summary = self._summary([None, None])
        assert summary.total_money_spent is None
        assert summary.spend_unavailable is True
        assert summary.spend_partial is False

    def test_a_mix_is_the_settled_subtotal_marked_partial(self):
        summary = self._summary([1.50, None, 2.25])
        assert summary.total_money_spent == 3.75
        assert summary.spend_partial is True
        assert summary.spend_unavailable is False

    def test_everything_settled_is_an_exact_unqualified_total(self):
        summary = self._summary([1.00, 2.00])
        assert summary.total_money_spent == 3.00
        assert summary.spend_partial is False
        assert summary.spend_unavailable is False

    def test_a_measured_zero_stays_a_zero_and_stays_unqualified(self):
        """A conversation that really did cost nothing is not the same as one nobody has priced."""
        summary = self._summary([0.0, 0.0])
        assert summary.total_money_spent == 0.0
        assert summary.spend_unavailable is False
        assert summary.spend_partial is False

    def test_an_unsettled_conversation_row_carries_no_fabricated_zero(self):
        result = MagicMock()
        result.total_count = 1
        result.metrics_with_assistants = [self._item(None)]
        _, outputs = platform_tool._calculate_metrics_summary(result)
        assert outputs[0].total_money_spent is None


def _caller(*, is_admin=False, name="Alice", username="alice", admin_projects=()):
    """A `User` double for `_validate_user_permissions`, built rather than constructed.

    The real `User` recomputes `is_admin` at construction from ambient config (see the `ADMIN`
    fixture above), which would make every non-admin case here depend on the environment. A spec'd
    mock pins the four attributes the validator actually reads and nothing else.
    """
    user = MagicMock(spec=User)
    user.is_admin = is_admin
    user.name = name
    user.username = username
    user.admin_project_names = list(admin_projects)
    return user


class TestValidateUserPermissions:
    """An omitted `user_name` is not the caller's own user.

    Rule 4 used to read omission as "own user" and allow it, but omission is no user filter at all:
    the read it waves through covers the whole project, or the whole platform. The rules below are
    the ones the docstring always claimed - "project and own user" - now that omission falls
    through to the administrator checks instead of past them.
    """

    @pytest.mark.parametrize(
        "user, project, target_user_name",
        [
            pytest.param(_caller(is_admin=True), None, None, id="admin-needs-no-scope"),
            pytest.param(_caller(is_admin=True), "acme", "Bob", id="admin-reads-anyone"),
            pytest.param(_caller(), "alice", None, id="project-equal-to-own-username"),
            pytest.param(_caller(), "acme", "Alice", id="project-and-own-name"),
            pytest.param(_caller(), "acme", "ALICE", id="own-name-is-case-insensitive"),
            pytest.param(_caller(admin_projects=["acme"]), "acme", "Bob", id="project-admin-reads-others"),
            pytest.param(_caller(admin_projects=["acme"]), "acme", None, id="project-admin-reads-whole-project"),
            pytest.param(_caller(), None, "Alice", id="own-name-needs-no-project"),
        ],
    )
    def test_allowed(self, user, project, target_user_name):
        platform_tool._validate_user_permissions(user, project, target_user_name)

    @pytest.mark.parametrize(
        "user, project, target_user_name, expected_message",
        [
            pytest.param(
                _caller(),
                "acme",
                "Bob",
                "admin permissions to access project",
                id="other-name-in-a-project-not-administered",
            ),
            pytest.param(
                _caller(),
                "acme",
                None,
                "admin permissions to access project",
                id="omitted-name-is-the-whole-project",
            ),
            pytest.param(
                _caller(),
                "acme",
                "",
                "admin permissions to access project",
                id="empty-name-is-no-name-at-all",
            ),
            pytest.param(
                _caller(),
                None,
                "Bob",
                "not an application admin",
                id="other-name-unscoped-with-no-projects-administered",
            ),
            pytest.param(
                _caller(admin_projects=["acme", "globex"]),
                None,
                "Bob",
                "must specify a project",
                id="other-name-unscoped-is-told-to-scope-it",
            ),
            pytest.param(
                _caller(),
                None,
                None,
                "not an application admin",
                id="omitted-name-unscoped-is-the-whole-platform",
            ),
            pytest.param(
                _caller(admin_projects=["acme"]),
                None,
                "",
                "must specify a project",
                id="empty-name-unscoped-is-the-whole-platform",
            ),
        ],
    )
    def test_refused(self, user, project, target_user_name, expected_message):
        with pytest.raises(UnauthorizedPlatformAccessError) as caught:
            platform_tool._validate_user_permissions(user, project, target_user_name)
        assert expected_message in str(caught.value)


# ==================== Assistant Transformation Tests ====================


class TestTransformAssistant:
    """Test assistant transformation with project validation."""

    def test_transform_assistant_with_valid_project(self):
        """Test transformation succeeds with valid project."""
        from codemie.agents.tools.platform.platform_tool import _transform_assistant

        assistant = MagicMock()
        assistant.id = "asst-123"
        assistant.name = "Test Assistant"
        assistant.description = "Test description"
        assistant.system_prompt = "Test prompt"
        assistant.project = "valid-project"
        assistant.created_by = None
        assistant.created_date = None
        assistant.update_date = None
        assistant.llm_model_type = "gpt-4"

        result = _transform_assistant(assistant)

        assert result.project == "valid-project"
        assert result.name == "Test Assistant"

    def test_transform_assistant_falls_back_for_none_project(self):
        """Regression for CR-003: a legacy assistant with project=None must use the
        'legacy_migration' fallback instead of crashing -- the function's own docstring
        documents this, but the computed fallback used to be discarded in favor of a
        second, unguarded require_valid_project(assistant.project) call."""
        from codemie.agents.tools.platform.platform_tool import _transform_assistant

        assistant = MagicMock()
        assistant.id = "asst-456"
        assistant.name = "No Project Assistant"
        assistant.description = "Test description"
        assistant.system_prompt = "Test prompt"
        assistant.project = None
        assistant.created_by = None
        assistant.created_date = None
        assistant.update_date = None
        assistant.llm_model_type = "gpt-4"

        result = _transform_assistant(assistant)

        assert result.project == "legacy_migration"

    def test_transform_assistant_falls_back_for_empty_project(self):
        """Same fallback as the None case, for an empty-string project."""
        from codemie.agents.tools.platform.platform_tool import _transform_assistant

        assistant = MagicMock()
        assistant.id = "asst-789"
        assistant.name = "Empty Project Assistant"
        assistant.description = "Test description"
        assistant.system_prompt = "Test prompt"
        assistant.project = ""
        assistant.created_by = None
        assistant.created_date = None
        assistant.update_date = None
        assistant.llm_model_type = "gpt-4"

        result = _transform_assistant(assistant)

        assert result.project == "legacy_migration"
