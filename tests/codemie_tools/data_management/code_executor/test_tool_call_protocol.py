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

"""The backend side of the protocol, driven without any transport: one request in, one response out."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections.abc import Mapping

import pytest

from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    ERROR_CODES,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
)
from codemie_tools.data_management.code_executor import tool_call_protocol
from codemie_tools.data_management.code_executor.tool_call_protocol import (
    DEADLINE_MARGIN_SECONDS,
    CallDeadline,
    CallOutcome,
    PendingRequest,
    RequestDispatcher,
    ToolCallRefused,
    current_call_cancelled,
    current_call_deadline,
    ensure_not_cancelled,
    error_response,
    parse_request,
)


def _body(op: str = "echo", payload: object = None, **overrides: object) -> str:
    request: dict[str, object] = {
        "v": PROTOCOL_VERSION,
        "id": "x",
        "op": op,
        "payload": {} if payload is None else payload,
    }
    request.update(overrides)
    return json.dumps(request)


def _request(body: str | None, **kwargs: object) -> PendingRequest:
    return PendingRequest(call_id="c1", file_name="req.c1.json", oversize=False, body=body, **kwargs)  # type: ignore[arg-type]


def _echo(payload: Mapping[str, object]) -> object:
    return dict(payload)


def _answer(dispatcher: RequestDispatcher, request: PendingRequest) -> Mapping[str, object]:
    parsed = json.loads(dispatcher.dispatch(request))
    assert isinstance(parsed, dict)
    return parsed


def _error_code(response: Mapping[str, object]) -> str:
    error = response["error"]
    assert isinstance(error, dict)
    return str(error["code"])


class TestDispatch:
    def test_a_known_operation_is_answered_with_its_result_and_the_request_id(self) -> None:
        response = _answer(RequestDispatcher({"echo": _echo}), _request(_body(payload={"a": 1})))

        assert response == {"v": PROTOCOL_VERSION, "id": "c1", "ok": True, "result": {"a": 1}}

    def test_an_unknown_operation_is_unknown_op(self) -> None:
        response = _answer(RequestDispatcher({"echo": _echo}), _request(_body(op="nope")))

        assert response["ok"] is False
        assert _error_code(response) == "unknown_op"

    def test_a_dispatcher_without_handlers_serves_nothing(self) -> None:
        assert _error_code(_answer(RequestDispatcher(), _request(_body()))) == "unknown_op"

    @pytest.mark.parametrize(
        "body",
        [
            None,
            "{not json",
            "[1]",
            json.dumps({"v": 99, "op": "echo", "payload": {}}),
            _body(op=""),
            _body(payload=[1]),
        ],
    )
    def test_an_unusable_request_is_bad_request(self, body: str | None) -> None:
        assert _error_code(_answer(RequestDispatcher({"echo": _echo}), _request(body))) == "bad_request"

    def test_an_oversize_request_is_payload_too_large(self) -> None:
        request = PendingRequest("c1", "req.c1.json", oversize=True, body=None)

        assert _error_code(_answer(RequestDispatcher({"echo": _echo}), request)) == "payload_too_large"

    def test_a_request_that_is_not_utf8_is_bad_request_and_is_not_repaired(self) -> None:
        request = PendingRequest("c1", "req.c1.json", oversize=False, body=None, bad_encoding=True)

        response = _answer(RequestDispatcher({"echo": _echo}), request)

        assert _error_code(response) == "bad_request"
        assert "UTF-8" in str(response["error"])

    def test_a_refusal_carries_its_code_and_message(self) -> None:
        def refuse(payload: Mapping[str, object]) -> object:
            raise ToolCallRefused("tool_blocked", "no way")

        response = _answer(RequestDispatcher({"echo": refuse}), _request(_body()))

        assert response["error"] == {"code": "tool_blocked", "message": "no way"}

    def test_a_handler_bug_is_internal_error_without_its_text(self) -> None:
        def explode(payload: Mapping[str, object]) -> object:
            raise RuntimeError("secret detail")

        response = _answer(RequestDispatcher({"echo": explode}), _request(_body()))

        assert _error_code(response) == "internal_error"
        assert "secret detail" not in json.dumps(response)

    def test_a_result_that_cannot_be_serialised_is_bad_request(self) -> None:
        response = _answer(RequestDispatcher({"echo": lambda payload: object()}), _request(_body()))

        assert _error_code(response) == "bad_request"

    def test_a_result_over_the_cap_is_payload_too_large_and_states_the_sizes(self) -> None:
        dispatcher = RequestDispatcher({"echo": lambda payload: "z" * 500}, max_payload_bytes=100)

        response = _answer(dispatcher, _request(_body()))

        assert _error_code(response) == "payload_too_large"
        assert "100 byte limit" in str(response["error"])

    def test_the_default_cap_is_the_sdk_cap(self) -> None:
        assert RequestDispatcher().max_payload_bytes == MAX_PAYLOAD_BYTES

    def test_non_ascii_results_are_not_escaped(self) -> None:
        raw = RequestDispatcher({"echo": lambda payload: {"t": "café 中"}}).dispatch(_request(_body()))

        assert "café 中".encode() in raw

    def test_dispatch_is_total_even_for_a_request_that_breaks_the_parser(self) -> None:
        deep = '{"v": 1, "id": "x", "op": "echo", "payload": ' + "[" * 100_000 + "]" * 100_000 + "}"

        response = _answer(RequestDispatcher({"echo": _echo}), _request(deep))

        assert response["ok"] is False


class TestDeadline:
    def test_a_call_with_too_little_time_left_is_refused_and_the_handler_is_not_run(self) -> None:
        ran: list[int] = []
        dispatcher = RequestDispatcher(
            {"echo": lambda payload: ran.append(1)}, time_left=lambda: 1.0, deadline_margin=5.0
        )

        assert _error_code(_answer(dispatcher, _request(_body()))) == "deadline_exceeded"
        assert ran == []

    def test_a_call_with_enough_time_left_runs(self) -> None:
        dispatcher = RequestDispatcher({"echo": _echo}, time_left=lambda: 60.0)

        assert _answer(dispatcher, _request(_body(payload={"ok": 1})))["ok"] is True

    def test_without_a_clock_a_call_is_never_refused(self) -> None:
        dispatcher = RequestDispatcher({"echo": _echo}, deadline_margin=1e9)

        assert _answer(dispatcher, _request(_body()))["ok"] is True

    def test_an_unknown_operation_is_reported_as_such_when_time_is_short(self) -> None:
        dispatcher = RequestDispatcher({"echo": _echo}, time_left=lambda: 1.0, deadline_margin=5.0)

        assert _error_code(_answer(dispatcher, _request(_body(op="nope")))) == "unknown_op"

    def test_the_default_margin_is_two_seconds(self) -> None:
        assert DEADLINE_MARGIN_SECONDS == 2.0

    def test_the_handler_can_read_the_clock_of_the_call_being_served_and_only_during_it(self) -> None:
        seen: list[CallDeadline | None] = []
        dispatcher = RequestDispatcher(
            {"echo": lambda payload: seen.append(current_call_deadline())}, time_left=lambda: 30.0, deadline_margin=2.0
        )

        dispatcher.dispatch(_request(_body()))

        deadline = seen[0]
        assert deadline is not None
        assert deadline.remaining() == 28.0
        assert current_call_deadline() is None

    def test_the_clock_is_removed_even_when_the_handler_raises(self) -> None:
        def explode(payload: Mapping[str, object]) -> object:
            raise RuntimeError("boom")

        RequestDispatcher({"echo": explode}, time_left=lambda: 30.0).dispatch(_request(_body()))

        assert current_call_deadline() is None

    def test_remaining_is_none_without_a_limit(self) -> None:
        assert CallDeadline(lambda: None).remaining() is None
        CallDeadline(lambda: None).ensure()


class TestParseRequest:
    def test_a_good_request_gives_op_and_payload(self) -> None:
        assert parse_request(_body(op="echo", payload={"a": 1})) == ("echo", {"a": 1}, None)

    def test_a_problem_never_quotes_the_request(self) -> None:
        _, _, problem = parse_request(json.dumps({"v": 1, "op": "echo", "payload": "SECRET-VALUE"}))

        assert problem is not None
        assert "SECRET-VALUE" not in problem


class TestErrorResponse:
    def test_it_is_a_valid_error_envelope(self) -> None:
        parsed = json.loads(error_response("c1", "timeout", "late"))

        assert parsed == {
            "v": PROTOCOL_VERSION,
            "id": "c1",
            "ok": False,
            "error": {"code": "timeout", "message": "late"},
        }

    def test_every_code_the_dispatcher_itself_emits_is_a_known_code(self) -> None:
        dispatcher = RequestDispatcher({"explode": lambda p: 1 / 0}, time_left=lambda: 0.0)
        requests = [
            PendingRequest("c", "f", True, None),
            PendingRequest("c", "f", False, None, bad_encoding=True),
            _request("{"),
            _request(_body(op="nope")),
            _request(_body(op="explode")),
        ]

        codes = {_error_code(_answer(dispatcher, request)) for request in requests}

        assert codes <= ERROR_CODES


def test_the_protocol_module_does_not_import_the_sandbox_stack() -> None:
    probe = (
        "import sys\n"
        "import codemie_tools.data_management.code_executor.tool_call_protocol\n"
        "print('kubernetes' in sys.modules)\n"
    )

    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)

    assert result.stdout.strip().splitlines()[-1] == "False"


class TestCallOutcome:
    """One record per request, whoever decided its fate: the single event later stories consume."""

    @staticmethod
    def _outcome(dispatcher: RequestDispatcher, request: PendingRequest) -> CallOutcome:
        result = dispatcher.handle(request)
        assert json.loads(result.body)  # the body is the same one dispatch() gives
        return result.outcome

    def test_a_result_has_no_code_and_names_op_tool_and_size(self) -> None:
        dispatcher = RequestDispatcher({"tool.call": lambda payload: {"ok": 1}})

        outcome = self._outcome(dispatcher, _request(_body(op="tool.call", payload={"name": "jira", "args": {}})))

        assert (outcome.call_id, outcome.op, outcome.tool, outcome.code) == ("c1", "tool.call", "jira", None)
        assert outcome.response_bytes > 0
        assert outcome.duration_seconds >= 0
        assert outcome.delivered is None and outcome.withdrawn is False, "the transport fills these"

    @pytest.mark.parametrize(
        ("request_", "code", "tool"),
        [
            (PendingRequest("c1", "f", True, None), "payload_too_large", None),
            (PendingRequest("c1", "f", False, None, bad_encoding=True), "bad_request", None),
            (_request("{nope"), "bad_request", None),
            (_request(_body(op="nope", payload={"name": "jira"})), "unknown_op", "jira"),
        ],
    )
    def test_a_refused_or_malformed_request_still_produces_a_record_with_its_code(
        self, request_: PendingRequest, code: str, tool: str | None
    ) -> None:
        outcome = self._outcome(RequestDispatcher({"tool.call": _echo}), request_)

        assert outcome.code == code
        assert outcome.tool == tool

    def test_a_refusal_raised_by_the_handler_gives_its_code(self) -> None:
        def refuse(payload: Mapping[str, object]) -> object:
            raise ToolCallRefused("tool_blocked", "no")

        outcome = self._outcome(
            RequestDispatcher({"tool.call": refuse}), _request(_body(op="tool.call", payload={"name": "x"}))
        )

        assert (outcome.code, outcome.tool) == ("tool_blocked", "x")

    def test_a_result_over_the_cap_and_a_handler_bug_are_recorded_too(self) -> None:
        big = RequestDispatcher({"tool.call": lambda payload: "z" * 500}, max_payload_bytes=100)
        bug = RequestDispatcher({"tool.call": lambda payload: 1 / 0})
        request = _request(_body(op="tool.call", payload={"name": "x"}))

        assert self._outcome(big, request).code == "payload_too_large"
        assert self._outcome(bug, request).code == "internal_error"

    def test_a_deadline_refusal_is_recorded(self) -> None:
        dispatcher = RequestDispatcher({"tool.call": _echo}, time_left=lambda: 0.5)

        assert (
            self._outcome(dispatcher, _request(_body(op="tool.call", payload={"name": "x"}))).code
            == "deadline_exceeded"
        )

    def test_settled_returns_a_copy_with_the_transport_facts(self) -> None:
        outcome = self._outcome(
            RequestDispatcher({"tool.call": _echo}), _request(_body(op="tool.call", payload={"name": "x"}))
        )

        settled = outcome.settled(delivered=False, withdrawn=True)

        assert (settled.delivered, settled.withdrawn) == (False, True)
        assert outcome.delivered is None


class TestCancellation:
    def test_a_request_withdrawn_before_it_starts_is_not_run_and_is_answered_timeout(self) -> None:
        ran: list[int] = []
        cancelled = threading.Event()
        cancelled.set()
        dispatcher = RequestDispatcher({"tool.call": lambda payload: ran.append(1)})

        result = dispatcher.handle(_request(_body(op="tool.call", payload={"name": "x"})), cancelled)

        assert ran == []
        assert result.outcome.code == "timeout"

    def test_the_handler_can_see_the_event_during_its_call_and_only_then(self) -> None:
        seen: list[threading.Event | None] = []
        event = threading.Event()
        dispatcher = RequestDispatcher({"tool.call": lambda payload: seen.append(current_call_cancelled())})

        dispatcher.handle(_request(_body(op="tool.call", payload={"name": "x"})), event)

        assert seen == [event]
        assert current_call_cancelled() is None

    def test_ensure_not_cancelled_raises_only_when_the_event_is_set(self) -> None:
        event = threading.Event()
        token = tool_call_protocol._current_cancel.set(event)
        try:
            ensure_not_cancelled()
            event.set()
            with pytest.raises(ToolCallRefused) as excinfo:
                ensure_not_cancelled()
        finally:
            tool_call_protocol._current_cancel.reset(token)

        assert excinfo.value.code == "timeout"

    def test_without_an_event_nothing_is_ever_cancelled(self) -> None:
        ensure_not_cancelled()
