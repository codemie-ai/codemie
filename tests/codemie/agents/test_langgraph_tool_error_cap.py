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

import uuid
from unittest.mock import Mock, patch

import pytest

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.core.exceptions import MaxConsecutiveToolErrorsExceededException
from codemie.core.errors import AgentErrorClassifier, ErrorCode
from codemie.configs import config


def _make_agent():
    agent = object.__new__(LangGraphAgent)
    agent._consecutive_tool_error_count = {}
    agent._inflight_tool_run_ids = {}
    agent._max_consecutive_tool_errors_cache = None
    agent._callback_bridge = Mock()
    return agent


def test_on_tool_error_increments_streak_counter():
    agent = _make_agent()
    agent._on_tool_error("boom", run_id=uuid.uuid4())
    assert agent._consecutive_tool_error_count[None] == 1


def test_on_tool_end_resets_streak_counter():
    agent = _make_agent()
    agent._consecutive_tool_error_count[None] = 3
    agent._on_tool_end("ok", run_id=uuid.uuid4())
    assert agent._consecutive_tool_error_count[None] == 0


def test_on_tool_error_raises_at_configured_cap():
    agent = _make_agent()
    for _ in range(config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS - 1):
        agent._on_tool_error("boom", run_id=uuid.uuid4())

    with pytest.raises(MaxConsecutiveToolErrorsExceededException) as exc_info:
        agent._on_tool_error("boom", run_id=uuid.uuid4())

    classified = AgentErrorClassifier().classify(exc_info.value)
    assert classified.agent_error.error_code == ErrorCode.AGENT_TOOL_ERROR_LIMIT


def test_on_tool_error_delegates_below_the_cap():
    agent = _make_agent()
    agent._on_tool_error("boom", run_id=uuid.uuid4())
    agent._callback_bridge.on_tool_error.assert_called_once()


def test_on_tool_error_streak_is_isolated_per_author():
    """AC6: each subagent (author) tracks its own independent consecutive-error
    streak; unrelated single errors from a different author must not accumulate
    onto the same counter (regression for CR-002)."""
    agent = _make_agent()
    for _ in range(config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS - 1):
        agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_a")

    # A single error from a different author must not push agent_a's streak over the cap.
    agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_b")

    assert agent._consecutive_tool_error_count["agent_a"] == config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS - 1
    assert agent._consecutive_tool_error_count["agent_b"] == 1


def test_on_tool_end_resets_only_that_authors_streak():
    """A tool success from one subagent must not reset another subagent's streak
    (regression for CR-002)."""
    agent = _make_agent()
    agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_a")
    agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_b")

    agent._on_tool_end("ok", run_id=uuid.uuid4(), author="agent_b")

    assert agent._consecutive_tool_error_count["agent_a"] == 1
    assert agent._consecutive_tool_error_count["agent_b"] == 0


def test_on_tool_error_raises_only_for_the_author_that_hit_the_cap():
    """A subagent that itself never reached N consecutive errors must not have the
    run aborted just because a different subagent independently hit the cap on its
    own errors interleaved in between (regression for CR-002)."""
    agent = _make_agent()
    for _ in range(config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS - 1):
        agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_a")
        # agent_b only ever errors once at a time (its own streak resets via a success).
        agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_b")
        agent._on_tool_end("ok", run_id=uuid.uuid4(), author="agent_b")

    with pytest.raises(MaxConsecutiveToolErrorsExceededException):
        agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_a")


def test_on_tool_error_finalizes_the_triggering_thought_before_aborting():
    """The tool call that trips the cap must still have its Thought finalized
    (sent to the client as errored, not left in-progress) via the callback
    bridge, even though the cap raises immediately afterward (regression for
    CR-003)."""
    agent = _make_agent()
    for _ in range(config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS - 1):
        agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_a")

    triggering_run_id = uuid.uuid4()
    with pytest.raises(MaxConsecutiveToolErrorsExceededException):
        agent._on_tool_error("boom", run_id=triggering_run_id, author="agent_a")

    assert agent._callback_bridge.on_tool_error.call_count == config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS
    agent._callback_bridge.on_tool_error.assert_called_with("boom", run_id=triggering_run_id, author="agent_a")


def test_on_tool_error_cap_is_sourced_from_dynamic_config():
    """F9: the cap must come from DynamicConfigService at call time, not the
    hardcoded config default — an override raised above the static default
    must NOT trip early, proving the static value alone is not what's read."""
    agent = _make_agent()
    dynamic_cap = config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS + 2

    with patch(
        "codemie.agents.langgraph_agent.DynamicConfigService.get_typed_value_safe",
        return_value=dynamic_cap,
    ):
        for _ in range(config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS + 1):
            agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_a")

        with pytest.raises(MaxConsecutiveToolErrorsExceededException):
            agent._on_tool_error("boom", run_id=uuid.uuid4(), author="agent_a")


def test_get_max_consecutive_tool_errors_caches_after_first_call():
    """Round-2 CR-002: the dynamic-config lookup for the tool-error cap must be
    cached for the lifetime of the agent instance -- a chatty agent or an error
    burst must not re-open a DB session on every single tool failure."""
    agent = _make_agent()

    with patch(
        "codemie.agents.langgraph_agent.DynamicConfigService.get_typed_value_safe",
        return_value=7,
    ) as mock_get:
        agent._get_max_consecutive_tool_errors()
        agent._get_max_consecutive_tool_errors()
        agent._get_max_consecutive_tool_errors()

    mock_get.assert_called_once()


def test_get_max_consecutive_tool_errors_clamps_to_floor_of_one():
    """Round-2 CR-003: a misconfigured dynamic-config value of 0 or negative must
    not make _on_tool_error abort on the very first tool error -- clamp to a
    floor of 1 to preserve "consecutive" semantics."""
    agent = _make_agent()

    with patch(
        "codemie.agents.langgraph_agent.DynamicConfigService.get_typed_value_safe",
        return_value=0,
    ):
        assert agent._get_max_consecutive_tool_errors() == 1


def test_parallel_tool_errors_in_one_batch_count_once():
    """R3-CR-005: a parallel tool-call batch (parallel_tool_calls) whose calls all
    fail is ONE failed round, not N consecutive failures -- only the batch tail may
    advance the streak, otherwise a single bad batch trips the abort cap immediately."""
    agent = _make_agent()
    run_ids = [uuid.uuid4() for _ in range(3)]
    for run_id in run_ids:
        agent._on_tool_start("t", "{}", run_id=run_id, author="agent_a")

    for run_id in run_ids:
        agent._on_tool_error("boom", run_id=run_id, author="agent_a")

    assert agent._consecutive_tool_error_count["agent_a"] == 1


def test_sequential_tool_errors_still_count_each():
    """Genuine sequential retries (each call fully resolved before the next one
    starts) must keep advancing the streak one per failure (R3-CR-005 companion)."""
    agent = _make_agent()

    for _ in range(3):
        run_id = uuid.uuid4()
        agent._on_tool_start("t", "{}", run_id=run_id, author="agent_a")
        agent._on_tool_error("boom", run_id=run_id, author="agent_a")

    assert agent._consecutive_tool_error_count["agent_a"] == 3


def test_repeated_failing_parallel_batches_still_trip_the_cap():
    """The batch-tail gate must not defang the runaway-loop guard: N consecutive
    rounds of failing parallel batches still abort the run (R3-CR-005 companion)."""
    agent = _make_agent()

    def one_failing_batch():
        run_ids = [uuid.uuid4() for _ in range(2)]
        for run_id in run_ids:
            agent._on_tool_start("t", "{}", run_id=run_id, author="agent_a")
        for run_id in run_ids:
            agent._on_tool_error("boom", run_id=run_id, author="agent_a")

    for _ in range(config.AI_AGENT_MAX_CONSECUTIVE_TOOL_ERRORS - 1):
        one_failing_batch()

    with pytest.raises(MaxConsecutiveToolErrorsExceededException):
        one_failing_batch()


def test_failed_batch_leaves_no_inflight_run_ids_behind():
    """Bookkeeping must be balanced on the error path too, otherwise a leaked
    run_id would make later sequential failures look concurrent forever."""
    agent = _make_agent()
    run_ids = [uuid.uuid4() for _ in range(2)]
    for run_id in run_ids:
        agent._on_tool_start("t", "{}", run_id=run_id, author="agent_a")
    for run_id in run_ids:
        agent._on_tool_error("boom", run_id=run_id, author="agent_a")

    assert agent._inflight_tool_run_ids["agent_a"] == set()


def test_tool_end_clears_inflight_run_id():
    """A successful call stops being in flight, so a later single failure of the
    same author is correctly treated as sequential rather than concurrent."""
    agent = _make_agent()
    run_id = uuid.uuid4()
    agent._on_tool_start("t", "{}", run_id=run_id, author="agent_a")
    agent._on_tool_end("ok", run_id=run_id, author="agent_a")

    assert agent._inflight_tool_run_ids["agent_a"] == set()


def test_replayed_start_under_none_is_cleared_by_end_under_author():
    """R3-CR-002: the resume replay path fires _on_tool_start without an author
    (bucket None), while the real on_tool_end arrives with the adapter-resolved
    author. The run_id must not leak in the None bucket."""
    agent = _make_agent()
    run_id = uuid.uuid4()
    agent._on_tool_start("t", "{}", run_id=run_id)  # replay: author=None
    agent._on_tool_end("ok", run_id=run_id, author="agent_a")

    assert agent._inflight_tool_run_ids.get(None, set()) == set()
    assert agent._inflight_tool_run_ids.get("agent_a", set()) == set()


def test_replayed_start_under_none_is_cleared_by_error_under_author():
    """R3-CR-002, error path: same mismatch must not leak either."""
    agent = _make_agent()
    run_id = uuid.uuid4()
    agent._on_tool_start("t", "{}", run_id=run_id)  # replay: author=None
    agent._on_tool_error("boom", run_id=run_id, author="agent_a")

    assert agent._inflight_tool_run_ids.get(None, set()) == set()
    assert agent._inflight_tool_run_ids.get("agent_a", set()) == set()
