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

"""Wire contract (TR-2): what CodeMie serializes has exactly the spec fixtures' key sets and none of the removed keys."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request

from codemie.rest_api.a2a.types import (
    ContentTypeNotSupportedError,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    Task,
    TaskArtifactUpdateEvent,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskStatusUpdateEvent,
    UnsupportedOperationError,
    VersionNotSupportedError,
    Message,
    Artifact,
    Part,
    Role,
    TaskState,
    TaskStatus,
    bad_request,
)
from codemie.rest_api.a2a.utils import assistant_to_agent_card, public_agent_card_dict
from codemie.rest_api.models.assistant import Assistant
from tests.codemie.rest_api.a2a.fixtures import load_fixture

_DUMP = {"by_alias": True, "exclude_none": True}

REMOVED_KEYS = {
    "final",
    "kind",
    "type",
    "sessionId",
    "authentication",
    "transport",
    "stateTransitionHistory",
    "preferredTransport",
    "additionalInterfaces",
    "assistant_id",
    "user_id",
    "date",
    "update_date",
}


def _keys(value, path=""):
    """Every dotted key path in a JSON value (list indexes are elided); dict keys under maps count too."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield f"{path}.{key}" if path else key
            yield from _keys(item, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item, path)


def _leaf_names(value):
    return {path.rsplit(".", 1)[-1] for path in _keys(value)}


def _server_card(idp="keycloak"):
    assistant = MagicMock(spec=Assistant)
    assistant.name, assistant.description, assistant.toolkits, assistant.id = "Agent", "Does things", [], "a-1"
    request = MagicMock(spec=Request)
    request.base_url.scheme, request.base_url.netloc = "https", "example.com"
    with patch("codemie.rest_api.a2a.utils.config") as cfg:
        cfg.API_ROOT_PATH, cfg.IDP_PROVIDER = "", idp
        cfg.A2A_PROVIDER_ORGANIZATION, cfg.A2A_PROVIDER_URL = "Example Org", "https://example.com"
        return assistant_to_agent_card(assistant, request)


def _fixture_task():
    return load_fixture("get_task_response")["result"]


def _server_task():
    fixture = _fixture_task()
    return Task.model_validate(
        {**fixture, "sessionId": "legacy", "assistant_id": "a", "user_id": "u", "date": "2026-01-01T00:00:00"}
    )


def test_card_key_sets_match_fixture():
    dumped = public_agent_card_dict(_server_card())
    fixture = load_fixture("agent_card")
    assert set(dumped) == set(fixture) - {"documentationUrl"}
    assert set(dumped["supportedInterfaces"][0]) == set(fixture["supportedInterfaces"][0])
    assert set(dumped["capabilities"]) == set(fixture["capabilities"])
    assert set(dumped["skills"][0]) <= set(fixture["skills"][0])
    assert set(dumped["securitySchemes"]["bearer"]) == set(fixture["securitySchemes"]["bearer"])
    assert dumped["securityRequirements"] == fixture["securityRequirements"]


@pytest.mark.parametrize("idp", ["keycloak", "local"])
def test_card_carries_no_removed_or_internal_keys(idp):
    dumped = public_agent_card_dict(_server_card(idp))
    assert not (REMOVED_KEYS | {"project_name", "integration_id", "bedrock_agentcore", "url"}) & set(dumped)
    assert not REMOVED_KEYS & _leaf_names(dumped)


def test_task_key_sets_match_fixture():
    dumped = _server_task().model_dump(**_DUMP)
    fixture = _fixture_task()
    assert set(dumped) == set(fixture)
    assert set(dumped["status"]) == set(fixture["status"])
    assert set(dumped["artifacts"][0]) == set(fixture["artifacts"][0])
    assert set(dumped["history"][0]) == set(fixture["history"][0])
    assert dumped == fixture


def test_task_carries_no_removed_keys():
    dumped = _server_task().model_dump(**_DUMP)
    assert not REMOVED_KEYS & _leaf_names(dumped)
    assert "taskId" not in dumped and dumped["id"]


def test_message_key_sets_and_enum_values():
    fixture = load_fixture("send_message_response_message")["result"]["message"]
    dumped = Message.model_validate(fixture).model_dump(**_DUMP)
    assert dumped == fixture and dumped["role"] == "ROLE_AGENT"
    assert not REMOVED_KEYS & _leaf_names(dumped)


def test_status_update_event_matches_fixture_and_has_no_final():
    fixture = load_fixture("stream_status_update")["result"]["statusUpdate"]
    event = TaskStatusUpdateEvent.model_validate(fixture)
    dumped = event.model_dump(**_DUMP)
    assert dumped == fixture and "final" not in dumped
    assert dumped["status"]["state"] == "TASK_STATE_COMPLETED"


def test_artifact_update_event_matches_fixture():
    fixture = load_fixture("stream_artifact_update")["result"]["artifactUpdate"]
    dumped = TaskArtifactUpdateEvent.model_validate(fixture).model_dump(**_DUMP)
    assert dumped == fixture
    assert not {"index", "append", "lastChunk"} & _leaf_names(dumped["artifact"])


def test_server_built_objects_serialize_with_proto_names():
    status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=Message(role=Role.AGENT, parts=[Part(text="q?")]))
    task = Task(id="t", contextId="c", status=status, artifacts=[Artifact(parts=[Part(text="a")])])
    dumped = task.model_dump(**_DUMP)
    assert dumped["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert dumped["status"]["message"]["role"] == "ROLE_AGENT"
    assert dumped["status"]["timestamp"].endswith("Z")
    assert dumped["artifacts"][0]["artifactId"] and dumped["status"]["message"]["messageId"]


@pytest.mark.parametrize(
    "error",
    [
        JSONParseError(),
        InvalidRequestError(),
        MethodNotFoundError(),
        InvalidParamsError(data=[bad_request([("f", "d")])]),
        InternalError(),
        TaskNotFoundError(),
        TaskNotCancelableError(),
        PushNotificationNotSupportedError(),
        UnsupportedOperationError(),
        ContentTypeNotSupportedError(),
        VersionNotSupportedError(),
    ],
)
def test_error_data_is_an_array_of_typed_objects(error):
    dumped = error.model_dump(exclude_none=True)
    assert set(dumped) == {"code", "message", "data"}
    assert isinstance(dumped["data"], list) and dumped["data"]
    assert all(detail["@type"].startswith("type.googleapis.com/google.rpc.") for detail in dumped["data"])
    info = [d for d in dumped["data"] if d["@type"].endswith("ErrorInfo")]
    assert all(d["domain"] == "a2a-protocol.org" and d["reason"] for d in info)


def test_error_fixtures_share_the_generated_shape():
    generated = TaskNotFoundError().model_dump(exclude_none=True)
    fixture = load_fixture("error_task_not_found")["error"]
    assert generated == fixture
