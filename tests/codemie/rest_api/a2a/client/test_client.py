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

"""Unit tests for the A2AClient class."""

import json
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx_sse import SSEError

from codemie.rest_api.a2a.client.client import A2AClient
from codemie.rest_api.a2a.types import (
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AProtocolVersionError,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentProvider,
    CancelTaskRPCResponse,
    GetTaskRPCResponse,
    Message,
    Part,
    Role,
    SendMessageResponse,
    SendMessageRPCResponse,
    StreamResponse,
    StreamRPCResponse,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from tests.codemie.rest_api.a2a.fixtures import load_fixture

_DUMP = {"by_alias": True, "exclude_none": True}


@pytest.fixture
def agent_card():
    """Create a sample AgentCard for testing."""
    return AgentCard(
        name="Test Agent",
        description="A test agent",
        supportedInterfaces=[AgentInterface(url="https://test-agent.example.com/api/agent", protocolBinding="JSONRPC")],
        version="1.0.0",
        capabilities=AgentCapabilities(streaming=True),
        provider=AgentProvider(organization="Test Org"),
        skills=[],
    )


@pytest.fixture
def sample_message():
    """Create a sample Message for testing."""
    return Message(role=Role.USER, parts=[Part(text="Hello, agent!")])


@pytest.fixture
def sample_task():
    """Create a sample Task for testing."""
    return Task(
        id="task-123",
        contextId="session-123",
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role=Role.AGENT, parts=[Part(text="Task completed")]),
        ),
    )


def _http_response(body=None, status_code=200):
    response = MagicMock()
    response.status_code = status_code
    if body is None:
        response.json.side_effect = json.JSONDecodeError("Invalid JSON", "", 0)
    else:
        response.json.return_value = body
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}", request=MagicMock(), response=response
        )
    return response


@contextmanager
def _posting(response, post_error=None):
    """Patch httpx.AsyncClient so client.post returns `response`; yields (client class mock, client instance)."""
    with patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient') as mock_async_client:
        instance = AsyncMock()
        instance.post = AsyncMock(return_value=response, side_effect=post_error)
        mock_async_client.return_value.__aenter__.return_value = instance
        yield mock_async_client, instance


class TestA2AClientInitialization:
    """Tests for A2AClient initialization."""

    def test_init_with_agent_card(self, agent_card):
        client = A2AClient(agent_card=agent_card)

        assert client.url == "https://test-agent.example.com/api/agent"
        assert client.agent_card == agent_card
        assert client.user_id is None
        assert client.project_name is None
        assert client.integration_id is None

    def test_init_with_url(self):
        client = A2AClient(url="https://custom-agent.example.com/api")

        assert client.url == "https://custom-agent.example.com/api"
        assert client.agent_card is None

    def test_init_with_all_params(self, agent_card):
        client = A2AClient(
            agent_card=agent_card,
            user_id="user-123",
            project_name="test-project",
            integration_id="integration-123",
        )

        assert client.url == "https://test-agent.example.com/api/agent"
        assert client.user_id == "user-123"
        assert client.project_name == "test-project"
        assert client.integration_id == "integration-123"

    def test_init_without_agent_card_or_url(self):
        with pytest.raises(ValueError, match="Must provide either agent_card or url"):
            A2AClient()


class TestA2AClientGetHeader:
    """Tests for A2AClient._get_header method."""

    @patch('codemie.rest_api.a2a.client.client.SettingsService')
    @patch('codemie.rest_api.a2a.client.client.get_auth_header')
    def test_get_header_success(self, mock_get_auth_header, mock_settings_service, agent_card):
        client = A2AClient(
            agent_card=agent_card, user_id="user-123", project_name="test-project", integration_id="integration-123"
        )
        mock_creds = {"auth_type": "bearer", "auth_value": "test_token"}
        mock_settings_service.get_a2a_creds.return_value = mock_creds
        mock_get_auth_header.return_value = {"Authorization": "Bearer test_token"}

        result = client._get_header()

        assert result == {"Authorization": "Bearer test_token", "A2A-Version": "1.0"}
        mock_settings_service.get_a2a_creds.assert_called_once_with(
            user_id="user-123", project_name="test-project", integration_id="integration-123"
        )
        mock_get_auth_header.assert_called_once_with(mock_creds, 'POST', None, None)

    @patch('codemie.rest_api.a2a.client.client.SettingsService')
    @patch('codemie.rest_api.a2a.client.client.logger')
    def test_get_header_failure(self, mock_logger, mock_settings_service, agent_card):
        client = A2AClient(agent_card=agent_card, user_id="user-123", project_name="test-project")
        mock_settings_service.get_a2a_creds.side_effect = Exception("Credentials not found")

        result = client._get_header()

        assert result == {"A2A-Version": "1.0"}
        mock_logger.error.assert_called_once()

    @patch('codemie.rest_api.a2a.client.client.SettingsService.get_a2a_creds')
    def test_get_header_aws_signature(self, mock_get_a2a_creds):
        mock_get_a2a_creds.return_value = {
            'auth_type': 'aws_signature',
            'aws_access_key_id': '***',
            'aws_region': 'eu-central-1',
            'aws_secret_access_key': '***',
            'aws_service_name': 'bedrock-agentcore',
        }

        client = A2AClient(
            agent_card=AgentCard(
                name="Test Agent",
                description="A test agent",
                supportedInterfaces=[AgentInterface(url="https://signed-agent.test.com", protocolBinding="JSONRPC")],
                version="1.0.0",
                capabilities=AgentCapabilities(streaming=True),
                provider=AgentProvider(organization="Test Org"),
                skills=[],
                user_id="user-123",
                project_name="test-project",
                integration_id="integration-123",
            )
        )

        headers = client._get_header(method="GET", url=client.url, body=None)

        assert 'Authorization' in headers
        assert 'Content-Type' in headers
        assert 'X-Amz-Date' in headers
        assert headers['Authorization'].startswith('AWS4-HMAC-SHA256 Credential=')


class TestA2AClientUrlResolution:
    def test_url_from_supported_interfaces(self, agent_card):
        assert A2AClient(agent_card=agent_card).url == "https://test-agent.example.com/api/agent"

    def test_url_override_wins(self, agent_card):
        assert A2AClient(agent_card=agent_card, url="https://override").url == "https://override"

    def test_card_without_jsonrpc_interface_raises(self, agent_card):
        agent_card.supportedInterfaces = [AgentInterface(url="https://g", protocolBinding="GRPC")]
        with pytest.raises(ValueError, match="no JSON-RPC interface"):
            A2AClient(agent_card=agent_card)


@pytest.fixture
def _no_credentials():
    with (
        patch('codemie.rest_api.a2a.client.client.SettingsService'),
        patch('codemie.rest_api.a2a.client.client.get_auth_header', return_value={}),
    ):
        yield


@pytest.mark.usefixtures("_no_credentials")
class TestA2AClientSendTask:
    """Tests for A2AClient.send_task method."""

    @pytest.mark.asyncio
    async def test_send_task_posts_spec_request_body_and_headers(self, agent_card):
        fixture = load_fixture("send_message_request")
        with _posting(_http_response(load_fixture("send_message_response_task"))) as (mock_async_client, instance):
            await A2AClient(agent_card=agent_card).send_task(fixture["params"])
        assert "verify" in mock_async_client.call_args.kwargs
        kwargs = instance.post.call_args.kwargs
        sent = dict(kwargs["json"])
        assert sent.pop("id")
        assert sent == {k: v for k, v in fixture.items() if k != "id"}
        assert kwargs["headers"]["A2A-Version"] == "1.0"

    @pytest.mark.asyncio
    async def test_send_task_parses_task_result(self, agent_card):
        with _posting(_http_response(load_fixture("send_message_response_task"))):
            result = await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])
        assert isinstance(result, SendMessageRPCResponse)
        assert isinstance(result.result, SendMessageResponse)
        assert result.result.task.status.state == TaskState.COMPLETED
        assert result.result.message is None

    @pytest.mark.asyncio
    async def test_send_task_parses_message_result(self, agent_card):
        with _posting(_http_response(load_fixture("send_message_response_message"))):
            result = await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])
        assert result.result.task is None
        assert result.result.message.role == Role.AGENT

    @pytest.mark.asyncio
    async def test_send_task_returns_jsonrpc_error_envelope(self, agent_card):
        with _posting(_http_response(load_fixture("error_task_not_found"))):
            result = await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])
        assert result.result is None and result.error.code == -32001

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fixture", ["error_method_not_found", "error_invalid_request", "error_version_not_supported"]
    )
    @pytest.mark.parametrize("http_status", [200, 400])
    async def test_version_rejection_raises_protocol_version_error(self, agent_card, fixture, http_status):
        with (
            _posting(_http_response(load_fixture(fixture), status_code=http_status)),
            pytest.raises(A2AProtocolVersionError, match="does not support A2A protocol 1.0"),
        ):
            await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])

    @pytest.mark.asyncio
    async def test_other_jsonrpc_error_on_http_400_is_returned_not_raised(self, agent_card):
        with _posting(_http_response(load_fixture("error_invalid_params"), status_code=400)):
            result = await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])
        assert result.error.code == -32602

    @pytest.mark.asyncio
    async def test_send_task_http_error_without_jsonrpc_body(self, agent_card):
        with (
            _posting(_http_response({"detail": "boom"}, status_code=500)),
            pytest.raises(A2AClientHTTPError) as exc_info,
        ):
            await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_send_task_http_error_raised_by_transport(self, agent_card):
        response = MagicMock(status_code=502)
        error = httpx.HTTPStatusError("Bad gateway", request=MagicMock(), response=response)
        with _posting(response, post_error=error), pytest.raises(A2AClientHTTPError) as exc_info:
            await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_send_task_json_decode_error(self, agent_card):
        with _posting(_http_response(None)), pytest.raises(A2AClientJSONError):
            await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])


def _sse(fixture_name=None, body=None):
    return MagicMock(data=json.dumps(body if body is not None else load_fixture(fixture_name)))


class _AsyncEventSource:
    def __init__(self, events, status_code=200, error_body=None, content_type="text/event-stream"):
        self._events = events
        self.response = MagicMock(status_code=status_code)
        self.response.headers = {"content-type": content_type}
        self.response.aread = AsyncMock(return_value=b"")
        if error_body is not None:
            self.response.json.return_value = error_body
        else:
            self.response.json.side_effect = json.JSONDecodeError("x", "", 0)
        self.response.raise_for_status = MagicMock()
        if status_code >= 400:
            self.response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "err", request=MagicMock(), response=self.response
            )

    async def aiter_sse(self):
        for e in self._events:
            yield e


def _patch_sse(events, **source_kwargs):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=_AsyncEventSource(events, **source_kwargs))
    cm.__aexit__ = AsyncMock(return_value=False)
    return (
        patch('codemie.rest_api.a2a.client.client.aconnect_sse', return_value=cm),
        patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient'),
    )


_STREAM_PAYLOAD = load_fixture("send_message_request")["params"]


async def _collect(client):
    return [r async for r in client.send_task_streaming(_STREAM_PAYLOAD)]


@pytest.mark.usefixtures("_no_credentials")
class TestA2AClientSendTaskStreaming:
    """Tests for A2AClient.send_task_streaming method."""

    @pytest.mark.asyncio
    async def test_streaming_frames_parse_as_stream_response(self, agent_card):
        p_sse, p_http = _patch_sse([_sse("stream_task"), _sse("stream_status_update"), _sse("stream_artifact_update")])
        with p_sse, p_http:
            results = await _collect(A2AClient(agent_card=agent_card))
        assert all(isinstance(r, StreamRPCResponse) and isinstance(r.result, StreamResponse) for r in results)
        assert results[0].result.task.status.state == TaskState.SUBMITTED
        assert isinstance(results[1].result.statusUpdate, TaskStatusUpdateEvent)
        assert results[1].result.statusUpdate.status.state == TaskState.COMPLETED
        assert results[2].result.artifactUpdate.artifact.parts[0].text.startswith("Climate change")

    @pytest.mark.asyncio
    async def test_streaming_message_frame(self, agent_card):
        frame = _sse(
            body={"jsonrpc": "2.0", "id": "r", "result": load_fixture("send_message_response_message")["result"]}
        )
        p_sse, p_http = _patch_sse([frame])
        with p_sse, p_http:
            results = await _collect(A2AClient(agent_card=agent_card))
        assert results[0].result.message.role == Role.AGENT

    @pytest.mark.asyncio
    async def test_bare_v03_frames_are_no_longer_accepted(self, agent_card):
        bare = _sse(
            body={
                "jsonrpc": "2.0",
                "id": "r",
                "result": {"taskId": "t", "contextId": "c", "status": {"state": "TASK_STATE_WORKING"}, "final": False},
            }
        )
        p_sse, p_http = _patch_sse([bare])
        with p_sse, p_http, pytest.raises(A2AClientJSONError):
            await _collect(A2AClient(agent_card=agent_card))

    @pytest.mark.asyncio
    async def test_streaming_request_body_headers_and_client(self, agent_card):
        p_sse, p_http = _patch_sse([_sse("stream_status_update")])
        with p_sse as mock_sse, p_http as mock_http:
            await _collect(A2AClient(agent_card=agent_card))
        assert "verify" in mock_http.call_args.kwargs
        kwargs = mock_sse.call_args.kwargs
        assert kwargs["json"]["method"] == "SendStreamingMessage"
        assert kwargs["json"]["params"] == load_fixture("send_message_request")["params"]
        assert kwargs["headers"]["A2A-Version"] == "1.0"

    @pytest.mark.asyncio
    async def test_error_frame_is_yielded_for_ordinary_errors(self, agent_card):
        p_sse, p_http = _patch_sse([_sse("error_task_not_found")])
        with p_sse, p_http:
            results = await _collect(A2AClient(agent_card=agent_card))
        assert results[0].error.code == -32001

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fixture", ["error_method_not_found", "error_invalid_request", "error_version_not_supported"]
    )
    async def test_version_rejection_frame_raises(self, agent_card, fixture):
        p_sse, p_http = _patch_sse([_sse(fixture)])
        with p_sse, p_http, pytest.raises(A2AProtocolVersionError):
            await _collect(A2AClient(agent_card=agent_card))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fixture", ["error_method_not_found", "error_invalid_request", "error_version_not_supported"]
    )
    async def test_non_200_json_rpc_body_raises_protocol_version_error(self, agent_card, fixture):
        p_sse, p_http = _patch_sse([], status_code=400, error_body=load_fixture(fixture))
        with p_sse, p_http, pytest.raises(A2AProtocolVersionError):
            await _collect(A2AClient(agent_card=agent_card))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "fixture", ["error_method_not_found", "error_invalid_request", "error_version_not_supported"]
    )
    async def test_200_non_sse_jsonrpc_body_raises_protocol_version_error(self, agent_card, fixture):
        """A streaming remote that rejects v1.0 with HTTP 200 and a JSON-RPC error body (not an SSE stream)."""
        p_sse, p_http = _patch_sse([], content_type="application/json", error_body=load_fixture(fixture))
        with p_sse, p_http, pytest.raises(A2AProtocolVersionError):
            await _collect(A2AClient(agent_card=agent_card))

    @pytest.mark.asyncio
    async def test_non_200_without_jsonrpc_body_raises_http_error(self, agent_card):
        p_sse, p_http = _patch_sse([], status_code=503)
        with p_sse, p_http, pytest.raises(A2AClientHTTPError) as exc_info:
            await _collect(A2AClient(agent_card=agent_card))
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_send_task_streaming_json_error(self, agent_card):
        p_sse, p_http = _patch_sse([MagicMock(data="invalid json")])
        with p_sse, p_http, pytest.raises(A2AClientJSONError):
            await _collect(A2AClient(agent_card=agent_card))

    @pytest.mark.asyncio
    async def test_send_task_streaming_validation_error(self, agent_card):
        frame = _sse(body={"jsonrpc": "2.0", "id": "r", "result": {"task": {"bogus": 1}}})
        p_sse, p_http = _patch_sse([frame])
        with p_sse, p_http, pytest.raises(A2AClientJSONError):
            await _collect(A2AClient(agent_card=agent_card))

    @pytest.mark.asyncio
    async def test_send_task_streaming_sse_error(self, agent_card):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=SSEError("Expected response Content-Type to be 'text/event-stream'"))
        cm.__aexit__ = AsyncMock(return_value=False)
        with (
            patch('codemie.rest_api.a2a.client.client.aconnect_sse', return_value=cm),
            patch('codemie.rest_api.a2a.client.client.httpx.AsyncClient'),
            pytest.raises(A2AClientJSONError),
        ):
            await _collect(A2AClient(agent_card=agent_card))


@pytest.mark.usefixtures("_no_credentials")
class TestA2AClientGetTask:
    """Tests for A2AClient.get_task method."""

    @pytest.mark.asyncio
    async def test_get_task_success(self, agent_card):
        with _posting(_http_response(load_fixture("get_task_response"))) as (_, instance):
            result = await A2AClient(agent_card=agent_card).get_task({"id": "task-123", "historyLength": 3})
        assert isinstance(result, GetTaskRPCResponse)
        assert result.result.id == "43667960-d455-4453-b0cf-1bae4955270d"
        body = instance.post.call_args.kwargs["json"]
        assert body["method"] == "GetTask" and body["params"] == {"id": "task-123", "historyLength": 3}


@pytest.mark.usefixtures("_no_credentials")
class TestA2AClientCancelTask:
    """Tests for A2AClient.cancel_task method."""

    @pytest.mark.asyncio
    async def test_cancel_task_success(self, agent_card):
        with _posting(_http_response(load_fixture("cancel_task_response"))) as (_, instance):
            result = await A2AClient(agent_card=agent_card).cancel_task({"id": "task-123"})
        assert isinstance(result, CancelTaskRPCResponse)
        assert result.result.status.state == TaskState.CANCELED
        body = instance.post.call_args.kwargs["json"]
        assert body["method"] == "CancelTask" and body["params"] == {"id": "task-123"}


class TestMalformedRemoteBodies:
    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_no_credentials")
    @pytest.mark.parametrize("method", ["send_task", "get_task", "cancel_task"])
    async def test_non_conformant_body_raises_client_json_error(self, agent_card, method):
        payload = load_fixture("send_message_request")["params"] if method == "send_task" else {"id": "t"}
        bad = {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {"unexpected": True},
            "error": {"code": 1, "message": "m", "data": "not-a-list"},
        }
        with _posting(_http_response(bad)), pytest.raises(A2AClientJSONError):
            await getattr(A2AClient(agent_card=agent_card), method)(payload)


@pytest.mark.usefixtures("_no_credentials")
class TestTlsVerification:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("verify", [True, False])
    async def test_post_uses_configured_verify(self, agent_card, verify):
        with (
            patch("codemie.rest_api.a2a.client.client.config") as cfg,
            _posting(_http_response(load_fixture("send_message_response_task"))) as (client_cls, _),
        ):
            cfg.HTTPS_VERIFY_SSL = verify
            await A2AClient(agent_card=agent_card).send_task(load_fixture("send_message_request")["params"])
        assert client_cls.call_args.kwargs["verify"] is verify

    @pytest.mark.asyncio
    @pytest.mark.parametrize("verify", [True, False])
    async def test_stream_uses_configured_verify(self, agent_card, verify):
        p_sse, p_http = _patch_sse([_sse("stream_status_update")])
        with patch("codemie.rest_api.a2a.client.client.config") as cfg, p_sse, p_http as mock_http:
            cfg.HTTPS_VERIFY_SSL = verify
            await _collect(A2AClient(agent_card=agent_card))
        assert mock_http.call_args.kwargs["verify"] is verify
