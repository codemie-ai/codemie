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

from __future__ import annotations

from unittest.mock import MagicMock

from codemie.service.script_tool_calls.fanout import fan_out
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome

OUTCOME = CallOutcome("c1", "tool.call", "t", None, 0.1, 10, 1, 4, delivered=True)


def test_the_observer_counts_the_outcome_before_any_listener_runs() -> None:
    observer = ScriptRunObserver()
    seen: list[int] = []

    def listener(_: CallOutcome) -> None:
        seen.append(observer.counted_calls)

    fan_out(observer, listener)(OUTCOME)

    assert seen == [1]


def test_a_raising_listener_does_not_hide_the_next_one() -> None:
    observer = ScriptRunObserver()
    failing = MagicMock(side_effect=RuntimeError("boom"))
    after = MagicMock()

    fan_out(observer, failing, after)(OUTCOME)

    failing.assert_called_once_with(OUTCOME)
    after.assert_called_once_with(OUTCOME)


def test_listeners_run_in_order() -> None:
    order: list[str] = []

    fan_out(ScriptRunObserver(), lambda _: order.append("a"), lambda _: order.append("b"))(OUTCOME)

    assert order == ["a", "b"]


def test_a_failing_observer_does_not_stop_the_listeners() -> None:
    observer = MagicMock()
    observer.on_settled.side_effect = RuntimeError("boom")
    listener = MagicMock()

    fan_out(observer, listener)(OUTCOME)

    listener.assert_called_once_with(OUTCOME)


def test_a_late_settle_still_reaches_the_listeners_but_is_not_aggregated() -> None:
    observer = ScriptRunObserver()
    listener = MagicMock()
    callback = fan_out(observer, listener)
    snapshot = observer.close()

    callback(OUTCOME)

    listener.assert_called_once_with(OUTCOME)
    assert snapshot.bridge_calls == 0
    assert observer.close().bridge_calls == 0
