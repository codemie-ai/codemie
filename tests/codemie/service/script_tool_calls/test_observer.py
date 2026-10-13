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

import threading

from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.context import ScriptRunContext
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome


def _outcome(
    seq: int,
    *,
    code: str | None = None,
    delivered: bool | None = True,
    withdrawn: bool = False,
    response_bytes: int = 10,
    request_bytes: int = 4,
    concurrency: int = 0,
) -> CallOutcome:
    return CallOutcome(
        f"c{seq}",
        "tool.call",
        "t",
        code,
        0.1,
        response_bytes,
        seq,
        request_bytes,
        delivered=delivered,
        withdrawn=withdrawn,
        concurrency=concurrency,
    )


def test_a_settled_outcome_is_counted() -> None:
    observer = ScriptRunObserver()
    observer.record_detail(1, "search", "internal")

    observer.on_settled(_outcome(1))
    snapshot = observer.close()

    assert snapshot.bridge_calls == 1
    assert snapshot.success_calls == 1
    assert snapshot.bridge_ok is True


def test_groups_split_success_failed_and_abandoned() -> None:
    observer = ScriptRunObserver()

    observer.on_settled(_outcome(1))
    observer.on_settled(_outcome(2, code="tool_failed"))
    observer.on_settled(_outcome(3, delivered=False))
    observer.on_settled(_outcome(4, delivered=False, withdrawn=True))
    snapshot = observer.close()

    assert (snapshot.bridge_calls, snapshot.success_calls, snapshot.failed_calls, snapshot.abandoned_calls) == (
        4,
        1,
        1,
        2,
    )


def test_no_call_means_no_bridge_calls_and_not_ok() -> None:
    snapshot = ScriptRunObserver().close()

    assert snapshot.bridge_calls == 0
    assert snapshot.bridge_ok is False


def test_only_failed_calls_are_not_ok() -> None:
    observer = ScriptRunObserver()
    observer.on_settled(_outcome(1, code="bad_arguments"))

    assert observer.close().bridge_ok is False


def test_tokens_are_summed_per_resolved_tool_for_success_delivered_calls_only() -> None:
    observer = ScriptRunObserver()
    observer.record_detail(1, "search", "internal")
    observer.record_detail(2, "search", "internal")
    observer.record_detail(3, "search", "internal")
    observer.record_detail(4, "other", "mcp")

    for seq, request, response in ((1, 3, 100), (2, 5, 50), (3, 7, 9), (4, 7, 9)):
        observer.record_tokens(seq, request, response)
    observer.on_settled(_outcome(1))
    observer.on_settled(_outcome(2))
    observer.on_settled(_outcome(3, code="tool_failed"))
    observer.on_settled(_outcome(4, delivered=False))
    snapshot = observer.close()

    assert snapshot.tool_tokens == {"search": (8, 150)}


def test_success_without_detail_is_summed_under_unknown() -> None:
    observer = ScriptRunObserver()

    observer.record_tokens(9, 2, 3)
    observer.on_settled(_outcome(9))

    assert observer.close().tool_tokens == {"unknown": (2, 3)}


def test_a_late_settle_after_close_is_not_aggregated() -> None:
    observer = ScriptRunObserver()
    observer.on_settled(_outcome(1))
    first = observer.close()

    observer.on_settled(_outcome(2))

    assert first.bridge_calls == 1
    assert observer.close() == first


def test_close_returns_the_same_snapshot_each_time() -> None:
    observer = ScriptRunObserver()

    assert observer.close() is observer.close()


def test_detail_maps_seq_to_the_resolved_name_and_unknown_otherwise() -> None:
    observer = ScriptRunObserver()
    observer.record_detail(1, "search", "mcp")

    assert observer.detail_for(1).tool_name == "search"
    assert observer.detail_for(1).tool_type == "mcp"
    assert observer.detail_for(2).tool_name == "unknown"
    assert observer.detail_for(None).tool_name == "unknown"


def test_a_repeated_call_id_does_not_merge_details_because_seq_is_the_key() -> None:
    observer = ScriptRunObserver()
    observer.record_detail(1, "a", "internal")
    observer.record_detail(2, "b", "internal")

    assert [observer.detail_for(1).tool_name, observer.detail_for(2).tool_name] == ["a", "b"]


def test_an_operation_other_than_a_tool_call_is_not_counted_as_a_call() -> None:
    observer = ScriptRunObserver()

    observer.on_settled(CallOutcome("c1", "other.op", None, None, 0.1, 1, 1, 1, delivered=True))

    assert observer.close().bridge_calls == 0


def test_attribution_is_kept_and_reported_in_the_snapshot() -> None:
    observer = ScriptRunObserver()
    context = ScriptRunContext.without_context(User(id="u", username="u"))

    observer.set_attribution(context)
    snapshot = observer.close()

    assert observer.attribution is context
    assert snapshot.attribution is context


def test_concurrent_settles_are_all_counted() -> None:
    observer = ScriptRunObserver()
    threads = [threading.Thread(target=observer.on_settled, args=(_outcome(i),)) for i in range(1, 51)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert observer.close().bridge_calls == 50


def test_the_peak_parallelism_is_the_most_calls_in_flight_at_once() -> None:
    observer = ScriptRunObserver()

    observer.on_settled(_outcome(1, concurrency=1))
    observer.on_settled(_outcome(2, concurrency=3))
    observer.on_settled(_outcome(3, concurrency=2))

    assert observer.close().peak_parallel == 3


def test_failed_and_abandoned_calls_count_toward_the_peak() -> None:
    observer = ScriptRunObserver()

    observer.on_settled(_outcome(1, code="tool_failed", concurrency=2))
    observer.on_settled(_outcome(2, delivered=False, concurrency=4))

    assert observer.close().peak_parallel == 4


def test_a_run_without_calls_has_no_peak() -> None:
    assert ScriptRunObserver().close().peak_parallel == 0


def test_only_calls_that_waited_for_the_gate_are_counted_with_their_waits() -> None:
    observer = ScriptRunObserver()

    observer.record_gate(False, 0.0)
    observer.record_gate(True, 0.5)
    observer.record_gate(True, 1.5)
    snapshot = observer.close()

    assert snapshot.gate_waited_calls == 2
    assert snapshot.gate_wait_seconds == 2.0
    assert snapshot.gate_wait_max_seconds == 1.5
    assert snapshot.gate_timeout_calls == 0


def test_a_call_that_gave_up_on_the_gate_is_a_wait_and_a_timeout() -> None:
    observer = ScriptRunObserver()

    observer.record_gate_timeout(2.0)
    snapshot = observer.close()

    assert (snapshot.gate_waited_calls, snapshot.gate_timeout_calls) == (1, 1)
    assert snapshot.gate_wait_seconds == 2.0


def test_gate_facts_arriving_after_close_are_ignored() -> None:
    observer = ScriptRunObserver()
    snapshot = observer.close()

    observer.record_gate(True, 1.0)
    observer.record_gate_timeout(1.0)

    assert snapshot.gate_waited_calls == 0
    assert observer.close().gate_waited_calls == 0


def test_an_outcome_without_an_op_is_still_a_call() -> None:
    """Unparsed, oversize and unfinished requests have no op; they are the abandoned and faulty calls."""
    observer = ScriptRunObserver()

    observer.on_settled(CallOutcome("c1", None, None, "bad_request", 0.1, 1, 1, 1, delivered=True))
    observer.on_settled(CallOutcome("c2", None, None, None, 0.1, 0, 2, 0, delivered=False, withdrawn=True))

    snapshot = observer.close()
    assert snapshot.bridge_calls == 2
    assert snapshot.failed_calls == 1
    assert snapshot.abandoned_calls == 1


def test_an_unknown_operation_is_a_failed_call_but_a_well_formed_other_operation_is_not_a_call() -> None:
    observer = ScriptRunObserver()

    observer.on_settled(CallOutcome("c1", "nope", None, "unknown_op", 0.1, 1, 1, 1, delivered=True))
    observer.on_settled(CallOutcome("c2", "other.op", None, None, 0.1, 1, 2, 1, delivered=True))

    snapshot = observer.close()
    assert snapshot.bridge_calls == 1
    assert snapshot.failed_calls == 1
