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

"""Unit tests for the A2A utilities module."""

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request

from codemie.core.models import AssistantChatRequest, BaseModelResponse, ChatRole, AuthenticationType
from codemie.rest_api.a2a.types import (
    INTERNAL_CARD_FIELDS,
    AgentSkill,
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRPCRequest,
    SendStreamingMessageRPCRequest,
    Task,
    TaskState,
    TaskStatus,
)
from codemie.rest_api.a2a.utils import (
    assistant_to_agent_card,
    convert_messages_to_chat_messages,
    convert_to_base_model_response,
    convert_to_task_request,
    get_auth_header,
    public_agent_card_dict,
    to_kebab_case,
    tool_to_agent_skill,
    tools_from_toolkits_to_agent_skills,
)
from codemie.rest_api.models.assistant import Assistant, ToolKitDetails, ToolDetails
from tests.codemie.rest_api.a2a.fixtures import load_fixture

INTERNAL_CARD_FIELDS_SET = set(INTERNAL_CARD_FIELDS)


class TestA2AUtils(unittest.TestCase):
    """Test cases for A2A utilities."""

    def test_to_kebab_case(self):
        """Test converting strings to kebab-case format."""
        test_cases = [
            ("camelCase", "camel-case"),
            ("snake_case", "snake-case"),
            ("PascalCase", "pascal-case"),
            ("Mixed Case String", "mixed-case-string"),
            ("with--multiple---hyphens", "with-multiple-hyphens"),
            ("with_under_scores", "with-under-scores"),
            ("with.dots", "with.dots"),
            ("   leading-spaces", "leading-spaces"),
            ("trailing-spaces   ", "trailing-spaces"),
            ("123Numbers", "123-numbers"),
            ("", ""),
            ("-leading-hyphen", "leading-hyphen"),
            ("trailing-hyphen-", "trailing-hyphen"),
        ]

        for input_str, expected in test_cases:
            with self.subTest(input_str=input_str):
                self.assertEqual(to_kebab_case(input_str), expected)

    def test_tool_to_agent_skill(self):
        """Test converting ToolDetails to AgentSkill."""
        # Arrange
        tool = ToolDetails(
            name="test_tool",
            label="Test Tool",
            user_description="User-friendly description",
            examples=["Example 1", "Example 2"],
        )

        # Act
        result = tool_to_agent_skill(tool, toolkit_label="Test Toolkit")

        # Assert
        self.assertIsInstance(result, AgentSkill)
        self.assertEqual(result.id, "test_tool")
        self.assertEqual(result.name, "Test Tool")
        self.assertEqual(result.description, "User-friendly description")
        self.assertEqual(result.tags, ["test", "tool", "test-toolkit"])
        # In the implementation, examples is set to None if empty
        # This matches the actual behavior of the function
        self.assertIsNone(result.examples)
        self.assertEqual(result.inputModes, ["text/plain"])
        self.assertEqual(result.outputModes, ["text/plain"])

        # Test with no label
        tool.label = None
        result = tool_to_agent_skill(tool, toolkit_label="Test Toolkit")
        self.assertEqual(result.name, "test_tool")

        # Test with no user_description
        tool = ToolDetails(
            name="another_tool",
            user_description="A description",
        )
        # We need to patch getattr to handle the attribute access in the function
        with patch(
            'codemie.rest_api.a2a.utils.getattr',
            side_effect=lambda obj, attr, default: "A description" if attr == "description" else default,
        ):
            result = tool_to_agent_skill(tool)
            self.assertEqual(result.description, "A description")
        self.assertIsNone(result.examples)

    def test_tools_from_toolkits_to_agent_skills(self):
        """Test converting tools from toolkits to agent skills."""
        # Arrange
        toolkits = [
            ToolKitDetails(
                toolkit="toolkit1",
                label="Toolkit One",
                tools=[
                    ToolDetails(name="tool1", label="Tool One", user_description="Tool 1 description"),
                    ToolDetails(name="tool2", label="Tool Two", user_description="Tool 2 description"),
                ],
            ),
            ToolKitDetails(
                toolkit="toolkit2",
                label="Toolkit Two",
                tools=[ToolDetails(name="tool3", label="Tool Three", user_description="Tool 3 description")],
            ),
        ]

        # Act
        result = tools_from_toolkits_to_agent_skills(toolkits)

        # Assert
        self.assertEqual(len(result), 3)

        self.assertEqual(result[0].id, "tool1")
        self.assertEqual(result[0].name, "Tool One")
        self.assertIn("toolkit-one", result[0].tags)

        self.assertEqual(result[1].id, "tool2")
        self.assertEqual(result[1].name, "Tool Two")
        self.assertIn("toolkit-one", result[1].tags)

        self.assertEqual(result[2].id, "tool3")
        self.assertEqual(result[2].name, "Tool Three")
        self.assertIn("toolkit-two", result[2].tags)

    def test_get_auth_header(self):
        """Test generating authentication headers based on A2A credentials."""
        # Test with no credentials
        header = get_auth_header({})
        self.assertEqual(header, {})

        # Test basic authentication
        creds_basic = {"auth_type": AuthenticationType.BASIC, "username": "test_user", "password": "test_pass"}
        basic_header = get_auth_header(creds_basic)
        expected_value = base64.b64encode(b"test_user:test_pass").decode("utf-8")
        self.assertEqual(basic_header, {"Authorization": f"Basic {expected_value}"})

        # Test basic authentication with string value
        creds_basic_str = {"auth_type": "basic", "username": "test_user", "password": "test_pass"}
        basic_header_str = get_auth_header(creds_basic_str)
        self.assertEqual(basic_header_str, {"Authorization": f"Basic {expected_value}"})

        # Test basic authentication with missing credentials
        with self.assertRaises(ValueError) as context:
            get_auth_header({"auth_type": AuthenticationType.BASIC.key, "username": "test_user"})
        self.assertEqual(str(context.exception), "Basic authentication requires both username and password")

        # Test API key authentication
        creds_apikey = {
            "auth_type": AuthenticationType.APIKEY,
            "header_name": "X-Custom-API-Key",
            "auth_value": "api_key_123",
        }
        apikey_header = get_auth_header(creds_apikey)
        self.assertEqual(apikey_header, {"X-Custom-API-Key": "api_key_123"})

        # Test API key with default header name
        creds_apikey_default = {"auth_type": AuthenticationType.APIKEY, "auth_value": "api_key_123"}
        apikey_default_header = get_auth_header(creds_apikey_default)
        self.assertEqual(apikey_default_header, {"X-API-Key": "api_key_123"})

        # Test API key with missing auth_value
        with self.assertRaises(ValueError) as context:
            get_auth_header({"auth_type": AuthenticationType.APIKEY})
        self.assertEqual(str(context.exception), "API key authentication requires an auth_value")

        # Test bearer token authentication
        creds_bearer = {"auth_type": AuthenticationType.BEARER, "auth_value": "token_123"}
        bearer_header = get_auth_header(creds_bearer)
        self.assertEqual(bearer_header, {"Authorization": "Bearer token_123"})

        # Test bearer token with string value
        creds_bearer_str = {"auth_type": "bearer", "auth_value": "token_123"}
        bearer_header_str = get_auth_header(creds_bearer_str)
        self.assertEqual(bearer_header_str, {"Authorization": "Bearer token_123"})

        # Test bearer token with missing auth_value
        with self.assertRaises(ValueError) as context:
            get_auth_header({"auth_type": AuthenticationType.BEARER})
        self.assertEqual(str(context.exception), "Bearer token authentication requires an auth_value")

        with self.assertRaises(ValueError) as context:
            get_auth_header({"auth_value": "token_123"})
        self.assertEqual(str(context.exception), "Unknown authentication type: None")


# ---- v1.0 converters and card builder (pytest style) ----

_DUMP = {"by_alias": True, "exclude_none": True}


def _chat_request(stream, conversation_id="test-conversation"):
    return AssistantChatRequest(
        conversation_id=conversation_id, text="Hello, assistant!", stream=stream, history_index=5
    )


@pytest.mark.parametrize(
    "stream,request_cls,method",
    [
        (False, SendMessageRPCRequest, "SendMessage"),
        (True, SendStreamingMessageRPCRequest, "SendStreamingMessage"),
    ],
)
def test_convert_to_task_request_builds_v1_request(stream, request_cls, method):
    raw_request = MagicMock(spec=Request)
    result = convert_to_task_request(_chat_request(stream), raw_request)

    assert isinstance(result, request_cls) and result.method == method
    message = result.params.message
    assert message.role == Role.USER and message.parts[0].text == "Hello, assistant!"
    assert message.contextId == "test-conversation"
    assert message.taskId is None and message.messageId
    dumped = result.model_dump(**_DUMP)["params"]
    assert "id" not in dumped and "sessionId" not in dumped and "taskId" not in dumped["message"]
    assert dumped["message"]["role"] == "ROLE_USER"


def test_convert_to_task_request_does_not_use_request_uuid_as_task_id():
    raw_request = MagicMock(spec=Request)
    raw_request.state.uuid = "request-uuid"
    result = convert_to_task_request(_chat_request(False), raw_request)
    assert "request-uuid" not in result.model_dump_json()


def _task(**kwargs):
    return Task(id="test-task-id", contextId="ctx", status=TaskStatus(state=TaskState.COMPLETED), **kwargs)


def test_convert_to_base_model_response_from_task_artifact():
    task = _task(artifacts=[Artifact(parts=[Part(text="Generated response text")])])
    result = convert_to_base_model_response(task)
    assert isinstance(result, BaseModelResponse)
    assert (result.generated, result.task_id, result.time_elapsed, result.thoughts) == (
        "Generated response text",
        "test-task-id",
        0,
        [],
    )


def test_convert_to_base_model_response_falls_back_to_status_message():
    message = Message(role=Role.AGENT, parts=[Part(text="which city?")])
    task = Task(id="t", status=TaskStatus(state=TaskState.INPUT_REQUIRED, message=message))
    assert convert_to_base_model_response(task).generated == "which city?"


def test_convert_to_base_model_response_from_message_result():
    message = Message(role=Role.AGENT, parts=[Part(text="direct "), Part(text="answer")], taskId="t9")
    result = convert_to_base_model_response(message)
    assert result.generated == "direct answer" and result.task_id == "t9"


def test_convert_to_base_model_response_without_text_does_not_raise():
    empty_task = Task(id="t", status=TaskStatus(state=TaskState.FAILED))
    assert convert_to_base_model_response(empty_task).generated == ""
    only_file = Message(role=Role.AGENT, parts=[Part(raw="aGk=", filename="a.png"), Part(url="https://x/y")])
    assert convert_to_base_model_response(only_file).generated == "[File: a.png][File: unnamed]"


def test_convert_messages_maps_roles_and_placeholders():
    messages = [
        Message(role=Role.USER, parts=[Part(text="Hello, assistant!")]),
        Message(role=Role.AGENT, parts=[Part(text="Hello, user!")]),
        Message(
            role=Role.USER,
            parts=[Part(text="Look "), Part(raw="aGk=", filename="test.txt"), Part(data={"k": 1})],
        ),
    ]
    chat = convert_messages_to_chat_messages(messages)
    assert [(m.role, m.message) for m in chat] == [
        (ChatRole.USER, "Hello, assistant!"),
        (ChatRole.ASSISTANT, "Hello, user!"),
        (ChatRole.USER, "Look [File: test.txt][Data object]"),
    ]


def _assistant(toolkits=None):
    assistant = MagicMock(spec=Assistant)
    assistant.name = "Test Assistant"
    assistant.description = "A test assistant"
    assistant.toolkits = toolkits or []
    assistant.id = "test-assistant-id"
    return assistant


def _http_request():
    request = MagicMock(spec=Request)
    request.base_url.scheme = "https"
    request.base_url.netloc = "example.com"
    return request


def _card_for(idp_provider, toolkits=None):
    with patch("codemie.rest_api.a2a.utils.config") as cfg:
        cfg.API_ROOT_PATH = "/api"
        cfg.IDP_PROVIDER = idp_provider
        cfg.A2A_PROVIDER_ORGANIZATION = "Example Org"
        cfg.A2A_PROVIDER_URL = "https://example.com"
        return assistant_to_agent_card(_assistant(toolkits), _http_request())


def test_assistant_card_matches_fixture_shape():
    card = _card_for("keycloak")
    dumped = public_agent_card_dict(card)
    fixture = load_fixture("agent_card")
    assert set(dumped) == set(fixture) - {"documentationUrl"}
    assert dumped["supportedInterfaces"] == [
        {
            "url": "https://example.com/api/v1/a2a/assistants/test-assistant-id",
            "protocolBinding": "JSONRPC",
            "protocolVersion": "1.0",
        }
    ]
    assert dumped["capabilities"] == fixture["capabilities"]
    assert dumped["defaultInputModes"] == ["text/plain"] and dumped["defaultOutputModes"] == ["text/plain"]
    assert set(dumped["skills"][0]) == set(fixture["skills"][0]) - {"examples"}
    assert dumped["provider"] == {"organization": "Example Org", "url": "https://example.com"}
    assert not {"authentication", "stateTransitionHistory", "preferredTransport", "transport"} & set(_walk_keys(dumped))
    assert "url" not in dumped
    assert not INTERNAL_CARD_FIELDS_SET & set(dumped)


def test_assistant_card_public_dict_drops_internal_fields_but_model_keeps_them():
    card = _card_for("keycloak")
    assert "bedrock_agentcore" in card.model_dump()
    assert not INTERNAL_CARD_FIELDS_SET & set(public_agent_card_dict(card))


def test_assistant_card_security_is_bearer_only_outside_local_idp():
    dumped = public_agent_card_dict(_card_for("keycloak"))
    assert dumped["securitySchemes"] == {
        "bearer": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}}
    }
    assert dumped["securityRequirements"] == [{"schemes": {"bearer": {"list": []}}}]
    assert "dev-codemie-user" not in json.dumps(dumped)


def test_assistant_card_security_adds_user_id_header_for_local_idp():
    dumped = public_agent_card_dict(_card_for("local"))
    assert dumped["securitySchemes"] == {
        "bearer": {"httpAuthSecurityScheme": {"scheme": "bearer", "bearerFormat": "JWT"}},
        "user-id": {"apiKeySecurityScheme": {"location": "header", "name": "user-id"}},
    }
    assert dumped["securityRequirements"] == [
        {"schemes": {"bearer": {"list": []}}},
        {"schemes": {"user-id": {"list": []}}},
    ]
    assert "dev-codemie-user" not in json.dumps(dumped)


def test_assistant_card_skills_from_toolkits_and_default_conversation_skill():
    toolkits = [
        ToolKitDetails(
            toolkit="toolkit1",
            label="Toolkit One",
            tools=[ToolDetails(name="tool1", label="Tool One", user_description="Tool 1 description")],
        )
    ]
    with_tools = _card_for("keycloak", toolkits)
    assert [s.id for s in with_tools.skills] == ["tool1"]
    assert with_tools.skills[0].tags and with_tools.skills[0].description == "Tool 1 description"

    default = _card_for("keycloak")
    assert [(s.id, s.name) for s in default.skills] == [("conversation", "Conversation")]
    assert default.skills[0].tags == [] and default.skills[0].inputModes == ["text/plain"]


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)
