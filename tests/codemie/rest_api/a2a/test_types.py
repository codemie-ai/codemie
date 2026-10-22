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

from unittest.mock import patch

import pytest
from pydantic import ValidationError

from codemie.rest_api.a2a.types import (
    A2AClientError,
    A2AProtocolVersionError,
    A2ARequest,
    AgentCapabilities,
    AgentCard,
    AgentCardInterfaceError,
    AgentInterface,
    Artifact,
    CancelTaskRPCResponse,
    ContentTypeNotSupportedError,
    GetTaskRPCResponse,
    INTERNAL_CARD_FIELDS,
    InvalidParamsError,
    JSONRPCError,
    JSONRPCResponse,
    Message,
    Part,
    Role,
    SendMessageResponse,
    SendMessageRPCRequest,
    SendMessageRPCResponse,
    StreamResponse,
    StreamRPCResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
    VersionNotSupportedError,
    bad_request,
    error_info,
    get_jsonrpc_url,
)
from codemie.rest_api.models.base import PydanticListType, PydanticType
from tests.codemie.rest_api.a2a.fixtures import load_fixture

_DUMP = {"by_alias": True, "exclude_none": True}

REMOVED_KEYS = {"final", "kind", "type", "sessionId", "authentication", "transport", "stateTransitionHistory"}


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


# ---- TR-3: fixtures parse and re-serialise unchanged ----

_ENVELOPE_FIXTURES = [
    ("send_message_response_task", SendMessageRPCResponse),
    ("send_message_response_message", SendMessageRPCResponse),
    ("stream_task", StreamRPCResponse),
    ("stream_status_update", StreamRPCResponse),
    ("stream_artifact_update", StreamRPCResponse),
    ("get_task_response", GetTaskRPCResponse),
    ("cancel_task_response", CancelTaskRPCResponse),
    ("error_json_parse", JSONRPCResponse),
    ("error_invalid_request", JSONRPCResponse),
    ("error_method_not_found", JSONRPCResponse),
    ("error_invalid_params", JSONRPCResponse),
    ("error_task_not_found", JSONRPCResponse),
    ("error_task_not_cancelable", JSONRPCResponse),
    ("error_unsupported_operation", JSONRPCResponse),
    ("error_content_type_not_supported", JSONRPCResponse),
    ("error_version_not_supported", JSONRPCResponse),
]


@pytest.mark.parametrize("name,model", _ENVELOPE_FIXTURES)
def test_response_fixture_round_trips(name, model):
    fixture = load_fixture(name)
    assert model.model_validate(fixture).model_dump(**_DUMP) == fixture


def test_send_message_request_fixture_round_trips():
    fixture = load_fixture("send_message_request")
    request = A2ARequest.validate_python(fixture)
    assert isinstance(request, SendMessageRPCRequest)
    assert request.model_dump(**_DUMP) == fixture


@pytest.mark.parametrize(
    "name,expected",
    [
        ("send_message_response_task", "task"),
        ("send_message_response_message", "message"),
    ],
)
def test_send_message_response_variants(name, expected):
    result = SendMessageRPCResponse.model_validate(load_fixture(name)).result
    assert getattr(result, expected) is not None


@pytest.mark.parametrize(
    "name,expected",
    [("stream_task", "task"), ("stream_status_update", "statusUpdate"), ("stream_artifact_update", "artifactUpdate")],
)
def test_stream_response_variants(name, expected):
    result = StreamRPCResponse.model_validate(load_fixture(name)).result
    assert getattr(result, expected) is not None


@pytest.mark.parametrize(
    "name,model",
    [
        ("send_message_response_task", SendMessageRPCResponse),
        ("stream_status_update", StreamRPCResponse),
        ("stream_artifact_update", StreamRPCResponse),
        ("get_task_response", GetTaskRPCResponse),
    ],
)
def test_dumps_carry_none_of_the_removed_keys(name, model):
    dumped = model.model_validate(load_fixture(name)).model_dump(**_DUMP)
    assert not REMOVED_KEYS & set(_walk_keys(dumped))


def test_task_dump_uses_id_not_task_id():
    task = Task(id="t1", contextId="c1", status=TaskStatus(state=TaskState.SUBMITTED))
    dumped = task.model_dump(**_DUMP)
    assert dumped["id"] == "t1" and "taskId" not in dumped
    assert not {"sessionId", "assistant_id", "user_id", "date", "update_date"} & set(dumped)


def test_task_dump_omits_empty_artifacts_and_history():
    task = Task(id="t1", contextId="c1", status=TaskStatus(state=TaskState.WORKING), artifacts=[], history=[])
    dumped = task.model_dump(**_DUMP)
    assert "artifacts" not in dumped and "history" not in dumped


# ---- enums and wire values ----


def test_enums_serialize_as_proto_names():
    status = TaskStatus(state=TaskState.INPUT_REQUIRED)
    assert status.model_dump(**_DUMP)["state"] == "TASK_STATE_INPUT_REQUIRED"
    message = Message(role=Role.AGENT, parts=[Part(text="hi")])
    assert message.model_dump(**_DUMP)["role"] == "ROLE_AGENT"


def test_task_status_timestamp_is_utc_z():
    dumped = TaskStatus(state=TaskState.WORKING).model_dump(**_DUMP)
    assert dumped["timestamp"].endswith("Z") and "+00:00" not in dumped["timestamp"]


def test_task_state_unspecified_is_rejected():
    with pytest.raises(ValidationError):
        TaskStatus.model_validate({"state": "TASK_STATE_UNSPECIFIED"})


# ---- TR-4: legacy input normalisers ----


@pytest.mark.parametrize(
    "legacy,expected",
    [
        ("submitted", TaskState.SUBMITTED),
        ("working", TaskState.WORKING),
        ("completed", TaskState.COMPLETED),
        ("failed", TaskState.FAILED),
        ("canceled", TaskState.CANCELED),
        ("input-required", TaskState.INPUT_REQUIRED),
        ("unknown", TaskState.FAILED),
    ],
)
def test_legacy_task_state_is_normalised(legacy, expected):
    assert TaskStatus.model_validate({"state": legacy}).state == expected


def test_legacy_unknown_state_logs_warning():
    with patch("codemie.rest_api.a2a.types.logger") as log:
        TaskStatus.model_validate({"state": "unknown"})
    log.warning.assert_called_once()


@pytest.mark.parametrize("legacy,expected", [("user", Role.USER), ("agent", Role.AGENT)])
def test_legacy_role_is_normalised(legacy, expected):
    message = Message.model_validate({"role": legacy, "parts": [{"text": "x"}]})
    assert message.role == expected


def test_message_id_is_generated_when_absent():
    first = Message.model_validate({"role": "user", "parts": [{"text": "x"}]})
    second = Message.model_validate({"role": "user", "parts": [{"text": "x"}]})
    assert first.messageId and second.messageId and first.messageId != second.messageId


def test_message_with_empty_parts_is_invalid():
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "ROLE_USER", "parts": []})


def test_legacy_file_part_with_bytes_maps_to_raw():
    part = Part.model_validate({"file": {"bytes": "aGk=", "name": "a.txt", "mimeType": "text/plain"}})
    assert (part.raw, part.filename, part.mediaType, part.url) == ("aGk=", "a.txt", "text/plain", None)


def test_legacy_file_part_with_uri_maps_to_url():
    part = Part.model_validate({"kind": "file", "file": {"uri": "https://x/y.png", "name": "y.png"}})
    assert part.url == "https://x/y.png" and part.filename == "y.png" and part.raw is None


def test_legacy_type_and_kind_keys_are_ignored_on_parts():
    assert Part.model_validate({"type": "text", "text": "hi"}).text == "hi"
    assert Part.model_validate({"kind": "text", "text": "hi"}).model_dump(**_DUMP) == {"text": "hi"}


def test_part_requires_exactly_one_content_member():
    with pytest.raises(ValidationError):
        Part.model_validate({"text": "a", "url": "https://x"})
    with pytest.raises(ValidationError):
        Part.model_validate({"filename": "x"})


def test_part_data_accepts_any_json_value():
    assert Part.model_validate({"data": [1, 2]}).data == [1, 2]
    assert Part.model_validate({"data": {"a": 1}}).model_dump(**_DUMP) == {"data": {"a": 1}}


def test_legacy_artifact_drops_index_and_generates_id():
    artifact = Artifact.model_validate({"parts": [{"text": "x"}], "index": 0, "append": True, "lastChunk": False})
    dumped = artifact.model_dump(**_DUMP)
    assert artifact.artifactId
    assert not {"index", "append", "lastChunk"} & set(dumped)


def test_legacy_task_json_loads_with_session_id_as_context():
    legacy = {
        "id": "legacy-task",
        "sessionId": "legacy-session",
        "status": {"state": "input-required", "timestamp": "2025-01-01T10:00:00.123456"},
        "history": [{"role": "user", "parts": [{"kind": "text", "text": "hello"}]}],
        "artifacts": [{"parts": [{"type": "text", "text": "x"}], "index": 0}],
    }
    task = Task.model_validate(legacy)
    assert task.id == "legacy-task"
    assert task.sessionId == "legacy-session"
    assert (task.contextId or task.sessionId) == "legacy-session"
    assert task.assistant_id is None and task.user_id is None
    assert task.status.state == TaskState.INPUT_REQUIRED
    assert task.history[0].parts[0].text == "hello"
    dumped = task.model_dump(**_DUMP)
    assert "sessionId" not in dumped and dumped["status"]["timestamp"].endswith("Z")


def test_legacy_stored_lists_load_without_dropping_items():
    messages = PydanticListType(Message).process_result_value(
        [
            {"role": "user", "parts": [{"kind": "text", "text": "a"}]},
            {"role": "agent", "parts": [{"file": {"uri": "https://x/f"}}]},
            {"role": "user", "messageId": "m3", "parts": [{"text": "c"}]},
        ],
        None,
    )
    assert [m.role for m in messages] == [Role.USER, Role.AGENT, Role.USER]
    artifacts = PydanticListType(Artifact).process_result_value(
        [{"parts": [{"text": "a"}], "index": 2}, {"name": "n", "parts": [{"data": {"k": 1}}]}], None
    )
    assert len(artifacts) == 2


def test_legacy_stored_status_with_unknown_state_loads():
    status = PydanticType(TaskStatus).process_result_value({"state": "unknown"}, None)
    assert status.state == TaskState.FAILED


# ---- oneof envelopes ----


def test_stream_response_requires_exactly_one_variant():
    status_event = TaskStatusUpdateEvent(taskId="t", contextId="c", status=TaskStatus(state=TaskState.WORKING))
    assert StreamResponse(statusUpdate=status_event).model_dump(**_DUMP)["statusUpdate"]["taskId"] == "t"
    with pytest.raises(ValidationError):
        StreamResponse()
    with pytest.raises(ValidationError):
        StreamResponse(statusUpdate=status_event, message=Message(role=Role.AGENT, parts=[Part(text="x")]))


def test_send_message_response_requires_exactly_one_variant():
    with pytest.raises(ValidationError):
        SendMessageResponse()
    with pytest.raises(ValidationError):
        SendMessageResponse(
            task=Task(id="t", status=TaskStatus(state=TaskState.WORKING)),
            message=Message(role=Role.AGENT, parts=[Part(text="x")]),
        )


def test_events_have_no_final_and_carry_ids():
    status_event = TaskStatusUpdateEvent(taskId="t", contextId="ctx", status=TaskStatus(state=TaskState.WORKING))
    artifact_event = TaskArtifactUpdateEvent(taskId="t", contextId="ctx", artifact=Artifact(parts=[Part(text="x")]))
    for event in (status_event, artifact_event):
        dumped = event.model_dump(**_DUMP)
        assert dumped["taskId"] == "t" and dumped["contextId"] == "ctx" and "final" not in dumped


# ---- request routing and errors ----


def test_a2a_request_rejects_list_tasks():
    with pytest.raises(ValidationError):
        A2ARequest.validate_python({"jsonrpc": "2.0", "method": "ListTasks", "params": {}})


def test_send_message_params_have_no_task_id_or_session_id():
    request = A2ARequest.validate_python(load_fixture("send_message_request"))
    assert request.params.message.role == Role.USER
    assert request.params.configuration.historyLength == 10
    assert "sessionId" not in request.params.model_dump(**_DUMP)


def test_get_task_and_cancel_params_use_id():
    get_task = A2ARequest.validate_python(
        {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "t", "historyLength": 0}}
    )
    cancel = A2ARequest.validate_python({"jsonrpc": "2.0", "id": 1, "method": "CancelTask", "params": {"id": "t"}})
    assert get_task.params.id == "t" and get_task.params.historyLength == 0
    assert cancel.params.id == "t"


@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "2.0", "id": "1", "method": "GetExtendedAgentCard", "params": {}},
        {"jsonrpc": "2.0", "id": "1", "method": "GetExtendedAgentCard"},
    ],
)
def test_get_extended_agent_card_accepts_empty_and_omitted_params(payload):
    assert A2ARequest.validate_python(payload).method == "GetExtendedAgentCard"


@pytest.mark.parametrize(
    "error,fixture",
    [
        (TaskNotFoundError(), "error_task_not_found"),
        (TaskNotCancelableError(), "error_task_not_cancelable"),
        (UnsupportedOperationError(), "error_unsupported_operation"),
        (ContentTypeNotSupportedError(), "error_content_type_not_supported"),
        (VersionNotSupportedError(), "error_version_not_supported"),
        (
            InvalidParamsError(data=[bad_request([("message.parts", "At least one part is required")])]),
            "error_invalid_params",
        ),
    ],
)
def test_error_classes_match_spec_fixtures(error, fixture):
    assert error.model_dump(exclude_none=True) == load_fixture(fixture)["error"]


def test_error_info_builds_typed_detail():
    detail = error_info("SOMETHING", taskId="t1")
    assert detail == {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "SOMETHING",
        "domain": "a2a-protocol.org",
        "metadata": {"taskId": "t1"},
    }
    assert "metadata" not in error_info("X")


def test_jsonrpc_error_data_is_a_list_of_typed_objects():
    assert JSONRPCError(code=-1, message="m", data=[{"@type": "x"}]).data == [{"@type": "x"}]


def test_protocol_version_error_is_a_client_error():
    assert issubclass(A2AProtocolVersionError, A2AClientError)


# ---- agent card ----


def _card(*interfaces):
    return AgentCard(
        name="a",
        description="d",
        version="1",
        capabilities=AgentCapabilities(),
        skills=[],
        supportedInterfaces=[AgentInterface(url=u, protocolBinding=b) for u, b in interfaces],
    )


def _public(card):
    return card.model_dump(by_alias=True, exclude_none=True, exclude=set(INTERNAL_CARD_FIELDS))


@pytest.mark.parametrize("name", ["agent_card", "extended_agent_card"])
def test_agent_card_fixture_round_trips(name):
    fixture = load_fixture(name)
    assert _public(AgentCard.model_validate(fixture)) == fixture


def test_agent_card_fixture_dump_has_no_removed_keys():
    dumped = _public(AgentCard.model_validate(load_fixture("agent_card")))
    assert not REMOVED_KEYS & set(_walk_keys(dumped))
    assert not {"url", "preferredTransport", "additionalInterfaces", "protocolVersion"} & set(dumped)


def test_agent_card_model_dump_keeps_internal_fields():
    card = AgentCard.model_validate(
        {**load_fixture("agent_card"), "project_name": "p", "integration_id": "i", "user_id": "u"}
    )
    dumped = card.model_dump()
    assert {"project_name", "integration_id", "user_id", "bedrock_agentcore"} <= set(dumped)
    assert dumped["project_name"] == "p" and dumped["bedrock_agentcore"] is False
    assert not set(INTERNAL_CARD_FIELDS) & set(_public(card))


def test_agent_card_url_is_back_compat_only_excluded_from_public_wire():
    """`url` is a model field (for codemie-ui), but `public_agent_card_dict`/`_public` strip it from the wire."""
    assert "url" in AgentCard.model_fields
    assert "url" in INTERNAL_CARD_FIELDS


def test_get_jsonrpc_url_picks_jsonrpc_entry_case_insensitively():
    card = _card(("https://g", "GRPC"), ("https://rpc", "jsonrpc"))
    assert get_jsonrpc_url(card) == "https://rpc"


def test_get_jsonrpc_url_raises_typed_error_without_jsonrpc():
    with pytest.raises(AgentCardInterfaceError, match="no JSON-RPC interface"):
        get_jsonrpc_url(_card(("https://g", "GRPC")))
    with pytest.raises(ValueError, match="no JSON-RPC interface"):
        get_jsonrpc_url(_card(("https://g", "GRPC")))


_LEGACY_CARD = {"name": "a", "version": "1", "capabilities": {}, "skills": []}

_V03_CARD = {
    "protocolVersion": "0.3.0",
    "name": "Legacy",
    "description": "Old card",
    "url": "https://legacy.example.com/rpc",
    "preferredTransport": "JSONRPC",
    "additionalInterfaces": [{"url": "https://legacy.example.com/grpc", "transport": "GRPC"}],
    "version": "1",
    "capabilities": {"streaming": True, "stateTransitionHistory": True},
    "authentication": {"schemes": ["Bearer"]},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [{"id": "s", "name": "S", "inputModes": ["text"]}],
    "securitySchemes": {
        "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
        "key": {"type": "apiKey", "in": "header", "name": "X-Key"},
    },
    "security": [{"bearer": []}],
    "supportsAuthenticatedExtendedCard": True,
}


def test_legacy_v03_card_normalises_to_v1_shape():
    card = AgentCard.model_validate(_V03_CARD)
    dumped = _public(card)
    assert dumped["supportedInterfaces"] == [
        {"url": "https://legacy.example.com/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "0.3.0"},
        {"url": "https://legacy.example.com/grpc", "protocolBinding": "GRPC", "protocolVersion": "0.3.0"},
    ]
    assert dumped["defaultInputModes"] == ["text/plain"] and dumped["defaultOutputModes"] == ["text/plain"]
    assert dumped["skills"] == [{"id": "s", "name": "S", "description": "", "tags": [], "inputModes": ["text/plain"]}]
    assert dumped["capabilities"] == {"streaming": True, "pushNotifications": False, "extendedAgentCard": True}
    assert dumped["securitySchemes"] == {
        "bearer": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}},
        "key": {"apiKeySecurityScheme": {"location": "header", "name": "X-Key"}},
    }
    assert dumped["securityRequirements"] == [{"schemes": {"bearer": {"list": []}}}]
    assert not REMOVED_KEYS & set(_walk_keys(dumped))
    assert not {"url", "preferredTransport", "additionalInterfaces", "protocolVersion"} & set(dumped)
    assert get_jsonrpc_url(card) == "https://legacy.example.com/rpc"


def test_legacy_interface_transport_maps_to_protocol_binding():
    card = AgentCard.model_validate(
        {**_LEGACY_CARD, "supportedInterfaces": [{"url": "https://x/rpc", "transport": "JSONRPC"}]}
    )
    assert _public(card)["supportedInterfaces"] == [
        {"url": "https://x/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
    ]


def test_legacy_card_without_protocol_version_defaults_interfaces_to_0_3():
    card = AgentCard.model_validate({**_LEGACY_CARD, "url": "https://legacy.example.com/rpc"})
    assert card.supportedInterfaces[0].protocolVersion == "0.3.0"


def test_compat_protocol_version_is_not_stamped_onto_v1_interfaces():
    """a2a-sdk 1.x emits a top-level compat `protocolVersion` 0.3 next to its own v1.0 interfaces."""
    card = AgentCard.model_validate(
        {
            **_LEGACY_CARD,
            "protocolVersion": "0.3",
            "supportedInterfaces": [
                {"url": "https://a.example.com/", "protocolBinding": "JSONRPC"},
                {"url": "https://b.example.com/", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
            ],
        }
    )
    assert [i.protocolVersion for i in card.supportedInterfaces] == ["1.0", "1.0"]


def test_legacy_card_with_null_description_and_unmappable_scheme_still_loads():
    card = AgentCard.model_validate(
        {**_LEGACY_CARD, "description": None, "url": "https://x/rpc", "securitySchemes": {"weird": {"type": "??"}}}
    )
    assert card.description == "" and not card.securitySchemes


def test_agent_card_legacy_url_synthesises_jsonrpc_interface():
    card = AgentCard.model_validate({**_LEGACY_CARD, "url": "https://legacy.example.com/rpc"})
    assert [(i.url, i.protocolBinding) for i in card.supportedInterfaces] == [
        ("https://legacy.example.com/rpc", "JSONRPC")
    ]
    # `url` is a back-compat top-level field for codemie-ui (not part of the v1.0 wire shape, see `_public`).
    assert card.model_dump()["url"] == "https://legacy.example.com/rpc"
    assert "url" not in _public(card)


def test_agent_card_interfaces_win_over_url():
    card = AgentCard.model_validate(
        {
            **_LEGACY_CARD,
            "url": "https://legacy.example.com/rpc",
            "supportedInterfaces": [{"url": "https://new.example.com/rpc", "protocolBinding": "JSONRPC"}],
        }
    )
    assert [i.url for i in card.supportedInterfaces] == ["https://new.example.com/rpc"]


def test_agent_card_url_with_empty_interfaces_synthesises():
    card = AgentCard.model_validate(
        {**_LEGACY_CARD, "url": "https://legacy.example.com/rpc", "supportedInterfaces": []}
    )
    assert get_jsonrpc_url(card) == "https://legacy.example.com/rpc"


@pytest.mark.parametrize("url", [None, ""])
def test_agent_card_without_url_and_interfaces_still_invalid(url):
    with pytest.raises(ValidationError):
        AgentCard.model_validate({**_LEGACY_CARD, "url": url})


def test_agent_card_legacy_row_loads_through_pydantic_type():
    card = PydanticType(AgentCard).process_result_value({**_LEGACY_CARD, "url": "https://legacy.example.com/rpc"}, None)
    assert get_jsonrpc_url(card) == "https://legacy.example.com/rpc"


def test_agent_card_legacy_non_jsonrpc_preferred_transport_is_not_mislabelled():
    card = AgentCard.model_validate(
        {**_LEGACY_CARD, "url": "https://legacy.example.com/grpc", "preferredTransport": "GRPC"}
    )
    assert [(i.url, i.protocolBinding) for i in card.supportedInterfaces] == [
        ("https://legacy.example.com/grpc", "GRPC")
    ]
    with pytest.raises(AgentCardInterfaceError):
        get_jsonrpc_url(card)


def test_agent_card_legacy_additional_interfaces_preserved():
    card = AgentCard.model_validate(
        {
            **_LEGACY_CARD,
            "url": "https://legacy.example.com/rpc",
            "preferredTransport": "jsonrpc",
            "additionalInterfaces": [{"url": "https://legacy.example.com/grpc", "transport": "GRPC"}],
        }
    )
    assert [(i.url, i.protocolBinding) for i in card.supportedInterfaces] == [
        ("https://legacy.example.com/rpc", "jsonrpc"),
        ("https://legacy.example.com/grpc", "GRPC"),
    ]
    assert get_jsonrpc_url(card) == "https://legacy.example.com/rpc"


def test_task_metadata_round_trips_on_the_wire():
    fixture = {**load_fixture("get_task_response")["result"], "metadata": {"k": "v"}}
    task = Task.model_validate(fixture)
    assert task.wire_metadata == {"k": "v"}
    assert task.model_dump(**_DUMP) == fixture


def test_task_without_metadata_omits_it():
    assert "metadata" not in Task(id="t", status=TaskStatus(state=TaskState.WORKING)).model_dump(**_DUMP)


def test_legacy_session_id_only_task_serializes_context_id():
    task = Task.model_validate({"id": "t", "sessionId": "legacy-ctx", "status": {"state": "completed"}})
    dumped = task.model_dump(**_DUMP)
    assert dumped["contextId"] == "legacy-ctx" and "sessionId" not in dumped


def _db_loaded_task(**values):
    """Build a Task the way SQLAlchemy's ORM loader does: no __init__, hence no pydantic private-state init."""
    task = Task.__new__(Task)
    task.__dict__.update(
        {"id": "t", "contextId": None, "sessionId": None, "artifacts": None, "history": None, **values}
    )
    return task


def test_db_loaded_task_serializes_without_private_state():
    task = _db_loaded_task(status=TaskStatus(state=TaskState.COMPLETED), sessionId="legacy-ctx")
    dumped = task.model_dump(**_DUMP)
    assert dumped["id"] == "t" and dumped["contextId"] == "legacy-ctx"
    assert "metadata" not in dumped and "sessionId" not in dumped
    assert task.wire_metadata is None
